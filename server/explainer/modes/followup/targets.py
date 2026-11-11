from __future__ import annotations

import anthropic
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ...core.followup import revision_request
from ...pricing import UsageLog
from . import playground_edit as pe
from ..explorer.stream import PLANNER_NOTE, strip_prebuilt
from .revise import parse_revision, restore_kept_widgets


# from targets.py
log = logging.getLogger("explainer.targets")


# from targets.py
MAX_WORKERS = 4


# from targets.py
PAGE_FIELDS = ("title", "subtitle", "keyIntuition", "level")


# from targets.py
def remove_stages(outline: dict, ids: set[str]) -> dict:
    """The outline without the named stages, by code (no planner call); the page always keeps at least one stage."""
    kept = [st for st in outline["stages"] if st["id"] not in ids]
    return {**outline, "stages": kept} if kept else outline


# from targets.py
def guard_untouched(old: dict, new: dict, allowed: set[str], new_stages: int, page_fields: bool = False) -> tuple[dict, list[str]]:
    """`new` with everything the targets didn't name put back as it was in `old`: a changed stage that wasn't a target, a
    stage that was removed, the page's title, subtitle, key intuition and level (unless page_fields: a change of the whole
    page may change them), and any new stage past `new_stages`. Returns (outline, warnings)."""
    old_by = {s["id"]: s for s in old["stages"]}
    warnings: list[str] = []
    stages: list[dict] = []
    added = 0
    for st in new["stages"]:
        before = old_by.get(st["id"])
        if before is None:
            added += 1
            if added > new_stages:
                warnings.append(f'Follow-up: "{st.get("label", st["id"])}" was not added (it wasn\'t asked for).')
                continue
        elif st["id"] not in allowed and st != before:
            warnings.append(f'Follow-up: "{before.get("label", before["id"])}" was changed without being asked; it was put back.')
            st = before
        stages.append(st)
    present = {s["id"] for s in stages}
    for i, st in enumerate(old["stages"]):
        if st["id"] not in present:  # removed without being asked: back after the stage that preceded it
            warnings.append(f'Follow-up: "{st.get("label", st["id"])}" was removed without being asked; it was put back.')
            prev = old["stages"][i - 1]["id"] if i else None
            at = next((j + 1 for j, s in enumerate(stages) if s["id"] == prev), 0)
            stages.insert(at, st)
            present.add(st["id"])
    out = {**new, "stages": stages}
    for k in PAGE_FIELDS:
        if k in old and not page_fields:
            out[k] = old[k]
    return out, warnings


# from targets.py
def scope_changes(raw: Any, allowed: set[str], new_stages: int) -> tuple[Any, list[str]]:
    """The planner's {"changes": ...} cut down to the targets before it is merged: only replacements of the named stages, the first
    `new_stages` additions, and no removals. Anything else (a full outline, say) passes through for guard_untouched to deal with."""
    if not (isinstance(raw, dict) and isinstance(raw.get("changes"), dict) and "stages" not in raw):
        return raw, []
    ch, warnings = dict(raw["changes"]), []
    replace = [x for x in ch.get("replace") or [] if isinstance(x, dict)]
    kept = [x for x in replace if x.get("id") in allowed]
    add = [x for x in ch.get("add") or [] if isinstance(x, dict)]
    if len(kept) < len(replace):
        warnings.append("Follow-up: the planner also rewrote stages that weren't asked about; they were left as they were.")
    if len(add) > new_stages:
        warnings.append("Follow-up: the planner added more stages than were asked for; the extra ones were left out.")
    if ch.get("remove"):
        warnings.append("Follow-up: the planner tried to remove stages; none were removed.")
    ch.pop("remove", None)
    return {**raw, "changes": {**ch, "replace": kept, "add": add[:new_stages]}}, warnings


