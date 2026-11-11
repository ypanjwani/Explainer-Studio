"""Follow-ups v3: running a plan (FollowUp.plan) on a Playground or Quick read page. Design: .claude/followup-v3-plan.md.

understand.py made the plan (sections with the reader's intent, structural steps); section_editor.py looks at a section
and decides what to change in it. run_plan, called by pipeline.py:

  1 structure   by code: moves, a merged-away section's playground carried over; written by the page planner through
                targets.apply_targets: new sections, splits, merges, the page title; removals by code (a merged-away
                section only once the section it went into was rewritten). A new section on a Playground page gets a
                playground when the marker gate opens.
  2 sections    every intent (a page-wide level / tone / length is an intent on every section, with the playground's level
                when it doesn't match) goes to the section editor, side by side; text changes merged, then playgrounds
                (removed, edited, added).
  3 knock-ons   a section that still names a removed section after the edit gets one more editor pass.
  4 verify      feedback_check.check on each changed section (Jev: did it do what was asked, is nothing worse; one retry
                through the editor, then that section alone is put back).
  5 report      spec.revision.steps: done / kept (the editor checked it and found nothing to change, with its reason) /
                undone / skipped. A follow-up where every section was checked and found right raises Checked: the app
                shows its message as a reply, not an error (no new version, not counted).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

import anthropic

from . import playground_edit as ce
from ... import data
from . import check as feedback_check
from . import page_index as pidx
from . import section_editor as se
from . import understand as U
from ...llm import ReaderError
from ...pricing import UsageLog
from .revise import restore_kept_widgets
from .targets import apply_targets, remove_stages

from ..explorer.judge import gate_one
from ..explorer.stream import MAX_MARKED
log = logging.getLogger("explainer.steps")

CHECKED = "✓ "  # keep equal to CHECKED_PREFIX in shared/spec.ts
CHECK_MAX = 6  # each changed section is checked when at most this many changed
LEVEL_FIT = {
    "beginner": "fewer controls (2 or 3), plain labels without jargon, one clear chart, and a readout that says what to notice",
    "intermediate": "a few controls with the usual terms, and the main quantity plotted with its readout",
    "advanced": "the full set of parameters, precise labels with units or symbols, and a second view or readout that shows the finer effect",
}


class Checked(ReaderError):
    """The page was left as it was without anything going wrong: every section the follow-up was about was found right, or a
    change was tried and put back because it didn't pass the check. Its message starts with CHECKED, and the app shows it
    as a calm reply in the thread, not as an error."""


def _label(outline: dict, sid: str) -> str:
    st = next((s for s in outline["stages"] if s["id"] == sid), None)
    return str((st or {}).get("label") or sid)


def in_order(outline: dict, ids: set[str]) -> list[str]:
    """`ids` in page order."""
    return [s["id"] for s in outline["stages"] if s["id"] in ids]


# ---------- 1. structure ----------


def code_ops(page: dict, steps: list[dict]) -> tuple[dict, set[str], int]:
    """(page, sections that got a merged-away section's playground, sections moved): by code, no model call."""
    stages = [dict(st) for st in page["stages"]]
    carried: set[str] = set()
    for s in steps:
        if s["op"] == "merge_sections":
            keep = next(st for st in stages if st["id"] == s["section"])
            gone = next(st for st in stages if st["id"] == s["other"])
            moved_in = [b for b in gone["blocks"] if b.get("type") == "widget"]
            if moved_in and not any(b.get("type") == "widget" for b in keep["blocks"]):
                keep["blocks"] = [*keep["blocks"], moved_in[0]]
                gone["blocks"] = [b for b in gone["blocks"] if b is not moved_in[0]]
                carried.add(keep["id"])
    ids = [st["id"] for st in stages]
    order = U.new_order(ids, steps)
    moved = 0
    if order != ids:
        moved = sum(1 for s in steps if s["op"] == "move_section")
        by = {st["id"]: st for st in stages}
        stages = [by[i] for i in order]
    return {**page, "stages": stages}, carried, moved


def structure_targets(steps: list[dict], page: dict) -> list[dict]:
    """The structural steps that need writing, as apply_targets targets."""
    out: list[dict] = []
    for s in steps:
        op, sid, what = s["op"], s["section"], s["what"]
        if op == "add_section":
            out.append({"part": "new-section", "stage": "", "after": s["after"], "what": what})
        elif op == "split_section":
            out.append({"part": "section", "stage": sid, "after": "", "what": f"This stage is split in two; keep only its first half here: {what}"})
            out.append({"part": "new-section", "stage": "", "after": sid, "what": f'The second half of "{_label(page, sid)}", split from it: {what}'})
        elif op == "merge_sections":
            out.append({"part": "section", "stage": sid, "after": "",
                        "what": f'Merge the content of the stage "{_label(page, s["other"])}" (id {s["other"]}) into this stage, so nothing it taught is lost: {what}'})
        elif op == "edit_page_meta" and page["stages"]:
            out.append({"part": "text", "stage": page["stages"][0]["id"], "after": "",
                        "what": f"Change only the page's title, subtitle or key intuition: {what}. Keep this stage's own text as it is."})
    return out


# ---------- 2. sections ----------


def expand(plan: dict, page: dict, index: dict) -> list[dict]:
    """Every intent the editor gets: the plan's, plus a page-wide level / tone / length as an intent on every section (with
    the playground's level when it doesn't match the new one)."""
    intents = [dict(i) for i in plan["sections"] if any(s["id"] == i["section"] for s in page["stages"])]
    by = pidx.by_id(index)
    for s in plan["steps"]:
        if s["op"] not in U.PAGE_WIDE:
            continue
        for st in page["stages"]:
            want = s["what"]
            if s["op"] == "set_level":
                want = f"Rewrite this section for {s['level']} readers: {s['what']}"
                pl = by.get(st["id"], {}).get("playgroundLevel")
                if pl and pl != s["level"] and any(b.get("type") == "widget" for b in st["blocks"]):
                    want += f" Its playground is {pl}-level: fit it to {s['level']} readers too ({LEVEL_FIT[s['level']]}), keeping what it teaches."
            same = next((i for i in intents if i["section"] == st["id"]), None)
            if same:
                same["intent"] = f'{same["intent"]} Also: {want}'
            else:
                intents.append({"section": st["id"], "intent": want, "quote": s["quote"]})
    return intents


def run_editors(client, usage, jev_key, system, page, intents, request, quality, index, effort, think, prose):
    """(outline, results, text-changed, playground-changed, warnings) for these intents on `page`."""
    results = se.edit_many(client, usage, jev_key, system, page, intents, request, quality, index, effort)
    outline, text_changed, warnings = page, set(), []
    for sid, res in results.items():
        if res.get("failed"):
            warnings.append(f'Couldn\'t change "{_label(page, sid)}": {res["failed"]}.')
            continue
        outline, changed, w = se.apply_text(outline, sid, res, prose)
        warnings += w
        if changed:
            text_changed.add(sid)
    outline, pg_changed, notes = se.apply_playgrounds(client, usage, jev_key, outline, {k: v for k, v in results.items() if not v.get("failed")},
                                                      quality, effort, think)
    return outline, results, text_changed, pg_changed, [*warnings, *notes]


# ---------- 5. report ----------


def report(plan: dict, intents: list[dict], before: dict, final: dict, revision: dict, results: dict[str, dict],
           warnings: list[str], moved: int, added: list[str]) -> list[dict]:
    present = {s["id"] for s in final["stages"]}
    touched = set(revision.get("changed") or []) | set(revision.get("widgets") or [])
    out = []
    for s in plan["steps"]:
        op = s["op"]
        rep = {"label": U.label_step(s, before)[:300], "status": "skipped"}
        if op == "remove_section":
            rep["status"] = "done" if s["section"] not in present else "skipped"
        elif op == "move_section":
            rep["status"] = "done" if moved else "skipped"
        elif op == "merge_sections":
            rep["status"] = "done" if s["other"] not in present and s["section"] in touched else "skipped"
        elif op in ("add_section", "split_section"):
            rep["status"] = "done" if added else "skipped"
        else:
            rep["status"] = "done" if touched else "skipped"
        out.append(rep)
    for i in intents:
        sid = i["section"]
        rep = {"label": U.label_intent(i, before)[:300], "status": "skipped"}
        if i.get("derived"):
            rep["derived"] = True
        res = results.get(sid) or {}
        name = _label(before, sid)
        undone = next((w for w in warnings if w.startswith("Kept the previous") and f'"{name}"' in w), "")
        if undone:
            rep.update(status="undone", why=undone[:300])
        elif sid in touched:
            rep["status"] = "done"
            if res.get("reason"):
                rep["why"] = res["reason"]
        elif res.get("failed"):
            rep["why"] = res["failed"]
        elif res and res["decision"] == "none":
            rep.update(status="kept", why=res["reason"] or "nothing needed changing")
        else:
            note = next((w for w in warnings if f'"{name}"' in w), "")
            if note:
                rep["why"] = note[:300]
        out.append(rep)
    return out


def _kept_why(warnings: list[str], label: str) -> str | None:
    """Why Jev's check put a section back ("Kept the previous "label": the change didn't work out (why)."), or None."""
    note = next((w for w in warnings if w.startswith("Kept the previous") and f'"{label}"' in w), None)
    if note is None:
        return None
    m = re.search(r"\((.*)\)\.?$", note)
    return m.group(1) if m else "it didn't pass the check"


def gentle_message(intents: list[dict], before: dict, results: dict[str, dict], warnings: list[str]) -> str:
    """The reply for a follow-up that left the page as it was without anything going wrong: every section was checked and
    found right, or a change was tried and put back because it didn't pass the check. Starts with CHECKED."""
    parts, tried = [], False
    for i in intents:
        sid = i["section"]
        res = results.get(sid) or {}
        label = _label(before, sid)
        n = next((k + 1 for k, s in enumerate(before["stages"]) if s["id"] == sid), "?")
        where = f'Section {n} "{label}"'
        kept = _kept_why(warnings, label)
        if kept is not None:
            tried = True
            parts.append(f"I tried to change {where}, but my new version didn't pass the quality check ({kept}), so I kept it as it was.")
        elif res.get("decision") == "change":
            tried = True
            parts.append(f"I couldn't make a clean new version of {where}, so I kept it as it was.")
        else:
            parts.append(f'{where}: {res.get("reason") or "it already does what you asked"}')
    if not tried:
        return CHECKED + "I checked it and nothing needed changing, so the page stays as it is. " + " ".join(parts)
    return (CHECKED + "I left the page as it was. " + " ".join(parts)
            + " You can send the request again, or describe the change another way (for example, which part to change and how).")


# ---------- run ----------


def run_plan(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, follow: dict, base: dict, plan: dict,
             request: str, ask: Callable[[str, Callable[[Any], dict]], dict], system: str, effort: str, think: bool,
             quality: str | None, prose: bool, store: pidx.PageIndexStore | None = None) -> dict:
    """{"outline", "warnings", "revision"} (as apply_targets returns, plus revision["steps"] and ["moved"]). Raises Checked when
    every section was checked and found right, and ReaderError (with the reason) when nothing could be changed."""
    quality = quality or "playground"
    page = restore_kept_widgets(base, follow["widgets"]) if follow.get("widgets") else base
    plan = U.clean_plan(plan, page)
    # The reader confirmed the parts understand.flag wasn't sure of: the report shouldn't repeat its note.
    plan = {k: [{f: v for f, v in x.items() if f != "unsure"} for x in plan[k]] for k in ("sections", "steps")}
    if U.is_empty(plan) or any(s["op"] == "revert" for s in plan["steps"]):
        raise ReaderError("I couldn't tell what to change. Send your request again.")
    flat = {**follow, "widgets": {}}  # the widgets are in the page already
    index = pidx.get_or_build(store, client, page, usage)
    lite = quality == U.LITE
    lite_note = data.prompt("LITE_REVISE_NOTE") if lite else ""
    page_fields = any(s["op"] in (*U.PAGE_WIDE, "edit_page_meta") for s in plan["steps"])
    warnings: list[str] = []
    revision: dict = {"changed": [], "removed": 0, "widgets": []}

    # 1. structure
    current, carried, moved = code_ops(page, plan["steps"])
    revision["widgets"] += in_order(current, carried)
    written = structure_targets(plan["steps"], current)
    if written:
        r = apply_targets(client, usage, jev_key, flat, current, written, request, ask, effort, think, quality, prose, page_fields, lite_note)
        current, warnings = r["outline"], [*warnings, *r["warnings"]]
        revision["changed"] += r["revision"]["changed"]
        revision["widgets"] += r["revision"].get("widgets") or []
    merged_away = {s["other"] for s in plan["steps"] if s["op"] == "merge_sections" and s["section"] in revision["changed"]}
    gone = {s["section"] for s in plan["steps"] if s["op"] == "remove_section"} | merged_away
    if gone:
        n = len(current["stages"])
        current = remove_stages(current, gone)
        revision["removed"] += n - len(current["stages"])
        if revision["removed"] < len(gone):
            warnings.append("Couldn't remove every section asked for: a page needs at least one.")
    added = [s["id"] for s in current["stages"] if s["id"] not in {x["id"] for x in page["stages"]}]
    kind = ce.page_kind(current, quality)
    if added and kind and not lite and jev_key:  # a new section gets a playground where the marker gate says it has one
        for sid in added:
            si = next(i for i, s in enumerate(current["stages"]) if s["id"] == sid)
            if len(ce.widgets_in(current)) >= MAX_MARKED:
                break
            verdict, note = gate_one(jev_key, current, si, usage, playground=True)
            if verdict.get("open") and not note:
                res = ce.add_widget(client, usage, jev_key, current, si, "", kind, effort, think)
                if res["built"]:
                    current = res["outline"]
                    revision["widgets"].append(sid)

    # 2. sections
    intents = expand(plan, current, index)
    before = current
    results: dict[str, dict] = {}
    if intents:
        current, results, text_changed, pg_changed, w = run_editors(client, usage, jev_key, system, before, intents, request, quality, index, effort, think, prose)
        warnings += w
        revision["changed"] += in_order(current, text_changed)
        revision["widgets"] += in_order(current, pg_changed)

        # 3. knock-ons: a section that still names a removed section gets one more pass
        if gone:
            names = {_label(page, g): g for g in gone}
            again = []
            for st in current["stages"]:
                words = " ".join(str(b.get("md") or b.get("text") or "") for b in st["blocks"]).lower()
                hit = [n for n in names if len(n) >= 6 and n.lower() in words]
                if hit:
                    again.append({"section": st["id"], "intent": f'The section "{hit[0]}" is no longer on the page: reword or drop the mention of it. Change nothing else.',
                                  "quote": "", "derived": True, "because": f'it still mentions "{hit[0]}"'})
            if again:
                current, more, t2, p2, w2 = run_editors(client, usage, jev_key, system, current, again, request, quality, index, effort, think, prose)
                warnings += w2
                results.update({k: v for k, v in more.items() if k not in results})
                revision["changed"] += in_order(current, t2)
                revision["widgets"] += in_order(current, p2)
                intents += [i for i in again if i["section"] not in {x["section"] for x in intents}]

        # 4. verify each changed section against what was asked (one retry through the editor, then put back)
        changed = list(dict.fromkeys([*revision["changed"], *revision["widgets"]]))
        checked_ids = [sid for sid in changed if sid in results]
        if jev_key and 0 < len(checked_ids) <= CHECK_MAX:
            by_sid = {i["section"]: i for i in intents}
            targets = [{"part": "text", "stage": sid, "after": "", "what": by_sid[sid]["intent"]} for sid in checked_ids if sid in by_sid]

            def retry(ts: list[dict]) -> dict:
                again = [{"section": t["stage"], "intent": t["what"], "quote": ""} for t in ts]
                out, *_ = run_editors(client, usage, jev_key, system, before, again, request, quality, index, effort, think, prose)
                return {"outline": out, "warnings": [], "revision": {"changed": [t["stage"] for t in ts], "removed": 0, "widgets": []}}

            rev_in = {**revision, "changed": [s for s in revision["changed"] if s in checked_ids], "widgets": [s for s in revision["widgets"] if s in checked_ids]}
            checked = feedback_check.check(client, usage, jev_key, flat, before, {"outline": current, "warnings": warnings, "revision": rev_in},
                                           targets, request, ask, retry, "follow-up", again="Send your request again")
            current, warnings = checked["outline"], checked["warnings"]
            revision["changed"] = [s for s in revision["changed"] if s not in checked_ids or s in checked["revision"]["changed"]]
            revision["widgets"] = [s for s in revision["widgets"] if s not in checked_ids or s in checked["revision"]["widgets"]]

    level = next((s["level"] for s in plan["steps"] if s["op"] == "set_level"), "")
    if level:
        current = {**current, "level": level}
    revision["changed"] = list(dict.fromkeys(revision["changed"]))
    revision["widgets"] = list(dict.fromkeys(revision["widgets"]))
    revision["steps"] = report(plan, intents, page, current, revision, results, warnings, moved, added)
    if moved:
        revision["moved"] = moved

    if not (revision["changed"] or revision["widgets"] or revision["removed"] or moved or level):
        # Nothing went wrong: every section was found right, or a change was tried and put back by the check. A calm reply,
        # not an error. A real failure (the editor couldn't be reached or wrote nothing usable at all) stays an error.
        if intents and not plan["steps"] and not any((results.get(i["section"]) or {}).get("failed") for i in intents):
            raise Checked(gentle_message(intents, page, results, warnings))
        why = "; ".join(dict.fromkeys(r.get("why", "") for r in revision["steps"] if r.get("why"))) or next((w for w in warnings if w), "")
        raise ReaderError("Nothing on the page was changed" + (f": {why.rstrip('.')}." if why else ".") + " This didn't count as one of today's explainers.")
    return {"outline": current, "warnings": warnings, "revision": revision}


# The /api/followup/plan route (routes/ai.py) and the request parser live in understand.py.
handle = U.handle
parse_plan_request = U.parse_plan_request
enabled = U.enabled


def touches_page(plan: dict) -> bool:
    """The title, key intuition and level may change."""
    return any(s.get("op") in (*U.PAGE_WIDE, "edit_page_meta") for s in plan.get("steps") or [])


__all__ = ["Checked", "CHECKED", "run_plan", "handle", "parse_plan_request", "enabled", "touches_page"]
