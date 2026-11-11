"""Follow-ups v3, steps 2 and 3: look at a section, then let one editor call decide and write its change.

evidence      by code, before any writing: the section as the planner sees it, its playground's spec, what the playground
              checker finds (empty charts, controls that change nothing, NaN, layout), whether the text and the playground
              agree (Jev, codelab_edit.agrees), the playground's level from the page map, the sections around it
edit_section  one Haiku call with Thinking (system prompt: the page planner's, cached, so the block format is the same;
              SECTION_EDIT_NOTE in the user turn). It sees the reader's intent and all the evidence and decides: change the
              text, the playground, both, add one, remove it, or nothing ("none" with a reason). It never gets told which part.
apply_result  the decision carried out with the existing tools: the stage merged by revise.parse_revision and guarded
              (targets.guard_untouched), playgrounds edited / added by codelab_edit (the checker first, its repair loop,
              Jev's review), a removed playground dropped by code.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic

from . import playground_edit as ce
from ... import data
from . import page_index as pidx
from ...js import js_slice
from ...json_util import extract_json
from ...llm import write_text
from ...pricing import UsageLog
from .revise import parse_revision, restore_kept_widgets
from .targets import _widget_at, guard_untouched

from ..explorer.playground import check_playground
from ..explorer.stream import PLANNER_NOTE, strip_prebuilt
log = logging.getLogger("explainer.steps")

ACTIONS = ("keep", "edit", "add", "remove")
LITE = "lite-plus"
EDIT_TOKENS = 16000
REASON_CHARS = 400
INSTRUCTION_CHARS = 1200
SPEC_CHARS = 14000  # a playground spec sent to the editor, cut to this
MAX_WORKERS = 4


def _clip(s: Any, n: int) -> str:
    return js_slice(re.sub(r"\s+", " ", str(s or "")).strip(), 0, n)


def _si(outline: dict, sid: str) -> int | None:
    return next((i for i, s in enumerate(outline["stages"]) if s["id"] == sid), None)


def evidence(page: dict, sid: str, index: dict, jev_key: str | None, usage: UsageLog) -> dict:
    """What the editor sees besides the request: the section (widgets as placeholders), its playground and what was found in it,
    and its neighbours. Nothing here writes; a failing check is left out."""
    si = _si(page, sid)
    st = page["stages"][si]
    view = ce.viewed(page)["stages"][si]
    out: dict = {"section": view, "playground": None}
    widget = next((b for b in st["blocks"] if b.get("type") == "widget"), None)
    if widget is not None:
        kind = ce.widget_kind(widget)
        pg: dict = {"title": widget.get("title", ""), "kind": kind or "unknown"}
        if kind == "playground":
            spec = ce.read_playground_spec(widget.get("html", ""))
            if spec is not None:
                text = json.dumps(spec, ensure_ascii=False)
                pg["spec"] = spec if len(text) <= SPEC_CHARS else js_slice(text, 0, SPEC_CHARS) + " …(cut)"
                try:
                    report = check_playground(spec)
                    pg["checker"] = [str(x) for x in [*(report.get("problems") or []), *(report.get("layout") or [])][:8]]
                except Exception:
                    log.exception("Follow-up evidence: the checker failed on %s", sid)
        why = ce.agrees(jev_key, page, si, ce.read_plan(widget), ce.facts_of(widget), usage)
        pg["agreement"] = why or "agree"
        level = pidx.by_id(index).get(sid, {}).get("playgroundLevel") if index else ""
        if level:
            pg["level"] = level
        out["playground"] = pg
    near = [page["stages"][i] for i in (si - 1, si + 1) if 0 <= i < len(page["stages"])]
    out["neighbours"] = [{"id": s["id"], "label": s.get("label", ""), "summary": _clip(s.get("blurb"), 200)} for s in near]
    return out


def clean_result(raw: Any, quality: str) -> dict:
    """{"decision", "reason", "stage" or None, "playground": {"action", "instructions"}} from the editor's reply."""
    raw = raw if isinstance(raw, dict) else {}
    pg = raw.get("playground") if isinstance(raw.get("playground"), dict) else {}
    action = pg.get("action") if pg.get("action") in ACTIONS else "keep"
    instructions = _clip(pg.get("instructions"), INSTRUCTION_CHARS)
    if quality == LITE or (action in ("edit", "add") and not instructions):
        action = "keep"
    stage = raw.get("stage") if isinstance(raw.get("stage"), dict) else None
    decision = "change" if raw.get("decision") == "change" and (stage is not None or action != "keep") else "none"
    if decision == "none":
        stage, action = None, "keep"
    return {"decision": decision, "reason": _clip(raw.get("reason"), REASON_CHARS), "stage": stage,
            "playground": {"action": action, "instructions": instructions if action in ("edit", "add") else ""}}


