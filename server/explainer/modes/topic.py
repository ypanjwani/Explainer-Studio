"""What a message that isn't a change to a page becomes (Nov 19 2026): an answer in the chat, or a new explainer from a
brief. Shared by the first-message step (intake.py, FIRST_TALK) and follow-ups v5's agent (followup/v5, new_topic), so
neither imports the other.

answer      {reply, offer?}: a reply, no page; offer is the brief of a page the reader can build with one click. Travels as
            the generation's error: ANSWER + JSON (history.py and the app show it as a reply, not counted).
new_topic   {brief, reply?}: build a new explainer; brief is the topic plus what the conversation settled. In the
            first-message step it becomes the request's text (apply_brief) and the build goes on; under a page (v5) it
            travels as NEW_TOPIC_BRIEF + JSON {reply, brief} and the reader's button builds from the brief.
"""

from __future__ import annotations

import json

from .. import data
from ..js import js_slice, js_trim
from ..llm import ReaderError

# Keep equal to ANSWER_PREFIX / NEW_TOPIC_BRIEF_PREFIX in shared/spec.ts (history.py reads them too).
ANSWER = "✓* "
NEW_TOPIC_BRIEF = "↗+ "
REPLY_CHARS = 2000
BRIEF_CHARS = 1000  # also a paper's note limit (requests.parse_generate)


class IntakeAnswer(ReaderError):
    """The first-message step answered: its message is ANSWER + JSON {reply, offer?}; no page, not counted."""


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


_BRIEF = {"type": "string", "description": "What the new explainer should teach, as the reader would ask for it, in 1 to 3 plain "
                                           "sentences: the topic plus anything the conversation settled (level, less maths, what "
                                           "to cover or leave out)."}


def answer_tool() -> dict:
    return _tool("answer", "Reply in the chat; no page is built. Ends your turn.",
                 {"reply": {"type": "string", "description": "What the reader sees: friendly, correct, under 150 words, Markdown."},
                  "offer": {**_BRIEF, "description": "Optional: the brief of an explainer you'd make about it; the reader gets a "
                                                     "button to build it. Leave out when no page fits."}},
                 ["reply"])


def new_topic_tool(under_page: bool = False) -> dict:
    """under_page: follow-ups v5's version (a different topic than the page; the reply is required, the reader clicks to build)."""
    if under_page:
        return _tool("new_topic", "The message asks about a different AI topic than this page: say so kindly in `reply` (the reader "
                     "gets a button to make a new explainer about it) and write that explainer's `brief`. Only before any change. "
                     "Ends your turn.", {"reply": {"type": "string"}, "brief": _BRIEF}, ["reply", "brief"])
    return _tool("new_topic", "Build a new explainer about this. Ends your turn.", {"brief": _BRIEF}, ["brief"])


def clip(s: object, n: int) -> str:
    return js_slice(js_trim(str(s or "")), 0, n)


def answer_message(reply: str, offer: str = "") -> str:
    out: dict = {"reply": clip(reply, REPLY_CHARS)}
    if clip(offer, BRIEF_CHARS):
        out["offer"] = clip(offer, BRIEF_CHARS)
    return ANSWER + json.dumps(out, ensure_ascii=False)


def new_topic_message(reply: str, brief: str) -> str:
    return NEW_TOPIC_BRIEF + json.dumps({"reply": clip(reply, REPLY_CHARS), "brief": clip(brief, BRIEF_CHARS)}, ensure_ascii=False)


def apply_brief(req: dict, brief: str) -> dict:
    """The request with the brief as what to build: a concept's text, a paper's note (its sections kept)."""
    if req.get("mode") == "paper":
        return {**req, "note": clip(brief, BRIEF_CHARS)}
    return {**req, "text": clip(brief, data.limits()["conceptChars"])}