# from targets.py
def scope_text(request: str, targets: list[dict], old: dict, plans: dict[str, str]) -> str:
    """The reader's request, narrowed to the targets: which stages to change and what to do, and which of them hold an
    interactive part (so the new text stays consistent with it)."""
    label = {s["id"]: s.get("label", "") for s in old["stages"]}
    lines = []
    for t in targets:
        if t["part"] == "new-section":
            lines.append(f'- add one new stage {"at the start" if t["after"] == "start" else "after stage " + json.dumps(t["after"])}: {t["what"]}')
        else:
            how = "rewrite it" if t["part"] == "section" else "change its wording or blocks, keeping its idea"
            lines.append(f'- stage {json.dumps(t["stage"])} ("{label.get(t["stage"], "")}"): {how}: {t["what"]}')
    out = [request, "Change ONLY these parts of the page. Leave every other stage out of your reply:", *lines]
    held = [f'- stage {json.dumps(sid)} has the interactive part {plan}' for sid, plan in plans.items()]
    if held:
        out += ["The interactive parts on the page (each is a text block \"(interactive widget: ...)\" in the outline; keep those blocks as they are):", *held]
    return "\n".join(out)


# from targets.py
def _plans(outline: dict) -> dict[str, str]:
    """stage id -> '"title", which teaches: ...' for the stages that hold a widget."""
    out: dict[str, str] = {}
    for st in outline["stages"]:
        for b in st["blocks"]:
            if b.get("type") == "widget":
                lesson = str(pe.read_plan(b).get("lesson") or "")
                out[st["id"]] = (out[st["id"]] + "; " if st["id"] in out else "") + f'"{b.get("title", "")}"' + (f", which teaches: {lesson}" if lesson else "")
    return out


# from targets.py
def _compact_json(outline: dict) -> str:
    return json.dumps(pe.viewed(outline), ensure_ascii=False, separators=(",", ":"))


# from targets.py
Ask = Callable[[str, Callable[[Any], dict]], dict]  # (planner request, parse) -> {"outline", "warnings", "revision"?}


# from targets.py
# Quick read pages (lite-plus): a text edit is written by the full planner prompt, so it is told the page's own rules. Sliders
# are not stripped afterwards (a chart reads its values from them; a Quick read page hides them), so the text must not ask
# the reader to use one.
LITE_EDIT_NOTE = ("This is a Quick read page: short and mostly reading. In the stages you write, use no quizzes, questions, reveal or "
                  "predict blocks, worked examples, flip cards, experiments, sampling or training blocks, and add no new sliders, "
                  "choices, toggles or presets. Charts are shown at fixed values: never ask the reader to move, drag, click or "
                  "change anything.")


# from targets.py
def edit_text(ask: Ask, follow: dict, current: dict, targets: list[dict], request: str, prose_only: bool = False,
              page_fields: bool = False, lite: bool = False, lite_note: str = "") -> tuple[dict, set[str], list[str]]:
    """(outline, changed stage ids, warnings): `current` with the text targets changed by one planner call (the change format of
    revise.py, scoped to the targets). `current` may hold real widgets; the planner sees them as placeholders and they come
    back where their placeholders are. lite_note: the Quick read rules to send instead of LITE_EDIT_NOTE (follow-ups v2 sends
    LITE_REVISE_NOTE). prose_only (a Code Lab 7.0 or Playground page, whose interactive parts are widgets): the planner
    writes explanation only, as for a new page of those modes, and any prebuilt interactive block it writes anyway is dropped from the
    stages it changed. On any failure: (current, set(), [a warning]) - nothing changes."""
    compact = pe.viewed(current)
    asked = {**follow, "outline": _compact_json(current)}
    req = revision_request(scope_text(request, targets, compact, _plans(current)), asked, changes=True)
    if prose_only:
        req += "\n\n" + PLANNER_NOTE
    if lite:
        req += "\n\n" + (lite_note or LITE_EDIT_NOTE)
    allowed = {t["stage"] for t in targets if t["part"] != "new-section"}
    new_stages = sum(1 for t in targets if t["part"] == "new-section")
    scoped: list[str] = []

    def parse(raw: Any) -> dict:
        cut, scoped[:] = scope_changes(raw, allowed, new_stages)
        return parse_revision(cut, compact)

    try:
        r = ask(req, parse)
    except Exception:
        log.exception("Follow-up text edit failed")
        return current, set(), ["Couldn't change the text as asked; it was kept as it was."]
    guarded, warnings = guard_untouched(compact, r["outline"], allowed, new_stages, page_fields)
    warnings = [*scoped, *warnings]
    before = {s["id"]: s for s in compact["stages"]}
    if prose_only:
        stripped = {s["id"]: s for s in strip_prebuilt(guarded)["stages"]}
        guarded = {**guarded, "stages": [stripped[s["id"]] if s["id"] not in before or s != before[s["id"]] else s for s in guarded["stages"]]}
    changed = {s["id"] for s in guarded["stages"] if before.get(s["id"]) != s}
    # The real widgets go back where their placeholders are (parse_revision only ever saw placeholders).
    widgets: dict[str, list[dict]] = {}
    for st in current["stages"]:
        for b in st["blocks"]:
            if b.get("type") == "widget":
                widgets.setdefault(st["id"], []).append(b)
    return restore_kept_widgets(guarded, widgets), changed, [*r.get("warnings", []), *warnings]


