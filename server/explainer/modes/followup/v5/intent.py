"""Follow-ups v5: does the reader's message ask for the page to be changed? One Jev yes/no question in the plan step
(talk.py), so a question, a request to check something or "anything to change?" never makes a new version: the agent then
runs answer-only (every write refused) and offers the fixes it found as suggestions.

The agent holds the edit tools and has just found something to improve, so it leans towards acting; Jev decides before
that, with no tools. A wrong "no" costs the reader one "yes" (the agent offers the fix); a wrong "yes" makes a version they
didn't want, so an unsure answer counts as no (CHANGE_P). Jev failing (no key, an error) leaves it to the agent (None).
"""

from __future__ import annotations

import logging

from ....jev import JevError, jev_ask
from ....js import js_slice
from ....pricing import UsageLog
from ...explorer.judge import _noul

log = logging.getLogger("explainer.followup.v5")

CHANGE_P = 0.6  # at or above: the message asks for a change
MESSAGE_CHARS = 2000
PREVIOUS_CHARS = 8000  # one whole turn (shared.HISTORY_CHARS): a long answer's offered fixes stay in view

QUESTIONS = {
    "wants_change": _noul(
        "Does `reader_message` ask for the explainer page to be changed now? `previous_turn` is the reader's message just before "
        "it and what the app did or answered then (it may have offered changes); `chose_option` is an option the reader picked "
        "when asked what they meant.",
        "The reader tells the app to make an edit to the page: to fix, change, add, remove, rename, rewrite, shorten, lengthen, "
        "move, restore or rebuild something on it, including asking for a fix only if something turns out to be wrong, or "
        "agreeing to a change offered in `previous_turn`. A message that asks a question and also asks for an edit counts.",
        "The reader only asks something: a question about the page or the topic, a request to check, review or look at "
        "something, whether something is right, good or needs changing, what was done or why, or says they don't understand. "
        "Nothing in it tells the app to edit the page now."),
}


def wants_change(jev_key: str | None, text: str, previous: str, usage: UsageLog, chose: str = "") -> bool | None:
    """True when the message asks for a change, False when it only asks; None when it can't be told (no key, Jev failed)."""
    if not jev_key or not text.strip():
        return None
    state = {"reader_message": js_slice(text.strip(), 0, MESSAGE_CHARS),
             "previous_turn": js_slice(previous.strip(), 0, PREVIOUS_CHARS) or "(none: this is the first message under the page)",
             **({"chose_option": chose} if chose else {})}
    try:
        r = jev_ask(jev_key, state, QUESTIONS)
    except JevError as e:
        log.warning("Follow-up v5 intent: Jev didn't answer (%s); the agent decides", e)
        return None
    usage.add("jev follow-up intent", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    a = r["answers"].get("wants_change")
    if not a:
        return None
    p = float(a["p"])
    log.info("Follow-up v5 intent: wants a change %.2f", p)
    return p >= CHANGE_P
