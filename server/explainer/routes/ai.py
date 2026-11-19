"""The AI endpoints (all signed in; each counts toward the user's daily allowance, quota.py):

  POST /api/generate  -> SSE stream of GenerateEvent (progress, then the spec). With "recordId", the server
                         also saves progress and the result to that history record (history.py), so a
                         generation finishes even if the tab is closed.
  POST /api/chat      -> SSE stream of {t: text} deltas, then {done: true}
  POST /api/followup/plan -> JSON {kind: change|ask|cannot|answer|revert, ...} | {error}, + usd: what the follow-up
                         asks (modes/followup/entry.py: v4's talk step under FOLLOWUP_V4=1, else v3's understand)
  POST /api/section   -> JSON {md, sources, searches, usd}: a section tool the reader clicked (web search)
  POST /api/formula   -> JSON {note, usd}: "Go deeper" on a formula (formula.py); the app saves the note on the page

/api/generate runs only Reader and Explorer (modes/); anything else is refused (400 mode_off).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from flask import Blueprint, Response, current_app, g
from limits import parse as parse_limit

from .. import data, history, quota, sse
from ..modes.followup import entry as followup_entry  # POST /api/followup/plan (v3, v4 or v5)
from ..modes.followup import run as followup_run  # is_agent: a follow-ups v5 run (past the day's explainers: answer-only)
from ..formula import formula_turn, parse_formula
from ..llm import HAIKU, friendly_error
from ..middleware import ApiError, ctx, json_body, json_response, rate_limit, require_auth
from .. import modes  # Reader and Explorer's own pipelines (new pages)
from ..modes import intake  # FIRST_TALK: a new page's message is read first (answered, or built from a brief)
from ..pricing import UsageLog, price_of
from ..requests import parse_chat, parse_generate
from ..schemas import RECORD_ID
from ..section import parse_section, section_turn

log = logging.getLogger("explainer")
bp = Blueprint("ai", __name__, url_prefix="/api")

# Per-user caps: generations, and chat/follow-up plan/section/formula messages together.
GEN_LIMIT = parse_limit("3/minute")
CHAT_LIMIT = parse_limit("20/minute")

DEBUG_FLAGS = ("debugRejected",)  # scripts/variety.mjs only, with ALLOW_DEBUG=1 (audit F10)
# What /api/generate answers for anything Reader and Explorer don't run (an old mode's page, an old app tab, a script).
MODE_OFF_MESSAGE = "This page was made with an older mode that's no longer available. Start a new conversation to change it."


class GenerationCancelled(BaseException):
    """The record (or account) was deleted while its generation ran (audit F18). A BaseException, so the
    pipeline's own `except Exception` fallbacks let it through and the run stops at its next step."""


def _policy(ai: dict[str, Any], req: dict) -> dict:
    """What the server allows of a parsed GenerateRequest, whatever the client sent."""
    if not ai.get("allow_debug"):
        req = {k: v for k, v in req.items() if k not in DEBUG_FLAGS}
    return req


def _ai() -> dict[str, Any]:
    """The Anthropic client and AI settings (made once in create_app)."""
    ai = current_app.extensions["explainer_ai"]
    if ai["client"] is None:
        raise ApiError(500, "Server is missing ANTHROPIC_API_KEY.", "misconfigured")
    return ai


