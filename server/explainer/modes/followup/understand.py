"""Follow-ups v3, step 1: what the reader's follow-up asks (FOLLOWUP_PLAN=1). Design: .claude/followup-v3-plan.md.

POST /api/followup/plan (handle):
  shortcuts   "undo", "remove section 3": no AI call
  page map    page_index.get_or_build (cached by page hash); Haiku sees each section as a handle (S1, S2 ...), not its id
  understand  one Haiku Low call (UNDERSTAND_SYSTEM): change | ask | cannot | answer | new_page | revert. A change names
              the sections it is about, each with the reader's intent ("check it's correct, fix anything off"), and typed
              structural steps (add / remove / move / split / merge a section, level, tone, length, page title). It never
              decides text versus playground: the section editor (section_editor.py) does, after seeing the section.
  resolve     each section Haiku names becomes the page's id by code (resolve_ref): a handle, a number in any form
              ("3", "section 3", "third"), a label. Nothing is dropped: a name code can't place with certainty becomes a
              question with the possible sections as options (no AI call), or an error naming it.
  flag        each part the reader asked for is checked against their words (quote_ok: their message, the chosen option,
              the added detail, their earlier messages). A part that doesn't match, or whose quote names another
              section, is kept but marked "unsure": the plan card then asks the reader to confirm it (they can untick it).
  clean       ids, ops and fields checked against the page, the mode's rules
  knock-ons   intents the reader didn't ask for, from the page map: mentions of a removed / moved / merged section, ideas
              only a removed section explained (derived, with `because`; one level)
  budget      an estimate of the editing cost; over FOLLOWUP_MAX_USD the reader is asked with cheaper options
  confirm     removing or merging sections, a level change, more than 4 sections, or a part marked unsure: the plan card
The app sends the result back as FollowUp.plan; steps.run_plan runs it.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import anthropic

from ... import data
from . import page_index as pidx
from ...js import js_slice, json_parse
from ...llm import HAIKU, cached_system, text_of
from ...pricing import UsageLog

log = logging.getLogger("explainer.steps")

# Keep equal to PLAN_OPS in shared/spec.ts.
OPS = ("add_section", "remove_section", "move_section", "split_section", "merge_sections", "set_level", "set_tone", "set_length",
       "edit_page_meta", "revert")
ON_SECTION = ("remove_section", "move_section", "split_section", "merge_sections")
PAGE_WIDE = ("set_level", "set_tone", "set_length")
LEVELS = ("beginner", "intermediate", "advanced")
MODES = ("playground", "lite-plus")
LITE = "lite-plus"
MAX_STEPS = 20
MAX_INTENTS = 20
MAX_DERIVED = 6
MAX_OPTIONS = 4
MAX_HISTORY = 5
WHAT_CHARS = 400
QUOTE_CHARS = 200
ANSWER_CHARS = 3000
# The budget check: a rough cost per section editor call (Haiku with Thinking) and per playground rebuild.
EDIT_USD = 0.01
PLAYGROUND_USD = 0.012

NOT_PLACED = "I couldn't tell what to change. Name the section or the playground and try again."
NOT_QUOTED = "I wasn't sure you asked for this"

# ---------- words (the quote check and section references) ----------

QUOTE_PASS = 0.6  # a quote matches when this share of its words is in what the reader said
PREFIX = 5  # words are compared by their first letters: simplify = simpler = simple
STOP = frozenset("a an the and or but of to in on at for with by from into is are was be been it its this that these those i me my you "
                 "your we our us please can could would should will just also too so then than as do does did make made get let".split())
CARDINALS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())}
ORDINALS = {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split())}
SECTION_WORDS = frozenset({"section", "sections", "stage", "stages", "part", "parts"})
PLAYGROUND_WORDS = frozenset({"playground", "playgrounds", "demo", "demos", "widget", "widgets", "interactive", "slider", "sliders",
                              "simulation", "simulator"})
START_WORDS = frozenset({"start", "the start", "beginning", "the beginning", "top", "the top"})
END_WORDS = frozenset({"end", "the end", "bottom", "the bottom", "last", "the last"})
HANDLE = re.compile(r"[sS](\d{1,2})")


def enabled() -> bool:
    """On by default (Nov 11 2026); FOLLOWUP_PLAN=0 turns it off, and the app then uses the old section pick (legacy)."""
    return os.environ.get("FOLLOWUP_PLAN") != "0"


def max_usd() -> float:
    try:
        return float(os.environ.get("FOLLOWUP_MAX_USD") or 0.15)
    except ValueError:
        return 0.15


def _clip(s: Any, n: int) -> str:
    return js_slice(re.sub(r"\s+", " ", str(s or "")).strip(), 0, n)


def _norm(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _words(s: Any) -> list[str]:
    """Lowercased words in any script (a quote in Hindi or Spanish is compared like one in English)."""
    return re.findall(r"\w+", str(s or "").lower())


def _ordinal(w: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", w)
    return int(m.group(1)) if m else ORDINALS.get(w)


def _num(w: str) -> int | None:
    """3 for "3", "three", "third", "3rd"; None otherwise."""
    if re.fullmatch(r"\d{1,2}", w):
        return int(w)
    return CARDINALS.get(w) or _ordinal(w)


def _key(w: str) -> str:
    """One word as the quote check compares it."""
    n = _num(w)
    if n is not None:
        return str(n)
    if HANDLE.fullmatch(w):
        return str(int(w[1:]))
    if w in SECTION_WORDS:
        return "section"
    return w[:PREFIX]


def _keys(s: Any) -> list[str]:
    return [_key(w) for w in _words(s) if w not in STOP]


def quote_ok(quote: str, said: str) -> bool:
    """The quote comes from what the reader said: found as is, or most of its words are (Haiku sometimes trims or rewords:
    "make section 4 simpler" for "simplify section 4")."""
    words = _words(quote)
    if not words:
        return False
    if f' {" ".join(words)} ' in f' {" ".join(_words(said))} ':
        return True
    q = _keys(quote)
    if not q:
        return False
    have = set(_keys(said))
    return sum(1 for k in q if k in have) / len(q) >= QUOTE_PASS


def numbers_named(text: str, n: int) -> set[int]:
    """The section numbers a text names: "section 4", "section four", "the fourth section", "4th part", "S4"."""
    words = _words(text)
    out: set[int] = set()
    for i, w in enumerate(words):
        k = None
        if HANDLE.fullmatch(w):
            k = int(w[1:])
        elif w in SECTION_WORDS and i + 1 < len(words):
            k = _num(words[i + 1])
        elif i + 1 < len(words) and words[i + 1] in SECTION_WORDS:
            k = _ordinal(w)
        if k and 1 <= k <= n:
            out.add(k)
    return out


def _label(page: dict, sid: str) -> str:
    st = next((s for s in page["stages"] if s["id"] == sid), None)
    return str((st or {}).get("label") or sid)


def _number(page: dict, sid: str) -> int | None:
    return next((i + 1 for i, s in enumerate(page["stages"]) if s["id"] == sid), None)


def _has_playground(st: dict) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "widget" for b in st["blocks"])


# ---------- section references ----------


def _ref_number(words: list[str], n: int) -> int | None:
    """3 for "3", "S3", "section 3", "section three", "3rd", "third", "the third section" (in range); None otherwise."""
    w = [x for x in words if x != "the"]
    k = None
    if len(w) == 1:
        k = int(w[0][1:]) if HANDLE.fullmatch(w[0]) else _num(w[0])
    elif len(w) == 2:
        if w[0] in SECTION_WORDS:
            k = _num(w[1])
        elif w[1] in SECTION_WORDS:
            k = _ordinal(w[0])
    return k if k and 1 <= k <= n else None


def resolve_ref(raw: Any, page: dict, field: str = "section", from_model: bool = True) -> tuple[str | None, list[str], str]:
    """(the page's id, [], how) for a section reference, or (None, candidate ids, how) when code can't tell for sure.

    from_model: Haiku wrote it, reading the map's handles (S3 = section 3), which come before ids (an id is a lowercase slug
    and could itself be "s3"). Otherwise the app sent a plan back: its ids are the page's own, so they come first.
    Only how a section was written is fixed here, never what the reader meant: "the last one" is two candidates when the
    last section and the last playground differ."""
    stages = page["stages"]
    ids = [s["id"] for s in stages]
    r = str(raw or "").strip()
    if not r:
        return None, [], "empty"
    handle = HANDLE.fullmatch(r)
    if handle and (from_model or r not in ids):
        k = int(handle.group(1))
        return (ids[k - 1], [], "handle") if 1 <= k <= len(ids) else (None, [], "unknown")
    if r in ids:
        return r, [], "id"
    words = _words(r)
    low = " ".join(words)
    if field == "after":
        if low in START_WORDS:
            return "start", [], "start"
        if low in END_WORDS:
            return ids[-1], [], "end"
    k = _ref_number(words, len(ids))
    if k:
        return ids[k - 1], [], "number"
    if "last" in words or "final" in words:
        pg = [s["id"] for s in stages if _has_playground(s)]
        cands = [pg[-1]] if pg and set(words) & PLAYGROUND_WORDS else list(dict.fromkeys([ids[-1], *pg[-1:]]))
        return (cands[0], [], "last") if len(cands) == 1 else (None, cands, "last")
    labels = {s["id"]: " ".join(_words(s.get("label"))) for s in stages}
    exact = [i for i, lab in labels.items() if lab and lab == low]
    if exact:
        return (exact[0], [], "label") if len(exact) == 1 else (None, exact[:MAX_OPTIONS], "label")
    if len(low) >= 4:
        part = [i for i, lab in labels.items() if lab and (low in lab or (len(lab) >= 4 and lab in low))]
        if len(part) == 1:
            return part[0], [], "label"
        if part:
            return None, part[:MAX_OPTIONS], "label"
    return None, [], "unknown"


def guess(item: dict, raw: Any, page: dict) -> list[str]:
    """Sections a reference code couldn't place may mean, for the question to the reader: the most words in common between
    the reference and the reader's quote, and a section's label and summary (at most MAX_OPTIONS). Nothing in common: the
    sections with a playground when the words are about one. Never the change itself (its words describe what to do, not
    where)."""
    text = f'{raw or ""} {item.get("quote") or ""}'
    want = {k for k in _keys(text) if k != "section" and not k.isdigit()}
    scored = []
    for st in page["stages"]:
        n = len(want & set(_keys(f'{st.get("label") or ""} {st.get("blurb") or ""}')))
        if n:
            scored.append((n, st["id"]))
    scored.sort(key=lambda x: -x[0])
    out = [sid for _, sid in scored[:MAX_OPTIONS]]
    if not out and set(_words(text)) & PLAYGROUND_WORDS:
        pg = [st["id"] for st in page["stages"] if _has_playground(st)]
        if len(pg) <= MAX_OPTIONS:
            out = pg
    return out


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def resolve_plan(raw: Any, page: dict, from_model: bool = True) -> tuple[dict, list[dict], list[str]]:
    """(the plan with every section reference written as the page's id, problems, fixes).

    A problem is an item with a reference code couldn't place: {"kind": "section" | "step", "item", "field", "raw",
    "candidates"}. It is held out of the plan, never dropped: plan() asks the reader about it. fixes: each reference that
    wasn't written as a handle or an id ("section 3" -> its id), logged and sent with the reply for the eval."""
    raw = raw if isinstance(raw, dict) else {}
    sections: list[dict] = []
    steps: list[dict] = []
    problems: list[dict] = []
    fixes: list[str] = []

    def place(x: dict, field: str, kind: str) -> tuple[str | None, dict | None]:
        sid, cands, how = resolve_ref(x.get(field), page, field, from_model)
        if sid is None:
            return None, {"kind": kind, "item": x, "field": field, "raw": _clip(x.get(field), 80), "candidates": cands or guess(x, x.get(field), page)}
        if how not in ("handle", "id"):
            fixes.append(f'{field} "{_clip(x.get(field), 80)}" -> {sid} ({how})')
        return sid, None

    for x in _list(raw.get("sections"))[:MAX_INTENTS]:
        if not isinstance(x, dict):
            continue
        sid, bad = place(x, "section", "section")
        if bad:
            problems.append(bad)
        else:
            sections.append({**x, "section": sid})
    for x in _list(raw.get("steps"))[:MAX_STEPS]:
        if not isinstance(x, dict):
            continue
        op = x.get("op")
        fields = [*(["section"] if op in ON_SECTION else []), *(["other"] if op == "merge_sections" else []),
                  *(["after"] if op in ("add_section", "move_section") and str(x.get("after") or "").strip() else [])]
        y, bad = dict(x), None
        for f in fields:
            sid, bad = place(x, f, "step")
            if bad:
                break
            y[f] = sid
        if bad:
            problems.append(bad)
        else:
            steps.append(y)
    if fixes:
        log.info("Follow-up: section names fixed by code: %s", "; ".join(fixes))
    if problems:
        log.info("Follow-up: %d part(s) name a section code can't place: %s", len(problems), "; ".join(p["raw"] or "(empty)" for p in problems))
    return {"sections": sections, "steps": steps}, problems, fixes


