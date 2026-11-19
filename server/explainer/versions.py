"""The version store (follow-ups v5 phase 1, .claude/followup-v5-plan.md): every version of a page, saved by the server.

A **line** is a page and all its revisions: a new explainer (or a new-topic page in a conversation) starts one, and its
id is that first record's id. Versions are numbered per line (v1, v2 …) by the server, from page_lines.next_version,
which only grows: a number is never reused, even after a delete. Versions form a tree (parent = the version a change
was made from).

Every version is a full copy of the page: canonical JSON (sorted keys), gzip-compressed, checked on save to unpack to
exactly the page given. Deleting a record leaves a tombstone: the page is cleared, the number, parent, log and section
ids stay, so numbers aren't reused, the log still reads, and section ids stay reserved.

Code only, no AI. Every function takes an open Session; the caller holds the user row lock where numbers are given out
(history.start_generation's lock).
"""

from __future__ import annotations

import gzip
import json
from typing import Any, Iterator

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .db import Explainer, PageLine, PageVersion, now_ms
from .js import json_stringify

KINDS = ("new", "change", "revert")
SNIPPET = 60  # characters each side of a search hit


class VersionError(Exception):
    """A page that doesn't survive packing, or a version that can't be written."""


# ---------- packing ----------


def canonical(page: Any) -> str:
    return json.dumps(page, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def pack(page: dict) -> bytes:
    return gzip.compress(canonical(page).encode("utf-8"), mtime=0)


def unpack(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


def section_ids(page: dict) -> list[str]:
    return [str(st.get("id")) for st in page.get("stages") or [] if isinstance(st, dict) and st.get("id")]


# ---------- rows ----------


def row(s: Session, user_id: str, line: str, version: int) -> PageVersion | None:
    return s.get(PageVersion, (user_id, line, version))


def of_record(s: Session, user_id: str, record_id: str) -> PageVersion | None:
    """The version a record holds (the newest one, if a record was ever saved twice)."""
    return s.scalars(select(PageVersion).where(PageVersion.user_id == user_id, PageVersion.record_id == record_id)
                     .order_by(PageVersion.updated_at.desc())).first()


def _line_row(s: Session, user_id: str, line: str, thread: str | None) -> PageLine:
    pl = s.get(PageLine, (user_id, line))
    if pl is None:
        pl = PageLine(user_id=user_id, line=line, thread=thread, next_version=1, created_at=now_ms())
        s.add(pl)
        s.flush()
    return pl


def next_number(s: Session, user_id: str, line: str, thread: str | None = None) -> int:
    """The line's next version number (taken: the counter moves on)."""
    pl = _line_row(s, user_id, line, thread)
    n = pl.next_version
    pl.next_version = n + 1
    return n


def save_version(s: Session, user_id: str, *, line: str, thread: str | None, record_id: str, parent: int | None,
                 kind: str, page: dict, log: dict | None = None, number: int | None = None) -> int:
    """Save `page` as a version of `line` and return its number. number=None takes the next one; a given number
    replaces that row (Retry / ✏️ Edit of the same version)."""
    if kind not in KINDS:
        raise VersionError(f"unknown kind {kind!r}")
    if not isinstance(page, dict) or not isinstance(page.get("stages"), list):
        raise VersionError("not a page")
    blob = pack(page)
    if canonical(unpack(blob)) != canonical(page):  # the invariant: load(v) is exactly the page saved
        raise VersionError("the packed page doesn't match")
    now = now_ms()
    pl = _line_row(s, user_id, line, thread)
    if number is None:
        number = next_number(s, user_id, line, thread)
    elif number >= pl.next_version:
        pl.next_version = number + 1
    v = row(s, user_id, line, number)
    ids = section_ids(page)
    if v is None:
        v = PageVersion(user_id=user_id, line=line, version=number, created_at=now)
        s.add(v)
    else:  # a replaced page's ids stay reserved: this page's ids first, then the ones only the old page had
        ids += [i for i in v.section_ids or [] if i not in ids]
    v.record_id, v.parent, v.kind = record_id, parent, kind
    v.payload, v.main_file, v.size, v.deleted = blob, None, len(blob), False
    v.section_ids = ids
    v.log = dict(log or {})
    v.updated_at = now
    s.flush()
    return number


def load(s: Session, user_id: str, line: str, version: int) -> dict | None:
    """The page of one version, or None when there's no such version or it was deleted."""
    v = row(s, user_id, line, version)
    return unpack(v.payload) if v is not None and not v.deleted and v.payload else None


def tombstone(s: Session, user_id: str, record_id: str) -> int:
    """A record was deleted: clear its versions' pages, keep the rows. Returns how many."""
    rows = s.scalars(select(PageVersion).where(PageVersion.user_id == user_id, PageVersion.record_id == record_id)).all()
    for v in rows:
        v.payload, v.main_file, v.size, v.deleted, v.updated_at = None, None, 0, True, now_ms()
    return len(rows)


def size_used(s: Session, user_id: str) -> int:
    return int(s.execute(select(func.coalesce(func.sum(PageVersion.size), 0)).where(PageVersion.user_id == user_id)).scalar_one())


def remove_all(s: Session, user_id: str) -> None:
    """Every version of a user (the FK cascade does this on account delete; for tests and tools)."""
    s.execute(delete(PageVersion).where(PageVersion.user_id == user_id))
    s.execute(delete(PageLine).where(PageLine.user_id == user_id))


# ---------- reading ----------


def used_ids(s: Session, user_id: str, line: str) -> set[str]:
    """Every section id the line has ever used, deleted versions included (ids stay reserved)."""
    out: set[str] = set()
    for ids in s.scalars(select(PageVersion.section_ids).where(PageVersion.user_id == user_id, PageVersion.line == line)):
        out.update(str(i) for i in ids or [])
    return out


def version_log(s: Session, user_id: str, line: str) -> list[dict]:
    """One entry per version, oldest first: number, parent, kind, what was asked and what changed."""
    rows = s.scalars(select(PageVersion).where(PageVersion.user_id == user_id, PageVersion.line == line)
                     .order_by(PageVersion.version)).all()
    return [{"version": v.version, "parent": v.parent, "kind": v.kind, "deleted": v.deleted, **(v.log or {})} for v in rows]


def change_of(s: Session, user_id: str, line: str, version: int) -> dict:
    """What a version changed compared with its parent, by section id (full content before and after)."""
    v = row(s, user_id, line, version)
    if v is None:
        return {"version": version, "missing": True}
    out: dict[str, Any] = {"version": version, "parent": v.parent, "kind": v.kind}
    if v.deleted:
        return {**out, "deleted": True}
    if v.parent is None:
        return {**out, "no_parent": True}
    before = load(s, user_id, line, v.parent)
    if before is None:
        return {**out, "parent_deleted": True}
    return {**out, **diff(before, unpack(v.payload))}


def diff(before: dict, after: dict) -> dict:
    """Sections added, removed and replaced, the new order (when it changed) and top-level fields that differ."""
    old = {st["id"]: st for st in before.get("stages") or [] if isinstance(st, dict) and st.get("id")}
    new = {st["id"]: st for st in after.get("stages") or [] if isinstance(st, dict) and st.get("id")}
    added = [new[i] for i in new if i not in old]
    removed = [old[i] for i in old if i not in new]
    replaced = [{"id": i, "before": old[i], "after": new[i]} for i in new if i in old and canonical(old[i]) != canonical(new[i])]
    kept_old = [i for i in old if i in new]
    kept_new = [i for i in new if i in old]
    fields = {k: {"before": before.get(k), "after": after.get(k)}
              for k in sorted((set(before) | set(after)) - {"stages"}) if canonical(before.get(k)) != canonical(after.get(k))}
    out: dict[str, Any] = {"added": added, "removed": removed, "replaced": replaced, "fields": fields}
    if kept_old != kept_new:
        out["order"] = list(new)
    return out


def _strings(x: Any) -> Iterator[str]:
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def search_page(s: Session, user_id: str, line: str, version: int, query: str) -> list[dict]:
    """Sections of one version that mention `query` (plain, case-insensitive), with a short snippet each."""
    page = load(s, user_id, line, version)
    return search(page, query) if page is not None else []


def search(page: dict, query: str) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    out = []
    for st in page.get("stages") or []:
        if not isinstance(st, dict):
            continue
        for text in _strings(st):
            at = text.lower().find(q)
            if at >= 0:
                a, b = max(0, at - SNIPPET), min(len(text), at + len(q) + SNIPPET)
                snippet = ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")
                out.append({"id": st.get("id"), "label": st.get("label"), "snippet": snippet})
                break
    return out


# ---------- pages made before the version store ----------


def _line_of(rid: str, data: dict[str, dict]) -> str:
    """A record's line: follow revise follow-ups back to the first page."""
    seen = {rid}
    while True:
        f = data[rid].get("followUp")
        base = f.get("baseId") if isinstance(f, dict) and f.get("kind") == "revise" else None
        if not isinstance(base, str) or base not in data or base in seen:
            return rid
        seen.add(base)
        rid = base


def backfill_line(s: Session, user_id: str, record_id: str) -> PageVersion | None:
    """Register the line of a record saved before the version store (its browser numbers kept where free) and return
    the record's version row, or None when the record has no finished page."""
    base = s.get(Explainer, (user_id, record_id))
    if base is None:
        return None
    thread = base.thread or base.id
    rows = s.scalars(select(Explainer).where(Explainer.user_id == user_id, (Explainer.thread == thread) | (Explainer.id == thread))).all()
    by_id = {r.id: r for r in rows}
    by_id.setdefault(base.id, base)
    data = {rid: r.data or {} for rid, r in by_id.items()}
    line = _line_of(record_id, data)
    members = sorted((r for rid, r in by_id.items() if _line_of(rid, data) == line and r.status == "done"
                      and isinstance((r.data or {}).get("spec"), dict)), key=lambda r: (r.created_at, r.id))
    taken = {v for v in s.scalars(select(PageVersion.version).where(PageVersion.user_id == user_id, PageVersion.line == line))}
    numbers: dict[str, int] = {}
    for r in members:
        have = of_record(s, user_id, r.id)
        if have is not None:
            numbers[r.id] = have.version
            continue
        d = r.data or {}
        want = d.get("version") if isinstance(d.get("version"), int) and d["version"] >= 1 else 1
        n = want if want not in taken else max(taken | {0}) + 1
        taken.add(n)
        f = d.get("followUp")
        revise = isinstance(f, dict) and f.get("kind") == "revise"
        parent = numbers.get(f.get("baseId")) if revise else None
        kind = "revert" if d.get("reverted") else "change" if revise else "new"
        prompt = d.get("prompt") if isinstance(d.get("prompt"), dict) else {}
        log = {"request": str(prompt.get("text") or d.get("title") or "")[:300], "backfilled": True}
        try:
            save_version(s, user_id, line=line, thread=thread, record_id=r.id, parent=parent, kind=kind, page=d["spec"],
                         log=log, number=n)
        except (VersionError, ValueError, TypeError):
            continue
        numbers[r.id] = n
        if d.get("version") != n and not (n == 1 and d.get("version") is None):
            # The app shows this number, so it matches the store; a new updatedAt makes the app fetch the record again.
            now = now_ms()
            r.data = {**d, "version": n, "updatedAt": max(now, int(d.get("updatedAt") or 0) + 1)}
            r.updated_at, r.saved_at = int(r.data["updatedAt"]), now
            r.size = len(json_stringify(r.data).encode("utf-8"))
    return of_record(s, user_id, record_id)
