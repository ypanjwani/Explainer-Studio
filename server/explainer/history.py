"""The reader's history on the server, and the generations that fill it.

The browser owns an ExplainerRecord (it edits, chats, renames); the server owns a running generation.
They are kept in separate rows so neither overwrites the other:
- explainers: the record as the browser last saved it.
- generations: progress and result of the record's latest generation, written by the server as it runs.

A record whose status is "generating" is settled from its generation row: on finish (by the worker
itself, so it lands even if the tab was closed), or when it is read and the generation died with its
worker (STALE_MS without progress). While it runs, reads merge in its live steps.

Reads can write (audit F17, kept on purpose): settle() is the only place a generation that died with its
worker, or never started, is noticed, so GET /api/explainers, POST /batch and GET /<id> may commit that
one status change. Two reads racing on it store the same status and error (only the timestamp can differ),
so the race is harmless; the reads stay under the read rate limit.

Saving: a browser may not move a record into "generating" by itself (that is /api/generate's job), so
a stale tab can't undo a finished generation. Nor may it move a record out of "generating" while that
generation still runs (audit F13), so a stale tab can't hide the result that is on its way.

Versions (versions.py, follow-ups v5 phase 1): a generation that finishes with a page saves it as a version, numbered
by the server, before anything else; the number goes into the `done` event and the record. Once the record holds the
finished page, the generation's copy (generations.spec) is cleared: it is never read again.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import versions
from .db import Database, Explainer, Generation, Share, User, now_ms
from .middleware import ApiError

CHECKED = "✓ "  # a follow-up that was checked and needed no change (steps.CHECKED; shared/spec.ts CHECKED_PREFIX)
ASKED = "? "  # follow-ups v4: the editor asked the reader first (v4.editor.ASKED; shared/spec.ts ASKED_PREFIX)
NEW_TOPIC = "↗ "  # follow-ups v5: the agent found a different topic (followup.shared.NEW_TOPIC; shared/spec.ts NEW_TOPIC_PREFIX)
OFFER = "✓+ "  # follow-ups v5: an answer listing fixes, with a "Yes, make these fixes" button (followup.shared.OFFER; OFFER_PREFIX)
# The first-message step's answer, JSON {reply, offer?} (modes/topic.ANSWER; ANSWER_PREFIX), and a new topic with a brief,
# JSON {reply, brief} (modes/topic.NEW_TOPIC_BRIEF; NEW_TOPIC_BRIEF_PREFIX). Neither starts with CHECKED or NEW_TOPIC.
ANSWER = "✓* "
NEW_TOPIC_BRIEF = "↗+ "

log = logging.getLogger("explainer")

STALE_MS = 10 * 60 * 1000  # a running generation with no progress for this long died with its worker
ORPHAN_MS = 2 * 60 * 1000  # "generating" but no generation was ever started (the tab closed in between)
GEN_KEYS = ("status", "steps", "spec", "usage", "error")
INTERRUPTED = "Generation was interrupted (the server restarted). Try again."


def _apply_generation(data: dict, gen: Generation) -> dict:
    out = {**data, "steps": gen.steps or {}, "updatedAt": max(int(data.get("updatedAt") or 0), gen.updated_at)}
    meta = getattr(gen, "meta", None) or {}
    version = shown_version(meta.get("version"))
    made_from = meta.get("madeFrom") if meta.get("version") else None
    if gen.status == "done" and gen.spec:
        out.update(status="done", spec=gen.spec, usage=gen.usage, title=str(gen.spec.get("title") or out.get("title", ""))[:200])
        out.pop("error", None)
        if version:
            out["version"] = version
        if made_from:
            out["madeFrom"] = made_from  # follow-ups v5: made from an older version than the one it's under
    elif gen.status == "done":
        # Already applied (generations.spec is cleared once the record holds the page): keep the record's page.
        out.update(status="done", usage=gen.usage or out.get("usage"))
        out.pop("error", None)
        if version:
            out["version"] = version
    elif gen.status == "error" and _json_after(gen.error, ANSWER):
        # The first-message step answered (modes/intake.py): a reply in the thread, no page, not counted; offer: a brief the
        # reader can build with one click.
        a = _json_after(gen.error, ANSWER)
        out.update(status="done", answer=a["reply"], **({"offer": a["offer"]} if isinstance(a.get("offer"), str) and a["offer"] else {}))
        out.pop("error", None)
    elif gen.status == "error" and _json_after(gen.error, NEW_TOPIC_BRIEF):
        # Follow-ups v5's new_topic with a brief: shown like NEW_TOPIC, and the button builds from the brief.
        a = _json_after(gen.error, NEW_TOPIC_BRIEF)
        out.update(status="picking", plan={"kind": "cannot", "reason": a["reply"], "alternative": None, "newTopic": True,
                                           **({"brief": a["brief"]} if isinstance(a.get("brief"), str) and a["brief"] else {})})
        out.pop("error", None)
    elif gen.status == "error" and (gen.error or "").startswith(OFFER):
        # Follow-ups v5: an answer that lists fixes the agent would make, shown with a "Yes, make these fixes" button.
        out.update(status="done", answer=gen.error[len(OFFER):], offersFix=True)
        out.pop("error", None)
    elif gen.status == "error" and (gen.error or "").startswith(CHECKED):
        # Follow-ups v3 (steps.Checked): the sections were checked and found right. A reply in the thread, not an error and
        # not a new version (no spec).
        out.update(status="done", answer=gen.error[len(CHECKED):])
        out.pop("error", None)
    elif gen.status == "error" and (gen.error or "").startswith(ASKED) and _asked(gen.error):
        # Follow-ups v4 (v4.editor.Asked): a question for the reader, shown like the plan step's; no new version.
        q = _asked(gen.error)
        out.update(status="picking", plan={"kind": "ask", "question": q["question"]})
        out.pop("error", None)
    elif gen.status == "error" and (gen.error or "").startswith(NEW_TOPIC):
        # Follow-ups v5 (shared.NewTopic): a different topic, shown with a "New explainer about this" button; no new version.
        out.update(status="picking", plan={"kind": "cannot", "reason": gen.error[len(NEW_TOPIC):], "alternative": None, "newTopic": True})
        out.pop("error", None)
    elif gen.status == "error":
        out.update(status="error", error=gen.error or "Generation failed.")
    return out


def _json_after(message: str | None, prefix: str) -> dict | None:
    """The JSON object after `prefix` (with a "reply" string), or None."""
    import json
    if not isinstance(message, str) or not message.startswith(prefix):
        return None
    try:
        out = json.loads(message[len(prefix):])
    except ValueError:
        return None
    return out if isinstance(out, dict) and isinstance(out.get("reply"), str) else None


def _asked(message: str) -> dict | None:
    """The question in a v4 Asked message ({text, question: {text, options}}), or None when it can't be read."""
    import json
    try:
        q = json.loads(message[len(ASKED):])
    except ValueError:
        return None
    if not (isinstance(q, dict) and isinstance(q.get("question"), dict) and isinstance(q["question"].get("options"), list)):
        return None
    options = [{"label": str(o), "sections": [], "steps": []} for o in q["question"]["options"] if isinstance(o, str) and o.strip()]
    return {"text": str(q.get("text") or ""), "question": {"text": str(q["question"].get("text") or ""), "options": options}}