# ---------- flags: did the reader ask for this? ----------


def flag(plan: dict, page: dict, said: str) -> dict:
    """The plan (ids resolved) with each part the reader may not have asked for marked "unsure" (a note for the plan card):
    its quote isn't in what they said, or it names another section than the part changes. Kept, never dropped: the reader
    confirms. Derived parts and undo aren't checked."""
    n = len(page["stages"])
    pos = {s["id"]: i + 1 for i, s in enumerate(page["stages"])}

    def note(item: dict, refs: set[int]) -> str:
        if item.get("derived"):
            return ""
        q = str(item.get("quote") or "")
        named = numbers_named(q, n)
        if named and not named & refs:
            return f"you mentioned section {min(named)}: check this is the one you meant"
        return "" if quote_ok(q, said) else NOT_QUOTED

    sections = []
    for i in plan["sections"]:
        why = note(i, {pos[i["section"]]} if i.get("section") in pos else set())
        sections.append({**i, "unsure": why} if why else i)
    steps = []
    for s in plan["steps"]:
        refs = {pos[s[f]] for f in ("section", "other", "after") if s.get(f) in pos}
        if s.get("after") in pos and pos[s["after"]] < n:
            refs.add(pos[s["after"]] + 1)  # "put it before section 5" is after = section 4
        if s.get("after") == "start":
            refs.add(1)
        why = "" if s.get("op") == "revert" else note(s, refs)
        steps.append({**s, "unsure": why} if why else s)
    flagged = sum(1 for x in [*sections, *steps] if x.get("unsure"))
    if flagged:
        log.info("Follow-up: %d part(s) marked unsure for the reader to confirm", flagged)
    return {"sections": sections, "steps": steps}