def edit_section(client: anthropic.Anthropic, usage: UsageLog, system: str, page: dict, sid: str, intent: dict, request: str,
                 quality: str, ev: dict, effort: str = "medium", style: str = "") -> dict:
    """One editor call. Returns clean_result's dict, plus "failed" (a reason) when the call or its reply is unusable."""
    lite = quality == LITE
    user = {
        "followUp": request, "intent": intent["intent"], **({"because": intent["because"]} if intent.get("because") else {}),
        "page": {"title": page.get("title", ""), "level": page.get("level", ""), "mode": quality},
        **({"styleBrief": style} if style else {}), **ev,
    }
    notes = [data.prompt("SECTION_EDIT_NOTE")]
    if lite:
        notes.append(data.prompt("LITE_REVISE_NOTE"))
    else:
        notes.append(PLANNER_NOTE)  # explanation only: a playground is a widget, never a prebuilt slider or plot
    text = json.dumps(user, ensure_ascii=False) + "\n\n" + "\n\n".join(notes)
    try:
        reply = write_text(client, usage, "follow-up editor", system=system, user=text, max_tokens=EDIT_TOKENS, effort=effort,
                           stream=True, thinking=True)
        raw, _ = extract_json(reply)
    except Exception as e:
        log.exception("Follow-up editor failed on %s", sid)
        return {"decision": "none", "reason": "", "stage": None, "playground": {"action": "keep", "instructions": ""},
                "failed": "the change couldn't be written" + (f" ({type(e).__name__})" if not isinstance(e, ValueError) else "")}
    out = clean_result(raw, quality)
    log.info("Follow-up editor (%s): %s, text %s, playground %s: %s", sid, out["decision"], "changed" if out["stage"] else "kept",
             out["playground"]["action"], out["reason"])
    return out


def merge_stage(outline: dict, sid: str, stage: dict, prose: bool) -> tuple[dict, list[str]] | None:
    """`outline` with the editor's stage in place of `sid` (its widgets put back where their placeholders are). None when the
    stage doesn't fit the outline format (the section is then kept as it was)."""
    si = _si(outline, sid)
    if si is None:
        return None
    if not isinstance(stage.get("blocks"), list) or not stage["blocks"]:  # the parser would keep it with no blocks: an empty section
        log.info("Follow-up editor (%s): its stage had no blocks", sid)
        return None
    compact = ce.viewed(outline)
    widgets = {sid: [b for b in outline["stages"][si]["blocks"] if b.get("type") == "widget"]}
    r = parse_revision({"changes": {"replace": [{**stage, "id": sid}]}}, compact)
    if "error" in r:
        log.info("Follow-up editor (%s): its stage was unusable: %s", sid, r["error"])
        return None
    if not next(s for s in r["outline"]["stages"] if s["id"] == sid)["blocks"]:
        log.info("Follow-up editor (%s): no usable blocks were left in its stage", sid)
        return None
    guarded, warnings = guard_untouched(compact, r["outline"], {sid}, 0)
    if prose:  # Playground pages: explanation only; a prebuilt interactive block written anyway is dropped
        stripped = {s["id"]: s for s in strip_prebuilt(guarded)["stages"]}
        guarded = {**guarded, "stages": [stripped[s["id"]] if s["id"] == sid else s for s in guarded["stages"]]}
    full = {s["id"]: s for s in outline["stages"]}
    back = restore_kept_widgets({**guarded, "stages": [s if s["id"] == sid else full[s["id"]] for s in guarded["stages"]]}, widgets)
    return back, warnings