def _sse(work: Callable) -> Response:
    return Response(sse.stream(work), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _parsed(parse: Callable[[Any], Any], body: Any = None) -> Any:
    """The parsed request, or a 400. body: the JSON body when the route already read it (it needs more of it)."""
    req = parse(json_body() if body is None else body)
    if isinstance(req, str):
        raise ApiError(400, req, "invalid")
    return req


@bp.post("/generate")
@require_auth
def generate_route() -> Response:
    ai = _ai()
    c = ctx()
    user_id = g.user.id
    rid = g.get("request_id")  # the stream runs in a thread, without the request context
    rate_limit(GEN_LIMIT, "gen", user_id, "Too many explainers at once. Please wait a minute.")
    body = json_body()
    req = _parsed(parse_generate, body)
    req = _policy(ai, req)
    # Only Reader and Explorer run (modes/): anything else is refused before it counts as one of today's explainers.
    if not modes.handles(req):
        raise ApiError(400, MODE_OFF_MESSAGE, "mode_off")
    # recordId: the history record to fill. baseId: the record a revise follow-up changes; replaces: the record a Retry
    # or Edit replaces (both decide the version number, history.version_meta).
    ids = {k: body.get(k) if isinstance(body, dict) else None for k in ("recordId", "baseId", "replaces")}
    for v in ids.values():
        if v is not None and (not isinstance(v, str) or not RECORD_ID.match(v)):
            raise ApiError(400, "Invalid record id.", "invalid")
    record_id = ids["recordId"]
    # Follow-ups v5: the agent also answers questions, which must keep working past the day's explainers; then it runs
    # answer-only (no page, so not counted).
    # The first-message step (intake.py) answers too, so it runs past the day's explainers in the same way.
    agent = followup_run.is_agent(req) or intake.runs(req)
    answer_only = agent and quota.over_daily(c.settings, c.db, user_id)
    event_id = quota.start_generation(c.settings, c.db, user_id, allow_over=agent)
    try:
        if record_id:
            history.start_generation(c.db, user_id, record_id, req, ids["baseId"], ids["replaces"])
    except Exception:
        # The run never starts, so it mustn't count as one of today's or stay "running" (audit F12).
        quota.finish_generation(c.db, event_id, 0.0, failed=True)
        raise

    def work(send) -> None:
        def emit(e: dict) -> None:
            if record_id and not history.generation_event(c.db, user_id, record_id, e):
                raise GenerationCancelled()
            send(e)

        # Made here, not in the pipeline, so a run that fails partway still has its spend counted.
        usage = UsageLog("")
        usd, failed = 0.0, True
        try:
            kw = {"jev_key": ai["jev_key"], "plan_effort": ai["plan_effort"], "paper_cache": ai["paper_cache"], "usage": usage,
                  "db": c.db, "user_id": user_id, "base_id": ids["baseId"],  # follow-ups v5 load the base from the version store
                  **({"answer_only": True} if answer_only else {})}
            r = modes.generate(ai["client"], req, emit, page_index=ai.get("page_index"), **kw)
            u = r["usage"]
            usd, failed = u["totalUsd"], False
            cached = sum(s["cacheRead"] for s in u["steps"])
            fresh = sum(s["input"] + s["cacheWrite"] for s in u["steps"])
            log.info('generated "%s" (%s, %s effort) $%.4f in %ss; prompt cache %d%% of input', r["spec"]["title"], u["quality"], u["effort"],
                     usd, u["seconds"], round(100 * cached / max(1, cached + fresh)))
            # Explorer test runs (scripts/variety.mjs, debugRejected): what Jev's review rejected, the writer's marks, the look check.
            debug = {k: r[k] for k in ("rejected", "labMarks", "labLook") if req.get("debugRejected") and r.get(k)}
            # startFrom: a v5 follow-up that went back to another version (history gives the new version that parent).
            emit({"type": "done", "spec": r["spec"], "usage": u, **debug, **({"startFrom": r["startFrom"]} if r.get("startFrom") else {})})
        except GenerationCancelled:
            usd = usage.summary()["totalUsd"]  # what was spent until it stopped still counts
            log.info("generation for deleted record %s stopped after $%.4f", record_id, usd)
            send({"type": "error", "message": "This explainer was deleted, so its generation was stopped."})
        except Exception as e:
            usd = usage.summary()["totalUsd"]
            log.exception("generate failed after $%.4f", usd)
            try:
                emit({"type": "error", "message": friendly_error(e, rid)})
            except GenerationCancelled:
                pass
        finally:
            quota.finish_generation(c.db, event_id, usd, failed=failed)

    return _sse(work)


def _chat_turn(kind: str, message: str, run: Callable[[Any], dict], parse: Callable[[Any], Any]) -> Response:
    """A one-shot JSON call (route, follow-up plan, section, formula): limits, allowance, then the reply with its cost recorded."""
    ai = _ai()
    c = ctx()
    rate_limit(CHAT_LIMIT, "chat", g.user.id, message)
    req = _parsed(parse)
    quota.check_spend(c.settings, c.db, g.user.id)
    try:
        r = run(ai["client"], req)
    except Exception as e:
        log.exception("%s failed", kind)
        # A turn that failed after a paid reply (an unusable answer, a later search call) says what it spent (audit F23).
        spent = getattr(e, "spent_usd", 0.0) or 0.0
        if spent > 0:
            quota.record(c.settings, c.db, g.user.id, kind, spent)
        raise ApiError(502, friendly_error(e), "upstream_error") from None
    quota.record(c.settings, c.db, g.user.id, kind, r.get("usd") or 0.0)
    if kind == "section":
        log.info("section %s: %d searches, $%.4f", req["kind"], r["searches"], r["usd"])
    return json_response(r)


@bp.post("/followup/plan")
@require_auth
def followup_plan_route() -> Response:
    """A typed follow-up on a Reader or Explorer page, read by modes/followup/entry.py (v4's talk step under FOLLOWUP_V4=1,
    else v3's understand): a change, a question, a refusal, an answer or an undo. Not one of the day's explainers; its cost
    is recorded."""
    ai = _ai()
    return _chat_turn("followup-plan", "Too many messages at once. Please wait a minute.",
                      lambda client, req: followup_entry.handle(client, ai.get("page_index"), req, db=ctx().db, user_id=g.user.id,
                                                                jev_key=ai["jev_key"]),
                      followup_entry.parse_plan_request)


@bp.post("/section")
@require_auth
def section_route() -> Response:
    return _chat_turn("section", "Too many requests at once. Please wait a minute.", section_turn, parse_section)


@bp.post("/formula")
@require_auth
def formula_route() -> Response:
    """"Go deeper" on a formula (formula.py): one Haiku call on the click; the app saves the note on the page."""
    return _chat_turn("formula", "Too many requests at once. Please wait a minute.", formula_turn, parse_formula)


def chat_system(context: str) -> list[dict]:
    """The chatbot's system blocks, built on the server only (audit F03): fixed rules first, then the
    client's explainer context wrapped in tags as data. A closing tag inside it can't end the block early."""
    context = re.sub(r"<\s*/?\s*explainer_context\s*>", "[explainer context tag removed]", context, flags=re.I)
    return [
        {"type": "text", "text": f"{data.prompt('CHAT_SYSTEM')}\n\n{data.prompt('CHAT_RULES')}"},
        # The explainer's context is the same on every question about it: cached.
        {"type": "text", "text": f"<explainer_context>\n{context}\n</explainer_context>", "cache_control": {"type": "ephemeral"}},
    ]


@bp.post("/chat")
@require_auth
def chat_route() -> Response:
    ai = _ai()
    c = ctx()
    user_id = g.user.id
    rid = g.get("request_id")  # the stream runs in a thread, without the request context
    rate_limit(CHAT_LIMIT, "chat", user_id, "Too many questions. Please wait a minute.")
    req = _parsed(parse_chat)
    quota.check_spend(c.settings, c.db, user_id)
    client = ai["client"]

    def chat(send) -> None:
        opened = None  # the stream, once it started: a stream that fails partway was still billed (audit F23)
        recorded = False
        try:
            with client.messages.stream(
                model=HAIKU,
                max_tokens=4000,
                output_config={"effort": "low"},
                system=chat_system(req["context"]),
                messages=req["messages"],
            ) as stream:
                opened = stream
                for t in stream.text_stream:
                    send({"t": t})
                msg = stream.get_final_message()
            recorded = True
            quota.record(c.settings, c.db, user_id, "chat", price_of(msg.model, msg.usage))
            if msg.stop_reason == "refusal":
                send({"t": "\n\n_Sorry, I can't help with that one._"})
            send({"done": True})
        except Exception as e:
            log.exception("chat failed")
            if opened is not None and not recorded:
                try:
                    snap = opened.current_message_snapshot
                    quota.record(c.settings, c.db, user_id, "chat", price_of(snap.model, snap.usage))
                except Exception:
                    log.exception("couldn't record the spend of a failed chat")
            send({"error": friendly_error(e, rid)})

    return _sse(chat)