# ---------- cleaning ----------


def _unsure(raw: dict, out: dict) -> dict:
    if isinstance(raw.get("unsure"), str) and raw["unsure"].strip():
        out["unsure"] = _clip(raw["unsure"], 200)
    return out


def clean_step(raw: Any, ids: list[str]) -> dict | None:
    """One structural step checked against the page. None when unusable."""
    if not isinstance(raw, dict) or raw.get("op") not in OPS:
        return None
    op = raw["op"]
    s = {"op": op, "section": "", "other": "", "after": "", "level": "", "what": _clip(raw.get("what"), WHAT_CHARS),
         "quote": _clip(raw.get("quote"), QUOTE_CHARS)}
    if op in ON_SECTION:
        if raw.get("section") not in ids:
            return None
        s["section"] = raw["section"]
    if op == "merge_sections":
        if raw.get("other") not in ids or raw.get("other") == s["section"]:
            return None
        s["other"] = raw["other"]
    if op in ("add_section", "move_section"):
        after = raw.get("after")
        s["after"] = after if after in ids or after == "start" else (ids[-1] if op == "add_section" else "")
        if op == "move_section" and (not s["after"] or s["after"] == s["section"]):
            return None
    if op == "set_level":
        if raw.get("level") not in LEVELS:
            return None
        s["level"] = raw["level"]
    if not s["what"]:
        defaults = {"remove_section": "Remove this section.", "move_section": "Move this section.", "revert": "Undo the last change.",
                    "set_level": f"Rewrite for {s['level']} readers."}
        if op not in defaults:
            return None
        s["what"] = defaults[op]
    return _unsure(raw, s)


