"""Follow-ups v5 (copied from v4's page.py, then changed): the page the editor works on, and the log of what each tool
call changed.

Every write goes through Page, so code always knows exactly which sections changed (no rewrite-then-restore): the log
becomes spec.revision.steps, the friendly reply is written from it, and Jev checks only the sections in it.

v5: the working page is the base version (any version, not only the latest; the agent's start_from switches it before any
write), and `store` reads the line's other versions (versions.py) so the agent can copy from them. New section ids avoid
every id the line has ever used.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .... import versions
from ....db import Database
from ..playground_edit import widget_kind
from ..revise import _id_of

LITE = "lite-plus"


class ToolError(ValueError):
    """A tool call that can't be applied; its message is written for the editor, so it can correct the call."""


def _clip(s: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def cut(s: Any, n: int) -> str:
    """s cut to n characters at the end of a sentence (else at a word, with …), never mid-word. For text a reader or the
    agent reads (Nov 17 2026: hard cuts ended replies and questions mid-word)."""
    s = str(s or "").strip()
    if len(s) <= n:
        return s
    head = s[:n]
    end = max(head.rfind(p) for p in (". ", "? ", "! ", ".\n", "?\n", "!\n"))
    if end >= n // 2:
        return head[:end + 1]
    space = max(head.rfind(" "), head.rfind("\n"))
    return (head[:space] if space > 0 else head).rstrip(" ,;:\n") + "…"


def whole(text: str, n: int) -> str:
    """text as the agent reads it: whole up to n characters; past that cut with a plain note, so the agent never takes
    half a JSON for all of it (Nov 17 2026: it was cut silently at 14k)."""
    if len(text) <= n:
        return text
    return text[:n] + f"\n…(cut here: {len(text) - n} more characters not shown)"


class Store:
    """Read access to the other versions of the page's line (version store, code only). Pages are loaded once."""

    def __init__(self, db: Database | None, user_id: str | None, line: str | None, base: int | None = None):
        self.db, self.user_id, self.line, self.base = db, user_id, line, base
        self._pages: dict[int, dict | None] = {}

    @property
    def on(self) -> bool:
        return bool(self.db and self.user_id and self.line)

    def page(self, version: int) -> dict | None:
        """A version's page; None when there's no such version or it was deleted."""
        if not self.on:
            return None
        if version not in self._pages:
            with self.db.session() as s:
                self._pages[version] = versions.load(s, self.user_id, self.line, version)
        return self._pages[version]

    def why_missing(self, version: int) -> str:
        if not self.on:
            return "Other versions can't be read here."
        with self.db.session() as s:
            v = versions.row(s, self.user_id, self.line, version)
        return f"Version {version} was deleted." if v is not None and v.deleted else f"There is no version {version}."

    def change(self, version: int) -> dict:
        if not self.on:
            return {}
        with self.db.session() as s:
            return versions.change_of(s, self.user_id, self.line, version)

    def search(self, version: int, query: str) -> list[dict]:
        page = self.page(version)
        return versions.search(page, query) if page is not None else []

    def offered(self) -> list[str]:
        """Every suggestion the line's versions offered (the version log), so the agent never offers one twice."""
        if not self.on:
            return []
        with self.db.session() as s:
            return [str(x) for e in versions.version_log(s, self.user_id, self.line) for x in e.get("suggestions") or []]

    def used_ids(self) -> set[str]:
        if not self.on:
            return set()
        with self.db.session() as s:
            return versions.used_ids(s, self.user_id, self.line)


def look_theme_of(follow: dict | None) -> dict:
    """The page's own colours for look.py's screenshots: {"mode": "light"|"dark", "palette"?}. Dark only when the page is
    set to dark (auto or unset: light, the page's default)."""
    follow = follow or {}
    design = follow.get("design") if isinstance(follow.get("design"), dict) else {}
    mode = design.get("mode") if design.get("mode") in ("light", "dark") else follow.get("colorMode")
    out: dict = {"mode": "dark" if mode == "dark" else "light"}
    if isinstance(design.get("theme"), str) and design["theme"]:
        out["palette"] = design["theme"]
    return out


class Page:
    def __init__(self, before: dict, quality: str, source: str = "", store: Store | None = None):
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
        # The agent's end (agent.py): finish's reply and suggestions, or new_topic's reply.
        self.reply = ""
        self.suggestions: list[str] = []
        self.offers_fixes = False  # finish's offers_fixes: the answer lists fixes the reader can say yes to (a button)
        self.cut_off = False  # agent.py: the run ended on replies cut off at EDIT_TOKENS (what was done is kept)
        self.new_topic = ""
        self.new_topic_brief = ""  # new_topic's brief: the reader's button builds from it (modes/topic.py)
        # start_from: the version the page now starts from and that version as a follow-up (base.follow_of), for run.py.
        self.started_from: int | None = None
        self.follow: dict | None = None
        self.read_cache: dict[str, Any] = {}  # a playground's html -> its default readings (tools.playground_readings)
        # look.py (FOLLOWUP_V5_LOOK): the page's own colours for screenshots, screenshots by html + colours, the
        # look_at_playground calls made, and the gate's look judgements by html + colours.
        self.look_theme: dict = {"mode": "light"}
        self.look_cache: dict[str, Any] = {}
        self.looks = 0
        self.look_judged: dict[str, list[str]] = {}
        self.store = store or Store(None, None, None)
        self._used: set[str] | None = None  # every id the line has used (store.used_ids), read once

    def rebase(self, outline: dict, version: int, follow: dict) -> None:
        """start_from: work on another version instead (only before any write). Ids used by any version stay reserved."""
        self.before = outline
        self.current = copy.deepcopy(outline)
        self.text_changed, self.widgets_changed, self.added = set(), set(), []
        self.removed = self.moved = 0
        self.page_fields = False
        self.facts = {}
        self.store.base = version
        self.started_from, self.follow = version, follow
        self.look_theme = look_theme_of(follow)

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
        """A new section id: never one the working page, the base or any version of the line (any branch) has used."""
        if self._used is None:
            self._used = self.store.used_ids()
        return _id_of({"label": label}, {s["id"] for s in self.stages} | {s["id"] for s in self.before["stages"]} | self._used, "section")

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
