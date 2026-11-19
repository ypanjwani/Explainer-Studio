"""Follow-ups v4 (modes/followup/v4/): the talk step, the editor's tools, the editor loop, the checks and the reply.
Haiku, Jev and the playground builder are faked; the node playground checker is faked where a spec is changed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from explainer import history
from explainer.db import Generation
from explainer.followup import parse_plan
from explainer.modes.followup import entry, playground_edit as pe, shared
from explainer.modes.followup.v4 import editor, page as P, reply, talk, tools as T
from explainer.pricing import UsageLog
from followup_v3_data import COMPACT, FOLLOW, PAGE, PG, Store, fake_client, index_of, stage_of

LITE_PAGE = {**COMPACT, "stages": [s for s in COMPACT["stages"] if s["id"] != "sampling"]}


@pytest.fixture(autouse=True)
def no_readings(monkeypatch):
    """Playground readings run node; the tests that need them fake them, the rest get none."""
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: None)
    monkeypatch.setattr(talk, "read_playground", lambda *a, **k: None)


def ctx() -> T.Ctx:
    return T.Ctx(None, UsageLog("x"), None)


def page(quality: str = "playground", base: dict = PAGE) -> P.Page:
    return P.Page(base, quality, "Attention lets each token look at every other token. Sampling with temperature reshapes the odds.")


# ---------- tools ----------


def test_titles_and_meta_change_directly_and_are_logged():
    pg = page()
    T.apply(pg, "set_page_meta", {"title": "LLMs, step by step"}, ctx())
    T.apply(pg, "rename_section", {"section": "S3", "label": "Why attention mixes context"}, ctx())
    assert pg.current["title"] == "LLMs, step by step" and stage_of(pg.current, "attention")["label"] == "Why attention mixes context"
    assert pg.page_fields and pg.text_changed == {"attention"}
    assert [e["status"] for e in pg.log] == ["done", "done"]
    assert [s for s in pg.stages if s["id"] != "attention"] == [s for s in PAGE["stages"] if s["id"] != "attention"]
    with pytest.raises(P.ToolError, match="There is no section"):
        T.apply(pg, "rename_section", {"section": "nope", "label": "x"}, ctx())


def test_edit_text_replaces_one_exact_piece_and_keeps_the_rest():
    pg = page()
    st = stage_of(PAGE, "tokens")
    md = st["blocks"][0]["md"]
    with pytest.raises(P.ToolError, match="isn't in this section"):
        T.apply(pg, "edit_text", {"section": "tokens", "old": "words nobody wrote", "new": "x"}, ctx())
    piece = md[:40]
    T.apply(pg, "edit_text", {"section": "tokens", "old": piece, "new": "NEW WORDS"}, ctx())
    after = stage_of(pg.current, "tokens")
    # the section's other blocks (quiz, flipcards) stay, although new blocks of those kinds would be refused
    assert after["blocks"][0]["md"].startswith("NEW WORDS") and after["blocks"][1:] == st["blocks"][1:]


def test_blocks_are_checked_for_the_mode_and_the_format():
    pg = page()
    with pytest.raises(P.ToolError, match="playgrounds"):  # Explorer text never gets a new kind of control
        T.apply(pg, "add_block", {"section": "attention", "index": -1, "block": {"type": "slider", "var": "k", "label": "k", "min": 0, "max": 1, "step": 0.1, "value": 0}}, ctx())
    with pytest.raises(P.ToolError, match="block format"):
        T.apply(pg, "add_block", {"section": "attention", "index": -1, "block": {"type": "text"}}, ctx())
    T.apply(pg, "add_block", {"section": "attention", "index": 1, "block": {"type": "text", "md": "A short worked example."}}, ctx())
    assert stage_of(pg.current, "attention")["blocks"][1] == {"type": "text", "md": "A short worked example."}
    lite = page("lite-plus", LITE_PAGE)
    with pytest.raises(P.ToolError, match="Reader page"):
        T.apply(lite, "add_block", {"section": "attention", "index": -1, "block": {"type": "quiz", "questions": []}}, ctx())
    with pytest.raises(P.ToolError, match="no playgrounds"):
        T.apply(lite, "get_playground", {"section": "attention"}, ctx())


def test_set_blocks_keeps_the_real_playground_where_its_entry_stays():
    pg = page()
    blocks = [{"type": "text", "md": "Sampling, said simply."}, {"type": "widget", "title": "Temperature explorer"}]
    T.apply(pg, "set_blocks", {"section": "sampling", "blocks": blocks}, ctx())
    assert stage_of(pg.current, "sampling")["blocks"] == [{"type": "text", "md": "Sampling, said simply."}, PG]
    with pytest.raises(P.ToolError, match="left out"):
        T.apply(pg, "set_blocks", {"section": "sampling", "blocks": [{"type": "text", "md": "No playground."}]}, ctx())
    with pytest.raises(P.ToolError, match="Never write a widget"):
        T.apply(pg, "set_blocks", {"section": "tokens", "blocks": [{"type": "widget", "title": "Mine", "html": "<script></script>"}]}, ctx())


def test_structure_tools():
    pg = page()
    out = T.apply(pg, "add_section", {"after": "tokens", "label": "A quick recap", "blocks": [{"type": "text", "md": "Recap."}]}, ctx())
    new_id = pg.added[0]
    assert new_id in out and [s["id"] for s in pg.stages][1] == new_id
    T.apply(pg, "move_section", {"section": "summary", "after": "start"}, ctx())
    T.apply(pg, "remove_section", {"section": "loop"}, ctx())
    ids = [s["id"] for s in pg.stages]
    assert ids[0] == "summary" and "loop" not in ids and pg.moved == 1 and pg.removed == 1
    rev = pg.revision()
    assert rev["changed"] == [new_id] and rev["removed"] == 1 and rev["moved"] == 1
    assert [x["status"] for x in rev["steps"]] == ["done", "done", "done"] and rev["steps"][0]["section"] == new_id


def test_patch_playground_sets_values_and_is_checked(monkeypatch):
    seen = {}
    monkeypatch.setattr(T, "check_playground", lambda spec: seen.update(spec=spec) or {"problems": [], "facts": {"controls": []}, "layout": []})
    pg = page()
    T.apply(pg, "patch_playground", {"section": "sampling", "sets": [{"path": "components.s1.label", "value": "temp"}, {"path": "state.t", "value": 1}]}, ctx())
    spec = pe.read_playground_spec(stage_of(pg.current, "sampling")["blocks"][-1]["html"])
    assert spec["components"][0]["label"] == "temp" and spec["state"] == {"t": 1} and seen["spec"] == spec
    assert pg.widgets_changed == {"sampling"} and pg.facts["sampling"] == {"controls": []}
    monkeypatch.setattr(T, "check_playground", lambda spec: {"problems": ["c1 stays empty"], "facts": None})
    with pytest.raises(P.ToolError, match="c1 stays empty"):
        T.apply(page(), "patch_playground", {"section": "sampling", "sets": [{"path": "components.0.label", "value": "x"}]}, ctx())
    with pytest.raises(P.ToolError, match="No item with id"):
        T.apply(page(), "patch_playground", {"section": "sampling", "sets": [{"path": "components.zz.label", "value": "x"}]}, ctx())


def test_replace_and_add_playground_use_the_builder(monkeypatch):
    new = {**PG, "title": "Temperature and top-k"}
    monkeypatch.setattr(pe, "edit_widget", lambda *a: {"block": new, "changed": True, "facts": {"f": 1}, "plan": {}, "notes": []})
    pg = page()
    T.apply(pg, "replace_playground", {"section": "sampling", "instructions": "add a top-k slider"}, ctx())
    assert stage_of(pg.current, "sampling")["blocks"][-1] == new
    monkeypatch.setattr(pe, "edit_widget", lambda *a: {"block": PG, "changed": False, "notes": ["it failed its checks"]})
    with pytest.raises(P.ToolError, match="failed its checks"):
        T.apply(page(), "replace_playground", {"section": "sampling", "instructions": "x"}, ctx())
    with pytest.raises(P.ToolError, match="already has a playground"):
        T.apply(page(), "add_playground", {"section": "sampling", "instructions": "x"}, ctx())
    added = {**PAGE, "stages": [{**s, "blocks": [*s["blocks"], PG]} if s["id"] == "tokens" else s for s in PAGE["stages"]]}
    monkeypatch.setattr(pe, "add_widget", lambda *a: {"outline": added, "built": True, "notes": []})
    pg = page()
    T.apply(pg, "add_playground", {"section": "tokens", "instructions": "explore how text splits"}, ctx())
    assert pg.widgets_changed == {"tokens"} and stage_of(pg.current, "tokens")["blocks"][-1] == PG


def test_custom_asks_the_builder_for_its_own_drawing(monkeypatch):
    """Phase 3: custom: true adds PLAYGROUND_DRAWING_NOTE to the builder's instructions; without it nothing is added."""
    from explainer import data
    note = data.prompt("PLAYGROUND_DRAWING_NOTE")
    assert "svgEl(tag, attrs, text)" in note and "${" not in note
    seen: list[str] = []
    monkeypatch.setattr(pe, "edit_widget", lambda *a: seen.append(a[6]) or {"block": PG, "changed": True, "facts": None, "plan": {}, "notes": []})
    pg = page()
    T.apply(pg, "replace_playground", {"section": "sampling", "instructions": "draw the tokens on a wheel", "custom": True}, ctx())
    T.apply(page(), "replace_playground", {"section": "sampling", "instructions": "add a top-k slider"}, ctx())
    assert seen[0].startswith("draw the tokens on a wheel") and note in seen[0] and seen[1] == "add a top-k slider"
    assert pg.log[-1]["what"].endswith("with its own drawing")
    added = {**PAGE, "stages": [{**s, "blocks": [*s["blocks"], PG]} if s["id"] == "tokens" else s for s in PAGE["stages"]]}
    monkeypatch.setattr(pe, "add_widget", lambda *a: seen.append(a[5]) or {"outline": added, "built": True, "notes": []})
    T.apply(page(), "add_playground", {"section": "tokens", "instructions": "a robot arm", "custom": True}, ctx())
    assert note in seen[-1]
    assert all("custom" in t["input_schema"]["properties"] for t in T.definitions(False) if t["name"] in ("replace_playground", "add_playground"))