def clean_intent(raw: Any, ids: list[str]) -> dict | None:
    if not isinstance(raw, dict) or raw.get("section") not in ids:
        return None
    intent = _clip(raw.get("intent"), WHAT_CHARS)
    if not intent:
        return None
    out = {"section": raw["section"], "intent": intent, "quote": _clip(raw.get("quote"), QUOTE_CHARS)}
    if raw.get("derived") is True:
        out["derived"] = True
        out["because"] = _clip(raw.get("because"), 200)
    return _unsure(raw, out)


def clean_plan(raw: Any, page: dict, from_model: bool = False) -> dict:
    """{"sections": [intents], "steps": [structural steps]} that fit the page. Section references are resolved first
    (resolve_plan); one code can't place is left out here (plan() asks the reader about those before a plan reaches this).
    Two intents for one section are joined (unsure if either was); a section that is removed or merged away keeps no
    intent. from_model: the references are Haiku's (handles first); otherwise the app's (ids first)."""
    raw, _, _ = resolve_plan(raw, page, from_model)
    ids = [s["id"] for s in page["stages"]]
    steps = [s for s in (clean_step(x, ids) for x in raw["steps"]) if s]
    if any(s["op"] == "revert" for s in steps):
        return {"sections": [], "steps": [next(s for s in steps if s["op"] == "revert")]}
    intents = [i for i in (clean_intent(x, ids) for x in raw["sections"]) if i]
    gone = {s["section"] for s in steps if s["op"] == "remove_section"} | {s["other"] for s in steps if s["op"] == "merge_sections"}
    joined: list[dict] = []
    for i in intents:
        if i["section"] in gone:
            continue
        same = next((x for x in joined if x["section"] == i["section"] and bool(x.get("derived")) == bool(i.get("derived"))), None)
        if same:
            if i["intent"] not in same["intent"]:
                same["intent"] = _clip(f'{same["intent"]} Also: {i["intent"]}', WHAT_CHARS * 2)
            if i.get("unsure") and not same.get("unsure"):
                same["unsure"] = i["unsure"]
        else:
            joined.append(i)
    uniq: list[dict] = []
    for s in steps:
        if not any((x["op"], x["section"], x["other"], x["after"], x["level"]) == (s["op"], s["section"], s["other"], s["after"], s["level"]) for x in uniq):
            uniq.append(s)
    return {"sections": joined, "steps": uniq}


