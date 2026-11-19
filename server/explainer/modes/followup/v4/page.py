"""Follow-ups v4: the page the editor works on, and the log of what each tool call changed.

Every write goes through Page, so code always knows exactly which sections changed (no rewrite-then-restore): the log
becomes spec.revision.steps, the friendly reply is written from it, and Jev checks only the sections in it.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from ..playground_edit import widget_kind
from ..revise import _id_of

LITE = "lite-plus"


class ToolError(ValueError):
    """A tool call that can't be applied; its message is written for the editor, so it can correct the call."""


def _clip(s: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


class Page:
    def __init__(self, before: dict, quality: str, source: str = ""):
        self.before = before
        self.current = copy.deepcopy(before)
        self.quality = quality
        self.lite = quality == LITE
        self.source = source or ""
        self.log: list[dict] = []  # {"section", "label", "tool", "what", "status": done|undone, "why"?}
        self.text_changed: set[str] = set()
        self.widgets_changed: set[str] = set()
        self.added: list[str] = []
        self.removed = 0
        self.moved = 0
        self.page_fields = False
        self.facts: dict[str, Any] = {}  # stage id -> a changed playground's checker facts (for the text-playground check)
        self.asked: dict | None = None
        self.note = ""
        self.finished = False
        self.read_cache: dict[str, Any] = {}  # a playground's html -> its default readings (tools.playground_readings)

    # ---------- reading ----------

    @property
    def stages(self) -> list[dict]:
        return self.current["stages"]

    def index_of(self, ref: Any) -> int:
        """A stage named by its id, its handle (S3) or its number ("3"); ToolError when there is none."""
        r = str(ref or "").strip()
        ids = [s["id"] for s in self.stages]
        if r in ids:
            return ids.index(r)
        m = re.fullmatch(r"[sS]?(\d{1,2})", r)
        if m and 1 <= int(m.group(1)) <= len(ids):
            return int(m.group(1)) - 1
        low = r.lower()
        hits = [i for i, s in enumerate(self.stages) if str(s.get("label", "")).strip().lower() == low]
        if len(hits) == 1:
            return hits[0]
        raise ToolError(f'There is no section "{_clip(r, 60)}". Use a section id from the page: {", ".join(ids)}.')

    def stage(self, ref: Any) -> tuple[int, dict]:
        i = self.index_of(ref)
        return i, self.stages[i]

    def label(self, sid: str) -> str:
        st = next((s for s in self.stages if s["id"] == sid), None) or next((s for s in self.before["stages"] if s["id"] == sid), None)
        return str((st or {}).get("label") or sid)

    @staticmethod
    def viewed_block(b: dict) -> dict:
        """A block as the editor reads it: a playground is its title (get_playground returns its spec)."""
        if b.get("type") == "widget":
            return {"type": "widget", "title": str(b.get("title") or ""), "playground": widget_kind(b) == "playground"}
        return b

    def viewed_stage(self, st: dict, i: int) -> dict:
        return {"handle": f"S{i + 1}", "id": st["id"], "label": st.get("label", ""), "blurb": st.get("blurb", ""),
                "blocks": [self.viewed_block(b) for b in st["blocks"]]}

    def overview(self) -> dict:
        o = self.current
        return {"title": o.get("title", ""), "subtitle": o.get("subtitle", ""), "keyIntuition": o.get("keyIntuition", ""),
                "level": o.get("level", ""), "mode": self.quality,
                "sections": [self.viewed_stage(st, i) for i, st in enumerate(self.stages)]}

    def widget_at(self, si: int, title: Any = None) -> int:
        """The block index of the section's playground (the one with this title when it has several)."""
        found = [bi for bi, b in enumerate(self.stages[si]["blocks"]) if b.get("type") == "widget"]
        if not found:
            raise ToolError(f'Section "{self.stages[si].get("label", "")}" has no playground. Use add_playground to give it one.')
        t = str(title or "").strip().lower()
        if t:
            named = [bi for bi in found if str(self.stages[si]["blocks"][bi].get("title") or "").strip().lower() == t]
            if named:
                return named[0]
        return found[0]

    # ---------- writing ----------

    def set_stage(self, si: int, st: dict) -> None:
        stages = list(self.stages)
        stages[si] = st
        self.current = {**self.current, "stages": stages}

    def record(self, sid: str, tool: str, what: str, widget: bool = False) -> None:
        if sid:
            (self.widgets_changed if widget else self.text_changed).add(sid)
        self.log.append({"section": sid, "label": self.label(sid) if sid else "", "tool": tool, "what": _clip(what, 300), "status": "done"})

    @property
    def writes(self) -> int:
        return len(self.log)

    def new_id(self, label: str) -> str:
        return _id_of({"label": label}, {s["id"] for s in self.stages} | {s["id"] for s in self.before["stages"]}, "section")

    def changed_ids(self) -> list[str]:
        """Sections whose text or playground changed (new ones included), in page order."""
        ids = self.text_changed | self.widgets_changed | set(self.added)
        return [s["id"] for s in self.stages if s["id"] in ids]

    def undo_section(self, sid: str, why: str) -> None:
        """Put one changed section back as it was before the follow-up; its log entries become "undone"."""
        old = next((s for s in self.before["stages"] if s["id"] == sid), None)
        i = next((k for k, s in enumerate(self.stages) if s["id"] == sid), None)
        if old is None or i is None:
            return
        self.set_stage(i, copy.deepcopy(old))
        self.text_changed.discard(sid)
        self.widgets_changed.discard(sid)
        self.facts.pop(sid, None)
        for e in self.log:
            if e["section"] == sid and e["status"] == "done":
                e.update(status="undone", why=_clip(why, 300))

    def revision(self) -> dict:
        """The revision run.py reads ({"changed", "removed", "widgets", "moved"?, "steps"}), from the log."""
        present = {s["id"] for s in self.stages}
        changed = [s for s in self.changed_ids() if s in self.text_changed or s in self.added]
        widgets = [s for s in self.changed_ids() if s in self.widgets_changed]
        steps = []
        for e in self.log:
            rep = {"label": e["what"] or e["tool"], "status": e["status"]}
            if e.get("why"):
                rep["why"] = e["why"]
            if e["section"] and e["section"] in present:
                rep["section"] = e["section"]
            steps.append(rep)
        out: dict = {"changed": changed, "removed": self.removed, "widgets": widgets, "steps": steps}
        if self.moved:
            out["moved"] = self.moved
        return out

    def did_anything(self) -> bool:
        return any(e["status"] == "done" for e in self.log)