def _drawing_sets(draw: str) -> list[dict]:
    return [{"path": "state.t", "value": 1}, {"path": "components.s1.key", "value": "t"}, {"path": "components.s1.min", "value": 0.1},
            {"path": "components.s1.max", "value": 2}, {"path": "components.c1.draw", "value": draw}]


def test_patch_a_drawing_goes_through_the_real_checker():
    """A draw returning drawing(...) is checked by the node checker like any mark; a forbidden part is refused in words."""
    pg = page()
    good = "return [drawing([svgEl('circle', {cx: 100, cy: 60, r: 10 + 20 * state.t, fill: 'blue'}), svgEl('text', {x: 100, y: 110}, 'odds')])];"
    T.apply(pg, "patch_playground", {"section": "sampling", "sets": _drawing_sets(good)}, ctx())
    spec = pe.read_playground_spec(stage_of(pg.current, "sampling")["blocks"][-1]["html"])
    assert spec["components"][1]["draw"] == good and pg.widgets_changed == {"sampling"}
    bad = "return [drawing([svgEl('rect', {x: 1, y: 1, width: 5, height: 5 * state.t, onclick: 'x'})])];"
    with pytest.raises(P.ToolError, match='"onclick" is not allowed'):
        T.apply(page(), "patch_playground", {"section": "sampling", "sets": _drawing_sets(bad)}, ctx())


