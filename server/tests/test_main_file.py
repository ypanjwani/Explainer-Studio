"""Follow-ups v5 phase 2: a version's main file (modes/followup/main_file.py): the code part, playground readings, the AI
part cached per section in section_notes, saved per version, and handles resolved against one version only."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from explainer import versions
from explainer.db import Database, Explainer, PageVersion, SectionNote, User, now_ms
from explainer.modes.explorer.playground import playground_html
from explainer.modes.followup import main_file as M
from explainer.pricing import UsageLog
from test_readings import SPEC, needs_node

SELECT_SPEC = {"title": "Pick", "state": {"m": "small", "on": True}, "components": [
    {"id": "m", "type": "select", "key": "m", "label": "Model", "options": ["small", {"value": "large", "label": "Large"}]},
    {"id": "t", "type": "toggle", "key": "on", "label": "Noise"},
    {"id": "r", "type": "button", "label": "Reset", "action": "reset"},
    {"id": "o", "type": "readout", "label": "Loss", "value": "return 1"}]}


def pg(spec, title="Fit", lesson="Higher degree overfits"):
    return {"type": "widget", "title": title, "html": playground_html(spec), "plan": json.dumps({"lesson": lesson})}


def stage(sid, label, *blocks, blurb="About it."):
    return {"id": sid, "label": label, "phase": "x", "blurb": blurb, "blocks": list(blocks)}


INTRO = stage("intro", "Overfitting", {"type": "text", "md": "A model that fits noise **overfits**."},
              {"type": "quiz", "questions": [{"q": "What is overfitting?", "choices": ["a", "b"], "answer": 0, "explain": ""},
                                             {"q": "How to spot it?", "choices": ["c", "d"], "answer": 1, "explain": ""}]})
DEGREE = stage("degree", "Polynomial degree", {"type": "text", "md": "Raise the degree and watch test error."}, pg(SPEC))
CHOICE = stage("choice", "Model size", {"type": "predict", "question": "Which model wins?", "choices": ["x", "y"], "answer": 0},
               {"type": "reveal", "prompt": "Why bigger?", "answer": "More capacity."}, pg(SELECT_SPEC, "Pick", "Size matters"))
PAGE = {"title": "Overfitting", "subtitle": "When models memorise", "level": "beginner", "stages": [INTRO, DEGREE, CHOICE]}


def fake_client(calls: list, fail: bool = False, skip: tuple = ()):
    """Answers every section it's sent (but those in `skip`); records the section ids of each call."""
    def create(**kw):
        user = kw["messages"][0]["content"]
        ids = re.findall(r'Section id "([^"]+)"', user)
        calls.append(ids)
        if fail:
            raise RuntimeError("down")
        reply = {"sections": [{"id": i, "introduces": [f"{i} idea"], "uses": ["loss"], "claims": [f"{i} claim 0.5"]} for i in ids if i not in skip]}
        return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                               content=[SimpleNamespace(type="text", text=json.dumps(reply))], stop_reason="end_turn")
    return SimpleNamespace(messages=SimpleNamespace(create=create))


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        s.add_all([User(id="u1", email="a@x.io"), User(id="u2", email="b@x.io")])
        s.flush()
        s.add(Explainer(user_id="u1", id="r1", status="done", title="t", data={"request": {"quality": "playground"}}, created_at=1, updated_at=1))
    return d


@pytest.fixture
def readings(monkeypatch):
    """read_playground faked (no node needed); counts the runs."""
    runs: list = []

    def fake(spec, runs_=None, max_chars=0):
        runs.append(spec.get("title"))
        return {"runs": [{"label": "at the start"}], "text": f'- at the start:\n  readouts: {spec.get("title")} = 1'}
    monkeypatch.setattr(M, "read_playground", fake)
    return runs


def save(db, page, user="u1", line="r1", record="r1", number=None) -> int:
    with db.session() as s:
        return versions.save_version(s, user, line=line, thread=line, record_id=record, parent=None, kind="new", page=page, number=number)


# ---------- code part ----------


def test_code_part_is_exact():
    code = M.section_code(INTRO)
    assert code["blocks"] == ["text: A model that fits noise **overfits**.", "quiz: Q: What is overfitting? | Q: How to spot it?"]
    assert "playground" not in code
    deg = M.section_code(DEGREE)
    assert deg["playground"] == '"Fit": controls: Degree 1–9 (default 3); shows: Test error (readout), Error vs degree (line chart); teaches: Higher degree overfits'
    ch = M.section_code(CHOICE)
    assert ch["blocks"][:2] == ["predict: Q: Which model wins?", "reveal: Q: Why bigger?"]
    assert "Model (small / Large; default small); Noise (on/off; default True)" in ch["playground"]
    assert "Reset" not in ch["playground"]


