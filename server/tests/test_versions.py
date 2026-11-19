"""The version store (versions.py, follow-ups v5 phase 1): server-numbered versions per page line, saved when a
generation finishes, tombstones on delete, Undo/revert by code, backfill of pages saved before it, and generations.spec
cleared once the record holds the page."""

from __future__ import annotations

import sqlite3

from conftest import H, J, signup
from explainer import history, versions
from explainer.db import Generation, PageLine, PageVersion, User
from test_history import CONCEPT, db_of, events, fake_pipeline, record

REVISE = {"mode": "concept", "text": "Make it simpler", "quality": "playground", "followUp": {"kind": "revise", "outline": "{}", "design": {}}}


def page(title="Dropout", *stages):
    return {"title": title, "stages": [{"id": sid, "label": label, "blocks": [{"type": "text", "md": md}]} for sid, label, md in stages]}


P1 = page("Dropout", ("intro", "Intro", "Dropout drops 50% of units."), ("math", "Math", "Scale by 1/(1-p)."))
P2 = page("Dropout", ("intro", "Intro", "Dropout randomly turns off units."), ("math", "Math", "Scale by 1/(1-p)."))
P3 = page("Dropout", ("math", "Math", "Scale by 1/(1-p)."), ("intro", "Intro", "Dropout drops 50% of units."), ("demo", "Demo", "Try p = 0.3."))


def user_of(client, a) -> str:
    return client.get("/api/auth/me", headers=a).get_json()["user"]["id"]


def finish(db, uid, rid, spec, req=CONCEPT, base_id=None, replaces=None) -> dict:
    """Run one generation for `rid` (record saved first, like the app's flushRecord) and return its done event."""
    history.start_generation(db, uid, rid, req, base_id, replaces)
    e = {"type": "done", "spec": spec, "usage": {}}
    history.generation_event(db, uid, rid, e)
    return e


def put(client, a, rid, **extra):
    r = client.put(f"/api/explainers/{rid}", json=record(rid, **extra), headers=a)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def get(client, a, rid) -> dict:
    got = client.get(f"/api/explainers/{rid}", headers=a).get_json()
    return got.get("record", got)


def rows(db, uid, line) -> list[PageVersion]:
    with db.session() as s:
        return list(s.query(PageVersion).filter_by(user_id=uid, line=line).order_by(PageVersion.version))


def gen_spec(db, uid, rid):
    with db.session() as s:
        return s.get(Generation, (uid, rid)).spec


# ---------- packing and reading ----------


def test_pack_round_trips_exactly(app):
    p = {"title": "Attention — “é” 注意", "stages": [{"id": "a", "x": 0.1 + 0.2, "n": [1, 2.5e-9, None, True]}], "b": {"z": 1, "a": 2}}
    assert versions.unpack(versions.pack(p)) == p
    assert versions.canonical(versions.unpack(versions.pack(p))) == versions.canonical(p)


def test_diff_reports_added_removed_replaced_moved_and_fields():
    d = versions.diff(P1, {**P3, "subtitle": "new"})
    assert [s["id"] for s in d["added"]] == ["demo"]
    assert d["removed"] == []
    assert d["replaced"] == []
    assert d["order"] == ["math", "intro", "demo"]
    assert d["fields"] == {"subtitle": {"before": None, "after": "new"}}
    d = versions.diff(P1, P2)
    assert [r["id"] for r in d["replaced"]] == ["intro"] and "order" not in d
    d = versions.diff(P2, page("Dropout", ("intro", "Intro", "Dropout randomly turns off units.")))
    assert [s["id"] for s in d["removed"]] == ["math"]


