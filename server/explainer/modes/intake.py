"""The first-message step (FIRST_TALK=1, Nov 19 2026): before a new explainer is built, one Haiku call reads the message
(and the conversation so far, when it has no page yet) and calls one tool (topic.py):

answer      a reply in the chat, no page: a question, "what would a page on X include?", chit-chat, a question about the app,
            a non-AI topic or paper, something too vague. Raised as topic.IntakeAnswer (ANSWER + JSON): the run ends as
            "failed", so it isn't one of the day's explainers (its spend still counts). offer: a brief the reader can build.
new_topic   the brief becomes the request's text (a paper's note) and the build goes on in the same request.

Skipped for revise follow-ups and for requests with direct: true (the reader already chose to build: an offer's button,
a new-topic button, a Retry of a page). A failed or garbled call builds, as before this step existed. Past the day's
explainers (answer_only) new_topic becomes an answer that says so.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from .. import data
from ..llm import HAIKU, cached_system
from ..pricing import UsageLog
from . import topic
from .common import Emit, _step
from .followup.shared import history_text

log = logging.getLogger("explainer.intake")

TOKENS = 1500
PAPER_CHARS = 6000  # the paper's first sections (title, abstract, intro): enough to tell what it's about
MODE_NAMES = {"lite-plus": "Reader", "playground": "Explorer"}
DAY_OVER = ("You've made today's free explainers, so I can't build a new page until tomorrow. Come back then and ask again!")


def on() -> bool:
    """The first-message step is on by default (Nov 19 2026); FIRST_TALK=0 turns it off."""
    return os.environ.get("FIRST_TALK", "1") != "0"


def runs(req: dict) -> bool:
    """Whether this request goes through the step: on, a new page (not a revise follow-up), and not direct."""
    follow = req.get("followUp")
    return on() and not (isinstance(follow, dict) and follow.get("kind") == "revise") and not req.get("direct")


def paper_excerpt(req: dict, limit: int = PAPER_CHARS) -> str:
    out, size = [], 0
    for s in req.get("sections") or []:
        piece = (f"## {s.get('heading')}\n" if s.get("heading") else "") + str(s.get("text") or "")
        if size + len(piece) > limit:
            out.append(piece[:max(limit - size, 0)])
            break
        out.append(piece)
        size += len(piece)
    return "\n\n".join(x for x in out if x.strip())


def message_of(req: dict, answer_only: bool) -> str:
    parts = [f"Mode the reader picked: {MODE_NAMES.get(req.get('quality'), 'Explorer')}."]
    conversation = history_text(req.get("history") or [])
    if conversation:
        parts.append(conversation)
    if req.get("mode") == "paper":
        parts.append(f'The reader uploaded a paper: "{req.get("title")}". Its first sections:\n{paper_excerpt(req)}')
        note = str(req.get("note") or "").strip()
        parts.append(f"The reader's message with it: {note}" if note else "The reader wrote no message with it.")
    else:
        parts.append(f"The reader's message: {req.get('text')}")
    if answer_only:
        parts.append("Today no page can be built: the reader has made today's free explainers. Answer what you can.")
    return "\n\n".join(parts)


def _call(client: anthropic.Anthropic, usage: UsageLog, content: str) -> Any:
    params: dict = dict(model=HAIKU, max_tokens=TOKENS, output_config={"effort": "low"}, thinking={"type": "disabled"},
                        system=cached_system(data.prompt("INTAKE_SYSTEM")), tools=[topic.answer_tool(), topic.new_topic_tool()],
                        tool_choice={"type": "any"}, messages=[{"role": "user", "content": content}])
    with client.messages.stream(**params) as s:
        msg = s.get_final_message()
    usage.add_message("first message", msg)
    return msg


def decide(client: anthropic.Anthropic, req: dict, emit: Emit, usage: UsageLog, answer_only: bool = False) -> dict:
    """The request to build (its text or note replaced by the brief), or raises topic.IntakeAnswer."""
    _step(emit, "intake", "running")
    try:
        msg = _call(client, usage, message_of(req, answer_only))
        call = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    except Exception:
        log.exception("First message: the call failed; building as asked")
        call = None
    inp = call.input if call is not None and isinstance(call.input, dict) else {}
    name = call.name if call is not None else ""
    reply = topic.clip(inp.get("reply"), topic.REPLY_CHARS)
    brief = topic.clip(inp.get("brief"), topic.BRIEF_CHARS)
    if name == "answer" and reply:
        log.info("First message: answered%s", " with an offer" if inp.get("offer") else "")
        _step(emit, "intake", "done")
        raise topic.IntakeAnswer(topic.answer_message(reply, "" if answer_only else str(inp.get("offer") or "")))
    if answer_only:  # new_topic (or a garbled call) past the day's explainers: no page today
        _step(emit, "intake", "done")
        raise topic.IntakeAnswer(topic.answer_message(DAY_OVER))
    if name == "new_topic" and brief:
        log.info("First message: build, brief %r", brief[:120])
        _step(emit, "intake", "done", brief)  # the detail: what is built (the app's Retry builds it again from this)
        return topic.apply_brief(req, brief)
    _step(emit, "intake", "done")
    if call is not None:
        log.warning("First message: unusable call %s %r; building as asked", name, inp)
    return req