@needs_node
def test_real_readings_at_the_start():
    assert M.section_readings(DEGREE).startswith("readouts: Test error = 0.9")
    assert M.section_readings(INTRO) == ""


def test_main_file_text_names_handles_and_ids(db, readings):
    save(db, PAGE)
    main = M.main_file_of(db, fake_client([]), "u1", "r1", 1, UsageLog(""))
    text = main["text"]
    assert text.startswith('Page: "Overfitting" — When models memorise. Level: beginner. Mode: Explorer. Version 1.')
    assert 'S1 · intro — "Overfitting": About it.' in text and 'S3 · choice — "Model size"' in text
    assert "  at the start it shows: readouts: Error by degree = 1" in text
    assert "  introduces: degree idea | uses: loss | claims: degree claim 0.5" in text
    assert [s["handle"] for s in main["sections"]] == ["S1", "S2", "S3"]


# ---------- cache and saving ----------


def test_built_once_saved_and_only_changes_reach_the_ai(db, readings):
    calls: list = []
    client = fake_client(calls)
    usage = UsageLog("")
    save(db, PAGE)
    first = M.main_file_of(db, client, "u1", "r1", 1, usage)
    assert calls == [["intro", "degree", "choice"]] and readings == ["Error by degree", "Pick"]
    assert [s["step"] for s in usage.summary()["steps"]] == ["follow-up main file"]
    # saved: no AI, no readings the second time
    assert M.main_file_of(db, client, "u1", "r1", 1, usage) == first
    assert len(calls) == 1 and len(readings) == 2
    # v2 changes one section: only it is described and run
    v2 = {**PAGE, "stages": [INTRO, {**DEGREE, "blurb": "Changed."}, CHOICE]}
    save(db, v2)
    second = M.main_file_of(db, client, "u1", "r1", 2, usage)
    assert calls[-1] == ["degree"] and readings[-1] == "Error by degree" and len(readings) == 3
    assert second["sections"][1]["blurb"] == "Changed." and second["version"] == 2


def test_an_older_format_is_rebuilt_from_the_notes(db, readings, monkeypatch):
    calls: list = []
    save(db, PAGE)
    M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    with db.session() as s:
        v = s.get(PageVersion, ("u1", "r1", 1))
        v.main_file = versions.pack({"format": "0", "text": "old"})
    again = M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    assert again["format"] == M.MAIN_FILE_FORMAT and len(calls) == 1  # from the cached notes, no AI


def test_a_deleted_or_missing_version_has_none(db, readings):
    save(db, PAGE)
    with db.session() as s:
        versions.tombstone(s, "u1", "r1")
    assert M.main_file_of(db, fake_client([]), "u1", "r1", 1, UsageLog("")) is None
    assert M.main_file_of(db, fake_client([]), "u1", "r1", 9, UsageLog("")) is None


def test_when_the_ai_fails_the_code_part_is_used_and_retried(db, readings):
    calls: list = []
    save(db, PAGE)
    main = M.main_file_of(db, fake_client(calls, fail=True), "u1", "r1", 1, UsageLog(""))
    assert main["complete"] is False and main["sections"][0]["introduces"] == []
    assert "S2 · degree" in main["text"]
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", 1)).main_file is None  # not saved
        notes = s.query(SectionNote).filter_by(user_id="u1").all()
        assert len(notes) == 3 and all(n.note["ai"] is None for n in notes)
    main = M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    assert main["complete"] is True and calls[-1] == ["intro", "degree", "choice"]
    assert len(readings) == 2  # readings were kept with the notes


def test_without_a_client_nothing_is_saved(db, readings):
    save(db, PAGE)
    main = M.main_file_of(db, None, "u1", "r1", 1, UsageLog(""))
    assert main["complete"] is False
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", 1)).main_file is None


def test_notes_are_per_user_deleted_with_the_account_and_pruned(db, readings):
    calls: list = []
    save(db, PAGE)
    save(db, PAGE, user="u2", line="x1", record="x1")
    M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    M.main_file_of(db, fake_client(calls), "u2", "x1", 1, UsageLog(""))
    assert len(calls) == 2  # u2's identical sections get their own notes
    with db.session() as s:
        old = s.query(SectionNote).filter_by(user_id="u1").first()
        old.used_at = now_ms() - (M.NOTES_MAX_DAYS + 1) * 24 * 3600 * 1000
        stale = old.hash
    save(db, {**PAGE, "stages": [stage("new", "New", {"type": "text", "md": "x"})]})
    M.main_file_of(db, fake_client(calls), "u1", "r1", 2, UsageLog(""))  # a build prunes u1's unused notes
    with db.session() as s:
        assert s.get(SectionNote, ("u1", stale)) is None
        s.delete(s.get(User, "u2"))
    with db.session() as s:
        assert s.query(SectionNote).filter_by(user_id="u2").count() == 0