# from targets.py
def _widget_at(outline: dict, sid: str, what: str) -> tuple[int, int] | None:
    """(stage index, block index) of the widget a target means: the stage's only widget, or the one whose title the change
    names, else its first. None if the stage has none."""
    si = next((i for i, s in enumerate(outline["stages"]) if s["id"] == sid), None)
    if si is None:
        return None
    found = [(bi, b) for bi, b in enumerate(outline["stages"][si]["blocks"]) if b.get("type") == "widget"]
    if not found:
        return None
    named = next((bi for bi, b in found if str(b.get("title") or "").lower() in what.lower() and b.get("title")), None)
    return si, named if named is not None else found[0][0]


# from targets.py
def _as_blocks(targets: list[dict], page: dict, quality: str | None) -> list[dict]:
    """A "widget" target on a page that can't have widgets (Interactive and Quick read pages build their interactive parts from
    ready-made blocks: sliders, plots, experiments) is a request to change that section, so it becomes a "section" target and the
    planner may use those blocks. A widget target on a section of a page that has widgets stays: edited if the section has one,
    otherwise added (or refused, at the cap)."""
    if pe.page_kind(page, quality) is not None or any(b.get("type") == "widget" for st in page["stages"] for b in st["blocks"]):
        return targets
    return [{**t, "part": "section"} if t["part"] == "widget" else t for t in targets]


# from targets.py
def _run_edits(client, usage, jev_key, outline, jobs: list[tuple[int, int, str]], effort: str, think: bool) -> list[dict]:
    """edit_widget for each (stage, block, what), side by side."""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as pool:
        return list(pool.map(lambda j: pe.edit_widget(client, usage, jev_key, outline, j[0], j[1], j[2], effort, think), jobs))