def apply_text(outline: dict, sid: str, result: dict, prose: bool) -> tuple[dict, bool, list[str]]:
    """(outline, text changed?, warnings)."""
    if result["stage"] is None:
        return outline, False, []
    merged = merge_stage(outline, sid, result["stage"], prose)
    if merged is None:
        return outline, False, [f'Couldn\'t use the new text for "{_label(outline, sid)}"; it was kept as it was.']
    new, warnings = merged
    si = _si(new, sid)
    changed = new["stages"][si] != outline["stages"][_si(outline, sid)]
    return new, changed, warnings


def _label(outline: dict, sid: str) -> str:
    si = _si(outline, sid)
    return str(outline["stages"][si].get("label") or sid) if si is not None else sid


def apply_playgrounds(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, outline: dict, results: dict[str, dict],
                      quality: str, effort: str, think: bool) -> tuple[dict, set[str], list[str]]:
    """(outline, sections whose playground changed, notes): removals by code, edits side by side (each the checker first,
    its repairs and Jev's review; a failed edit keeps the old playground), additions one after another (the page's cap)."""
    changed: set[str] = set()
    notes: list[str] = []
    for sid, res in results.items():
        if res["playground"]["action"] == "remove":
            si = _si(outline, sid)
            if si is not None and any(b.get("type") == "widget" for b in outline["stages"][si]["blocks"]):
                stages = list(outline["stages"])
                stages[si] = {**stages[si], "blocks": [b for b in stages[si]["blocks"] if b.get("type") != "widget"]}
                outline = {**outline, "stages": stages}
                changed.add(sid)
    jobs = []
    adds = []
    for sid, res in results.items():
        act = res["playground"]["action"]
        if act not in ("edit", "add"):
            continue
        at = _widget_at(outline, sid, "")
        if at:
            jobs.append((sid, at[0], at[1], res["playground"]["instructions"]))
        else:
            adds.append((sid, res["playground"]["instructions"]))
    if jobs:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as pool:
            done = list(pool.map(lambda j: ce.edit_widget(client, usage, jev_key, outline, j[1], j[2], j[3], effort, think), jobs))
        for (sid, si, bi, _), r in zip(jobs, done):
            notes.extend(r["notes"])
            if r["changed"]:
                outline = ce.replace_block(outline, si, bi, r["block"])
                changed.add(sid)
    kind = ce.page_kind(outline, quality)
    for sid, what in adds:
        si = _si(outline, sid)
        if si is None:
            continue
        r = ce.add_widget(client, usage, jev_key, outline, si, what, kind, effort, think)
        notes.extend(r["notes"])
        if r["built"]:
            outline = r["outline"]
            changed.add(sid)
    return outline, changed, notes


def edit_many(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, system: str, page: dict, intents: list[dict],
              request: str, quality: str, index: dict, effort: str, style: str = "") -> dict[str, dict]:
    """sid -> the editor's result for each intent, side by side (evidence first, then the editor call)."""
    def one(intent: dict) -> tuple[str, dict]:
        sid = intent["section"]
        ev = evidence(page, sid, index, jev_key, usage)
        return sid, edit_section(client, usage, system, page, sid, intent, request, quality, ev, effort, style)

    todo = [i for i in intents if _si(page, i["section"]) is not None]
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
        return dict(pool.map(one, todo))