def test_ask_only_before_any_write():
    pg = page()
    T.apply(pg, "ask_user", {"question": "Which part?", "options": ["The text", "The playground"]}, ctx())
    assert pg.asked == {"text": "Which part?", "options": ["The text", "The playground"]} and pg.finished
    pg = page()
    T.apply(pg, "rename_section", {"section": "S1", "label": "Tokens"}, ctx())
    with pytest.raises(P.ToolError, match="already changed"):
        T.apply(pg, "ask_user", {"question": "Which?", "options": ["a", "b"]}, ctx())
    assert T.run_tool(page(), "nope", {}, ctx())["is_error"]


def test_tool_lists_per_mode():
    names = lambda lite: [t["name"] for t in T.definitions(lite)]  # noqa: E731
    assert "patch_playground" in names(False) and "patch_playground" not in names(True)
    assert names(False)[-1] == "finish" and "cache_control" in T.definitions(False)[-1]


# ---------- the editor loop ----------


def tool_use(name: str, inp: dict, i: int = 0) -> SimpleNamespace:
    d = {"type": "tool_use", "id": f"t{name}{i}", "name": name, "input": inp}
    return SimpleNamespace(**d, to_dict=lambda **k: d)


def msg(*blocks, stop: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=1000, output_tokens=200), stop_reason=stop,
                           content=list(blocks))