def _store(row: Explainer, data: dict) -> None:
    from .js import json_stringify
    row.data = data
    row.status = data["status"]
    row.title = str(data.get("title") or "")[:200]
    row.updated_at = int(data.get("updatedAt") or now_ms())
    row.saved_at = now_ms()
    row.size = len(json_stringify(data).encode("utf-8"))


def shown_version(n: Any) -> int | None:
    """The number a record carries: a page's first version shows none in the app (as before the version store)."""
    return n if isinstance(n, int) and n > 1 else None


def _clear_applied(s: Session, row: Explainer, gen: Generation | None = None) -> None:
    """The record holds the finished page: drop the generation's copy (never read again). Only when the two are the
    same page, so a record holding anything else never loses the result on its way."""
    if row.status != "done":
        return
    gen = gen if gen is not None else s.get(Generation, (row.user_id, row.id))
    if gen is None or gen.status != "done" or gen.spec is None:
        return
    if (row.data or {}).get("spec") == gen.spec:
        gen.spec = None


def settle(s: Session, row: Explainer) -> dict:
    """The record as the reader should see it now (persisting a finished or dead generation)."""
    if row.status != "generating":
        return row.data
    gen = s.get(Generation, (row.user_id, row.id))
    now = now_ms()
    if gen is None:
        if now - row.saved_at > ORPHAN_MS:
            _store(row, {**row.data, "status": "error", "error": "Generation didn't start (the page was closed). Try again.", "updatedAt": now})
        return row.data
    if gen.status == "running" and now - gen.updated_at > STALE_MS:
        gen.status, gen.error, gen.updated_at = "error", INTERRUPTED, now
    if gen.status == "running":
        return {**row.data, "steps": gen.steps or {}}  # live progress, not stored
    _store(row, _apply_generation(row.data, gen))
    _clear_applied(s, row, gen)
    return row.data


