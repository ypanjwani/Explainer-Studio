"""Follow-ups v4, step 1 (POST /api/followup/plan when FOLLOWUP_V4=1): the reader's follow-up read against the page.

shortcut  "undo" and the like: revert, no AI call
option    the reader picked an option of a question (no added words): the brief is the request plus that choice, no AI call
talk      one Haiku Low call (FOLLOWUP_TALK_SYSTEM, format followupTalk) with the page map, the page's text, what each
          playground really shows (readings, Explorer pages; no AI) and the conversation: answer | ask (2-4 options) | cannot (+ alternative) | new page | revert | change (one brief)
The reply has the v3 PlanResponse shape, so the app needs no flag: a change is {sections: [], steps: [], v4: {brief}}.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from .... import data
from ....js import js_slice, json_parse
from ....llm import HAIKU, cached_system, text_of
from ....pricing import UsageLog
from ...explorer.playground import read_playground, readings_text
from .. import page_index as pidx
from .. import playground_edit as pe
from ..cards import page_text
from ..shared import EARLIER_ANSWER_CHARS, history_text, is_undo, last_answer

log = logging.getLogger("explainer.followup.v4")

PAGE_CHARS = 24_000  # the whole page (cards.page_text, every block in full; Nov 13 2026, was outline_context at 12k)
BRIEF_CHARS = 2000
ANSWER_CHARS = 3000
MAX_OPTIONS = 4
READINGS_CHARS = 2500  # each playground's readings for the talk step (was 1200, which cut readouts off)
MAX_PLAYGROUNDS = 4
# Haiku thinks by default and thinking counts against max_tokens; 3000 ran out before any answer on a 4-playground page.
# Non-streaming, so it stays under the SDK's ~21k limit.
TALK_TOKENS = 12_000
SORRY = "Something went wrong while reading your request. Please try again."


def _clip(s: Any, n: int) -> str:
    return js_slice(" ".join(str(s or "").split()), 0, n)


def _option(label: str) -> dict:
    return {"label": label, "sections": [], "steps": []}


def change(brief: str) -> dict:
    """A change for the editor. plan() adds v4.earlier (shared.last_answer) when the reader's previous message got an
    answer, so "fix those" after a check reaches the editor with the check itself, not only the brief's restatement."""
    brief = js_slice(brief.strip(), 0, BRIEF_CHARS)
    return {"kind": "change", "sections": [], "steps": [], "v4": {"brief": brief}, "summary": [brief], "confirm": False}


def playground_readings(page: dict, quality: str) -> str:
    """What each playground on an Explorer page really shows (explorer.playground.read_playground at the default settings:
    the start, each slider's ends, options, Play), so a question like "is this playground right?" is answered from its
    numbers, not from what the text claims. "" on Reader pages or when nothing can be run."""
    if quality == "lite-plus":
        return ""
    parts: list[str] = []
    for i, st in enumerate(page.get("stages") or []):
        for b in st.get("blocks") or []:
            if len(parts) >= MAX_PLAYGROUNDS or b.get("type") != "widget" or pe.widget_kind(b) != "playground":
                continue
            spec = pe.read_playground_spec(b.get("html", ""))
            text = readings_text(read_playground(spec, None, READINGS_CHARS)) if spec else ""
            if text:
                parts.append(f'S{i + 1} "{st.get("label", "")}", playground "{b.get("title") or ""}":\n{text}')
    return ("What each playground really shows (run at a few settings):\n\n" + "\n\n".join(parts)) if parts else ""


