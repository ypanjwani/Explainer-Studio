"""Follow-ups v5: the checks the agent (agent.py) uses after it changes the page. Copied from v4's editor.py, then
changed; the edit loop itself is in agent.py since phase 4 (Nov 16 2026).

Asked        the agent asked the reader before changing anything (ask_user): ASKED + JSON, shown as a question, not counted
problems     Jev's numbers check: each changed section's text against what its playground really shows (readings); what
             is wrong, nothing put back (the agent gets it as feedback)
check_changes  the same check, putting back each section the change made contradict its playground (the final guard)

Jev checks numbers only (Nov 17 2026). It no longer judges whether a change did what the reader asked: it saw only the
reader's last message, so after "fix those" it undid the very fixes the reader had agreed to ("the change didn't deal with
what you asked for") and they came back as issues next turn. The agent, which reads the whole conversation, decides that.
"""

from __future__ import annotations

import json
import logging

from ....llm import ReaderError
from ....pricing import UsageLog
from ...explorer.judge import readings_contradict
from ..shared import CHECK_MAX
from . import tools as T
from .page import Page, cut

log = logging.getLogger("explainer.followup.v5")

ASKED = "? "  # keep equal to ASKED_PREFIX in shared/spec.ts


class Asked(ReaderError):
    """The agent asked the reader before changing anything: ASKED + JSON {text, question: {text, options}}. The app shows
    the question (history._apply_generation stores it as status picking); not counted."""


QUESTION_CHARS, OPTION_CHARS = 600, 200  # were 240 / 100 (a 1000-character column, Text since migration 0012): cut mid-word


def asked_message(q: dict) -> str:
    """ASKED + compact JSON {text, question: {text, options: [label]}} (history._asked and the app turn the labels into
    plan options). Cut at a sentence or word, never mid-word."""
    return ASKED + json.dumps({"text": "Before I change anything, one quick question.",
                               "question": {"text": cut(q["text"], QUESTION_CHARS),
                                            "options": [cut(o, OPTION_CHARS) for o in q["options"][:4]]}}, ensure_ascii=False)


def check_changes(page: Page, usage: UsageLog, jev_key: str | None, only: set[str] | None = None) -> list[str]:
    """Jev's numbers check of the first CHECK_MAX changed sections with a playground: a section whose text the change made
    contradict what its playground really shows is put back alone. Returns notes for the reply. only: check just these."""
    notes: list[str] = []
    if not jev_key:
        return notes
    before = {s["id"]: s for s in page.before["stages"]}
    changed = [s for s in page.changed_ids() if only is None or s in only][:CHECK_MAX]
    for sid in changed:
        after = next((s for s in page.stages if s["id"] == sid), None)
        if after is None or not any(b.get("type") == "widget" for b in after["blocks"]):
            continue
        bad = readings_contradict(jev_key, after, T.playground_readings(page, after), usage)
        if not bad:
            continue
        old = before.get(sid)
        was = readings_contradict(jev_key, old, T.playground_readings(page, old), usage) if old else None
        if old is not None and not was:
            log.info("Follow-up v5: %s now contradicts its playground (%s); put back", sid, bad)
            page.undo_section(sid, f"after the change, {bad}")
        elif sid in page.added and not was:
            notes.append(f'In the new section "{page.label(sid)}", {bad}.')
        else:
            notes.append(f'In "{page.label(sid)}", the text and the playground still do not fully match: {bad}.')
    return notes


def problems(page: Page, usage: UsageLog, jev_key: str | None, only: set[str] | None = None) -> list[dict]:
    """Jev's numbers check of the changed sections (the first CHECK_MAX; `only`: just these), nothing put back:
    [{"section", "what", "kind": "readings"}]. A text-playground disagreement that was already there before the change
    isn't reported."""
    out: list[dict] = []
    if not jev_key:
        return out
    before = {s["id"]: s for s in page.before["stages"]}
    for sid in [s for s in page.changed_ids() if only is None or s in only][:CHECK_MAX]:
        after = next((s for s in page.stages if s["id"] == sid), None)
        if after is None or not any(b.get("type") == "widget" for b in after["blocks"]):
            continue
        bad = readings_contradict(jev_key, after, T.playground_readings(page, after), usage)
        old = before.get(sid)
        if bad and not (old is not None and readings_contradict(jev_key, old, T.playground_readings(page, old), usage)):
            out.append({"section": sid, "what": f"the text and the playground disagree: {bad}", "kind": "readings"})
    return out
