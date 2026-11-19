"""Follow-ups v5 (copied from v4's tools.py, then changed): the agent's tools. Each one is applied to the Page by code and
checked at once; a refused call returns a sentence the editor can act on (ToolError), so it can correct itself.

read        get_section, get_playground, try_playground (what it really shows: readings), search_source; v5: each of the
            first three takes an optional `version` (any version of the page's line), plus get_change(version),
            search_page(query, version?) and get_overview(version) (that version's main file)
versions    v5: restore_section / restore_playground copy a section or a playground of another version back exactly, by
            code (a restored section keeps its id: the same id is the same section in every version); start_from(version)
            works on another version instead (only before any write)
page        set_page_meta, set_level
sections    rename_section, edit_text, set_blocks, add_block, edit_block, remove_block
structure   add_section, remove_section, move_section
playground  patch_playground (direct sets into the spec + the checker), replace_playground / add_playground (Explorer's
            builder with its checks and Jev's review, via playground_edit), remove_playground. Explorer pages only.
            Each playground write returns what the playground now shows (readings at the start, each slider's ends,
            Play ...) and Jev's check of the section's text against them, so the editor sees a wrong number at once.
            Phase 3: replace/add with custom: true let the builder draw a part no built-in view can show as the
            playground's own picture (the drawing mark, PLAYGROUND_DRAWING_NOTE), checked like any mark.
end         ask_user (only before any write; applied here), and check_changes, new_topic, finish (applied by agent.py,
            which runs the checks; END_TOOLS)
answer-only when the message only asks (intent.py) or past the day's explainers (agent.py): every write is refused
            (Ctx.no_writes)
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

from .... import data, versions
from ....core.spec import normalize_block, parse_block
from ....core.validate import check_edit, declared_vars, repair_block
from ....pricing import UsageLog
from ...explorer.judge import plan_text, readings_contradict
from ...topic import new_topic_tool
from ...explorer.playground import check_playground, playground_html, playground_plan, read_playground, readings_text
from ...explorer.stream import MAX_MARKED, PREBUILT
from .. import main_file as mf
from .. import playground_edit as pe
from ..revise import restore_kept_widgets
from . import base as base_mod
from . import look as look_mod
from . import look_on
from .page import Page, ToolError, _clip, cut, whole

log = logging.getLogger("explainer.followup.v5")

LEVELS = ("beginner", "intermediate", "advanced")
MAX_LABEL = 80
MAX_META = 400
# What the read tools return, whole up to these (page.whole: past them a plain "cut here" note). Were 14k each, cut silently
# mid-JSON (Nov 17 2026); most sections and playgrounds are ~5k.
MAX_SPEC_CHARS = 40_000  # get_playground
MAX_SECTION_CHARS = 40_000
BUILDER_CHARS = 5000  # replace_playground / add_playground instructions (were 1500: details silently dropped)
MAX_SETS = 20
MAX_OPTIONS = 4
SEARCH_WINDOWS = 4
SEARCH_CHARS = 700

# What each mode's text may hold (the prompt's block guide lists the same).
LITE_BANNED = set(data.constants()["LITE_DROPPED"]) | set(data.constants()["CONTROL_TYPES"]) | {"widget", "chart", "scene"}
EXPLORER_BANNED = set(PREBUILT)

WRITES = {"set_page_meta", "set_level", "rename_section", "edit_text", "set_blocks", "add_block", "edit_block", "remove_block",
          "add_section", "remove_section", "move_section", "patch_playground", "replace_playground", "add_playground",
          "remove_playground", "restore_section", "restore_playground"}
PLAYGROUND_TOOLS = {"get_playground", "try_playground", "patch_playground", "replace_playground", "add_playground", "remove_playground",
                    "restore_playground", "look_at_playground"}
MAX_CHANGE_CHARS = 40_000
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
    no_writes: str = ""  # set: every write is refused with this sentence (the message only asks; or past the day's explainers)


# ---------- definitions ----------

_SECTION = {"type": "string", "description": "The section's id (or its handle, S1, S2 ...)."}
_CUSTOM = {"type": "boolean", "description": "Only when no built-in view (chart, diagram, graph, tree, matrix, sequence, grid, table) "
           "can show what the reader wants: the builder may then draw that part as its own picture. Say exactly what to draw."}
_BLOCK = {"type": "object", "description": "One block in the block format of the guide (with its \"type\")."}
_VERSION = {"type": "integer", "description": "Another version of this page (its number in the version log); leave out for the page you are editing."}
_OLD_SECTION = {"type": "string", "description": "The section's id in that version (or its handle there, S1, S2 ...)."}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


_COMMON = [
    _tool("get_section", "Read one section in full: its label, blurb and every block. Playgrounds show as their title. With `version`, the section as it is in that version.",
          {"section": _SECTION, "version": _VERSION}, ["section"]),
    _tool("get_overview", "Read another version's main file: a short description of each of its sections (handle and id, blocks, playground, what it covers).",
          {"version": {"type": "integer"}}, ["version"]),
    _tool("get_change", "What one version changed compared with the version it was made from: sections added, removed and replaced (before and after), the order, and page fields.",
          {"version": {"type": "integer"}}, ["version"]),
    _tool("search_page", "Find every section that mentions some words (plain text, any block): in the page you are editing, or with `version` in that version. Use it to find passing mentions (\"as we saw in ...\", a label, a number).",
          {"query": {"type": "string"}, "version": _VERSION}, ["query"]),
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
    _tool("restore_section", "Bring back a section exactly as it was in another version (text and playground, copied by code). If the page still has that section, it is replaced by the old one; otherwise it is added after `after` (a section id, or \"start\").",
          {"version": {"type": "integer"}, "section": _OLD_SECTION, "after": {"type": "string"}}, ["version", "section"]),
    _tool("start_from", "Work on another version of the page instead of the one the message is under (\"go back to version 3 and ...\"): only before any change. The changes made after that version are then not in the result; finish right away to simply go back to it.",
          {"version": {"type": "integer"}}, ["version"]),
]

_PLAYGROUND = [
    _tool("get_playground", "Read a section's playground spec (JSON: state, init, components with their snippets, plan). With `version`, as it is in that version.",
          {"section": _SECTION, "title": {"type": "string", "description": "Only when the section has several playgrounds."}, "version": _VERSION}, ["section"]),
    _tool("try_playground", "See what a playground really shows: run it at some settings and get each readout's value and a summary of each chart (lines: first and last value, min, max, shape; bars: their values; tables ...). Without `runs`: the start, each slider at its min and max, each option, Play and button presses. Use it before changing a playground's maths, and to check a number or trend the text states.",
          {"section": _SECTION, "title": {"type": "string"}, "version": _VERSION,
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
    _tool("restore_playground", "Bring back a playground exactly as it was in another version (copied by code): from that version's section into `to_section` of this page (default: the section with the same id). It replaces the section's playground, or is added when the section has none.",
          {"version": {"type": "integer"}, "section": _OLD_SECTION, "to_section": _SECTION}, ["version", "section"]),
]


_END = [
    _tool("check_changes", "Run the page checks on what you changed so far: each changed section against the request, its text against its playground's numbers, and the whole page for anything the change made wrong. Returns the problems found (nothing is undone).",
          {}, []),
    _tool("ask_user", "Ask the reader a multiple-choice question before changing anything, when the message could mean quite different changes. Ends your turn.",
          {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": MAX_OPTIONS}},
          ["question", "options"]),
    new_topic_tool(under_page=True),  # shared with the first-message step (modes/topic.py)
    _tool("finish", "End your turn with the reply the reader sees (an answer, or what you changed), and optional suggestions for next changes.",
          {"reply": {"type": "string"}, "suggestions": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
           "offers_fixes": {"type": "boolean", "description": "true when you changed nothing and your reply lists fixes you'd make: "
                            "the reader then gets a button to say yes to all of them."}}, ["reply"]),
]
END_TOOLS = {"check_changes", "new_topic", "finish"}  # applied by agent.py


def definitions(lite: bool) -> list[dict]:
    """The tool list for this page's mode (byte-stable, so the request's cache hits); the last one carries the cache mark."""
    looks = [look_mod.TOOL] if look_on() and not lite else []  # look.py: Explorer pages, FOLLOWUP_V5_LOOK=1
    tools = copy.deepcopy([*_COMMON, *_END] if lite else [*_COMMON, *_PLAYGROUND, *looks, *_END])
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


def _version(page: Page, inp: dict) -> int | None:
    """The `version` a read tool names, or None for the page being edited (the base version itself counts as that)."""
    v = inp.get("version")
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        try:
            v = int(str(v).strip().lstrip("vV"))
        except ValueError:
            raise ToolError("`version` is a version number from the version log.") from None
    return None if v == page.store.base else v


def _old_page(page: Page, version: int) -> dict:
    old = page.store.page(version)
    if old is None:
        raise ToolError(page.store.why_missing(version))
    return old


def _old_stage(page: Page, version: int, ref: Any) -> tuple[int, dict]:
    """A section of another version, named by its id, its handle there (S3) or its exact label."""
    stages = [s for s in _old_page(page, version).get("stages") or [] if isinstance(s, dict)]
    r = str(ref or "").strip()
    ids = [s.get("id") for s in stages]
    if r in ids:
        return ids.index(r), stages[ids.index(r)]
    m = re.fullmatch(r"[sS]?(\d{1,2})", r)
    if m and 1 <= int(m.group(1)) <= len(stages):
        return int(m.group(1)) - 1, stages[int(m.group(1)) - 1]
    hits = [i for i, s in enumerate(stages) if str(s.get("label", "")).strip().lower() == r.lower()]
    if len(hits) == 1:
        return hits[0], stages[hits[0]]
    raise ToolError(f'Version {version} has no section "{_clip(r, 60)}". Its sections: {", ".join(str(i) for i in ids)}.')


def _outline_stage(st: dict) -> dict:
    """A stage of a saved version as the working page holds it (no icon: assembly gives one)."""
    return copy.deepcopy({k: v for k, v in st.items() if k != "icon"})


def _section_view(page: Page, si: int) -> str:
    return whole(json.dumps(page.viewed_stage(page.stages[si], si), ensure_ascii=False), MAX_SECTION_CHARS)


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
    what = cut(_clip(inp.get("instructions"), 4 * BUILDER_CHARS), BUILDER_CHARS)
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


def versions_search(page_: dict, query: str) -> list[dict]:
    return versions.search(page_, query)


def _read_stage(page: Page, inp: dict) -> dict:
    """The section a read tool names: on the page being edited, or with `version` in that version."""
    v = _version(page, inp)
    if v is not None:
        return _old_stage(page, v, inp.get("section"))[1]
    return page.stage(inp.get("section"))[1]


def _widget_in(st: dict, title: Any = None) -> int:
    found = [bi for bi, b in enumerate(st.get("blocks") or []) if b.get("type") == "widget"]
    if not found:
        raise ToolError(f'Section "{st.get("label", "")}" has no playground there.')
    t = str(title or "").strip().lower()
    named = [bi for bi in found if t and str(st["blocks"][bi].get("title") or "").strip().lower() == t]
    return (named or found)[0]


def apply(page: Page, name: str, inp: Any, ctx: Ctx) -> str | list[dict]:
    """Apply one tool call; returns the result for the editor: text, or content blocks with images (look_at_playground).
    Raises ToolError when it can't be applied."""
    inp = inp if isinstance(inp, dict) else {}
    if page.lite and name in PLAYGROUND_TOOLS:
        raise ToolError("This Reader page has no playgrounds.")
    if ctx.no_writes and name in WRITES | {"start_from"}:
        raise ToolError(ctx.no_writes)

    if name == "get_section":
        v = _version(page, inp)
        if v is not None:
            oi, ost = _old_stage(page, v, inp.get("section"))
            return f"(version {v}) " + whole(json.dumps(page.viewed_stage(ost, oi), ensure_ascii=False), MAX_SECTION_CHARS)
        si, _ = page.stage(inp.get("section"))
        return _section_view(page, si)
    if name == "search_source":
        return _search(page, str(inp.get("query") or ""))
    if name == "search_page":
        v = _version(page, inp)
        hits = versions_search(page.current if v is None else _old_page(page, v), str(inp.get("query") or ""))
        where = "this page" if v is None else f"version {v}"
        return json.dumps(hits, ensure_ascii=False) if hits else f"No section of {where} mentions that."
    if name == "get_change":
        v = _version(page, {"version": inp.get("version")})
        v = page.store.base if v is None else v
        if v is None:
            raise ToolError("Other versions can't be read here.")
        ch = page.store.change(v)
        if ch.get("missing"):
            raise ToolError(f"There is no version {v}.")
        return whole(json.dumps(ch, ensure_ascii=False), MAX_CHANGE_CHARS)
    if name == "get_overview":
        v = _version(page, {"version": inp.get("version")})
        if v is None:
            return whole(json.dumps(page.overview(), ensure_ascii=False), MAX_SECTION_CHARS)
        _old_page(page, v)
        main = mf.main_file_of(page.store.db, ctx.client, page.store.user_id, page.store.line, v, ctx.usage)
        if main is None:
            raise ToolError(page.store.why_missing(v))
        return main["text"]

    if name == "restore_section":
        v = _version(page, {"version": inp.get("version")})
        if v is None:
            raise ToolError("Name the older version to bring the section back from.")
        _, ost = _old_stage(page, v, inp.get("section"))
        st = _outline_stage(ost)
        if page.lite:
            st["blocks"] = [b for b in st["blocks"] if b.get("type") != "widget"] or st["blocks"]
        ids = [s["id"] for s in page.stages]
        old_widgets = [b for b in st["blocks"] if b.get("type") == "widget"]
        if st["id"] in ids:  # the same section: put its old content back in place
            si = ids.index(st["id"])
            now = page.stages[si]
            if json.dumps(now, sort_keys=True) == json.dumps(st, sort_keys=True):
                return "It is already as it was in that version."
            others = len(pe.widgets_in(page.current)) - len(_widgets(now))
            if old_widgets and others + len(old_widgets) > MAX_MARKED:
                raise ToolError(pe.CAP_NOTE)
            widget = json.dumps(_widgets(now), sort_keys=True) != json.dumps(old_widgets, sort_keys=True)
            page.set_stage(si, st)
            page.record(st["id"], name, f'Brought back "{st.get("label", "")}" as it was in version {v}')
            if widget:
                page.widgets_changed.add(st["id"])
            return f"Restored in place from version {v}."
        if old_widgets and len(pe.widgets_in(page.current)) + len(old_widgets) > MAX_MARKED:
            raise ToolError(pe.CAP_NOTE)
        at = _after_index(page, inp.get("after") or "end")
        stages = list(page.stages)
        stages.insert(at, st)
        page.current = {**page.current, "stages": stages}
        page.added.append(st["id"])
        page.record(st["id"], name, f'Brought back the section "{st.get("label", "")}" from version {v}')
        if old_widgets:
            page.widgets_changed.add(st["id"])
        return f'Added back as "{st["id"]}" (its id in version {v}).'

    if name == "restore_playground":
        v = _version(page, {"version": inp.get("version")})
        if v is None:
            raise ToolError("Name the older version to bring the playground back from.")
        _, ost = _old_stage(page, v, inp.get("section"))
        old = [b for b in ost.get("blocks") or [] if b.get("type") == "widget" and pe.widget_kind(b) == "playground"]
        if not old:
            raise ToolError(f'That section has no playground in version {v}.')
        si, st = page.stage(inp.get("to_section") or ost.get("id"))
        mine = _widgets(st)
        if mine:
            bi = page.widget_at(si)
            if json.dumps(st["blocks"][bi], sort_keys=True) == json.dumps(old[0], sort_keys=True):
                return "It is already that playground."
            _new_widget(page, si, bi, copy.deepcopy(old[0]))
        else:
            if len(pe.widgets_in(page.current)) >= MAX_MARKED:
                raise ToolError(pe.CAP_NOTE)
            _put_blocks(page, si, [*st["blocks"], copy.deepcopy(old[0])])
        page.record(st["id"], name, f'Brought back the playground of version {v} in "{st.get("label", "")}"', widget=True)
        return f"Restored from version {v}." + _after_playground(page, si, ctx)

    if name == "start_from":
        if page.writes:
            raise ToolError("You already changed this page: start_from works only before any change.")
        v = _version(page, {"version": inp.get("version")})
        if v is None:
            return "That is already the page you are working on."
        spec = _old_page(page, v)
        follow = base_mod.follow_of(spec)
        outline = json.loads(follow["outline"])
        if follow.get("widgets"):
            outline = restore_kept_widgets(outline, follow["widgets"])
        page.rebase(outline, v, follow)
        return (f"You now work on version {v}. Its sections: " + ", ".join(f'S{i + 1} {s["id"]} "{s.get("label", "")}"' for i, s in enumerate(page.stages))
                + ". Finish now to go back to it as it was, or make the change on it.")
    if name == "ask_user":
        if page.writes:
            raise ToolError("You already changed the page, so don't ask with a tool now: finish, and put the question as the last sentence of your reply.")
        options = [cut(_clip(o, 1000), 200) for o in (inp.get("options") or []) if _clip(o, 200)][:MAX_OPTIONS]
        q = cut(_clip(inp.get("question"), 2000), 600)
        if not q or len(options) < 2:
            raise ToolError("A question needs its text and 2 to 4 concrete options.")
        page.asked = {"text": q, "options": options}
        page.finished = True
        return "Asked. Stop here."
    if name in END_TOOLS:
        raise ToolError(f'"{name}" is not applied here.')

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
        st = _read_stage(page, inp)
        bi = _widget_in(st, inp.get("title"))
        spec = pe.read_playground_spec(st["blocks"][bi].get("html", ""))
        if spec is None:
            raise ToolError("This playground can't be read (an older kind): use replace_playground to rebuild it.")
        return whole(json.dumps(spec, ensure_ascii=False), MAX_SPEC_CHARS)
    if name == "try_playground":
        st = _read_stage(page, inp)
        bi = _widget_in(st, inp.get("title"))
        spec = pe.read_playground_spec(st["blocks"][bi].get("html", ""))
        if spec is None:
            raise ToolError("This playground can't be run (an older kind): read it with get_playground.")
        runs = [r for r in inp["runs"] if isinstance(r, dict)][:MAX_RUNS] if isinstance(inp.get("runs"), list) else []
        r = read_playground(spec, runs or None)
        if r is None:
            raise ToolError("The playground couldn't be run just now: read its spec with get_playground instead.")
        return r["text"] or "It shows nothing at those settings."
    if name == "look_at_playground":
        if not look_on():
            raise ToolError(f'There is no tool "{name}".')
        return look_mod.tool(page, inp, ctx)
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
    """A tool_result's content (text, or content blocks) and is_error, never raising (an unexpected failure is reported to
    the editor)."""
    try:
        return {"content": apply(page, name, inp, ctx), "is_error": False}
    except ToolError as e:
        return {"content": str(e), "is_error": True}
    except Exception:
        log.exception("Follow-up v5: tool %s failed", name)
        return {"content": "That didn't work because of an internal problem. Try another way, or explain it in your reply.", "is_error": True}


Run = Callable[[Page, str, Any, Ctx], dict]
