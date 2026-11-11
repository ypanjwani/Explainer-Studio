"""A v3 follow-up on a Reader or Explorer page (a revise follow-up carrying the plan /api/followup/plan made): copied from
the revise path of pipeline.generate, limited to these two modes.

  plan     -> Jev's page brief (as for a new page), then steps.run_plan: structure by code and the page planner, each
              section through the section editor (text and/or playground), Jev's after-edit check
  code     -> Reader: quizzes, examples and click-to-use blocks stripped; playgrounds the revision kept are put back
  design   -> kept from the previous version; the spec assembled with the reader's colours
  checks   -> worked examples; Explorer: clarity and fact check on the changed sections only; section notes
steps.Checked ("✓ checked, nothing needed changing") and ReaderError pass through to the route, as before.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic

from ... import data
from ...brief import ask_brief
from ...clarity import clarity_check
from ...core.examples import apply_example_steps, example_briefs
from ...core.validate import assemble_spec, without_blocks
from ...design import apply_prefs
from ...factcheck import fact_check
from ...js import js_slice, json_parse
from ...llm import ReaderError
from ...notes import note_fields, write_notes
from ...pricing import UsageLog
from ..common import (CONTEXT_CHARS, FACTCHECK_SECTIONS, Emit, _prompt, _step, drops_examples, outline_context, planner_hint, section_tools,
                      write_examples, write_outline)
from . import steps as plan_steps
from .playground_edit import page_kind
from .revise import restore_kept_widgets, revision_summary

QUALITIES = ("lite-plus", "playground")
# Usage is logged under the names the old path used: an Explorer follow-up ran as an Interactive revision there.
USAGE_LABEL = {"lite-plus": "lite-plus", "playground": "quality-plus"}


def chart_on() -> bool:
    """The chart block switch (copied from chartpick.chart_on: default on; CHART_BLOCK=0 turns it off)."""
    return os.environ.get("CHART_BLOCK", "1") != "0"


def planner_system() -> str:
    """The page planner's prompt for a Reader page's text edits (copied from pipeline.planner_system)."""
    return _prompt("OUTLINE_CHART_SYSTEM" if chart_on() else "OUTLINE_SYSTEM")


def handles(req: dict) -> bool:
    """A typed revise follow-up with a v3 plan on a Reader or Explorer page (not Add section, Feedback or a review round)."""
    f = req.get("followUp")
    return (req.get("quality") in QUALITIES and req.get("mode") == "concept" and isinstance(f, dict) and f.get("kind") == "revise"
            and f.get("plan") is not None and not f.get("add") and f.get("feedback") is None and not f.get("improve"))


def generate(client: anthropic.Anthropic, req: dict, emit: Emit, jev_key: str | None = None, plan_effort: str = "medium",
             usage: UsageLog | None = None, checks: bool = True, page_index: Any = None) -> dict:
    """{"spec", "usage"} for a follow-up handles() accepted. Raises steps.Checked when every section was checked and needed
    no change, ReaderError when nothing could be changed."""
    quality = req["quality"]
    lite = quality == "lite-plus"
    follow = req["followUp"]
    planned = follow["plan"]
    effort = "low" if lite else (req.get("effort") or plan_effort or "medium")
    label = USAGE_LABEL[quality]
    if usage is None:
        usage = UsageLog(label, effort)
    else:
        usage.label(label, effort)
    try:
        o = json_parse(follow["outline"])
        base_outline = o if isinstance(o, dict) and isinstance(o.get("stages"), list) and o["stages"] else None
    except ValueError:
        base_outline = None
    if base_outline is None:
        raise ReaderError("The previous explainer couldn't be read for this revision.")
    early_warnings: list[str] = []
    # The planner hint itself isn't sent (run_plan writes each step's request), but a stage count the reader typed past
    # the limit still adds its warning, as on the old path.
    planner_hint(req, follow, early_warnings, interactivity=not lite)

    with ThreadPoolExecutor(max_workers=1) as pool:
        def get_brief() -> dict:
            b = ask_brief(jev_key, req, usage, early_warnings)
            if lite:  # Reader drops the random-process check, whose lines are about experiment blocks.
                b = {**b, "shape": {k: v for k, v in b["shape"].items() if k != "random"}}
            return b

        brief = pool.submit(get_brief).result()
    source_context = follow.get("source") or f"Reader's request: {req['text']}"

    _step(emit, "plan", "running", "Revising the previous version")
    page_fields = plan_steps.touches_page(planned)
    n = len(planned["sections"]) + len(planned["steps"])
    _step(emit, "plan", "running", "Looking at " + (f"{n} parts" if n > 1 else "one part") + " of the page")
    # An Explorer page's playgrounds are widgets, so the planner writes explanation only.
    prose = page_kind(restore_kept_widgets(base_outline, follow.get("widgets") or {}), quality) is not None
    revise_system = _prompt("OUTLINE_SYSTEM") if prose else planner_system()

    def ask(text: str, parse) -> dict:
        return write_outline(client, text, emit, usage, revise_system, parse, effort, whole="your reply")

    r = plan_steps.run_plan(client, usage, jev_key, follow, base_outline, planned, req["text"], ask, revise_system, effort,
                            req.get("planThinking", True), quality, prose, page_index)
    outline, warnings, revision = r["outline"], r["warnings"], r.get("revision")
    if brief and brief.get("level") and not page_fields and outline["level"] != brief["level"]:
        outline = {**outline, "level": brief["level"]}
    if lite:  # strip any question, example or click-to-use block the planner wrote anyway
        outline = without_blocks(outline, set(data.constants()["LITE_DROPPED"]))
    if drops_examples(req.get("extras")):
        outline = without_blocks(outline, {"examples"})
    # The revision saw each playground as a placeholder; put back the ones it kept (before the checks, which skip widgets).
    if follow.get("widgets"):
        outline = restore_kept_widgets(outline, follow["widgets"])
    if revision is not None:
        _step(emit, "plan", "done", revision_summary(revision, outline, base_outline)["text"])
    else:
        _step(emit, "plan", "done", f"{len(outline['stages'])} stages")

    _step(emit, "design", "running")
    design = follow["design"]  # a revision keeps the previous version's look
    _step(emit, "design", "done", "kept from the previous version")

    _step(emit, "build", "running")
    context = js_slice(f"{outline_context(outline)}\n\nSource material:\n{source_context}", 0, CONTEXT_CHARS)
    spec = assemble_spec(outline, design, context, [*early_warnings, *warnings])
    if lite:
        spec = {**spec, "noAsk": True, "noControls": True}
    tools = section_tools(req.get("extras"))
    if tools:
        spec = {**spec, "sectionTools": tools}
    if req["mode"] == "concept" and not follow.get("source"):
        spec = {**spec, "fromMemory": True}
    if follow.get("accent") or follow.get("colorMode"):
        spec = apply_prefs(spec, {"palette": "custom" if follow.get("accent") else follow["design"]["theme"], "accent": follow.get("accent"),
                                  "colorMode": follow.get("colorMode") or "auto"})
    if req.get("colorMode") in ("light", "dark"):
        spec = {**spec, "design": {**spec["design"], "mode": req["colorMode"]}}
    # Unchanged stages keep what the previous version wrote for them; the later steps run only on the changed ones.
    only = None
    kept_notes = None
    if revision is not None:
        changed = set(revision["changed"])
        only = [i for i, st in enumerate(spec["stages"]) if st["id"] in changed]
        spec = {**spec, "revision": revision_summary(revision, outline, base_outline)["counts"]}
        kept = follow.get("kept") or {}
        kept_steps = {k: v for k, v in (kept.get("steps") or {}).items() if k.rsplit(":", 2)[0] not in changed}
        if kept_steps:
            spec = apply_example_steps(spec, kept_steps)
        kept_notes = {sid: n for sid, n in (kept.get("notes") or {}).items() if sid not in changed}
    _step(emit, "build", "done")

    if example_briefs(spec):
        _step(emit, "examples", "running")
        spec = write_examples(client, spec, usage)
        _step(emit, "examples", "done")
    if checks and not lite:  # Explorer: clarity, then fact check, on the changed sections
        _step(emit, "clarity", "running")
        spec, detail = clarity_check(client, jev_key, spec, usage, only=only)
        _step(emit, "clarity", "done", detail)
        _step(emit, "check", "running")
        spec, detail = fact_check(client, jev_key, spec, usage, section_check=FACTCHECK_SECTIONS, only=only)
        _step(emit, "check", "done", detail)
    if note_fields(req.get("extras")):
        _step(emit, "notes", "running")
        spec, detail = write_notes(client, spec, req.get("extras"), usage, kept=kept_notes)
        _step(emit, "notes", "done", detail)
    return {"spec": spec, "usage": usage.summary()}