def test_search_finds_mentions_in_that_version_only(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    with db.session() as s:
        assert [h["id"] for h in versions.search_page(s, uid, "r1", 1, "50%")] == ["intro"]
        assert versions.search_page(s, uid, "r1", 2, "50%") == []
        assert [h["id"] for h in versions.search_page(s, uid, "r1", 2, "1/(1-P)")] == ["math"]


# ---------- numbers ----------


def test_new_explainer_is_v1_and_the_record_shows_no_number(app, client, monkeypatch):
    fake_pipeline(monkeypatch)
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "rec1", status="generating", spec=None)
    ev = events(client.post("/api/generate", json={**CONCEPT, "recordId": "rec1"}, headers=a))
    assert ev[-1]["type"] == "done" and ev[-1]["version"] == 1
    assert "version" not in get(client, a, "rec1")  # a first page shows no number, as before
    [v] = rows(db, uid, "rec1")
    assert (v.version, v.parent, v.kind, v.record_id) == (1, None, "new", "rec1")
    with db.session() as s:
        assert versions.load(s, uid, "rec1", 1) == {"title": "Dropout, explained", "stages": []}


def test_revise_numbers_branches_and_never_reuses(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    assert finish(db, uid, "r1", P1)["version"] == 1
    for rid in ("r2", "r3"):  # two follow-ups from the same base: two branches
        put(client, a, rid, status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    assert finish(db, uid, "r2", P2, REVISE, "r1")["version"] == 2
    assert finish(db, uid, "r3", P3, REVISE, "r1")["version"] == 3
    assert [(v.version, v.parent, v.kind) for v in rows(db, uid, "r1")] == [(1, None, "new"), (2, 1, "change"), (3, 1, "change")]
    assert get(client, a, "r3")["version"] == 3
    # deleting the latest keeps its number taken
    assert client.delete("/api/explainers/r3", headers=a).status_code == 200
    put(client, a, "r4", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r2"}, thread="r1")
    assert finish(db, uid, "r4", P1, REVISE, "r2")["version"] == 4
    with db.session() as s:
        assert versions.change_of(s, uid, "r1", 4)["parent"] == 2
        assert versions.used_ids(s, uid, "r1") == {"intro", "math", "demo"}  # demo only in the deleted v3: still reserved


def test_a_stale_tab_without_base_id_uses_the_stored_record(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    assert finish(db, uid, "r2", P2, REVISE)["version"] == 2  # no baseId sent


def test_new_topic_page_starts_its_own_line(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "new", "baseId": "r1"}, thread="r1")
    new_topic = {**CONCEPT, "followUp": {"kind": "new", "previous": "Dropout"}}
    assert finish(db, uid, "r2", P3, new_topic, "r1")["version"] == 1
    assert [v.version for v in rows(db, uid, "r2")] == [1]
    with db.session() as s:
        assert s.get(PageLine, (uid, "r2")).thread == "r1"


def test_no_version_without_a_page(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    history.start_generation(db, uid, "r1", CONCEPT)
    for msg in ("✓ Checked, nothing needed changing", '? {"question": {"text": "Which?", "options": ["a", "b"]}}', "boom"):
        history.generation_event(db, uid, "r1", {"type": "error", "message": msg})
    assert rows(db, uid, "r1") == []


def test_retry_keeps_its_number_and_replaces_the_row(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    # Retry of v2 (a new record replacing r2): a failed run changes nothing
    put(client, a, "r2b", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1", replaces="r2")
    history.start_generation(db, uid, "r2b", REVISE, "r1", "r2")
    history.generation_event(db, uid, "r2b", {"type": "error", "message": "boom"})
    assert [(v.version, v.record_id) for v in rows(db, uid, "r1")] == [(1, "r1"), (2, "r2")]
    # ...a finished one takes v2's place
    assert finish(db, uid, "r2b", P3, REVISE, "r1", "r2")["version"] == 2
    assert [(v.version, v.record_id, v.parent) for v in rows(db, uid, "r1")] == [(1, "r1", None), (2, "r2b", 1)]
    client.delete("/api/explainers/r2", headers=a)  # the app deletes the replaced record: v2 is the retry's, untouched
    with db.session() as s:
        assert versions.load(s, uid, "r1", 2) == P3


def test_retry_of_a_first_page_keeps_v1(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r1b", status="generating", spec=None, replaces="r1")
    assert finish(db, uid, "r1b", P2, CONCEPT, None, "r1")["version"] == 1
    assert [(v.version, v.record_id) for v in rows(db, uid, "r1")] == [(1, "r1b")]


# ---------- revert ----------


def test_revert_is_a_new_version_made_by_code(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", {**P2, "revision": {"reply": "Simpler now."}}, REVISE, "r1")
    r = client.post("/api/explainers/r3/revert", json={"baseId": "r2"}, headers=a)
    assert r.status_code == 200, r.get_json()
    got = r.get_json()
    assert got["version"] == 3 and got["reverted"] == 1 and got["spec"] == {**P1, "warnings": []}
    v3 = rows(db, uid, "r1")[2]
    assert (v3.kind, v3.parent, v3.record_id) == ("revert", 2, "r3")
    # asking again for the same record gives the same version
    assert client.post("/api/explainers/r3/revert", json={"baseId": "r2"}, headers=a).get_json()["version"] == 3
    # nothing before v1
    r = client.post("/api/explainers/r4/revert", json={"baseId": "r1"}, headers=a)
    assert r.status_code == 409
    assert client.post("/api/explainers/r5/revert", json={"baseId": "nope"}, headers=a).status_code == 404
    assert client.post("/api/explainers/r5/revert", json={"baseId": "r2", "to": 0}, headers=a).status_code == 400


# ---------- delete ----------


def test_delete_leaves_a_tombstone_and_account_delete_removes_all(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    client.delete("/api/explainers/r1", headers=a)
    v1 = rows(db, uid, "r1")[0]
    assert v1.deleted and v1.payload is None and v1.section_ids == ["intro", "math"] and v1.log["request"]
    with db.session() as s:
        assert versions.load(s, uid, "r1", 1) is None
        assert versions.change_of(s, uid, "r1", 2)["parent_deleted"] is True
        assert [e["deleted"] for e in versions.version_log(s, uid, "r1")] == [True, False]
        s.delete(s.get(User, uid))  # account delete: cascade
    with db.session() as s:
        assert s.query(PageVersion).count() == 0 and s.query(PageLine).count() == 0


def test_version_sizes_count_toward_the_cap(app, client, monkeypatch):
    from explainer.routes import history as routes
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    with db.session() as s:
        used = versions.size_used(s, uid)
    assert used > 0
    monkeypatch.setattr(routes, "USER_MAX_BYTES", len(str(record("r9"))) + used // 2)
    assert client.put("/api/explainers/r9", json=record("r9"), headers=a).status_code == 413


# ---------- generations.spec cleared ----------


def test_generation_spec_is_cleared_once_the_record_holds_the_page(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    assert gen_spec(db, uid, "r1") is None
    got = get(client, a, "r1")
    assert got["status"] == "done" and got["spec"] == P1
    put(client, a, "r1", spec=P1, chat=[{"role": "user", "text": "hi"}])  # later saves keep working
    assert get(client, a, "r1")["spec"] == P1


def test_generation_spec_kept_until_the_record_exists(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    finish(db, uid, "r1", P1)  # the browser never saved the record
    assert gen_spec(db, uid, "r1") == P1
    assert rows(db, uid, "r1")[0].version == 1  # the version is saved anyway
    put(client, a, "r1", status="generating", spec=None)  # the tab saves it now: the result is applied, then cleared
    got = get(client, a, "r1")
    assert got["status"] == "done" and got["spec"] == P1 and "version" not in got
    assert gen_spec(db, uid, "r1") is None


def test_done_with_an_empty_spec_keeps_the_record_page(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    history.start_generation(db, uid, "r1", CONCEPT)
    with db.session() as s:
        gen = s.get(Generation, (uid, "r1"))
        gen.status, gen.spec = "done", None
    out = history._apply_generation({**record("r1"), "status": "generating", "spec": P1}, gen)
    assert out["status"] == "done" and out["spec"] == P1


def test_a_record_holding_another_page_never_clears_the_result(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    finish(db, uid, "r1", P2)
    put(client, a, "r1", spec=P1)  # a done record with a different page: the result stays
    assert gen_spec(db, uid, "r1") == P2


def test_migration_cleanup_clears_only_applied_rows(tmp_path):
    import importlib.util  # the migration's module name starts with a digit, so it's loaded by path
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0010_page_versions.py"
    spec = importlib.util.spec_from_file_location("m0010", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    con = sqlite3.connect(tmp_path / "x.db")
    con.executescript("""
        create table explainers (user_id text, id text, status text);
        create table generations (user_id text, record_id text, status text, spec text);
        insert into explainers values ('u', 'done', 'done'), ('u', 'gen', 'generating');
        insert into generations values ('u', 'done', 'done', '{}'), ('u', 'gen', 'done', '{}'), ('u', 'none', 'done', '{}'), ('u', 'err', 'error', '{}');
    """)
    con.execute(m.CLEAR_APPLIED)
    assert dict(con.execute("select record_id, spec from generations").fetchall()) == {"done": None, "gen": "{}", "none": "{}", "err": "{}"}


# ---------- pages saved before the version store ----------


def test_backfill_keeps_the_app_numbers(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", spec=P1, createdAt=1000)
    put(client, a, "r2", spec=P2, createdAt=2000, thread="r1", version=2, followUp={"kind": "revise", "baseId": "r1"})
    put(client, a, "r3", spec=P3, createdAt=3000, thread="r1", version=3, followUp={"kind": "revise", "baseId": "r2"})
    put(client, a, "r3x", spec=P1, createdAt=3500, thread="r1", version=3, followUp={"kind": "revise", "baseId": "r2"})  # a duplicate number
    put(client, a, "r9", spec=P1, createdAt=4000, thread="r1", followUp={"kind": "new", "baseId": "r3"})  # another line
    put(client, a, "r4", status="generating", spec=None, createdAt=5000, thread="r1", followUp={"kind": "revise", "baseId": "r3"})
    assert finish(db, uid, "r4", P2, REVISE, "r3")["version"] == 5
    assert [(v.version, v.record_id, v.parent) for v in rows(db, uid, "r1")] == [
        (1, "r1", None), (2, "r2", 1), (3, "r3", 2), (4, "r3x", 2), (5, "r4", 3)]
    got = get(client, a, "r3x")
    assert got["version"] == 4 and got["updatedAt"] > 3500  # renumbered, written back, and newer so the app fetches it
    assert rows(db, uid, "r9") == []
    # a tab still holding the old number can't put it back
    assert put(client, a, "r3x", spec=P1, createdAt=3500, thread="r1", version=3, followUp={"kind": "revise", "baseId": "r2"})["version"] == 4


# ---------- review fixes ----------


def test_a_save_always_carries_the_servers_number(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    assert put(client, a, "r2", spec=P2, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")["version"] == 2  # sent none
    assert put(client, a, "r2", spec=P2, version=7, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")["version"] == 2
    assert "version" not in put(client, a, "r1", spec=P1, version=5)  # v1 shows none


def test_revert_respects_the_storage_cap(app, client, monkeypatch):
    from explainer.routes import history as routes
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    monkeypatch.setattr(routes, "USER_MAX_BYTES", 10)
    r = client.post("/api/explainers/r3/revert", json={"baseId": "r2"}, headers=a)
    assert r.status_code == 413
    assert len(rows(db, uid, "r1")) == 2


def test_a_replaced_pages_ids_stay_reserved(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P3)  # intro, math, demo
    put(client, a, "r1b", status="generating", spec=None, replaces="r1")
    finish(db, uid, "r1b", page("Dropout", ("other", "Other", "x")), CONCEPT, None, "r1")
    with db.session() as s:
        assert versions.used_ids(s, uid, "r1") == {"other", "math", "intro", "demo"}
        assert versions.load(s, uid, "r1", 1)["stages"][0]["id"] == "other"


def test_a_new_page_never_takes_over_a_revision(app, client):
    a = signup(client)
    uid, db = user_of(client, a), db_of(app)
    put(client, a, "r1", status="generating", spec=None)
    finish(db, uid, "r1", P1)
    put(client, a, "r2", status="generating", spec=None, followUp={"kind": "revise", "baseId": "r1"}, thread="r1")
    finish(db, uid, "r2", P2, REVISE, "r1")
    put(client, a, "r2b", status="generating", spec=None, thread="r1", replaces="r2")
    assert finish(db, uid, "r2b", P3, CONCEPT, None, "r2")["version"] == 1  # its own line, v1
    assert [(v.version, v.record_id) for v in rows(db, uid, "r1")] == [(1, "r1"), (2, "r2")]
    assert [v.version for v in rows(db, uid, "r2b")] == [1]
