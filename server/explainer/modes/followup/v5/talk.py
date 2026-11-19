"""Follow-ups v5, step 1 (POST /api/followup/plan when FOLLOWUP_V5=1): code and one Jev question, no Haiku (phase 4).

Everything the reader's message needs (answering, asking, changing, going back to a version, a new topic) is done by the
one agent in /api/generate (agent.py). This step only:
undo      "undo" and the like: revert (to the version the page was made under, else its parent), no AI call
intent    does the message ask for a change? (intent.wants_change, one Jev yes/no question with the turn before it):
          v5.intent "change" or "answer"; left out when Jev can't tell (the agent decides). A picked option is a change
          (the agent asked before changing).
agent     a plan for the agent, carrying the conversation (shared.history_text), the option the reader picked of a
          question and the words they added, and the answer they got just before (shared.last_answer)
The app runs a non-confirm change straight away (runSteps), so the reader waits for one call, not two.
"""

from __future__ import annotations

from typing import Any

from ....js import js_slice
from ....pricing import UsageLog
from ..shared import EARLIER_ANSWER_CHARS, history_text, is_undo, last_answer
from .intent import wants_change


def plan(text: str, option: dict | None = None, extra: str = "", history: list[str] | None = None, jev_key: str | None = None,
         usage: UsageLog | None = None) -> dict:
    history = history or []
    if not option and is_undo(text):
        return {"kind": "revert"}
    v5: dict[str, Any] = {"agent": True}
    conversation = history_text(history)
    if conversation:
        v5["conversation"] = conversation
    if option and str(option.get("label") or "").strip():
        v5["chose"] = js_slice(str(option["label"]).strip(), 0, 200)  # editor.OPTION_CHARS
    if extra.strip():
        v5["extra"] = js_slice(extra.strip(), 0, 300)
    if earlier := last_answer(history):
        v5["earlier"] = js_slice(earlier, 0, EARLIER_ANSWER_CHARS)
    if v5.get("chose"):
        v5["intent"] = "change"
    else:
        previous = next((h for h in reversed(history) if isinstance(h, str) and h.strip()), "")
        wants = wants_change(jev_key, text, previous, usage or UsageLog("followup-plan"))
        if wants is not None:
            v5["intent"] = "change" if wants else "answer"
    return {"kind": "change", "sections": [], "steps": [], "v5": v5, "summary": [js_slice(text.strip(), 0, 300)], "confirm": False}


def handle(client: Any, req: dict, db: Any = None, user_id: str | None = None, jev_key: str | None = None) -> dict:
    usage = UsageLog("followup-plan", "low")
    out = plan(req["text"], req.get("option"), req.get("extra") or "", req.get("history"), jev_key, usage)
    return {**out, "usd": usage.summary()["totalUsd"]}
