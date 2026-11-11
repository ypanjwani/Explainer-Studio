"""Follow-ups v3: the page map that understand.py reads and its knock-on rules follow.

One entry per section:
  code part   number, id, label, summary, a line per block (feedback_pick.block_line), whether it has a playground, and the
              other sections its text names ("section 3", another section's label, "the previous/next section")
  AI part     one Haiku Low call (INDEX_SYSTEM): the ideas it introduces and uses, the sections it points to, what its text
              claims the playground shows, and how hard the playground is

The map is cached under a hash of the page's JSON (PageIndex table), so a page is mapped once, however many follow-ups it
gets, and old saved pages are mapped on their first follow-up. Without a client, or when the call fails, the code part is
used alone: references found by code still work, concepts and claims are empty.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import anthropic
from sqlalchemy import delete, update

from . import playground_edit as ce
from ... import data
from ...db import Database, PageIndex, now_ms
from .cards import block_line
from ...js import js_slice, json_parse, json_stringify
from ..explorer.judge import section_text
from ...llm import HAIKU, cached_system, text_of
from ...pricing import UsageLog

log = logging.getLogger("explainer.steps")

INDEX_VERSION = "1"  # bump when INDEX_SYSTEM or the map's shape changes: old entries are then never hit
MAX_AGE_DAYS = 30
SECTION_CHARS = 1500  # each section's text for the AI part, cut to this
LEVELS = ("beginner", "intermediate", "advanced")
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
PREV = re.compile(r"\b(previous|last|earlier|preceding) (section|stage|part)\b|\bas (we|you) saw (above|earlier|before)\b", re.I)
NEXT = re.compile(r"\b(next|following|later|coming) (section|stage|part)\b|\b(see|shown|covered) below\b", re.I)


def page_key(page: dict) -> str:
    return hashlib.sha256(json_stringify({"v": INDEX_VERSION, "stages": page.get("stages"), "level": page.get("level")}).encode("utf-8")).hexdigest()


def _clip(s: Any, n: int) -> str:
    return js_slice(re.sub(r"\s+", " ", str(s or "")).strip(), 0, n)


def _words(st: dict) -> str:
    return " ".join(x for x in (section_text(st), *(str(b.get("caption") or "") for b in st["blocks"])) if x)


def code_refs(page: dict) -> dict[str, list[str]]:
    """stage id -> ids of other stages its text names: "section 3" / "the third section", another stage's label (6+
    characters), "the previous section" / "as we saw above" (the stage before), "the next section" (the stage after)."""
    stages = page["stages"]
    ids = [s["id"] for s in stages]
    out: dict[str, list[str]] = {}
    for i, st in enumerate(stages):
        text = _words(st)
        low = text.lower()
        found: list[str] = []
        for m in re.finditer(r"\b(?:section|stage|part)\s+(\d{1,2})\b", low):
            n = int(m.group(1))
            if 1 <= n <= len(ids) and n - 1 != i:
                found.append(ids[n - 1])
        for m in re.finditer(r"\b(" + "|".join(ORDINALS) + r")\s+(?:section|stage|part)\b", low):
            n = ORDINALS[m.group(1)]
            if n <= len(ids) and n - 1 != i:
                found.append(ids[n - 1])
        for j, other in enumerate(stages):
            label = str(other.get("label") or "").strip().lower()
            if j != i and len(label) >= 6 and label in low:
                found.append(other["id"])
        if i > 0 and PREV.search(text):
            found.append(ids[i - 1])
        if i + 1 < len(ids) and NEXT.search(text):
            found.append(ids[i + 1])
        out[st["id"]] = list(dict.fromkeys(found))
    return out


def code_index(page: dict) -> dict:
    """The map's code part (no AI)."""
    refs = code_refs(page)
    sections = []
    for i, st in enumerate(page["stages"]):
        widgets = [b for b in st["blocks"] if isinstance(b, dict) and b.get("type") == "widget"]
        sections.append({
            "id": st["id"], "number": i + 1, "label": _clip(st.get("label") or st["id"], 120), "summary": _clip(st.get("blurb"), 200),
            "blocks": [block_line(b) for b in st["blocks"] if isinstance(b, dict)][:14],
            "playground": bool(widgets) and ce.widget_kind(widgets[0]) is not None,
            "introduces": [], "uses": [], "refersTo": refs.get(st["id"], []), "claims": [], "playgroundLevel": "",
        })
    level = page.get("level") if page.get("level") in LEVELS else ""
    return {"v": INDEX_VERSION, "level": level, "sections": sections}


def _ai_input(page: dict) -> str:
    parts = []
    for st in page["stages"]:
        widgets = [b for b in st["blocks"] if isinstance(b, dict) and b.get("type") == "widget"]
        lines = [f'Section id "{st["id"]}": "{_clip(st.get("label"), 120)}"' + (f" ({_clip(st.get('blurb'), 200)})" if st.get("blurb") else ""),
                 _clip(section_text(st), SECTION_CHARS)]
        if widgets:
            lines.append("Playground: " + block_line(widgets[0]))
        parts.append("\n".join(x for x in lines if x))
    return f'Page: "{_clip(page.get("title"), 200)}" (level: {page.get("level", "")})\n\n' + "\n\n".join(parts)