def ask_model(client: anthropic.Anthropic, index: dict, page: dict, quality: str, text: str, usage: UsageLog, history: list[str],
              option: dict | None, extra: str) -> Any:
    try:
        body = js_slice(page_text(page), 0, PAGE_CHARS)
    except Exception:
        body = ""
    chose = f'\n\nThe reader was asked what they meant and chose: "{option["label"]}". They added: {extra}' if option else ""
    past = history_text(history)
    try:
        shown = playground_readings(page, quality)
    except Exception:
        log.exception("Follow-up v4 talk: couldn't read the playgrounds")
        shown = ""
    user = (f"{pidx.map_text(index, quality)}\n\nThe page's text:\n{body}\n\n{shown + chr(10) + chr(10) if shown else ''}"
            f"{past + chr(10) + chr(10) if past else ''}"
            f"The reader's follow-up: {text}{chose}")
    def call(thinking: bool) -> Any:
        params: dict = dict(
            model=HAIKU, max_tokens=TALK_TOKENS,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": data.formats()["followupTalk"]}},
            system=cached_system(data.prompt("FOLLOWUP_TALK_SYSTEM")),
            messages=[{"role": "user", "content": user}],
        )
        if not thinking:
            params["thinking"] = {"type": "disabled"}
        msg = client.messages.create(**params)
        usage.add_message("follow-up talk", msg)
        return msg

    msg = call(True)
    if getattr(msg, "stop_reason", None) == "max_tokens" and not text_of(msg).strip():
        # Haiku's adaptive thinking used the whole budget before writing anything (Nov 14: "are all graphs correct?" on a
        # page with 4 playgrounds' readings). Once more without thinking, so the reader still gets an answer.
        log.warning("Follow-up v4 talk: thinking used all %d tokens; again without thinking", TALK_TOKENS)
        msg = call(False)
    return json_parse(text_of(msg))


def plan(client: anthropic.Anthropic, store: pidx.PageIndexStore | None, page: dict, text: str, quality: str, usage: UsageLog,
         option: dict | None = None, extra: str = "", history: list[str] | None = None) -> dict:
    history = history or []
    if option and not extra:
        return change(f'The reader asked: "{text}". Asked what they meant, they chose: "{option.get("label", "")}". Do that.')
    if not option and is_undo(text):  # no AI call (shared.is_undo)
        return {"kind": "revert"}
    index = pidx.get_or_build(store, client, page, usage)
    try:
        raw = ask_model(client, index, page, quality, text, usage, history, option, extra)
    except Exception:
        log.exception("Follow-up v4 talk: the call failed")
        return {"error": SORRY}
    if not isinstance(raw, dict):
        return {"error": SORRY}
    out = _reply(raw, text, option, extra)
    if out.get("kind") == "change" and (earlier := last_answer(history)):
        out["v4"]["earlier"] = js_slice(earlier, 0, EARLIER_ANSWER_CHARS)
    return out


def _reply(raw: dict, text: str, option: dict | None, extra: str) -> dict:
    """The talk call's JSON as a PlanResponse."""
    kind = raw.get("kind")
    log.info("Follow-up v4 talk: %s", kind)
    reply = js_slice(str(raw.get("reply") or "").strip(), 0, ANSWER_CHARS)
    if kind == "revert":
        return {"kind": "revert"}
    if kind == "answer" and reply:
        return {"kind": "answer", "answer": reply}
    if kind == "new_page":
        return {"kind": "cannot", "reason": reply or "That sounds like a different topic. Start a new explainer for it!", "alternative": None, "newTopic": True}
    if kind == "cannot":
        alt = _clip((raw.get("alternative") or {}).get("label") if isinstance(raw.get("alternative"), dict) else "", 160)
        return {"kind": "cannot", "reason": reply or "This page can't do that, sorry.", "alternative": _option(alt) if alt else None}
    if kind == "ask":
        q = raw.get("question") if isinstance(raw.get("question"), dict) else {}
        labels = [_clip(o.get("label"), 120) for o in (q.get("options") or []) if isinstance(o, dict)]
        labels = [x for x in labels if x][:MAX_OPTIONS]
        if len(labels) >= 2 and _clip(q.get("text"), 10):
            return {"kind": "ask", "question": {"text": _clip(q["text"], 300), "options": [_option(x) for x in labels]}}
        if len(labels) == 1:
            return change(f'The reader asked: "{text}". Do this: {labels[0]}')
    brief = str(raw.get("brief") or "").strip()
    if kind in ("change", "ask") and brief:
        return change(brief)
    if kind == "change":  # no brief written: the reader's own words are the brief
        return change(f'The reader asked: "{text}"' + (f' (they chose "{option["label"]}" and added: {extra})' if option else ""))
    return {"error": "I couldn't tell what you'd like me to do. Could you say it another way?"}


def handle(client: anthropic.Anthropic, store: pidx.PageIndexStore | None, req: dict) -> dict:
    usage = UsageLog("followup-plan", "low")
    out = plan(client, store, req["page"], req["text"], req["quality"], usage, req.get("option"), req.get("extra") or "", req.get("history"))
    return {**out, "usd": usage.summary()["totalUsd"]}