def test_main_file_counts_toward_the_version_size(db, readings):
    save(db, PAGE)
    with db.session() as s:
        before = s.get(PageVersion, ("u1", "r1", 1)).size
    M.main_file_of(db, fake_client([]), "u1", "r1", 1, UsageLog(""))
    with db.session() as s:
        v = s.get(PageVersion, ("u1", "r1", 1))
        assert v.size == len(v.payload) + len(v.main_file) > before


# ---------- AI part checks and handles ----------


def test_the_ai_part_is_checked():
    raw = {"sections": [{"id": "intro", "introduces": ["x" * 200] + [f"t{i}" for i in range(9)], "uses": [3, "ok"], "claims": ["c"] * 9},
                        {"id": "ghost", "introduces": ["no"], "uses": [], "claims": []}]}
    got = M.merge_ai(raw, {"intro"})
    assert list(got) == ["intro"]
    assert len(got["intro"]["introduces"]) == M.LIST_MAX and len(got["intro"]["introduces"][0]) == M.ITEM_CHARS
    assert got["intro"]["uses"] == ["ok"] and len(got["intro"]["claims"]) == M.CLAIMS_MAX
    assert M.merge_ai("not json", {"intro"}) == {}


def test_handles_resolve_against_this_main_file_only(db, readings):
    save(db, PAGE)
    main = M.main_file_of(db, None, "u1", "r1", 1, UsageLog(""))
    assert M.handle_of(main, "S2") == "degree" and M.handle_of(main, "s3") == "choice" and M.handle_of(main, "1") == "intro"
    assert M.handle_of(main, "choice") == "choice"
    assert M.handle_of(main, "S4") is None and M.handle_of(main, "attention") is None


# ---------- no endless retries, no kept failures ----------


def test_a_section_the_ai_skips_is_not_asked_about_again(db, readings):
    calls: list = []
    save(db, PAGE)
    main = M.main_file_of(db, fake_client(calls, skip=("degree",)), "u1", "r1", 1, UsageLog(""))
    assert main["complete"] is True and main["sections"][1]["introduces"] == []
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", 1)).main_file is not None  # saved
    save(db, {**PAGE, "title": "Same sections"})
    M.main_file_of(db, fake_client(calls), "u1", "r1", 2, UsageLog(""))
    assert len(calls) == 1  # the skipped section's note is complete too: no second call


def test_long_pages_go_in_batches(db, readings):
    calls: list = []
    big = {**PAGE, "stages": [stage(f"s{i}", f"Part {i}", {"type": "text", "md": f"Text {i}."}) for i in range(M.AI_BATCH + 3)]}
    save(db, big)
    main = M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    assert [len(c) for c in calls] == [M.AI_BATCH, 3] and main["complete"] is True


def test_one_failed_batch_is_retried_alone(db, readings):
    calls: list = []
    big = {**PAGE, "stages": [stage(f"s{i}", f"Part {i}", {"type": "text", "md": f"Text {i}."}) for i in range(M.AI_BATCH + 2)]}
    save(db, big)
    good = fake_client(calls)

    def flaky(**kw):  # the second batch fails
        if len(calls) == 1:
            calls.append(["failed"])
            raise RuntimeError("down")
        return good.messages.create(**kw)
    first = M.main_file_of(db, SimpleNamespace(messages=SimpleNamespace(create=flaky)), "u1", "r1", 1, UsageLog(""))
    assert first["complete"] is False
    M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
    assert calls[-1] == [f"s{M.AI_BATCH}", f"s{M.AI_BATCH + 1}"]


def test_a_failed_playground_run_is_not_kept(db, monkeypatch):
    runs: list = []
    answers = [None, {"runs": [], "text": "Couldn't run it: it took too long."},
               {"runs": [{}], "text": "- at the start:\n  readouts: Loss = 2"}]

    def fake(spec, runs_=None, max_chars=0):
        runs.append(1)
        return answers[min(len(runs), len(answers)) - 1]
    monkeypatch.setattr(M, "read_playground", fake)
    save(db, {**PAGE, "stages": [CHOICE]})
    calls: list = []
    for expect_complete in (False, False, True):  # no node, then a timeout, then it runs
        main = M.main_file_of(db, fake_client(calls), "u1", "r1", 1, UsageLog(""))
        assert main["complete"] is expect_complete
    assert len(runs) == 3 and len(calls) == 1  # the AI part was kept; only the run was tried again
    assert main["sections"][0]["readings"] == "readouts: Loss = 2"
    assert "Couldn't run it" not in main["text"]
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", 1)).main_file is not None