def list_index(db: Database, user_id: str) -> list[dict]:
    """[{id, updatedAt, status}] for every record, newest first: the browser fetches only what changed."""
    with db.session() as s:
        # Only the three columns (audit F15): a record's JSON can be megabytes. Just the "generating" ones are
        # loaded in full, to settle them.
        rows = s.execute(select(Explainer.id, Explainer.updated_at, Explainer.status).where(Explainer.user_id == user_id)
                         .order_by(Explainer.updated_at.desc())).all()
        out = []
        for rid, updated_at, status in rows:
            if status == "generating":
                r = s.get(Explainer, (user_id, rid))
                if r is not None:
                    d = settle(s, r)
                    updated_at, status = int(d.get("updatedAt") or r.updated_at), d.get("status", r.status)
            out.append({"id": rid, "updatedAt": int(updated_at), "status": status})
        return out


def get_many(db: Database, user_id: str, ids: list[str]) -> list[dict]:
    with db.session() as s:
        rows = s.scalars(select(Explainer).where(Explainer.user_id == user_id, Explainer.id.in_(ids))).all()
        return [settle(s, r) for r in rows]


def storage_used(s: Session, user_id: str, except_id: str) -> tuple[int, int]:
    from sqlalchemy import func
    count, size = s.execute(select(func.count(), func.coalesce(func.sum(Explainer.size), 0)).where(
        Explainer.user_id == user_id, Explainer.id != except_id)).one()
    return int(count), int(size)


