"""Follow-ups v4, step 2 (/api/generate, a revise follow-up whose plan carries v4.brief): Haiku edits the page with tools.

loop      Haiku (Thinking per the page's setting) gets the brief and the page overview, then calls tools (tools.py) until it
          calls finish or ask_user, at most MAX_ROUNDS rounds and the follow-up's spend cap (shared.max_usd); after
          that the tools are switched off. Each call is applied and checked by code at once and its result sent back.
ask       ask_user (only before any write) ends the run with Asked: the app shows the question, nothing is counted.
check     each changed section (the first CHECK_MAX) is judged by Jev against the request (check.judge); one that fails
          is put back alone (a new one is removed). Then each changed section with a playground: Jev checks its text
          against what the playground really shows (readings, explorer.judge.readings_contradict). A contradiction the
          change brought in puts the section back; one that was already there stays, as a note for the reply.
reply     reply.py writes the friendly reply from the log (never claims an undone change): spec.revision.reply.
nothing   no change survived: shared.Checked with the reply (a calm reply in the thread, not counted).
Returns {"outline", "warnings", "revision", "pageFields"} like v3's steps.run_plan (run.py takes either).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import anthropic

from .... import data
from ....jev import JevError
from ....llm import HAIKU, ReaderError, cached_system, check_stop, text_of
from ....pricing import UsageLog
from .. import check as jev_check
from ..revise import restore_kept_widgets
from ...explorer.judge import readings_contradict
from ..shared import CHECK_MAX, CHECKED, Checked, max_usd
from . import reply as reply_mod
from . import tools as T
from .page import Page

log = logging.getLogger("explainer.followup.v4")

ASKED = "? "  # keep equal to ASKED_PREFIX in shared/spec.ts
MAX_ROUNDS = 12
EDIT_TOKENS = 16_000
PROGRESS = {
    "get_section": "Reading a section", "get_playground": "Looking at a playground", "try_playground": "Trying a playground", "search_source": "Checking the source",
    "set_page_meta": "Changing the page title", "set_level": "Changing the level", "rename_section": "Renaming a section",
    "edit_text": "Rewording", "set_blocks": "Rewriting a section", "add_block": "Adding to a section", "edit_block": "Changing part of a section",
    "remove_block": "Removing part of a section", "add_section": "Adding a section", "remove_section": "Removing a section",
    "move_section": "Moving a section", "patch_playground": "Adjusting a playground", "replace_playground": "Rebuilding a playground",
    "add_playground": "Building a playground", "remove_playground": "Removing a playground",
}


class Asked(ReaderError):
    """The editor asked the reader before changing anything: ASKED + JSON {text, question: {text, options}}. The app shows
    the question (history._apply_generation stores it as status picking); not counted."""


def asked_message(q: dict) -> str:
    """ASKED + compact JSON {text, question: {text, options: [label]}} (kept well under the 1000 characters a generation's
    error holds; history._asked and the app turn the labels into plan options)."""
    return ASKED + json.dumps({"text": "Before I change anything, one quick question.",
                               "question": {"text": q["text"][:240], "options": [o[:100] for o in q["options"][:4]]}}, ensure_ascii=False)


def call(client: anthropic.Anthropic, usage: UsageLog, system: str, tools: list[dict], messages: list[dict], effort: str, think: bool,
         final: bool) -> Any:
    """One editor round (streamed: replies with thinking can be long). final: tools switched off, the model must stop."""
    params: dict = dict(model=HAIKU, max_tokens=EDIT_TOKENS, output_config={"effort": effort}, system=cached_system(system), tools=tools,
                        tool_choice={"type": "none"} if final else {"type": "auto"}, messages=messages)
    if not think:
        params["thinking"] = {"type": "disabled"}
    with client.messages.stream(**params) as s:
        msg = s.get_final_message()
    usage.add_message("follow-up editor", msg)
    return msg


def _block_dict(b: Any) -> dict:
    return b.to_dict(exclude_none=True) if hasattr(b, "to_dict") else dict(b)


def edit_loop(client: anthropic.Anthropic, usage: UsageLog, page: Page, brief: str, request: str, ctx: T.Ctx, effort: str, think: bool,
              progress: Callable[[str], None] | None = None, rounds: int = MAX_ROUNDS, earlier: str = "") -> None:
    """Run the editor until it finishes, asks, or runs out of rounds or budget. Changes land in page."""
    system = data.prompt("FOLLOWUP_EDITOR_LITE_SYSTEM" if page.lite else "FOLLOWUP_EDITOR_SYSTEM")
    tools = T.definitions(page.lite)
    # earlier: the answer the reader got just before asking for this change (talk.plan), so "fix those" is exact.
    first = json.dumps({"brief": brief, "reader_said": request, **({"earlier_answer": earlier} if earlier else {}),
                        "page": page.overview()}, ensure_ascii=False)
    messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": first, "cache_control": {"type": "ephemeral"}}]}]
    cap = max_usd()
    for rnd in range(rounds + 1):
        final = rnd == rounds or usage.summary()["totalUsd"] > cap
        msg = call(client, usage, system, tools, messages, effort, think, final)
        check_stop(msg, "follow-up edit")
        calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if not calls:
            if not page.note:
                page.note = text_of(msg).strip()[:1200]
            return
        messages.append({"role": "assistant", "content": [_block_dict(b) for b in msg.content]})
        results = []
        for c in calls:
            if page.finished:  # finish / ask_user came earlier in this reply: the rest is not applied
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": "Not applied: the edit already ended.", "is_error": True})
                continue
            if progress and c.name in PROGRESS:
                progress(PROGRESS[c.name] + "…")
            r = T.run_tool(page, c.name, c.input, ctx)
            log.info("Follow-up v4 tool %s: %s%s", c.name, "refused: " if r["is_error"] else "", str(r["content"])[:160])
            results.append({"type": "tool_result", "tool_use_id": c.id, **r})
        if page.finished:
            return
        if rnd + 1 == rounds or usage.summary()["totalUsd"] > cap:
            results.append({"type": "text", "text": "That was the last change you can make. Reply with a short note on what you did and anything left undone."})
        messages.append({"role": "user", "content": results})


def check_changes(page: Page, usage: UsageLog, jev_key: str | None, request: str, brief: str) -> list[str]:
    """Jev's check of the first CHECK_MAX changed sections (a failed one is put back alone), then each one with a
    playground: its text against what the playground really shows. Returns notes for the reply."""
    notes: list[str] = []
    if not jev_key:
        return notes
    before = {s["id"]: s for s in page.before["stages"]}
    changed = page.changed_ids()[:CHECK_MAX]
    if changed:
        for sid in changed:
            after = next((s for s in page.stages if s["id"] == sid), None)
            if after is None:
                continue
            try:
                failed = jev_check.judge(jev_key, before.get(sid), after, request, brief, usage)
            except JevError:
                continue
            if not failed:
                continue
            why = jev_check.why_of(failed)
            log.info("Follow-up v4: %s failed the check (%s); put back", sid, why)
            if sid in page.added:
                page.current = {**page.current, "stages": [s for s in page.stages if s["id"] != sid]}
                page.added.remove(sid)
                for e in page.log:
                    if e["section"] == sid:
                        e.update(status="undone", why=why)
            else:
                page.undo_section(sid, why)
    for sid in [s for s in page.changed_ids() if s in changed]:
        after = next((s for s in page.stages if s["id"] == sid), None)
        if after is None or not any(b.get("type") == "widget" for b in after["blocks"]):
            continue
        bad = readings_contradict(jev_key, after, T.playground_readings(page, after), usage)
        if not bad:
            continue
        old = before.get(sid)
        was = readings_contradict(jev_key, old, T.playground_readings(page, old), usage) if old else None
        if old is not None and not was:
            log.info("Follow-up v4: %s now contradicts its playground (%s); put back", sid, bad)
            page.undo_section(sid, f"after the change, {bad}")
        elif sid in page.added and not was:
            notes.append(f'In the new section "{page.label(sid)}", {bad}.')
        else:
            notes.append(f'In "{page.label(sid)}", the text and the playground still do not fully match: {bad}.')
    return notes


def run(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, follow: dict, base: dict, plan: dict, request: str,
        effort: str, think: bool, quality: str, progress: Callable[[str], None] | None = None) -> dict:
    page_outline = restore_kept_widgets(base, follow["widgets"]) if follow.get("widgets") else base
    page = Page(page_outline, quality, follow.get("source") or "")
    brief = plan["v4"]["brief"]
    ctx = T.Ctx(client, usage, jev_key, effort, think)
    edit_loop(client, usage, page, brief, request, ctx, effort, think, progress, earlier=plan["v4"].get("earlier") or "")

    if page.asked and not page.writes:
        raise Asked(asked_message(page.asked))
    if progress and page.writes:
        progress("Checking the changes…")
    notes = check_changes(page, usage, jev_key, request, brief)
    text = reply_mod.write(client, usage, request, page.note, page.log, notes)
    if not page.did_anything():
        raise Checked(CHECKED + text)
    revision = {**page.revision(), "reply": text}
    return {"outline": page.current, "warnings": [], "revision": revision, "pageFields": page.page_fields}
