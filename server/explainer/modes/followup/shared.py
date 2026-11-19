"""What follow-ups v3 (v3/) and v4 (v4/) both use, so neither imports the other and v3 can be deleted on its own.

  Checked     a follow-up that left the page as it was without anything going wrong (a reply in the thread, not an error)
  max_usd     the spend cap for one follow-up (FOLLOWUP_MAX_USD)
  is_undo     "undo", "revert", "go back": answered without an AI call
  history_text  the conversation's earlier messages, for the model that reads the follow-up
Nothing here imports v3/ or v4/ (test_modes_boundary.py checks).
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...js import js_slice
from ...llm import ReaderError

CHECKED = "✓ "  # keep equal to CHECKED_PREFIX in shared/spec.ts
CHECK_MAX = 6  # each changed section is checked when at most this many changed
MAX_STEPS = 20
MAX_INTENTS = 20
# The conversation's earlier turns (Nov 13 2026: whole answers and replies, was 500 characters a turn, which cut off what
# "fix that" pointed at). The last FULL_HISTORY turns go in whole (up to HISTORY_CHARS each), older ones are cut to
# OLD_HISTORY_CHARS, and the oldest are dropped while the total is over HISTORY_TOTAL.
# Nov 17 2026: 8000 a turn (was 4000) and 24000 in all (was 15000), since a v5 answer can be 6000 characters
# (v5/agent.py MESSAGE_CHARS) and a turn is the prompt + "answered in the chat…" + the answer: at 4000 the end of a long
# fix list was lost before the agent could act on "Yes, make these fixes". Keep followup.parse_plan's caps in step.
MAX_HISTORY = 12  # keep equal to HISTORY_TURNS in app/src/shell/app.ts
FULL_HISTORY = 8
HISTORY_CHARS = 8000
OLD_HISTORY_CHARS = 300
HISTORY_TOTAL = 24000

# A whole message that only undoes: fillers in front ("so revert the changes", "ok undo those changes", Nov 17 2026: the
# first went to the agent, which claimed a revert that never happened), and nothing after but which change. A message
# naming a part ("revert the colour change in S3") doesn't match and goes to the AI.
_FILLER = r"((so|ok|okay|yes|yeah|now|just|please|then|and) )*"
_WHICH = r"( (that|it|this|those|these|the|your|my|last|latest|recent|change|changes|edit|edits))*"
UNDO = re.compile(rf"{_FILLER}(undo|revert){_WHICH}( please)?"
                  rf"|{_FILLER}go back( to the (previous|last|old|earlier) version)?( please)?")

# A v5 answer that lists fixes the agent would make (keep equal to OFFER_PREFIX in shared/spec.ts): shown like a CHECKED
# answer plus a "Yes, make these fixes" button, whose click is a picked option, so it is always a change.
OFFER = "✓+ "


class Checked(ReaderError):
    """The page was left as it was without anything going wrong: every section the follow-up was about was found right, or a
    change was tried and put back because it didn't pass the check. Its message starts with CHECKED, and the app shows it
    as a calm reply in the thread, not as an error."""


NEW_TOPIC = "↗ "  # keep equal to NEW_TOPIC_PREFIX in shared/spec.ts (history.NEW_TOPIC)


class NewTopic(ReaderError):
    """Follow-ups v5's agent found the message is about a different topic: its message (NEW_TOPIC + the reply) is shown
    with a "New explainer about this" button, like the plan step's new-topic reply; no version, not counted."""


def max_usd() -> float:
    try:
        return float(os.environ.get("FOLLOWUP_MAX_USD") or 0.15)
    except ValueError:
        return 0.15


def _clip(s: Any, n: int) -> str:
    return js_slice(re.sub(r"\s+", " ", str(s or "")).strip(), 0, n)


def norm(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def is_undo(text: str) -> bool:
    """A literal undo ("undo", "revert that", "go back to the previous version"): no AI call needed."""
    return UNDO.fullmatch(" ".join(norm(text))) is not None


ANSWERED = "answered in the chat, nothing changed: "  # how app.ts historyOf writes a turn that got an answer
EARLIER_ANSWER_CHARS = 8000  # one whole turn (HISTORY_CHARS); followup.parse_plan keeps the same


def last_answer(history: list[str] | None) -> str:
    """The reader's previous turn when it got an answer (prompt + answer, as historyOf wrote it), else "". Follow-ups v4 pass
    it to the editor with the brief, so "fix those" after a check of the page gets the check's own words."""
    last = next((h for h in reversed(history or []) if isinstance(h, str) and h.strip()), "")
    return js_slice(last.strip(), 0, EARLIER_ANSWER_CHARS) if ANSWERED in last else ""


def history_text(history: list[str]) -> str:
    turns = [h for h in history if isinstance(h, str) and h.strip()][-MAX_HISTORY:]
    cut = len(turns) - FULL_HISTORY
    lines = [f"- {_clip(h, OLD_HISTORY_CHARS if i < cut else HISTORY_CHARS)}" for i, h in enumerate(turns)]
    while len(lines) > 1 and sum(len(x) + 1 for x in lines) > HISTORY_TOTAL:
        lines.pop(0)
    return ("The reader's earlier messages in this conversation (oldest first), with what each did:\n" + "\n".join(lines)) if lines else ""