def save(s: Session, user_id: str, data: dict) -> dict:
    """Upsert a record from the browser. Returns what is stored."""
    row = s.get(Explainer, (user_id, data["id"]))
    if row is None:
        row = Explainer(user_id=user_id, id=data["id"], created_at=int(data["createdAt"]), thread=data.get("thread"), status=data["status"], title="", data={}, updated_at=0)
        s.add(row)
    elif data["status"] == "generating" and row.status != "generating":
        # Only /api/generate starts a generation; keep what the server knows (a stale tab, or a retry about to start).
        data = {**data, **{k: row.data[k] for k in GEN_KEYS if k in row.data}}
        for k in GEN_KEYS:
            if k not in row.data:
                data.pop(k, None)
    elif row.status == "generating" and data["status"] != "generating":
        # The other direction (audit F13): a stale tab's older "done" copy must not move a record out of a
        # generation that is still running, or the result would never be applied. Keep the server's
        # status/steps/spec while it runs; the browser's other fields (chat, reading progress) still save.
        gen = s.get(Generation, (user_id, row.id))
        if gen is not None and gen.status == "running" and now_ms() - gen.updated_at <= STALE_MS:
            data = {**{k: v for k, v in data.items() if k not in GEN_KEYS}, **{k: row.data[k] for k in GEN_KEYS if k in row.data}}
        elif gen is not None and gen.status in ("done", "error"):
            data = _apply_generation(data, gen)  # finished but not yet applied: its result wins over the tab's copy
    # The server owns version numbers: whatever a tab sends (a stale copy, a number renumbered by backfill), the record
    # carries the number of its version row.
    v = versions.of_record(s, user_id, data["id"])
    if v is not None and not v.deleted:
        n = shown_version(v.version)
        data = {**data, "version": n} if n else {k: x for k, x in data.items() if k != "version"}
    row.thread = data.get("thread")
    _store(row, data)
    _clear_applied(s, row)
    return row.data


def remove(db: Database, user_id: str, record_id: str) -> bool:
    with db.session() as s:
        n = s.execute(delete(Explainer).where(Explainer.user_id == user_id, Explainer.id == record_id)).rowcount
        s.execute(delete(Generation).where(Generation.user_id == user_id, Generation.record_id == record_id))
        s.execute(delete(Share).where(Share.user_id == user_id, Share.record_id == record_id))  # its share link stops working
        versions.tombstone(s, user_id, record_id)  # the page goes; the number, parent and log stay
    return bool(n)


# ---------- the server's side of a generation ----------


def _summary(req: dict | None) -> str:
    """What was asked, short, for the version log."""
    if not isinstance(req, dict):
        return ""
    if req.get("mode") == "paper":
        return " · ".join(str(x) for x in (req.get("title"), req.get("note")) if x)[:300]
    return str(req.get("text") or "")[:300]


def version_meta(s: Session, user_id: str, record_id: str, req: dict | None, base_id: str | None = None,
                 replaces: str | None = None) -> dict:
    """Which version a run will make: {line, base, kind, number?, thread, request}. A revise follow-up continues its
    base's line (registering a line saved before the version store); anything else starts a line of its own. Retry and
    Edit keep the number of the version they replace."""
    row = s.get(Explainer, (user_id, record_id))
    data = row.data if row is not None and isinstance(row.data, dict) else {}
    f = data.get("followUp") if isinstance(data.get("followUp"), dict) else {}
    base_id = base_id or (f.get("baseId") if f.get("kind") == "revise" else None)  # a stale tab sends neither
    replaces = replaces or data.get("replaces")
    follow = (req or {}).get("followUp")
    revise = isinstance(follow, dict) and follow.get("kind") == "revise"
    thread = row.thread if row is not None and row.thread else None
    meta: dict[str, Any] = {"line": record_id, "base": None, "kind": "new", "thread": thread or record_id, "request": _summary(req)}
    if revise and isinstance(base_id, str):
        b = versions.of_record(s, user_id, base_id) or versions.backfill_line(s, user_id, base_id)
        if b is not None:
            meta.update(line=b.line, base=b.version, kind="change", thread=thread or b.line, under=b.version)
            # Follow-ups v5: a change that starts from an older version ("go back to v3 and ...") has that one as parent.
            plan = follow.get("plan") if isinstance(follow.get("plan"), dict) else {}
            start = (plan.get("v5") or {}).get("base") if isinstance(plan.get("v5"), dict) else None
            if isinstance(start, int) and start != b.version:
                old = versions.row(s, user_id, b.line, start)
                if old is not None and not old.deleted:
                    meta.update(base=start, madeFrom=start)
    if isinstance(replaces, str):
        old = versions.of_record(s, user_id, replaces) or versions.backfill_line(s, user_id, replaces)
        # A revision takes over a version of its own line; a new page only a line's first page.
        if old is not None and (old.line == meta["line"] if meta["kind"] == "change" else old.parent is None and old.kind == "new"):
            meta.update(line=old.line, number=old.version)
    return meta


