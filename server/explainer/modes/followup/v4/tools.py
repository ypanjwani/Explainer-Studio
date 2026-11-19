"""Follow-ups v4: the editor's tools. Each one is applied to the Page by code and checked at once; a refused call returns a
sentence the editor can act on (ToolError), so it can correct itself in the next round.

read        get_section, get_playground, try_playground (what it really shows: readings), search_source
page        set_page_meta, set_level
sections    rename_section, edit_text, set_blocks, add_block, edit_block, remove_block
structure   add_section, remove_section, move_section
playground  patch_playground (direct sets into the spec + the checker), replace_playground / add_playground (Explorer's
            builder with its checks and Jev's review, via playground_edit), remove_playground. Explorer pages only.
            Each playground write returns what the playground now shows (readings at the start, each slider's ends,
            Play ...) and Jev's check of the section's text against them, so the editor sees a wrong number at once.
            Phase 3: replace/add with custom: true let the builder draw a part no built-in view can show as the
            playground's own picture (the drawing mark, PLAYGROUND_DRAWING_NOTE), checked like any mark.
talk        ask_user (only before any write), finish
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from .... import data
from ....core.spec import normalize_block, parse_block
from ....core.validate import check_edit, declared_vars, repair_block
from ....pricing import UsageLog
from ...explorer.judge import plan_text, readings_contradict
from ...explorer.playground import check_playground, playground_html, playground_plan, read_playground, readings_text
from ...explorer.stream import MAX_MARKED, PREBUILT
from .. import playground_edit as pe
from .page import Page, ToolError, _clip

log = logging.getLogger("explainer.followup.v4")

LEVELS = ("beginner", "intermediate", "advanced")
MAX_LABEL = 80
MAX_META = 400
MAX_SPEC_CHARS = 14_000  # get_playground's reply, cut to this
MAX_SECTION_CHARS = 14_000
MAX_SETS = 20
MAX_OPTIONS = 4
SEARCH_WINDOWS = 4
SEARCH_CHARS = 700

# What each mode's text may hold (the prompt's block guide lists the same).
LITE_BANNED = set(data.constants()["LITE_DROPPED"]) | set(data.constants()["CONTROL_TYPES"]) | {"widget", "chart", "scene"}
EXPLORER_BANNED = set(PREBUILT)

WRITES = {"set_page_meta", "set_level", "rename_section", "edit_text", "set_blocks", "add_block", "edit_block", "remove_block",
          "add_section", "remove_section", "move_section", "patch_playground", "replace_playground", "add_playground",
          "remove_playground"}
PLAYGROUND_TOOLS = {"get_playground", "try_playground", "patch_playground", "replace_playground", "add_playground", "remove_playground"}
MAX_RUNS = 8  # try_playground's settings per call
NUMBER = re.compile(r"\d")


@dataclass
class Ctx:
    """What the playground tools need to build (Explorer's builder makes its own Haiku and Jev calls)."""
    client: anthropic.Anthropic | None
    usage: UsageLog
    jev_key: str | None
    effort: str = "medium"
    think: bool = True


# ---------- definitions ----------

_SECTION = {"type": "string", "description": "The section's id (or its handle, S1, S2 ...)."}
_CUSTOM = {"type": "boolean", "description": "Only when no built-in view (chart, diagram, graph, tree, matrix, sequence, grid, table) "
           "can show what the reader wants: the builder may then draw that part as its own picture. Say exactly what to draw."}
_BLOCK = {"type": "object", "description": "One block in the block format of the guide (with its \"type\")."}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


_COMMON = [
    _tool("get_section", "Read one section in full: its label, blurb and every block. Playgrounds show as their title.", {"section": _SECTION}, ["section"]),
    _tool("search_source", "Search the source the page was made from (the paper or the reader's request) for a few words; returns the passages that contain them.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("set_page_meta", "Change the page's title, subtitle and/or key intuition. Leave out what stays.",
          {"title": {"type": "string"}, "subtitle": {"type": "string"}, "keyIntuition": {"type": "string"}}, []),
    _tool("set_level", "Set the page's reader level (only the label; rewrite sections yourself if the level change needs it).",
          {"level": {"type": "string", "enum": list(LEVELS)}}, ["level"]),
    _tool("rename_section", "Give a section a new title.", {"section": _SECTION, "label": {"type": "string"}}, ["section", "label"]),
    _tool("edit_text", "Replace an exact piece of a section's text (in any of its blocks) with new text. `old` must appear exactly once in that section; copy it exactly.",
          {"section": _SECTION, "old": {"type": "string"}, "new": {"type": "string"}}, ["section", "old", "new"]),
    _tool("set_blocks", "Replace all of a section's blocks (keep each playground's {\"type\": \"widget\", \"title\"} entry where it should stay). Optionally a new blurb.",
          {"section": _SECTION, "blocks": {"type": "array", "items": _BLOCK}, "blurb": {"type": "string"}}, ["section", "blocks"]),
    _tool("add_block", "Insert one block into a section at a 0-based position (-1 or past the end = at the end).",
          {"section": _SECTION, "index": {"type": "integer"}, "block": _BLOCK}, ["section", "index", "block"]),
    _tool("edit_block", "Replace the block at a 0-based position in a section with a new block.",
          {"section": _SECTION, "index": {"type": "integer"}, "block": _BLOCK}, ["section", "index", "block"]),
    _tool("remove_block", "Remove the block at a 0-based position in a section (not a playground: use remove_playground).",
          {"section": _SECTION, "index": {"type": "integer"}}, ["section", "index"]),
    _tool("add_section", "Add a new section after another one (`after`: a section id, or \"start\").",
          {"after": {"type": "string"}, "label": {"type": "string"}, "blurb": {"type": "string"}, "blocks": {"type": "array", "items": _BLOCK}},
          ["after", "label", "blocks"]),
    _tool("remove_section", "Remove a section from the page.", {"section": _SECTION}, ["section"]),
    _tool("move_section", "Move a section right after another one (`after`: a section id, or \"start\").",
          {"section": _SECTION, "after": {"type": "string"}}, ["section", "after"]),
    _tool("ask_user", "Ask the reader a multiple-choice question before changing anything, when the brief could mean quite different changes. Ends your turn.",
          {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": MAX_OPTIONS}},
          ["question", "options"]),
    _tool("finish", "End the edit: a short note on what you did, and anything you couldn't do and why.", {"note": {"type": "string"}}, ["note"]),
]

_PLAYGROUND = [
    _tool("get_playground", "Read a section's playground spec (JSON: state, init, components with their snippets, plan).",
          {"section": _SECTION, "title": {"type": "string", "description": "Only when the section has several playgrounds."}}, ["section"]),
    _tool("try_playground", "See what a playground really shows: run it at some settings and get each readout's value and a summary of each chart (lines: first and last value, min, max, shape; bars: their values; tables ...). Without `runs`: the start, each slider at its min and max, each option, Play and button presses. Use it before changing a playground's maths, and to check a number or trend the text states.",
          {"section": _SECTION, "title": {"type": "string"},
           "runs": {"type": "array", "maxItems": MAX_RUNS, "description": "Settings to try, each applied from the start like a reader would.",
                    "items": {"type": "object", "properties": {
                        "set": {"type": "object", "description": "State keys to set, as the controls would (e.g. {\"lr\": 0.5})."},
                        "play": {"type": "integer", "description": "Play steps after that (at most 60)."},
                        "press": {"type": "object", "description": "Button ids and how often to press each (at most 5)."}}}}},
          ["section"]),
    _tool("patch_playground", "Small exact changes to a playground's spec: each set puts `value` at `path` (dots between keys; a list item by its index or by its id, e.g. \"components.lr.max\" or \"components.2.label\" or \"state.n\"). The changed spec is checked; a failed check is refused with the checker's problems.",
          {"section": _SECTION, "title": {"type": "string"},
           "sets": {"type": "array", "maxItems": MAX_SETS, "items": {"type": "object", "properties": {"path": {"type": "string"}, "value": {}}, "required": ["path", "value"]}}},
          ["section", "sets"]),
    _tool("replace_playground", "A bigger change to a section's playground, built and checked by the playground builder: describe exactly what to change or build (which control, range, chart, table, formula or label, and why).",
          {"section": _SECTION, "title": {"type": "string"}, "instructions": {"type": "string"}, "custom": _CUSTOM}, ["section", "instructions"]),
    _tool("add_playground", "Give a section that has none a new playground (at most 4 on the page): describe what the reader should explore and see.",
          {"section": _SECTION, "instructions": {"type": "string"}, "custom": _CUSTOM}, ["section", "instructions"]),
    _tool("remove_playground", "Remove a section's playground.", {"section": _SECTION, "title": {"type": "string"}}, ["section"]),
]


def definitions(lite: bool) -> list[dict]:
    """The tool list for this page's mode (byte-stable, so the request's cache hits); the last one carries the cache mark."""
    tools = copy.deepcopy(_COMMON if lite else [*_COMMON[:-2], *_PLAYGROUND, *_COMMON[-2:]])
    tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


# ---------- blocks ----------


def _check_blocks(page: Page, si: int, blocks: Any, old_widgets: list[dict]) -> list[dict]:
    """New blocks for stage si, parsed and repaired like a planner's (core/validate.check_edit), with the section's real
    playgrounds put back where the editor kept their entries. Raises ToolError."""
    if not isinstance(blocks, list) or not blocks:
        raise ToolError("A section needs at least one block.")
    # A type the section already holds stays allowed (older pages, a Reader page's charts): only new kinds are refused.
    banned = (LITE_BANNED if page.lite else EXPLORER_BANNED - {"widget"}) - {x.get("type") for x in page.stages[si]["blocks"]}
    spare = list(old_widgets)
    out: list[dict] = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict) or not isinstance(b.get("type"), str):
            raise ToolError(f"Block {i} has no type.")
        if b["type"] == "widget":
            if page.lite:
                raise ToolError("This page has no playgrounds; use a text, table or chart block instead.")
            t = str(b.get("title") or "").strip().lower()
            w = next((x for x in spare if str(x.get("title") or "").strip().lower() == t), None) or (spare[0] if spare else None)
            if w is None:
                raise ToolError("Never write a widget yourself: keep an existing playground's entry, or use add_playground.")
            spare.remove(w)
            out.append(w)
            continue
        if b["type"] in banned:
            raise ToolError(f'A "{b["type"]}" block can\'t go in this page\'s text'
                            + (" (it is a Reader page: no quizzes, controls or anything to click)." if page.lite
                               else " (interactive parts live in playgrounds: use the playground tools)."))
        ok, val = parse_block(normalize_block(b))
        if not ok:
            raise ToolError(f'Block {i} ("{b["type"]}") doesn\'t follow the block format: check its fields against the guide.')
        out.append(val)
    if spare:
        raise ToolError(f'The playground "{spare[0].get("title", "")}" was left out. Keep its entry, or remove it with remove_playground.')
    r = check_edit(page.current, si, out)
    if r["ok"]:
        return r["spec"]["stages"][si]["blocks"]
    # The same problem with the section as it was: it is elsewhere on the page and not this change's doing. The new
    # blocks are still repaired one by one (the first half of check_edit).
    was = check_edit(page.current, si, page.stages[si]["blocks"])
    if was["ok"] or was["reason"] != r["reason"]:
        raise ToolError(f"That change can't be used: {r['reason']}")
    stages = [{**st, "blocks": out} if k == si else st for k, st in enumerate(page.stages)]
    vars_ = declared_vars(stages)
    problems: list[str] = []
    fixed = [repair_block(b, vars_, problems.append) for b in out]
    if problems or any(b is None for b in fixed):
        raise ToolError(f"That change can't be used: {problems[0] if problems else 'a block is no longer valid'}")
    return fixed


def _widgets(st: dict) -> list[dict]:
    return [b for b in st["blocks"] if b.get("type") == "widget"]


def _put_blocks(page: Page, si: int, blocks: list[dict], **extra: Any) -> None:
    page.set_stage(si, {**page.stages[si], **extra, "blocks": blocks})


def _strings(v: Any, path: tuple = ()) -> list[tuple[tuple, str]]:
    """Every string leaf of a block, with its path (type and ids excluded)."""
    if isinstance(v, str):
        return [(path, v)]
    if isinstance(v, dict):
        return [x for k, w in v.items() if k not in ("type", "id", "var", "html", "plan") for x in _strings(w, (*path, k))]
    if isinstance(v, list):
        return [x for i, w in enumerate(v) for x in _strings(w, (*path, i))]
    return []


def _set_in(v: Any, path: tuple, new: Any) -> Any:
    if not path:
        return new
    k, rest = path[0], path[1:]
    if isinstance(v, dict):
        return {**v, k: _set_in(v[k], rest, new)}
    out = list(v)
    out[k] = _set_in(v[k], rest, new)
    return out


# ---------- playground specs ----------


def _step_into(cur: Any, key: str) -> tuple[Any, Any]:
    """(container key, child) for one path step: a dict key, a list index, or a list item's id."""
    if isinstance(cur, dict):
        return key, cur.get(key)
    if isinstance(cur, list):
        if re.fullmatch(r"-?\d+", key):
            i = int(key)
            if -len(cur) <= i < len(cur):
                return i, cur[i]
            raise ToolError(f'Index {key} is out of range (the list has {len(cur)} items).')
        for i, item in enumerate(cur):
            if isinstance(item, dict) and str(item.get("id")) == key:
                return i, item
        raise ToolError(f'No item with id "{key}" in that list.')
    raise ToolError(f'"{key}" goes inside something that is not an object or a list.')


def apply_sets(spec: dict, sets: Any) -> dict:
    """spec with each {path, value} set (a list item by index or by its id; a new key may be added to an object)."""
    if not isinstance(sets, list) or not sets:
        raise ToolError("Give at least one {path, value} set.")
    out = copy.deepcopy(spec)
    for s in sets[:MAX_SETS]:
        if not isinstance(s, dict) or not isinstance(s.get("path"), str) or not s["path"].strip():
            raise ToolError("Each set needs a path like \"components.lr.max\" and a value.")
        keys = [k for k in re.split(r"[.\[\]]+", s["path"].strip()) if k]
        cur: Any = out
        for k in keys[:-1]:
            ck, child = _step_into(cur, k)
            if child is None:
                raise ToolError(f'The path "{s["path"]}" doesn\'t exist in the spec (no "{k}"). Read it with get_playground.')
            cur = child
        last = keys[-1]
        if isinstance(cur, dict):
            cur[last] = s.get("value")
        else:
            ck, _ = _step_into(cur, last)
            cur[ck] = s.get("value")
    return out


# ---------- the tools ----------


def _section_view(page: Page, si: int) -> str:
    return json.dumps(page.viewed_stage(page.stages[si], si), ensure_ascii=False)[:MAX_SECTION_CHARS]


def _search(page: Page, query: str) -> str:
    text = page.source
    words = [w for w in re.findall(r"\w{3,}", query.lower())][:6]
    if not text or not words:
        return "There is no source text to search." if not text else "Give a few words to search for."
    low = text.lower()
    hits: list[int] = []
    for w in words:
        for m in re.finditer(re.escape(w), low):
            if all(abs(m.start() - h) > SEARCH_CHARS for h in hits):
                hits.append(m.start())
            if len(hits) >= SEARCH_WINDOWS:
                break
    if not hits:
        return "Nothing in the source matches those words."
    return "\n\n---\n\n".join(text[max(0, h - SEARCH_CHARS // 2): h + SEARCH_CHARS // 2] for h in sorted(hits))


def _after_index(page: Page, after: Any) -> int:
    """The position a section goes to after `after` ("start" = 0)."""
    a = str(after or "").strip().lower()
    if a in ("start", "top", "beginning", ""):
        return 0
    if a in ("end", "bottom"):
        return len(page.stages)
    return page.index_of(after) + 1


def _new_widget(page: Page, si: int, bi: int, block: dict) -> None:
    blocks = list(page.stages[si]["blocks"])
    blocks[bi] = block
    _put_blocks(page, si, blocks)


def playground_readings(page: Page, st: dict, runs: list[dict] | None = None) -> list[dict]:
    """[{"title", "readings"}] for each playground in section st: what it really shows (explorer.playground.read_playground;
    "" when it can't be run). The default readings are cached by the playground's html (page.read_cache)."""
    out = []
    for b in st["blocks"]:
        if b.get("type") != "widget" or pe.widget_kind(b) != "playground":
            continue
        html = str(b.get("html") or "")
        if runs is None and html in page.read_cache:
            r = page.read_cache[html]
        else:
            spec = pe.read_playground_spec(html)
            r = read_playground(spec, runs) if spec else None
            if runs is None:
                page.read_cache[html] = r
        out.append({"title": str(b.get("title") or ""), "readings": readings_text(r)})
    return out


def _after_playground(page: Page, si: int, ctx: Ctx) -> str:
    """What a changed playground now shows, and Jev's check of the section's text against it, for the tool's result."""
    st = page.stages[si]
    shown = [r for r in playground_readings(page, st) if r["readings"]]
    if not shown:
        return ""
    text = "\n\nIt now shows:\n" + "\n".join((f'"{r["title"]}":\n' if len(shown) > 1 else "") + r["readings"] for r in shown)
    bad = readings_contradict(ctx.jev_key, st, shown, ctx.usage)
    if bad:
        text += (f"\n\nThe text and the playground disagree: {bad}. Compare the section's text (get_section) with these readings "
                 "and fix whichever is wrong.")
    return text


def _builder_what(inp: Any, empty: str) -> tuple[str, bool]:
    """The builder's instructions, and whether the editor asked for the playground's own picture (phase 3: custom adds
    PLAYGROUND_DRAWING_NOTE, so the builder may use the drawing mark; it is checked like any mark)."""
    what = _clip(inp.get("instructions"), 1500)
    if not what:
        raise ToolError(empty)
    custom = inp.get("custom") is True
    return (f"{what}\n\n{data.prompt('PLAYGROUND_DRAWING_NOTE')}" if custom else what), custom


def _number_hint(page: Page, si: int, *changed: Any) -> str:
    """A text change with a number in a section that has a playground: remind the editor to check it against the playground."""
    if page.lite or not any(b.get("type") == "widget" for b in page.stages[si]["blocks"]):
        return ""
    if not any(NUMBER.search(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)) for c in changed):
        return ""
    return " This section has a playground: if its text now states a number or trend the playground shows, check it with try_playground."


def apply(page: Page, name: str, inp: Any, ctx: Ctx) -> str:
    """Apply one tool call; returns the result text for the editor. Raises ToolError when it can't be applied."""
    inp = inp if isinstance(inp, dict) else {}
    if page.lite and name in PLAYGROUND_TOOLS:
        raise ToolError("This Reader page has no playgrounds.")

    if name == "get_section":
        si, _ = page.stage(inp.get("section"))
        return _section_view(page, si)
    if name == "search_source":
        return _search(page, str(inp.get("query") or ""))

    if name == "ask_user":
        if page.writes:
            raise ToolError("You already changed the page, so don't ask now: finish what you can and explain the rest in finish.")
        options = [_clip(o, 120) for o in (inp.get("options") or []) if _clip(o, 120)][:MAX_OPTIONS]
        q = _clip(inp.get("question"), 300)
        if not q or len(options) < 2:
            raise ToolError("A question needs its text and 2 to 4 concrete options.")
        page.asked = {"text": q, "options": options}
        page.finished = True
        return "Asked. Stop here."
    if name == "finish":
        page.note = _clip(inp.get("note"), 1200)
        page.finished = True
        return "Done."

    if name == "set_page_meta":
        changes = {k: _clip(inp[k], MAX_META) for k in ("title", "subtitle", "keyIntuition") if isinstance(inp.get(k), str) and _clip(inp[k], MAX_META)}
        if not changes:
            raise ToolError("Give a new title, subtitle or keyIntuition.")
        page.current = {**page.current, **changes}
        page.page_fields = True
        page.record("", name, "Page " + ", ".join(f'{k} → "{v[:80]}"' for k, v in changes.items()))
        return "Changed."
    if name == "set_level":
        level = inp.get("level")
        if level not in LEVELS:
            raise ToolError(f"The level is one of {', '.join(LEVELS)}.")
        page.current = {**page.current, "level": level}
        page.page_fields = True
        page.record("", name, f"Page level → {level}")
        return "Changed."

    if name == "rename_section":
        si, st = page.stage(inp.get("section"))
        label = _clip(inp.get("label"), MAX_LABEL)
        if not label:
            raise ToolError("Give the new title.")
        old = st.get("label", "")
        page.set_stage(si, {**st, "label": label})
        page.record(st["id"], name, f'Renamed "{old}" → "{label}"')
        return "Renamed."
    if name == "edit_text":
        si, st = page.stage(inp.get("section"))
        old, new = str(inp.get("old") or ""), str(inp.get("new") or "")
        if not old.strip():
            raise ToolError("Copy the exact text to replace into `old`.")
        hits = [(bi, p) for bi, b in enumerate(st["blocks"]) if b.get("type") != "widget" for p, s in _strings(b) if old in s]
        count = sum(s.count(old) for bi, b in enumerate(st["blocks"]) if b.get("type") != "widget" for _, s in _strings(b))
        if count == 0:
            raise ToolError("That text isn't in this section. Read it with get_section and copy the words exactly.")
        if count > 1:
            raise ToolError("That text appears more than once in this section; include more words around it so it is unique.")
        bi, path = hits[0]
        leaf = next(s for p, s in _strings(st["blocks"][bi]) if p == path)
        blocks = list(st["blocks"])
        blocks[bi] = _set_in(blocks[bi], path, leaf.replace(old, new, 1))
        _put_blocks(page, si, _check_blocks(page, si, blocks, _widgets(st)))
        page.record(st["id"], name, f'Reworded part of "{st.get("label", "")}"')
        return "Replaced." + _number_hint(page, si, old, new)
    if name == "set_blocks":
        si, st = page.stage(inp.get("section"))
        blocks = _check_blocks(page, si, inp.get("blocks"), _widgets(st))
        extra = {"blurb": _clip(inp["blurb"], 400)} if isinstance(inp.get("blurb"), str) and inp["blurb"].strip() else {}
        _put_blocks(page, si, blocks, **extra)
        page.record(st["id"], name, f'Rewrote "{st.get("label", "")}"')
        return "Section updated." + _number_hint(page, si, [b for b in blocks if b.get("type") != "widget"])
    if name in ("add_block", "edit_block", "remove_block"):
        si, st = page.stage(inp.get("section"))
        blocks = list(st["blocks"])
        try:
            i = int(inp.get("index"))
        except (TypeError, ValueError):
            raise ToolError("Give the block's 0-based index.") from None
        if name == "add_block":
            i = len(blocks) if i < 0 or i > len(blocks) else i
            blocks.insert(i, inp.get("block"))
            what = f'Added a {(inp.get("block") or {}).get("type", "block") if isinstance(inp.get("block"), dict) else "block"} to "{st.get("label", "")}"'
        else:
            if not 0 <= i < len(blocks):
                raise ToolError(f"Block {i} doesn't exist (the section has {len(blocks)} blocks, 0 to {len(blocks) - 1}).")
            if blocks[i].get("type") == "widget":
                raise ToolError("That block is a playground: use the playground tools for it.")
            if name == "edit_block":
                blocks[i] = inp.get("block")
                what = f'Changed a {blocks[i].get("type", "block") if isinstance(blocks[i], dict) else "block"} in "{st.get("label", "")}"'
            else:
                gone = blocks.pop(i)
                what = f'Removed a {gone.get("type", "block")} from "{st.get("label", "")}"'
        _put_blocks(page, si, _check_blocks(page, si, blocks, _widgets(st)))
        page.record(st["id"], name, what)
        return "Done." + (_number_hint(page, si, inp.get("block")) if name != "remove_block" else "")

    if name == "add_section":
        at = _after_index(page, inp.get("after"))
        label = _clip(inp.get("label"), MAX_LABEL)
        if not label:
            raise ToolError("Give the new section a title.")
        sid = page.new_id(label)
        near = page.stages[min(max(at - 1, 0), len(page.stages) - 1)]
        st = {"id": sid, "label": label, "phase": near.get("phase", ""), "blurb": _clip(inp.get("blurb"), 400), "blocks": []}
        stages = list(page.stages)
        stages.insert(at, st)
        page.current = {**page.current, "stages": stages}
        try:
            blocks = _check_blocks(page, at, inp.get("blocks"), [])
        except ToolError:
            stages.pop(at)
            page.current = {**page.current, "stages": stages}
            raise
        _put_blocks(page, at, blocks)
        page.added.append(sid)
        page.record(sid, name, f'Added the section "{label}"')
        return f'Added as "{sid}".'
    if name == "remove_section":
        si, st = page.stage(inp.get("section"))
        if len(page.stages) <= 1:
            raise ToolError("A page needs at least one section.")
        page.current = {**page.current, "stages": [s for s in page.stages if s["id"] != st["id"]]}
        page.removed += 0 if st["id"] in page.added else 1
        page.text_changed.discard(st["id"])
        page.widgets_changed.discard(st["id"])
        if st["id"] in page.added:
            page.added.remove(st["id"])
        page.record("", name, f'Removed the section "{st.get("label", "")}"')
        return "Removed."
    if name == "move_section":
        si, st = page.stage(inp.get("section"))
        rest = [s for s in page.stages if s["id"] != st["id"]]
        a = str(inp.get("after") or "").strip()
        if a == st["id"]:
            raise ToolError("A section can't go after itself.")
        at = 0 if a.lower() in ("start", "top", "beginning", "") else (len(rest) if a.lower() in ("end", "bottom") else None)
        if at is None:
            ai = page.index_of(a)
            target = page.stages[ai]["id"]
            at = next(k for k, s in enumerate(rest) if s["id"] == target) + 1
        rest.insert(at, st)
        if [s["id"] for s in rest] == [s["id"] for s in page.stages]:
            return "It is already there."
        page.current = {**page.current, "stages": rest}
        page.moved += 1
        page.record("", name, f'Moved "{st.get("label", "")}"')
        return "Moved."

    # ---------- playgrounds (Explorer) ----------
    if name == "get_playground":
        si, _ = page.stage(inp.get("section"))
        bi = page.widget_at(si, inp.get("title"))
        spec = pe.read_playground_spec(page.stages[si]["blocks"][bi].get("html", ""))
        if spec is None:
            raise ToolError("This playground can't be read (an older kind): use replace_playground to rebuild it.")
        return json.dumps(spec, ensure_ascii=False)[:MAX_SPEC_CHARS]
    if name == "try_playground":
        si, st = page.stage(inp.get("section"))
        bi = page.widget_at(si, inp.get("title"))
        spec = pe.read_playground_spec(st["blocks"][bi].get("html", ""))
        if spec is None:
            raise ToolError("This playground can't be run (an older kind): read it with get_playground.")
        runs = [r for r in inp["runs"] if isinstance(r, dict)][:MAX_RUNS] if isinstance(inp.get("runs"), list) else []
        r = read_playground(spec, runs or None)
        if r is None:
            raise ToolError("The playground couldn't be run just now: read its spec with get_playground instead.")
        return r["text"] or "It shows nothing at those settings."
    if name == "patch_playground":
        si, st = page.stage(inp.get("section"))
        bi = page.widget_at(si, inp.get("title"))
        old = st["blocks"][bi]
        spec = pe.read_playground_spec(old.get("html", ""))
        if spec is None:
            raise ToolError("This playground can't be read (an older kind): use replace_playground instead.")
        new = apply_sets(spec, inp.get("sets"))
        report = check_playground(new)
        if report["problems"]:
            raise ToolError("The changed playground fails its check, so nothing was changed: " + "; ".join(report["problems"][:4]))
        plan = playground_plan(new)
        title = _clip(new.get("title"), 80) or old.get("title") or "Playground"
        _new_widget(page, si, bi, {**old, "title": title, "html": playground_html(new), "plan": plan_text(plan)})
        page.facts[st["id"]] = report.get("facts")
        page.record(st["id"], name, f'Adjusted the playground in "{st.get("label", "")}"', widget=True)
        extra = report.get("layout") or []
        return ("Changed; it runs without errors." + (f" Layout notes (fix if they matter): {'; '.join(extra[:3])}" if extra else "")
                + _after_playground(page, si, ctx))
    if name == "replace_playground":
        si, st = page.stage(inp.get("section"))
        bi = page.widget_at(si, inp.get("title"))
        what, custom = _builder_what(inp, "Describe exactly what to change.")
        res = pe.edit_widget(ctx.client, ctx.usage, ctx.jev_key, page.current, si, bi, what, ctx.effort, ctx.think)
        if not res["changed"]:
            raise ToolError((res["notes"] or ["The playground couldn't be changed."])[0])
        _new_widget(page, si, bi, res["block"])
        page.facts[st["id"]] = res.get("facts")
        own = " with its own drawing" if custom else ""
        page.record(st["id"], name, f'Changed the playground in "{st.get("label", "")}"{own}', widget=True)
        return "Changed; it passed the playground checks and review." + _after_playground(page, si, ctx)
    if name == "add_playground":
        si, st = page.stage(inp.get("section"))
        if _widgets(st):
            raise ToolError("This section already has a playground: change it with patch_playground or replace_playground.")
        if len(pe.widgets_in(page.current)) >= MAX_MARKED:
            raise ToolError(pe.CAP_NOTE)
        what, custom = _builder_what(inp, "Describe what the reader should explore and see.")
        res = pe.add_widget(ctx.client, ctx.usage, ctx.jev_key, page.current, si, what, "playground", ctx.effort, ctx.think)
        if not res["built"]:
            raise ToolError((res["notes"] or ["The playground couldn't be built."])[0])
        page.current = res["outline"]
        own = " with its own drawing" if custom else ""
        page.record(st["id"], name, f'Added a playground to "{st.get("label", "")}"{own}', widget=True)
        return "Added; it passed the playground checks and review." + _after_playground(page, si, ctx)
    if name == "remove_playground":
        si, st = page.stage(inp.get("section"))
        bi = page.widget_at(si, inp.get("title"))
        blocks = [b for k, b in enumerate(st["blocks"]) if k != bi]
        if not blocks:
            raise ToolError("The section would be empty: add text to it first (set_blocks), then remove the playground.")
        _put_blocks(page, si, blocks)
        page.record(st["id"], name, f'Removed the playground from "{st.get("label", "")}"', widget=True)
        return "Removed."

    raise ToolError(f'There is no tool "{name}".')


def run_tool(page: Page, name: str, inp: Any, ctx: Ctx) -> dict:
    """A tool_result's content and is_error, never raising (an unexpected failure is reported to the editor)."""
    try:
        return {"content": apply(page, name, inp, ctx), "is_error": False}
    except ToolError as e:
        return {"content": str(e), "is_error": True}
    except Exception:
        log.exception("Follow-up v4: tool %s failed", name)
        return {"content": "That didn't work because of an internal problem. Try another way, or explain it in finish.", "is_error": True}


Run = Callable[[Page, str, Any, Ctx], dict]