def stream_client(*replies, sent: list | None = None):
    queue = list(replies)

    class Stream:
        def __init__(self, **kw):
            if sent is not None:
                sent.append(kw)
            self.m = queue.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self.m
    return SimpleNamespace(messages=SimpleNamespace(stream=Stream))


def text(t: str) -> SimpleNamespace:
    d = {"type": "text", "text": t}
    return SimpleNamespace(**d, to_dict=lambda **k: d)


def plan(brief: str) -> dict:
    return {"sections": [], "steps": [], "v4": {"brief": brief}}


def test_the_editor_runs_tools_until_finish_and_replies_from_the_log(monkeypatch):
    sent: list = []
    client = stream_client(
        msg(tool_use("rename_section", {"section": "S2", "label": "From tokens to vectors"})),
        msg(tool_use("finish", {"note": "Renamed section 2."})),
        sent=sent)
    monkeypatch.setattr(reply, "write_text", lambda *a, **k: "I renamed *Tokens become vectors* to “From tokens to vectors”.")
    progress: list = []
    r = editor.run(client, UsageLog("x"), None, FOLLOW, COMPACT, plan("Rename S2 to 'From tokens to vectors'."), "rename section 2",
                   "medium", True, "playground", progress=progress.append)
    assert stage_of(r["outline"], "embeddings")["label"] == "From tokens to vectors"
    assert stage_of(r["outline"], "sampling")["blocks"][-1] == PG  # the real playground, not its placeholder
    assert r["revision"]["changed"] == ["embeddings"] and r["revision"]["reply"].startswith("I renamed")
    assert r["revision"]["steps"] == [{"label": 'Renamed "Tokens become vectors" → "From tokens to vectors"', "status": "done", "section": "embeddings"}]
    assert progress[0] == "Renaming a section…" and not r["pageFields"]
    first = json.loads(sent[0]["messages"][0]["content"][0]["text"])
    assert first["brief"].startswith("Rename S2") and first["page"]["sections"][3]["blocks"][-1]["type"] == "widget"
    assert "html" not in json.dumps(first["page"])  # playgrounds are read on demand
    assert sent[1]["messages"][2]["content"][0]["type"] == "tool_result" and not sent[1]["messages"][2]["content"][0]["is_error"]


def test_a_refused_call_goes_back_to_the_editor_to_correct(monkeypatch):
    sent: list = []
    client = stream_client(
        msg(tool_use("rename_section", {"section": "S99", "label": "x"})),
        msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"}), tool_use("finish", {"note": "ok"}, 1)),
        sent=sent)
    monkeypatch.setattr(reply, "write_text", lambda *a, **k: "Done.")
    r = editor.run(client, UsageLog("x"), None, FOLLOW, COMPACT, plan("rename"), "rename", "low", False, "playground")
    res = sent[1]["messages"][2]["content"][0]
    assert res["is_error"] and "There is no section" in res["content"]
    assert stage_of(r["outline"], "tokens")["label"] == "Tokens" and sent[0]["thinking"] == {"type": "disabled"}


def test_the_editor_stops_at_the_round_cap():
    sent: list = []
    calls = [msg(tool_use("get_section", {"section": "S1"}, i)) for i in range(3)] + [msg(text("Ran out."), stop="end_turn")]
    pg = page()
    editor.edit_loop(stream_client(*calls, sent=sent), UsageLog("x"), pg, "b", "r", ctx(), "low", True, rounds=3)
    assert len(sent) == 4 and sent[-1]["tool_choice"] == {"type": "none"} and pg.note == "Ran out."
    assert "last change" in sent[-1]["messages"][-1]["content"][-1]["text"]


def test_asking_ends_the_run_without_changes(monkeypatch):
    client = stream_client(msg(tool_use("ask_user", {"question": "Which part should be simpler?", "options": ["Section 2", "Section 4"]})))
    with pytest.raises(editor.Asked) as e:
        editor.run(client, UsageLog("x"), None, FOLLOW, COMPACT, plan("make it simpler"), "simpler", "low", True, "playground")
    m = str(e.value)
    assert m.startswith(editor.ASKED) and len(m) < 1000
    gen = Generation(status="error", error=m, steps={}, updated_at=1)
    out = history._apply_generation({"status": "generating"}, gen)
    assert out["status"] == "picking" and out["plan"]["question"]["text"] == "Which part should be simpler?"
    assert out["plan"]["question"]["options"][1] == {"label": "Section 4", "sections": [], "steps": []}