def is_empty(plan: dict) -> bool:
    return not plan["sections"] and not plan["steps"]


# ---------- knock-on intents ----------


def _lower(xs: list[str]) -> set[str]:
    return {x.lower() for x in xs}


def new_order(ids: list[str], steps: list[dict]) -> list[str]:
    order = list(ids)
    for s in steps:
        if s["op"] == "move_section" and s["section"] in order:
            order.remove(s["section"])
            at = 0 if s["after"] == "start" else (order.index(s["after"]) + 1 if s["after"] in order else len(order))
            order.insert(at, s["section"])
    return order


def knock_ons(plan: dict, index: dict, page: dict) -> list[dict]:
    """Intents the reader didn't ask for, from the page map (derived, with `because`; at most MAX_DERIVED)."""
    steps = plan["steps"]
    by = pidx.by_id(index)
    ids = [s["id"] for s in page["stages"]]
    removed = {s["section"] for s in steps if s["op"] == "remove_section"} | {s["other"] for s in steps if s["op"] == "merge_sections"}
    merged_into = {s["other"]: s["section"] for s in steps if s["op"] == "merge_sections"}
    out: list[dict] = []

    def add(sid: str, intent: str, because: str) -> None:
        if sid in removed or sid not in by:
            return
        same = next((x for x in out if x["section"] == sid), None)
        if same:
            if intent not in same["intent"]:
                same["intent"] = _clip(f'{same["intent"]} {intent}', WHAT_CHARS * 2)
            return
        out.append({"section": sid, "intent": intent, "quote": "", "derived": True, "because": because})

    for x in [i for i in ids if i in removed]:
        lx = _label(page, x)
        for s in index["sections"]:
            if x in s["refersTo"]:
                if x in merged_into:
                    add(s["id"], f'The section "{lx}" is now part of "{_label(page, merged_into[x])}": update any mention of it. Change nothing else.',
                        f'it mentions "{lx}", which is merged away')
                else:
                    add(s["id"], f'The section "{lx}" is removed from the page: reword or drop any mention of it (such as "as we saw in ..."). Change nothing else.',
                        f'it mentions "{lx}", which is removed')
        if x in merged_into:
            continue
        lost = _lower(by.get(x, {}).get("introduces") or [])
        if not lost:
            continue
        still = set().union(*(_lower(s["introduces"]) for s in index["sections"] if s["id"] not in removed))
        for s in index["sections"]:
            need = [u for u in s["uses"] if u.lower() in lost and u.lower() not in still]
            if need:
                add(s["id"], f'Explain {", ".join(need)} in a sentence or two where it is first used here: the section "{lx}" that explained it is removed.',
                    f'it relies on {", ".join(need)}, explained only in "{lx}"')

    if any(s["op"] == "move_section" for s in steps):
        before = {sid: i for i, sid in enumerate(ids)}
        after = {sid: i for i, sid in enumerate(new_order(ids, steps))}
        for s in index["sections"]:
            for r in s["refersTo"]:
                if r in before and (before[s["id"]] < before[r]) != (after[s["id"]] < after[r]):
                    add(s["id"], f'The sections were reordered: "{_label(page, r)}" now comes {"after" if after[r] > after[s["id"]] else "before"} this one. '
                        'Fix words like "above", "below", "earlier" or "next" that now point the wrong way. Change nothing else.', "the sections were reordered")
        first: dict[str, str] = {}
        for sid in sorted(after, key=after.get):
            for c in _lower(by.get(sid, {}).get("introduces") or []):
                first.setdefault(c, sid)
        for s in index["sections"]:
            early = [u for u in s["uses"] if u.lower() in first and after[first[u.lower()]] > after[s["id"]]]
            if early:
                add(s["id"], f'Add a one-sentence explanation of {", ".join(early)}: after the reordering it is explained only later on the page.',
                    f'{", ".join(early)} is now explained later')
    asked = {i["section"] for i in plan["sections"]}
    return [x for x in out if x["section"] not in asked][:MAX_DERIVED]


# ---------- confirm, labels, cost ----------


def touched(plan: dict, page: dict) -> list[str]:
    """The sections a section editor will open (every section for a page-wide step)."""
    if any(s["op"] in PAGE_WIDE for s in plan["steps"]):
        gone = {s["section"] for s in plan["steps"] if s["op"] == "remove_section"}
        return [s["id"] for s in page["stages"] if s["id"] not in gone]
    return list(dict.fromkeys(i["section"] for i in plan["sections"]))


