"""Explorer (`playground`): a deep page where up to 4 marked sections get a hands-on playground.

  paper       -> references cut, Jev's section filter, Haiku's summaries (common.read_source)
  plan        -> Jev's page brief + Haiku's outline (OUTLINE_SYSTEM + PLANNER_NOTE_PG: prose only, each stage marked for a
                 playground or not)
  playgrounds -> started while the outline streams (stream.Streamed): the writer's mark or Jev's gate, Haiku writes the
                 playground spec, the node checker dry-runs and repairs it, Jev reviews it (playground.py, judge.py);
                 at most 4 per page, repeats dropped
  design      -> Haiku's pick, Jev's brief; worked examples; clarity check; fact check; section notes when asked for
Revise follow-ups and "Add section" still run through pipeline.py (Stage B of the modes split).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic

from ...brief import ask_brief
from ...clarity import clarity_check
from ...core.spec import parse_outline
from ...core.validate import without_blocks
from ...factcheck import fact_check
from ...notes import note_fields, write_notes
from ...pricing import UsageLog
from ..common import (FACTCHECK_SECTIONS, FULL_BUDGET, Emit, _prompt, _step, drops_examples, finish_spec, level_from_brief, plan_detail,
                      planner_hint, read_source, write_outline)
from .settings import ExplorerSettings
from .stream import PLANNER_NOTE_PG, Streamed, strip_prebuilt

QUALITY = "playground"


def generate(client: anthropic.Anthropic, req: dict, emit: Emit, jev_key: str | None = None, plan_effort: str = "medium",
             paper_cache: Any = None, usage: UsageLog | None = None, checks: bool = True) -> dict:
    """{"spec", "usage", debug fields} for a new Explorer page (or a "new" follow-up). Progress goes to emit().
    checks: False skips the clarity and fact check."""
    follow = req.get("followUp")
    effort = req.get("effort") or plan_effort or "medium"
    if usage is None:
        usage = UsageLog(QUALITY, effort)
    else:
        usage.label(QUALITY, effort)
    settings = ExplorerSettings.for_effort(effort, think=req.get("planThinking", True))
    early_warnings: list[str] = []
    hint = planner_hint(req, follow, early_warnings, interactivity=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        brief_future = pool.submit(ask_brief, jev_key, req, usage, early_warnings)
        request, source_context = read_source(client, req, follow, hint, emit, usage, jev_key, paper_cache, early_warnings, pool, brief_future)
        brief = brief_future.result()

    _step(emit, "plan", "running", plan_detail(follow, brief))
    # The planner writes prose only (told in the user turn, so the cached system prompt stays shared); playgrounds start
    # while the text streams.
    streamed = Streamed(client, usage, jev_key, settings)
    r = write_outline(client, f"{request}\n\n{PLANNER_NOTE_PG}", emit, usage, _prompt("OUTLINE_SYSTEM"), parse_outline, effort, "json",
                      FULL_BUDGET, on_text=streamed.feed)
    outline, warnings = level_from_brief(r["outline"], brief), r["warnings"]
    if drops_examples(req.get("extras")):
        outline = without_blocks(outline, {"examples"})
    outline = strip_prebuilt(outline)
    _step(emit, "plan", "done", f"{len(outline['stages'])} stages")  # the lesson text is finished: say so before the playgrounds

    _step(emit, "interaction", "running")
    outline, cl = streamed.finish(outline, warnings, lambda d: _step(emit, "interaction", "running", d))
    _step(emit, "interaction", "done", f"{cl['built']} of {cl['sections']} sections got a playground"
          + (f" · {cl['charts']} as charts" if cl.get("charts") else "") + (f" · {cl['repaired']} repaired" if cl["repaired"] else ""))

    spec = finish_spec(client, req, follow, outline, request, brief, source_context, [*early_warnings, *warnings], usage, emit, {})
    if checks:
        # Jev judges each stage (unexplained ideas, too hard), Haiku fixes what it flags (clarity.py). It runs before the
        # fact check, so the text it adds is fact-checked too. Both edit the prose only, never a playground.
        _step(emit, "clarity", "running")
        spec, detail = clarity_check(client, jev_key, spec, usage)
        _step(emit, "clarity", "done", detail)
        _step(emit, "check", "running")
        spec, detail = fact_check(client, jev_key, spec, usage, section_check=FACTCHECK_SECTIONS)
        _step(emit, "check", "done", detail)
    if note_fields(req.get("extras")):
        _step(emit, "notes", "running")
        spec, detail = write_notes(client, spec, req.get("extras"), usage)
        _step(emit, "notes", "done", detail)
    # Test runs only (debugRejected): what the reviewer rejected, what the writer marked, what the screenshots showed.
    debug = {"rejected": cl.get("rejected") or [], "labMarks": cl.get("marks") or {}, "labLook": cl.get("look") or []}
    return {"spec": spec, "usage": usage.summary(), **{k: v for k, v in debug.items() if v}}