def test_nothing_changed_is_a_calm_reply(monkeypatch):
    client = stream_client(msg(tool_use("finish", {"note": "It already says that."})))
    monkeypatch.setattr(reply, "write_text", lambda *a, **k: "I checked: it already says that, so I left it as it is.")
    with pytest.raises(shared.Checked) as e:
        editor.run(client, UsageLog("x"), None, FOLLOW, COMPACT, plan("check S3"), "is section 3 right?", "low", True, "playground")
    assert str(e.value).startswith(shared.CHECKED + "I checked")


def test_a_section_that_fails_jevs_check_is_put_back_alone(monkeypatch):
    client = stream_client(
        msg(tool_use("rename_section", {"section": "S1", "label": "Tokens!"}), tool_use("rename_section", {"section": "S2", "label": "Vectors!"}, 1)),
        msg(tool_use("finish", {"note": "Renamed two."})))
    monkeypatch.setattr(editor.jev_check, "judge", lambda key, before, after, said, change, usage: ["fixes"] if after["id"] == "tokens" else [])
    monkeypatch.setattr(reply, "write_text", lambda *a, user, **k: user)
    r = editor.run(client, UsageLog("x"), "jev", FOLLOW, COMPACT, plan("rename"), "rename", "low", True, "playground")
    assert stage_of(r["outline"], "tokens")["label"] == "Text becomes tokens" and stage_of(r["outline"], "embeddings")["label"] == "Vectors!"
    assert r["revision"]["changed"] == ["embeddings"]
    assert [x["status"] for x in r["revision"]["steps"]] == ["undone", "done"]
    log = json.loads(r["revision"]["reply"])["change_log"]
    assert log[0]["status"] == "undone" and "didn't deal" in log[0]["why"]  # the reply is written from what really happened


def test_the_plain_reply_when_the_call_fails():
    entries = [{"what": "Renamed a section", "status": "done"}, {"what": "Rewrote the intro", "status": "undone", "why": "it lost a step"}]
    out = reply.write(None, UsageLog("x"), "r", "", entries, [])
    assert "Renamed a section" in out and "put back Rewrote the intro (it lost a step)" in out


# ---------- talk ----------


def talked(kind: str, brief: str = "", reply_: str = "", question=None, alternative: str = "") -> dict:
    return {"kind": kind, "brief": brief, "sections": [], "reply": reply_, "question": question or {"text": "", "options": []},
            "alternative": {"label": alternative}}


def store() -> Store:
    return Store({**index_of(PAGE), "v": "1", "ai": True})


def test_talk_answers_even_when_thinking_uses_the_whole_budget():
    """Nov 14: "are all graphs correct??" on a 4-playground page. Haiku's adaptive thinking used all of max_tokens and wrote no
    text, so json_parse("") failed and the reader saw "Something went wrong". Now: once more with thinking off."""
    sent: list[dict] = []
    replies = [msg(SimpleNamespace(type="thinking", thinking=""), stop="max_tokens"),
               msg(text(json.dumps(talked("answer", reply_="Two are right; one label is off the chart."))), stop="end_turn")]

    def create(**kw):
        sent.append(kw)
        return replies.pop(0)
    out = talk.plan(SimpleNamespace(messages=SimpleNamespace(create=create)), store(), PAGE, "are all graphs correct??", "playground", UsageLog("x"))
    assert out == {"kind": "answer", "answer": "Two are right; one label is off the chart."}
    assert "thinking" not in sent[0] and sent[1]["thinking"] == {"type": "disabled"} and sent[0]["max_tokens"] == talk.TALK_TOKENS


def test_talk_turns_a_change_into_one_brief():
    out = talk.plan(fake_client(talked("change", brief="Rename S2 to 'Vectors'.")), store(), PAGE, "rename section 2 to Vectors", "playground", UsageLog("x"))
    assert out == {"kind": "change", "sections": [], "steps": [], "v4": {"brief": "Rename S2 to 'Vectors'."}, "summary": ["Rename S2 to 'Vectors'."], "confirm": False}
    assert parse_plan({"sections": out["sections"], "steps": out["steps"], "v4": out["v4"]})["v4"]["brief"] == "Rename S2 to 'Vectors'."