def needs_confirm(plan: dict, page: dict) -> bool:
    main = [i for i in plan["sections"] if not i.get("derived")]
    return (any(s["op"] in ("remove_section", "merge_sections", "set_level") for s in plan["steps"]) or len(main) > 4
            or sum(1 for i in plan["sections"] if i.get("derived")) > 2
            or any(x.get("unsure") for x in [*plan["sections"], *plan["steps"]]))


def estimate_usd(plan: dict, page: dict, keep_playgrounds: bool = False) -> float:
    """A rough cost of running the plan: one editor call per opened section (and per written new section), plus a
    playground rebuild for about half the opened sections that have one (none when the playgrounds are kept)."""
    ids = touched(plan, page)
    with_pg = sum(1 for st in page["stages"] if st["id"] in ids and any(b.get("type") == "widget" for b in st["blocks"]))
    writes = sum(1 for s in plan["steps"] if s["op"] in ("add_section", "split_section", "merge_sections", "edit_page_meta"))
    return round((len(ids) + writes) * EDIT_USD + (0 if keep_playgrounds else 0.5 * with_pg * PLAYGROUND_USD), 4)


def _with_note(label: str, item: dict) -> str:
    return f"{label} ({item['unsure']})" if item.get("unsure") else label


def label_step(s: dict, page: dict) -> str:
    n = lambda sid: f'Section {_number(page, sid) or "?"} "{_label(page, sid)}"'  # noqa: E731
    op, what = s["op"], s["what"]
    if op == "remove_section":
        out = f"Remove {n(s['section'])}"
    elif op == "move_section":
        out = f"Move {n(s['section'])} " + ("to the start" if s["after"] == "start" else f"after {n(s['after'])}")
    elif op == "merge_sections":
        out = f"Merge {n(s['other'])} into {n(s['section'])}: {what}"
    elif op == "split_section":
        out = f"Split {n(s['section'])}: {what}"
    elif op == "add_section":
        out = "New section " + ("at the start" if s["after"] == "start" else f"after {n(s['after'])}") + f": {what}"
    elif op == "set_level":
        out = f"Whole page: rewrite for {s['level']} readers"
    elif op == "edit_page_meta":
        out = f"Page title and summary: {what}"
    elif op == "revert":
        out = "Undo the last change"
    else:
        out = f"Whole page: {what}"
    return _with_note(out, s)


def label_intent(i: dict, page: dict) -> str:
    out = f'Section {_number(page, i["section"]) or "?"} "{_label(page, i["section"])}": {i["intent"]}'
    out += f" (because {i['because']})" if i.get("derived") and i.get("because") else ""
    return _with_note(out, i)


def summary(plan: dict, page: dict) -> list[str]:
    """One line per part, steps first, then sections, in the plan's order (the app ticks lines by this order)."""
    return [*(label_step(s, page) for s in plan["steps"]), *(label_intent(i, page) for i in plan["sections"])]


# ---------- the call ----------


def shortcuts(text: str, page: dict) -> dict | None:
    """Literal requests that need no AI call: undo, or "remove section N"."""
    t = " ".join(_norm(text))
    if re.fullmatch(r"(please )?(undo|revert)( (that|it|this|the last change|last change|the change|changes))?( please)?|go back( to the (previous|last|old) version)?", t):
        return {"kind": "revert"}
    m = re.fullmatch(r"(please )?(remove|delete|drop) (the )?(section|stage|part) (\d{1,2})( please)?", t)
    if m:
        k = int(m.group(5))
        if 1 <= k <= len(page["stages"]):
            return {"kind": "change", "plan": {"sections": [], "steps": [{"op": "remove_section", "section": page["stages"][k - 1]["id"], "other": "",
                                                                        "after": "", "level": "", "what": "Remove this section.", "quote": text}]}}
    return None


def history_text(history: list[str]) -> str:
    lines = [f"- {_clip(h, 300)}" for h in history[-MAX_HISTORY:] if isinstance(h, str) and h.strip()]
    return ("The reader's earlier messages in this conversation (oldest first), with what each did:\n" + "\n".join(lines)) if lines else ""


def ask_model(client: anthropic.Anthropic, index: dict, quality: str, text: str, usage: UsageLog, history: list[str],
              option: dict | None = None, extra: str = "") -> Any:
    chose = f'\n\nThe reader was asked what they meant and chose: "{option["label"]}". They added: {extra}' if option else ""
    past = history_text(history)
    msg = client.messages.create(
        model=HAIKU, max_tokens=3000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": data.formats()["followupUnderstand"]}},
        system=cached_system(data.prompt("UNDERSTAND_SYSTEM")),
        messages=[{"role": "user", "content": f"{pidx.map_text(index, quality)}\n\n{past + chr(10) + chr(10) if past else ''}The reader's follow-up: {text}{chose}"}],
    )
    usage.add_message("follow-up understand", msg)
    return json_parse(text_of(msg))


