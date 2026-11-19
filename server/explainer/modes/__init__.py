"""The two modes, each with its own pipeline: Reader (`lite-plus`, reader.py) and Explorer (`playground`, explorer/).
Shared steps are in common.py.

The rule: nothing under modes/ imports mode logic from outside it, only shared tools (llm, jev, data, core/, brief,
design, clarity, factcheck, notes, filter, paper_cache, ...); test_modes_boundary.py enforces it.

These pipelines make new pages (a typed concept, a paper, Retry/Edit, a "new" follow-up) and run v3 follow-ups
(followup/, a revise follow-up carrying a plan). /api/generate refuses anything else (handles() is False).
With FIRST_TALK=1 a new page's message is read first (intake.py): answered in the chat, or built from a brief.
"""

from __future__ import annotations

from typing import Any

import anthropic

from . import intake, reader
from .common import Emit
from .explorer import pipeline as explorer_pipeline
from .followup import run as followup_run
from ..pricing import UsageLog

QUALITIES = ("lite-plus", "playground")


def handles(req: dict) -> bool:
    """Whether this request runs here: a new Reader or Explorer page, or a v3 follow-up on one."""
    if req.get("quality") not in QUALITIES:
        return False
    follow = req.get("followUp")
    if follow and follow.get("kind") == "revise":
        return followup_run.handles(req)
    return True


def generate(client: anthropic.Anthropic, req: dict, emit: Emit, jev_key: str | None = None, plan_effort: str = "medium",
             paper_cache: Any = None, usage: UsageLog | None = None, checks: bool = True, page_index: Any = None, db: Any = None,
             user_id: str | None = None, base_id: str | None = None, answer_only: bool = False, **_: Any) -> dict:
    """{"spec", "usage", ...} for a request handles() accepted: the same shape as pipeline.generate."""
    follow = req.get("followUp")
    if follow and follow.get("kind") == "revise":
        return followup_run.generate(client, req, emit, jev_key=jev_key, plan_effort=plan_effort, usage=usage, checks=checks, page_index=page_index,
                                     db=db, user_id=user_id, base_id=base_id, answer_only=answer_only)
    if intake.runs(req):  # FIRST_TALK: answer in the chat (raises topic.IntakeAnswer) or build from the brief
        req = intake.decide(client, req, emit, usage if usage is not None else UsageLog(""), answer_only=answer_only)
    if req["quality"] == "lite-plus":
        return reader.generate(client, req, emit, jev_key=jev_key, paper_cache=paper_cache, usage=usage)
    return explorer_pipeline.generate(client, req, emit, jev_key=jev_key, plan_effort=plan_effort, paper_cache=paper_cache, usage=usage, checks=checks)