def test_talk_answers_asks_and_refuses_kindly():
    usage = UsageLog("x")
    assert talk.plan(fake_client(talked("answer", reply_="Because $T$ flattens it.")), store(), PAGE, "why?", "lite-plus", usage) == {"kind": "answer", "answer": "Because $T$ flattens it."}
    q = {"text": "What would help most?", "options": [{"label": "Clearer wording"}, {"label": "A worked example"}]}
    out = talk.plan(fake_client(talked("ask", question=q)), store(), PAGE, "make it better", "playground", usage)
    assert out["kind"] == "ask" and [o["label"] for o in out["question"]["options"]] == ["Clearer wording", "A worked example"]
    out = talk.plan(fake_client(talked("cannot", reply_="A page can't play video.", alternative="Add a play button")), store(), PAGE, "add a video", "playground", usage)
    assert out == {"kind": "cannot", "reason": "A page can't play video.", "alternative": {"label": "Add a play button", "sections": [], "steps": []}}
    assert talk.plan(fake_client(RuntimeError("down")), store(), PAGE, "x", "playground", usage) == {"error": talk.SORRY}


def test_talk_marks_a_different_topic_for_the_new_explainer_button():
    """No router any more: the talk step is what says "a different topic", and newTopic is what makes the app offer a new
    explainer. Its reply is kept; a missing one gets a friendly default."""
    usage = UsageLog("x")
    reply_ = "That's a different topic. I can make a new explainer about it."
    out = talk.plan(fake_client(talked("new_page", reply_=reply_)), store(), PAGE, "now explain beam search", "playground", usage)
    assert out == {"kind": "cannot", "reason": reply_, "alternative": None, "newTopic": True}
    out = talk.plan(fake_client(talked("new_page")), store(), PAGE, "now explain beam search", "lite-plus", usage)
    assert out["newTopic"] is True and out["reason"]


def test_a_picked_option_and_undo_need_no_ai_call():
    boom = fake_client(RuntimeError("no call expected"))
    out = talk.plan(boom, store(), PAGE, "make it better", "playground", UsageLog("x"), option={"label": "Clearer wording", "sections": [], "steps": []})
    assert out["kind"] == "change" and "Clearer wording" in out["v4"]["brief"]
    assert talk.plan(boom, store(), PAGE, "undo", "playground", UsageLog("x")) == {"kind": "revert"}


def test_the_plan_route_uses_talk_only_with_the_switch(monkeypatch):
    monkeypatch.setattr(talk, "plan", lambda *a, **k: {"kind": "answer", "answer": "v4"})
    req = {"page": PAGE, "text": "why?", "quality": "playground"}
    monkeypatch.setenv("FOLLOWUP_V5", "0")  # v5 is the default (Nov 19 2026): v4 runs only with it off
    monkeypatch.setenv("FOLLOWUP_V4", "1")
    assert entry.handle(None, None, req)["answer"] == "v4"


# ---------- playground readings (Nov 13 2026) ----------

READ = {"runs": [{"label": "at the start", "readouts": {"Top token": 0.62}, "charts": {}}], "text": "- at the start:\n  readouts: Top token = 0.62"}


def test_try_playground_runs_it_at_the_settings_asked(monkeypatch):
    seen = {}
    monkeypatch.setattr(T, "read_playground", lambda spec, runs=None, *a: seen.update(spec=spec, runs=runs) or READ)
    out = T.apply(page(), "try_playground", {"section": "sampling", "runs": [{"set": {"t": 2}}, "junk"]}, ctx())
    assert out == READ["text"] and seen["runs"] == [{"set": {"t": 2}}] and seen["spec"]["title"] == "Temperature explorer"
    T.apply(page(), "try_playground", {"section": "sampling"}, ctx())
    assert seen["runs"] is None  # the default settings
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: None)
    with pytest.raises(P.ToolError, match="couldn't be run"):
        T.apply(page(), "try_playground", {"section": "sampling"}, ctx())
    assert "try_playground" in [t["name"] for t in T.definitions(False)] and "try_playground" not in [t["name"] for t in T.definitions(True)]