# from targets.py
def apply_targets(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, follow: dict, base: dict, targets: list[dict],
                  request: str, ask: Ask, effort: str = "medium", think: bool = True, quality: str | None = None, prose_only: bool = False,
                  page_fields: bool = False, lite_note: str = "") -> dict:
    """{"outline", "warnings", "revision": {"changed", "removed", "widgets"}}: the page with just the targets changed.
    base: the previous outline as the follow-up carries it (widgets as placeholders); follow["widgets"] holds the real ones."""
    warnings: list[str] = []
    lite = quality == "lite-plus"
    targets = _as_blocks(targets, restore_kept_widgets(base, follow["widgets"]) if follow.get("widgets") else base, quality)
    gone = {t["stage"] for t in targets if t["part"] == "remove"}
    targets = [t for t in targets if t["part"] != "remove" and t["stage"] not in gone]
    text_targets = [t for t in targets if t["part"] != "widget"]
    widget_targets = [t for t in targets if t["part"] == "widget"]

    # The page with its real widgets back (the follow-up's outline only has placeholders); edit_text keeps them in place.
    current, text_changed = (restore_kept_widgets(base, follow["widgets"]) if follow.get("widgets") else base), set()
    removed = 0
    if gone:  # "remove" targets: dropped by code, with their widgets; the page keeps at least one stage
        before = len(current["stages"])
        current = remove_stages(current, gone)
        removed = before - len(current["stages"])
        if removed < len(gone):
            warnings.append("Couldn't remove every section asked for: a page needs at least one.")
    if text_targets:
        current, text_changed, w = edit_text(ask, follow, current, text_targets, request, prose_only, page_fields, lite, lite_note)
        warnings += w

    # 1. widgets that exist: changed side by side; the page keeps the old one when a change fails
    widgets_changed: set[str] = set()
    fresh: dict[str, tuple[dict | None, dict | None]] = {}  # stage id -> (facts, plan) of a widget just rebuilt
    jobs, adds = [], []
    for t in widget_targets:
        at = _widget_at(current, t["stage"], t["what"])
        (jobs if at else adds).append((*at, t["what"]) if at else t)

    def apply(results: list[dict], where: list[tuple[int, int, str]]) -> None:
        nonlocal current
        for res, (si, bi, _) in zip(results, where):
            sid = current["stages"][si]["id"]
            warnings.extend(res["notes"])
            if res["changed"]:
                current = pe.replace_block(current, si, bi, res["block"])
                widgets_changed.add(sid)
                fresh[sid] = (res["facts"], res["plan"])

    apply(_run_edits(client, usage, jev_key, current, jobs, effort, think), jobs)

    # 2. widgets to add, one after another (each counts against the page's cap)
    for t in adds:
        si = next(i for i, s in enumerate(current["stages"]) if s["id"] == t["stage"])
        r = pe.add_widget(client, usage, jev_key, current, si, t["what"], pe.page_kind(current, quality), effort, think)
        warnings.extend(r["notes"])
        if r["built"]:
            current = r["outline"]
            widgets_changed.add(t["stage"])
            fresh[t["stage"]] = (r["facts"], r["plan"])

    # 3. text and widget must still agree: one round, fixing whichever side no longer matches
    fixes = []
    for sid, (facts, plan) in fresh.items():
        si = next(i for i, s in enumerate(current["stages"]) if s["id"] == sid)
        why = pe.agrees(jev_key, current, si, plan, facts, usage)
        if why:
            lesson = str((plan or {}).get("lesson") or "")
            fixes.append({"part": "text", "stage": sid, "after": "", "what": f"Make the text match the interactive part: {why}." + (f" The interactive part teaches: {lesson}" if lesson else "")})
    if fixes:
        current, more, w = edit_text(ask, follow, current, fixes, request, prose_only, lite=lite, lite_note=lite_note)
        text_changed |= more
        warnings += w
    back = []
    for sid in sorted(text_changed - set(fresh)):
        at = _widget_at(current, sid, "")
        if not at:
            continue
        block = current["stages"][at[0]]["blocks"][at[1]]
        if pe.widget_kind(block) is None:
            continue
        why = pe.agrees(jev_key, current, at[0], pe.read_plan(block), pe.facts_of(block), usage)
        if why:
            back.append((at[0], at[1], f"Make the interactive part match the revised text: {why}."))
    if back:
        results = _run_edits(client, usage, jev_key, current, back, effort, think)
        apply(results, back)
        for res, (si, bi, _) in zip(results, back):
            if not res["changed"]:
                warnings.append(f'The text and the interactive part in "{current["stages"][si].get("label", "")}" may not match after this change.')

    ids = [s["id"] for s in current["stages"]]
    revision = {"changed": [i for i in ids if i in text_changed], "removed": removed, "widgets": [i for i in ids if i in widgets_changed]}
    return {"outline": current, "warnings": warnings, "revision": revision}
