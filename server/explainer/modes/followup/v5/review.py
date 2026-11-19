"""Follow-ups v5: the after-edit review (.claude/followup-v5-plan.md §5). Since phase 4 (Nov 16 2026) the agent
(agent.py) runs it at its first finish after a change (and on check_changes) and gets the findings as feedback; the final
guard puts back the cause of a problem the agent left alone.

skip      only a title or look change (versions.diff: no section added, removed, replaced or moved; changed fields within
          title and design) is not reviewed
review    one Haiku Low call with read tools (search_page and get_section on the new page or the base), ending with the
          report tool (FOLLOWUP_V5_REVIEW_SYSTEM, format followupV5Review): what this edit made wrong, each with the changed
          section that caused it. Only that (Nov 17 2026): no ideas for next changes (they made a new list under every
          reply, so "fix all" never ended) and no judging of the request (it sees only the last message, so after
          "fix those" it can't know what was asked; the agent, which reads the conversation, decides that).
Fails open: no review, no problems.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .... import data, versions
from ....llm import HAIKU, cached_system
from ....pricing import UsageLog
from .. import main_file as mf
from .page import Page, _clip

log = logging.getLogger("explainer.followup.v5")

LOOK_FIELDS = {"title", "design"}
REVIEW_ROUNDS = 3  # read rounds before the report is forced
REVIEW_TOKENS = 6000
DIFF_CHARS = 12_000
SECTION_CHARS = 8000
MAX_PROBLEMS = 6


def needs_review(before: dict, after: dict) -> bool:
    """Every change but a title-only or look-only one is reviewed."""
    d = versions.diff(before, after)
    if d["added"] or d["removed"] or d["replaced"] or d.get("order"):
        return True
    return not set(d["fields"]) <= LOOK_FIELDS


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


_WHICH = {"type": "string", "enum": ["new", "before"], "description": "\"new\": the page after this edit; \"before\": the page it was made from."}


def tools() -> list[dict]:
    out = [
        _tool("search_page", "Find every section that mentions some words (labels, terms, numbers, \"section 3\").",
              {"query": {"type": "string"}, "page": _WHICH}, ["query", "page"]),
        _tool("get_section", "Read one section in full.", {"section": {"type": "string"}, "page": _WHICH}, ["section", "page"]),
        {"name": "report", "description": "Your findings. Call it once, at the end.", "input_schema": data.formats()["followupV5Review"]},
    ]
    out[-1]["cache_control"] = {"type": "ephemeral"}
    return out


def _call(client: anthropic.Anthropic, usage: UsageLog, messages: list[dict], force: bool) -> Any:
    params: dict = dict(model=HAIKU, max_tokens=REVIEW_TOKENS, output_config={"effort": "low"},
                        system=cached_system(data.prompt("FOLLOWUP_V5_REVIEW_SYSTEM")), tools=tools(), messages=messages,
                        tool_choice={"type": "tool", "name": "report"} if force else {"type": "auto"})
    if force:
        params["thinking"] = {"type": "disabled"}  # a forced tool call can't think first
    with client.messages.stream(**params) as s:
        msg = s.get_final_message()
    usage.add_message("follow-up review", msg)
    return msg


def _stage(page: dict, ref: Any) -> dict | None:
    r = str(ref or "").strip()
    stages = page.get("stages") or []
    for st in stages:
        if st.get("id") == r:
            return st
    if r[:1] in "sS" and r[1:].isdigit() and 1 <= int(r[1:]) <= len(stages):
        return stages[int(r[1:]) - 1]
    return None


def _read(page: Page, name: str, inp: dict) -> str:
    which = page.current if inp.get("page") != "before" else page.before
    if name == "search_page":
        hits = versions.search(which, str(inp.get("query") or ""))
        return json.dumps(hits, ensure_ascii=False) if hits else "No section mentions that."
    if name == "get_section":
        st = _stage(which, inp.get("section"))
        return json.dumps(st, ensure_ascii=False)[:SECTION_CHARS] if st else "There is no such section on that page."
    return "There is no such tool."


def _overview(client: Any, usage: UsageLog, page: Page) -> str:
    """The new page's main file (section notes reused; nothing saved), or its plain overview without the store."""
    if page.store.on:
        try:
            return mf.describe(page.store.db, client, page.store.user_id, page.current, version=None, quality=page.quality, usage=usage)["text"]
        except Exception:
            log.exception("Follow-up v5 review: no main file for the new page")
    return json.dumps(page.overview(), ensure_ascii=False)


def _clip_stage(st: dict) -> str:
    return json.dumps(st, ensure_ascii=False)[:2500]


def _changes(page: Page) -> str:
    d = versions.diff(page.before, page.current)
    out = {"added": [{"id": s.get("id"), "label": s.get("label")} for s in d["added"]],
           "removed": [{"id": s.get("id"), "label": s.get("label"), "old": _clip_stage(s)} for s in d["removed"]],
           "replaced": [{"id": r["id"], "old": _clip_stage(r["before"])} for r in d["replaced"]],
           **({"new_order": d["order"]} if d.get("order") else {}),
           "page_fields": {k: v for k, v in d["fields"].items() if k not in LOOK_FIELDS}}
    return json.dumps(out, ensure_ascii=False)[:DIFF_CHARS]


def review(client: Any, usage: UsageLog, page: Page, request: str, brief: str) -> dict:
    """{"problems": [{section, cause, what}]} with ids checked; empty when it fails."""
    empty: dict = {"problems": []}
    if client is None:
        return empty
    first = json.dumps({"reader_asked": request, "brief": brief, "new_page": _overview(client, usage, page),
                        "what_changed": _changes(page)}, ensure_ascii=False)
    messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": first, "cache_control": {"type": "ephemeral"}}]}]
    try:
        for rnd in range(REVIEW_ROUNDS + 1):
            msg = _call(client, usage, messages, force=rnd == REVIEW_ROUNDS)
            calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            rep = next((c for c in calls if c.name == "report"), None)
            if rep is not None:
                return _checked(page, rep.input)
            if not calls:
                messages.append({"role": "assistant", "content": [_block(b) for b in msg.content]})
                messages.append({"role": "user", "content": [{"type": "text", "text": "Call report with your findings now."}]})
                continue
            messages.append({"role": "assistant", "content": [_block(b) for b in msg.content]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": _read(page, c.name, c.input or {})}
                                                         for c in calls]})
    except Exception:
        log.exception("Follow-up v5: the review failed; no review")
    return empty


def _block(b: Any) -> dict:
    return b.to_dict(exclude_none=True) if hasattr(b, "to_dict") else dict(b)


def _checked(page: Page, raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    present = {s["id"] for s in page.stages}
    changed = set(page.changed_ids()) | {s["id"] for s in page.before["stages"] if s["id"] not in present}
    problems = []
    for p in raw.get("problems") or []:
        if not isinstance(p, dict):
            continue
        st = _stage(page.current, p.get("section"))
        cause = str(p.get("cause") or "").strip()
        what = _clip(p.get("what"), 400)
        if st is None or not what:
            continue
        problems.append({"section": st["id"], "cause": cause if cause in changed else "", "what": what})
    return {"problems": problems[:MAX_PROBLEMS]}