def test_a_playground_change_answers_with_what_it_now_shows_and_jevs_check(monkeypatch):
    monkeypatch.setattr(T, "check_playground", lambda spec: {"problems": [], "facts": {}, "layout": []})
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: READ)
    asked = []
    monkeypatch.setattr(T, "readings_contradict", lambda key, st, shown, usage: asked.append(shown) or None)
    out = T.apply(page(), "patch_playground", {"section": "sampling", "sets": [{"path": "state.t", "value": 1}]}, T.Ctx(None, UsageLog("x"), "jev"))
    assert "It now shows:\n- at the start" in out and "disagree" not in out
    assert asked[0] == [{"title": "Temperature explorer", "readings": READ["text"]}]
    monkeypatch.setattr(T, "readings_contradict", lambda *a: "a number the text states isn't what its playground shows")
    out = T.apply(page(), "patch_playground", {"section": "sampling", "sets": [{"path": "state.t", "value": 1}]}, T.Ctx(None, UsageLog("x"), "jev"))
    assert "The text and the playground disagree: a number the text states" in out


def test_a_text_change_with_a_number_beside_a_playground_gets_a_reminder():
    pg = page()
    out = T.apply(pg, "add_block", {"section": "sampling", "index": 0, "block": {"type": "text", "md": "At T = 2 the top token drops to 30%."}}, ctx())
    assert "try_playground" in out
    out = T.apply(pg, "add_block", {"section": "tokens", "index": 0, "block": {"type": "text", "md": "At T = 2 it drops."}}, ctx())
    assert "try_playground" not in out  # no playground in that section
    out = T.apply(pg, "add_block", {"section": "sampling", "index": 0, "block": {"type": "text", "md": "No numbers here."}}, ctx())
    assert "try_playground" not in out


def _edited(sid: str = "sampling") -> P.Page:
    pg = page()
    T.apply(pg, "add_block", {"section": sid, "index": 0, "block": {"type": "text", "md": "At T = 2 the top token drops to 30%."}}, ctx())
    return pg


def test_a_change_that_brings_in_a_contradiction_is_put_back(monkeypatch):
    monkeypatch.setattr(editor.jev_check, "judge", lambda *a: [])
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: READ)
    # after the change the text disagrees with the playground; before it agreed
    monkeypatch.setattr(editor, "readings_contradict", lambda key, st, shown, usage: "a number the text states isn't what its playground shows" if "30%" in json.dumps(st) else None)
    pg = _edited()
    notes = editor.check_changes(pg, UsageLog("x"), "jev", "r", "b")
    assert stage_of(pg.current, "sampling") == stage_of(PAGE, "sampling") and not notes
    assert pg.log[-1]["status"] == "undone" and "after the change" in pg.log[-1]["why"]


def test_a_contradiction_that_was_already_there_is_kept_as_a_note(monkeypatch):
    monkeypatch.setattr(editor.jev_check, "judge", lambda *a: [])
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: READ)
    monkeypatch.setattr(editor, "readings_contradict", lambda *a: "a trend or comparison the text describes isn't what its playground shows")
    pg = _edited()
    notes = editor.check_changes(pg, UsageLog("x"), "jev", "r", "b")
    assert "30%" in json.dumps(stage_of(pg.current, "sampling")) and pg.log[-1]["status"] == "done"
    assert notes and "still do not fully match" in notes[0]


def test_more_than_check_max_changed_sections_still_get_checked(monkeypatch):
    judged = []
    monkeypatch.setattr(editor.jev_check, "judge", lambda key, before, after, *a: judged.append(after["id"]) or [])
    pg = page()
    many = {**PAGE, "stages": [*PAGE["stages"], *[{**PAGE["stages"][0], "id": f"extra{i}", "label": f"Extra {i}"} for i in range(5)]]}
    pg = page(base=many)
    for st in pg.stages:
        T.apply(pg, "rename_section", {"section": st["id"], "label": st["label"] + "!"}, ctx())
    assert len(pg.changed_ids()) > shared.CHECK_MAX
    editor.check_changes(pg, UsageLog("x"), "jev", "r", "b")
    assert judged == pg.changed_ids()[:shared.CHECK_MAX]


def test_talk_sees_what_each_playground_shows_on_explorer_pages_only(monkeypatch):
    monkeypatch.setattr(talk, "read_playground", lambda *a, **k: READ)
    sent: list = []
    talk.plan(fake_client(talked("answer", reply_="ok"), sent=sent), store(), PAGE, "is the playground right?", "playground", UsageLog("x"))
    assert "What each playground really shows" in sent[0] and 'S4 "' in sent[0] and "Top token = 0.62" in sent[0]
    sent.clear()
    talk.plan(fake_client(talked("answer", reply_="ok"), sent=sent), store(), LITE_PAGE, "why?", "lite-plus", UsageLog("x"))
    assert "What each playground really shows" not in sent[0]