def _option(raw: Any, page: dict) -> dict | None:
    """An option the model offered (a question's or a refusal's alternative), its sections as the page's ids. None when it
    is unusable, or names a section code can't place: an option must do what its label says."""
    if not isinstance(raw, dict):
        return None
    label = _clip(raw.get("label"), 120)
    resolved, problems, _ = resolve_plan(raw, page)
    if problems:
        log.info('Follow-up: option "%s" left out: it names a section code can\'t place', label)
        return None
    plan = clean_plan(resolved, page)
    return {"label": label, **plan} if label and not is_empty(plan) and not any(s["op"] == "revert" for s in plan["steps"]) else None


def which(placed: dict, problems: list[dict], page: dict) -> dict:
    """The reader is asked about the first section reference code couldn't place (no AI call): one option per possible
    section, each the rest of the plan plus this part placed there. Other unplaced parts ride along in the options, still
    as written, and are asked about when an option comes back. No possible section: an option to do the rest without
    it (the app's "Add detail" box lets the reader name it), or, with nothing else to do, an error naming it."""
    p, rest = problems[0], problems[1:]
    others = {"sections": [*placed["sections"], *(x["item"] for x in rest if x["kind"] == "section")],
              "steps": [*placed["steps"], *(x["item"] for x in rest if x["kind"] == "step")]}
    what = _clip(p["item"].get("quote") or p["raw"] or p["item"].get("intent") or p["item"].get("what"), 120)
    if p["candidates"]:
        options = []
        for sid in p["candidates"][:MAX_OPTIONS]:
            item = {**p["item"], p["field"]: sid}
            kind = "sections" if p["kind"] == "section" else "steps"
            options.append({"label": f'Section {_number(page, sid)} "{_clip(_label(page, sid), 80)}"', **{**others, kind: [*others[kind], item]}})
        return {"kind": "ask", "question": {"text": f'Which section did you mean for "{what}"?', "options": options}}
    if not is_empty(others):
        return {"kind": "ask", "question": {"text": f'I couldn\'t tell which section you meant for "{what}". Name it in the box below, or do the rest without it.',
                                            "options": [{"label": "Skip that part, do the rest", **others}]}}
    return {"error": f'I couldn\'t tell which section you meant by "{what}". Name it, for example "section 3".'}


def cheaper(plan: dict, page: dict, limit: float) -> list[dict]:
    """Options for a plan over the budget: the playgrounds kept as they are, and the first sections that fit."""
    out = []
    keep = {"sections": [{**i, "intent": f'{i["intent"]} Keep its playground as it is; change only the text.'} for i in plan["sections"]],
            "steps": plan["steps"]}
    if any(s["op"] in PAGE_WIDE for s in plan["steps"]):
        keep["steps"] = [{**s, "what": f'{s["what"]} Keep every playground as it is.'} if s["op"] in PAGE_WIDE else s for s in plan["steps"]]
    if estimate_usd(keep, page, keep_playgrounds=True) <= limit:
        out.append({"label": "Change the text only, keep the playgrounds", **keep})
    if plan["sections"] and not any(s["op"] in PAGE_WIDE for s in plan["steps"]):
        fit: list[dict] = []
        for i in plan["sections"]:
            if estimate_usd({"sections": [*fit, i], "steps": plan["steps"]}, page) > limit:
                break
            fit.append(i)
        if fit and len(fit) < len(plan["sections"]):
            nums = ", ".join(str(_number(page, i["section"])) for i in fit)
            out.append({"label": f"Only section{'s' if len(fit) > 1 else ''} {nums} for now", "sections": fit, "steps": plan["steps"]})
    return out


def _finish(plan: dict, index: dict, page: dict, quality: str, budget_checked: bool = False, fixes: list[str] | None = None) -> dict:
    if any(s["op"] == "revert" for s in plan["steps"]):
        return {"kind": "revert"}
    if is_empty(plan):
        return {"error": NOT_PLACED}
    plan = {**plan, "sections": [*plan["sections"], *knock_ons(plan, index, page)]}
    limit = max_usd()
    if not budget_checked and estimate_usd(plan, page) > limit:
        options = cheaper(plan, page, limit)
        if options:
            return {"kind": "ask", "question": {"text": "That's a big change for one follow-up. Which part should I do first?", "options": options}}
    return {"kind": "change", **plan, "summary": summary(plan, page), "confirm": needs_confirm(plan, page), **({"fixes": fixes} if fixes else {})}