def _save_version(s: Session, user_id: str, record_id: str, gen: Generation) -> int | None:
    """Save a finished page as a version (in a savepoint: a version-store failure never loses the page)."""
    meta = gen.meta
    if not isinstance(meta, dict) or not isinstance(gen.spec, dict):
        return None
    rev = gen.spec.get("revision") if isinstance(gen.spec.get("revision"), dict) else {}
    entry: dict[str, Any] = {"request": meta.get("request") or "", "record": record_id}
    if rev.get("reply"):
        entry["reply"] = str(rev["reply"])[:600]
    for k in ("changed", "removed", "widgets"):
        if isinstance(rev.get(k), list) and rev[k]:
            entry[k] = [str(x) for x in rev[k]][:30]
    if meta.get("under") and meta.get("under") != meta.get("base"):  # made from an older version than the one it was under
        entry["under"] = meta["under"]
    if isinstance(rev.get("suggestions"), list) and rev["suggestions"]:  # follow-ups v5's ideas, so "yes, do that" is clear later
        entry["suggestions"] = [str(x)[:600] for x in rev["suggestions"]][:2]
    try:
        with s.begin_nested():
            # One number at a time per user (the same row lock as start_generation), so two runs finishing together
            # can't both take the line's next number.
            s.execute(update(User).where(User.id == user_id).values(token_version=User.token_version))
            n = versions.save_version(s, user_id, line=meta["line"], thread=meta.get("thread"), record_id=record_id,
                                      parent=meta.get("base"), kind=meta.get("kind", "new"), page=gen.spec, log=entry,
                                      number=meta.get("number"))
    except Exception:
        log.exception("couldn't save a version for %s", record_id)
        return None
    gen.meta = {**meta, "version": n}
    return n


def start_generation(db: Database, user_id: str, record_id: str, req: dict | None = None, base_id: str | None = None,
                     replaces: str | None = None) -> None:
    """A fresh generation row, and the record (if saved) moved to "generating". A 409 while the record's last
    generation is still running (audit F14): two runs would write into one row and both be billed. With `req`, it also
    works out which version the run will make (version_meta)."""
    now = now_ms()
    with db.session() as s:
        # One start per user at a time (the same row lock as quota.start_generation), so two can't both pass the check.
        s.execute(update(User).where(User.id == user_id).values(token_version=User.token_version))
        gen = s.get(Generation, (user_id, record_id))
        if gen is not None and gen.status == "running" and now - gen.updated_at <= STALE_MS:
            raise ApiError(409, "This explainer is already being made. Wait for it to finish.", "already_running")
        s.execute(delete(Generation).where(Generation.user_id == user_id, Generation.record_id == record_id))
        meta = version_meta(s, user_id, record_id, req, base_id, replaces) if req is not None else None
        s.add(Generation(user_id=user_id, record_id=record_id, status="running", steps={}, started_at=now, updated_at=now, meta=meta))
        row = s.get(Explainer, (user_id, record_id))
        if row is not None:
            d = {k: v for k, v in row.data.items() if k != "error"}
            _store(row, {**d, "status": "generating", "steps": {}, "updatedAt": max(now, int(d.get("updatedAt") or 0))})