def test_an_answer_is_passed_on_in_the_history():
    out = shared.history_text(["x" * 450])
    assert "x" * 450 in out


def test_recent_turns_go_in_whole_and_older_ones_are_cut():
    long = "S4 matches. " + "y" * 900 + " S5 has one mismatch."  # what "fix that" points at sits at the end
    hist = [f"old{i} " + "z" * 1000 for i in range(4)] + [long] * shared.FULL_HISTORY
    out = shared.history_text(hist)
    assert out.count("S5 has one mismatch.") == shared.FULL_HISTORY
    assert "old0 " + "z" * (shared.OLD_HISTORY_CHARS - 5) in out and "z" * (shared.OLD_HISTORY_CHARS + 1) not in out


def test_the_history_total_is_capped_by_dropping_the_oldest_turns():
    hist = [f"t{i} " + "w" * 3000 for i in range(shared.FULL_HISTORY)]
    out = shared.history_text(hist)
    assert len(out) < shared.HISTORY_TOTAL + 200
    assert "t0 " not in out and f"t{shared.FULL_HISTORY - 1} " in out


# ---------- the whole page for the talk step, and the earlier answer for the editor (Nov 13 2026) ----------

FULL_PAGE = {**PAGE, "stages": [*PAGE["stages"], {"id": "extra", "label": "Extra", "blurb": "More.", "blocks": [
    {"type": "table", "title": "Scores", "columns": ["Model", "Train", "New"], "rows": [["Small", "0.70", "0.69"], ["Big", "0.88", "-0.32"]]},
    {"type": "cards", "variant": "mistake", "title": "Common confusion", "items": [{"title": "Bigger is always better", "md": "Not on new data."}]},
    {"type": "quiz", "questions": [{"q": "Which one overfits?", "choices": ["Small", "Big"], "answer": 1, "explain": "Its new-data score drops."}]},
    {"type": "tabs", "tabs": [{"label": "What you see", "md": "Both scores are low."}]}]}]}


def test_the_page_text_has_every_block_in_full():
    from explainer.modes.followup.cards import page_text
    out = page_text(FULL_PAGE)
    assert "Big | 0.88 | -0.32" in out and "What you see: Both scores are low." in out
    assert "each title is the wrong belief" in out and "Bigger is always better: Not on new data." in out
    assert "Right: Big" in out and f'S{len(FULL_PAGE["stages"])} "Extra"' in out


def test_talk_reads_the_whole_page():
    sent: list = []
    talk.plan(fake_client(talked("answer", reply_="ok"), sent=sent), Store({**index_of(FULL_PAGE), "v": "1", "ai": True}), FULL_PAGE,
              "is the table right?", "playground", UsageLog("x"))
    assert "Big | 0.88 | -0.32" in sent[0] and "Right: Big" in sent[0]


ANSWER_TURN = '"check the page": answered in the chat, nothing changed: "S4 matches. S5 has one mismatch: the gap is widest at degree 1."'


def test_a_change_right_after_an_answer_carries_that_answer_to_the_editor():
    out = talk.plan(fake_client(talked("change", brief="Fix S5's gap sentence.")), store(), PAGE, "fix that", "playground", UsageLog("x"),
                    history=['"rename S1": made version 2. Reply: "Done."', ANSWER_TURN])
    assert out["v4"] == {"brief": "Fix S5's gap sentence.", "earlier": ANSWER_TURN}
    assert parse_plan({"sections": [], "steps": [], "v4": out["v4"]})["v4"]["earlier"] == ANSWER_TURN
    after_change = talk.plan(fake_client(talked("change", brief="Shorter intro.")), store(), PAGE, "shorter intro", "playground", UsageLog("x"),
                             history=[ANSWER_TURN, '"fix that": made version 3. Reply: "Fixed."'])
    assert "earlier" not in after_change["v4"]


def test_the_editor_gets_the_earlier_answer_next_to_the_brief():
    sent: list = []
    editor.edit_loop(stream_client(msg(text("Done."), stop="end_turn"), sent=sent), UsageLog("x"), page(), "b", "r", ctx(), "low", True,
                     earlier=ANSWER_TURN)
    first = json.loads(sent[0]["messages"][0]["content"][0]["text"])
    assert first["earlier_answer"] == ANSWER_TURN and first["brief"] == "b"
