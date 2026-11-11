"""The two modes on the menu, each with its own pipeline: Reader (`lite-plus`, reader.py) and Explorer (`playground`,
explorer/). Shared steps are in common.py.

The rule: nothing under modes/ imports another mode's code (pipeline.py, codelab.py, labplan.py, pgpick.py, ...), only
shared tools (llm, jev, data, core/, brief, design, clarity, factcheck, notes, filter, paper_cache, ...). Deleting every
other mode must leave these two working; test_modes_boundary.py enforces it.

These pipelines make new pages (a typed concept, a paper, Retry/Edit, a "new" follow-up) and run v3 follow-ups
(followup/, a revise follow-up carrying a plan). Still on pipeline.generate (legacy, to be deleted with the other modes):
"Add section", the hidden Feedback button, review rounds, a revise follow-up without a v3 plan (an old tab, a script),
and every other mode. MODES_SPLIT=0 sends everything back there.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

from . import reader
from .common import Emit
from .explorer import pipeline as explorer_pipeline
from .followup import run as followup_run
from ..pricing import UsageLog

QUALITIES = ("lite-plus", "playground")


def enabled() -> bool:
    return os.environ.get("MODES_SPLIT") != "0"


def handles(req: dict) -> bool:
    """Whether this request runs here: a new Reader or Explorer page, or a v3 follow-up on one (not review rounds)."""
    if not enabled() or req.get("quality") not in QUALITIES or req.get("reviewRounds"):
        return False
    follow = req.get("followUp")
    if follow and follow.get("kind") == "revise":
        return followup_run.handles(req)
    return True


def generate(client: anthropic.Anthropic, req: dict, emit: Emit, jev_key: str | None = None, plan_effort: str = "medium",
             paper_cache: Any = None, usage: UsageLog | None = None, checks: bool = True, page_index: Any = None, **_: Any) -> dict:
    """{"spec", "usage", ...} for a request handles() accepted: the same shape as pipeline.generate."""
    follow = req.get("followUp")
    if follow and follow.get("kind") == "revise":
        return followup_run.generate(client, req, emit, jev_key=jev_key, plan_effort=plan_effort, usage=usage, checks=checks, page_index=page_index)
    if req["quality"] == "lite-plus":
        return reader.generate(client, req, emit, jev_key=jev_key, paper_cache=paper_cache, usage=usage)
    return explorer_pipeline.generate(client, req, emit, jev_key=jev_key, plan_effort=plan_effort, paper_cache=paper_cache, usage=usage, checks=checks)
