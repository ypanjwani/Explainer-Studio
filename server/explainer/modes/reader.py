"""Reader (`lite-plus`, was Quick read): a short, mostly-reading page with charts at fixed values, the cheapest mode.

  paper  -> references cut, Jev's section filter, Haiku's summaries (common.read_source)
  plan   -> Jev's page brief (no random-process check) + Haiku's outline in the compact format (LITE_SYSTEM), always on Low
  code   -> quizzes, examples and click-to-use blocks stripped (LITE_DROPPED); noAsk + noControls on the spec
  design -> Haiku's pick, Jev's brief; worked examples; section notes when asked for
No clarity or fact check. Revise follow-ups still run through pipeline.py (Stage B of the modes split).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic

from .. import data
from ..brief import ask_brief
from ..core.spec import parse_outline
from ..core.validate import without_blocks
from ..notes import note_fields, write_notes
from ..pricing import UsageLog
from .common import (LITE_BUDGET, Emit, _prompt, _step, drops_examples, finish_spec, level_from_brief, plan_detail, planner_hint,
                     read_source, write_outline)

QUALITY = "lite-plus"
EFFORT = "low"  # whatever the composer says


def generate(client: anthropic.Anthropic, req: dict, emit: Emit, jev_key: str | None = None, paper_cache: Any = None,
             usage: UsageLog | None = None) -> dict:
    """{"spec", "usage"} for a new Reader page (or a "new" follow-up). Progress goes to emit()."""
    follow = req.get("followUp")
    if usage is None:
        usage = UsageLog(QUALITY, EFFORT)
    else:
        usage.label(QUALITY, EFFORT)
    early_warnings: list[str] = []
    hint = planner_hint(req, follow, early_warnings, interactivity=False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        def get_brief() -> dict:
            b = ask_brief(jev_key, req, usage, early_warnings)
            # Reader drops the random-process check, whose lines are about experiment blocks.
            return {**b, "shape": {k: v for k, v in b["shape"].items() if k != "random"}}

        brief_future = pool.submit(get_brief)
        request, source_context = read_source(client, req, follow, hint, emit, usage, jev_key, paper_cache, early_warnings, pool, brief_future)
        brief = brief_future.result()

    _step(emit, "plan", "running", plan_detail(follow, brief))
    r = write_outline(client, request, emit, usage, _prompt("LITE_SYSTEM"), parse_outline, EFFORT, "compact", LITE_BUDGET)
    outline, warnings = level_from_brief(r["outline"], brief), r["warnings"]
    # Strip any question, example or click-to-use block the planner wrote anyway.
    outline = without_blocks(outline, set(data.constants()["LITE_DROPPED"]))
    if drops_examples(req.get("extras")):
        outline = without_blocks(outline, {"examples"})
    _step(emit, "plan", "done", f"{len(outline['stages'])} stages")

    spec = finish_spec(client, req, follow, outline, request, brief, source_context, [*early_warnings, *warnings], usage, emit,
                       {"noAsk": True, "noControls": True})
    if note_fields(req.get("extras")):
        _step(emit, "notes", "running")
        spec, detail = write_notes(client, spec, req.get("extras"), usage)
        _step(emit, "notes", "done", detail)
    return {"spec": spec, "usage": usage.summary()}