def generation_event(db: Database, user_id: str, record_id: str, e: dict[str, Any]) -> bool:
    """Record one GenerateEvent; a final one also settles the record. Never raises (the stream must go on).
    Returns False only when the generation row is gone (the record or the account was deleted while it ran),
    so the caller can stop spending on it (audit F18)."""
    try:
        with db.session() as s:
            gen = s.get(Generation, (user_id, record_id))
            if gen is None:  # deleted while running
                return False
            now = now_ms()
            if e.get("type") == "step":
                old = (gen.steps or {}).get(e.get("step")) or {}
                # at: when the step started (the app's progress ring times the running step from it, also after a reload).
                step = {"status": e.get("status"), "at": old.get("at") or now}
                if e.get("detail"):
                    step["detail"] = e["detail"]
                if isinstance(e.get("preview"), dict):
                    step["preview"] = e["preview"]
                gen.steps = {**(gen.steps or {}), e.get("step"): step}
            elif e.get("type") == "done":
                gen.status, gen.spec, gen.usage = "done", e.get("spec"), e.get("usage")
                start = e.pop("startFrom", None)  # follow-ups v5's agent went back to that version: it is the parent
                meta = dict(gen.meta or {})
                if isinstance(start, int) and not isinstance(start, bool) and meta.get("line") and meta.get("kind") == "change":
                    old = versions.row(s, user_id, meta["line"], start)
                    if old is not None and not old.deleted and start != meta.get("base"):
                        gen.meta = {**meta, "base": start, "madeFrom": start}
                n = _save_version(s, user_id, record_id, gen)  # before the copy can be cleared below
                if n is not None:
                    e["version"] = n  # the done event the browser gets carries the number
                    if (gen.meta or {}).get("madeFrom"):
                        e["madeFrom"] = gen.meta["madeFrom"]
            elif e.get("type") == "error":
                gen.status, gen.error = "error", str(e.get("message") or "Generation failed.")[:8000]  # answers too (Text)
            else:
                return True
            gen.updated_at = now
            if gen.status != "running":
                row = s.get(Explainer, (user_id, record_id))
                if row is not None and row.status == "generating":
                    _store(row, _apply_generation(row.data, gen))
                    _clear_applied(s, row, gen)
    except Exception:
        log.exception("couldn't save generation progress for %s", record_id)
    return True


def revert_version(db: Database, user_id: str, record_id: str, base_id: str, to: int | None = None,
                   max_bytes: int | None = None) -> dict:
    """Undo / "go back to version N": version `to` (default: the version the base was made under, else its parent) saved again as a new version of the
    base's line, made by code (no AI, not counted). Returns {spec, version, reverted}. Asking twice for the same record
    returns the same version. max_bytes: the user's storage cap (records + versions), checked before saving."""
    with db.session() as s:
        s.execute(update(User).where(User.id == user_id).values(token_version=User.token_version))
        have = versions.of_record(s, user_id, record_id)
        if have is not None and have.kind == "revert" and not have.deleted:
            page = versions.load(s, user_id, have.line, have.version) or {}
            return {"spec": page, "version": have.version, "reverted": (have.log or {}).get("reverted")}
        base = versions.of_record(s, user_id, base_id) or versions.backfill_line(s, user_id, base_id)
        if base is None:
            raise ApiError(404, "Not found.")
        # Undo goes back to the page the reader was on: the version this one was made under (v5 "go back to v1 and ..."
        # makes v5 from v1 under v4: undo is v4), else its parent.
        to = to or (base.log or {}).get("under") or base.parent
        page = versions.load(s, user_id, base.line, to) if to else None
        if page is None:
            raise ApiError(409, "There's no earlier version of this page to go back to.", "no_version")
        spec = {k: v for k, v in page.items() if k not in ("revision", "addition")}
        spec["warnings"] = []
        if max_bytes is not None:
            _, used = storage_used(s, user_id, "")
            if used + versions.size_used(s, user_id) + len(versions.pack(spec)) > max_bytes:
                raise ApiError(413, "Your history is full. Delete some old explainers to save new ones.", "storage_full")
        n = versions.save_version(s, user_id, line=base.line, thread=None, record_id=record_id, parent=base.version,
                                  kind="revert", page=spec, log={"request": f"Back to version {to}", "reverted": to})
        return {"spec": spec, "version": n, "reverted": to}