def plan(client: anthropic.Anthropic, store: pidx.PageIndexStore | None, page: dict, text: str, quality: str, usage: UsageLog,
         option: dict | None = None, extra: str = "", history: list[str] | None = None) -> dict:
    """The /api/followup/plan reply without its cost. option alone: the reader picked it; it runs as it is, with knock-ons,
    and no model call (a part in it that still names no section is asked about). option + extra: the model reads the
    choice and the added words."""
    history = history or []
    if option and not extra:
        index = pidx.get_or_build(store, client, page, usage)
        placed, problems, _ = resolve_plan(option, page, from_model=False)
        if problems:
            return which(placed, problems, page)
        return _finish(clean_plan(placed, page), index, page, quality, budget_checked=True)
    quick = None if option else shortcuts(text, page)
    if quick and quick["kind"] == "revert":
        return quick
    index = pidx.get_or_build(store, client, page, usage)
    if quick:
        return _finish(quick["plan"], index, page, quality)
    try:
        raw = ask_model(client, index, quality, text, usage, history, option, extra)
    except Exception:
        log.exception("Follow-up understand: the call failed")
        return {"error": "Something went wrong while reading your request. Please try again."}
    if not isinstance(raw, dict):
        return {"error": NOT_PLACED}
    kind = raw.get("kind")
    log.info("Follow-up understand: %s, %d section(s), %d step(s)", kind, len(raw.get("sections") or []), len(raw.get("steps") or []))
    if kind == "revert":
        return {"kind": "revert"}
    if kind == "answer" and _clip(raw.get("reason"), 10):
        return {"kind": "answer", "answer": js_slice(str(raw["reason"]).strip(), 0, ANSWER_CHARS)}
    if kind == "new_page":
        return {"kind": "cannot", "reason": _clip(raw.get("reason"), 400) or "This sounds like a different topic.", "alternative": None, "newTopic": True}
    if kind == "cannot":
        return {"kind": "cannot", "reason": _clip(raw.get("reason"), 400) or "This page can't do that.", "alternative": _option(raw.get("alternative"), page)}
    if kind == "ask":
        q = raw.get("question") if isinstance(raw.get("question"), dict) else {}
        options = [o for o in (_option(x, page) for x in (q.get("options") or [])[:MAX_OPTIONS]) if o]
        if len(options) >= 2 and _clip(q.get("text"), 10):
            return {"kind": "ask", "question": {"text": _clip(q["text"], 300), "options": options}}
        if len(options) == 1:  # one reading left: show it as a plan to confirm
            out = _finish({"sections": options[0]["sections"], "steps": options[0]["steps"]}, index, page, quality)
            return {**out, "confirm": True} if out.get("kind") == "change" else out
        return {"error": NOT_PLACED}
    said = " ".join(x for x in (text, extra, (option or {}).get("label", ""), *history) if x)
    placed, problems, fixes = resolve_plan(raw, page)
    placed = flag(placed, page, said)
    if problems:
        return which(placed, problems, page)
    return _finish(clean_plan(placed, page), index, page, quality, fixes=fixes)


def parse_plan_request(body: Any) -> dict | str:
    """POST /api/followup/plan: {text, quality, followUp, option?, extra?, history?}. Reuses the pick request's checks."""
    from .cards import parse_pick_request
    req = parse_pick_request(body)
    if isinstance(req, str):
        return req
    body = body if isinstance(body, dict) else {}
    opt = body.get("option")
    if isinstance(opt, dict):
        req["option"] = {"label": _clip(opt.get("label"), 120),
                         "sections": opt["sections"][:MAX_INTENTS] if isinstance(opt.get("sections"), list) else [],
                         "steps": opt["steps"][:MAX_STEPS] if isinstance(opt.get("steps"), list) else []}
    extra = body.get("extra")
    req["extra"] = _clip(extra, 1000) if isinstance(extra, str) else ""
    hist = body.get("history")
    req["history"] = [_clip(h, 300) for h in hist[-MAX_HISTORY:] if isinstance(h, str)] if isinstance(hist, list) else []
    return req


def handle(client: anthropic.Anthropic, store: pidx.PageIndexStore | None, req: dict) -> dict:
    """The /api/followup/plan reply plus its cost ("usd"); {"kind": "off"} when FOLLOWUP_PLAN isn't on (the app then uses
    /api/followup/pick)."""
    if not enabled():
        return {"kind": "off", "usd": 0.0}
    usage = UsageLog("followup-plan", "low")
    out = plan(client, store, req["page"], req["text"], req["quality"], usage, req.get("option"), req.get("extra") or "", req.get("history"))
    return {**out, "usd": usage.summary()["totalUsd"]}