def merge_ai(index: dict, raw: Any) -> dict:
    """The code part with the AI part filled in. Unknown ids, self-references and junk are dropped; code refs are kept."""
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        return index
    ids = {s["id"] for s in index["sections"]}
    by = {x["id"]: x for x in raw["sections"] if isinstance(x, dict) and x.get("id") in ids}

    def phrases(v: Any, n: int, k: int = 80) -> list[str]:
        return [p for p in (_clip(x, k) for x in (v if isinstance(v, list) else [])) if p][:n]

    out = []
    for s in index["sections"]:
        a = by.get(s["id"])
        if a is None:
            out.append(s)
            continue
        refs = [r for r in (a.get("refersTo") if isinstance(a.get("refersTo"), list) else []) if r in ids and r != s["id"]]
        out.append({**s, "introduces": phrases(a.get("introduces"), 6), "uses": phrases(a.get("uses"), 6),
                    "refersTo": list(dict.fromkeys([*s["refersTo"], *refs])),
                    "claims": phrases(a.get("claims"), 3, 200) if s["playground"] else [],
                    "playgroundLevel": a.get("playgroundLevel") if s["playground"] and a.get("playgroundLevel") in LEVELS else ""})
    return {**index, "sections": out, "ai": True}


def build(client: anthropic.Anthropic | None, page: dict, usage: UsageLog) -> dict:
    """The full map: code part, plus the AI part when there is a client and the call works."""
    index = code_index(page)
    if client is None:
        return index
    try:
        msg = client.messages.create(
            model=HAIKU, max_tokens=3000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": data.formats()["pageIndex"]}},
            system=cached_system(data.prompt("INDEX_SYSTEM")),
            messages=[{"role": "user", "content": _ai_input(page)}],
        )
        usage.add_message("follow-up page map", msg)
        return merge_ai(index, json_parse(text_of(msg)))
    except Exception:
        log.exception("Page map: the AI part failed; using the code part alone")
        return index


class PageIndexStore:
    """The cache (PageIndex table). Errors are logged, never raised: a miss only costs one more cheap call."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> dict | None:
        try:
            with self.db.session() as s:
                row = s.get(PageIndex, key)
                if row is None:
                    return None
                s.execute(update(PageIndex).where(PageIndex.key == key).values(used_at=now_ms()))
                return row.value
        except Exception:
            log.exception("page map cache read failed")
            return None

    def put(self, key: str, value: dict) -> None:
        try:
            with self.db.session() as s:
                row = s.get(PageIndex, key)
                if row is None:
                    s.add(PageIndex(key=key, value=value))
                else:
                    row.value, row.used_at = value, now_ms()
        except Exception:
            log.exception("page map cache write failed")

    def prune(self) -> int:
        with self.db.session() as s:
            return s.execute(delete(PageIndex).where(PageIndex.used_at < now_ms() - MAX_AGE_DAYS * 86_400_000)).rowcount


def get_or_build(store: PageIndexStore | None, client: anthropic.Anthropic | None, page: dict, usage: UsageLog) -> dict:
    """The cached map of this exact page, or a new one (stored only when its AI part worked)."""
    key = page_key(page)
    hit = store.get(key) if store else None
    if isinstance(hit, dict) and hit.get("v") == INDEX_VERSION and isinstance(hit.get("sections"), list):
        return hit
    index = build(client, page, usage)
    if store and index.get("ai"):
        store.put(key, index)
    return index


def by_id(index: dict) -> dict[str, dict]:
    return {s["id"]: s for s in index.get("sections") or []}


def map_text(index: dict, quality: str) -> str:
    """The map for the planner (understand.py). Each section is named by its handle, S1, S2 ... (section 1, 2 ...), never by
    its id: one name to copy, the same as its number, so Haiku can't mix up a number, an id and a label
    (understand.resolve_ref turns the handle back into the id)."""
    lines = [f"Mode: {quality}. Page level: {index.get('level') or 'not set'}."]
    for s in index["sections"]:
        head = f'S{s["number"]} (section {s["number"]}): "{s["label"]}"' + (f" ({s['summary']})" if s["summary"] else "")
        lines.append(head)
        lines += [f"  - {b}" for b in s["blocks"]]
        if s["introduces"]:
            lines.append(f"  introduces: {', '.join(s['introduces'])}")
        if s["uses"]:
            lines.append(f"  uses: {', '.join(s['uses'])}")
        if s["claims"]:
            lines.append(f"  the text says its playground shows: {'; '.join(s['claims'])}")
        lines.append("  has a playground" + (f" ({s['playgroundLevel']})" if s["playgroundLevel"] else "") if s["playground"] else "  no playground")
    return "\n".join(lines)
