"""Follow-ups v5 (modes/followup/v5/): the plan step (code only), the one agent (answers, asks, changes any base version with
version-aware reads and restores, start_from, the checks as feedback and the final guard), the base page built on the
server, the version store's parent, and the routing.
Haiku, Jev and the playground runs are faked. Imports neither v3 nor v4 (test_modes_boundary runs this file with both
deleted)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from explainer import data, history, quota, versions
from explainer.db import Database, Explainer, Generation, PageVersion, User
from explainer.followup import parse_plan
from explainer.js import js_slice
from explainer.modes.followup import entry
from explainer.modes.followup import main_file as mf
from explainer.modes.followup import run as followup_run
from explainer.modes.followup.shared import Checked, NewTopic
from explainer.modes.followup.v5 import agent, base as B, editor, intent, page as P, reply, review, talk, tools as T
from explainer.pricing import UsageLog
from followup_v3_data import COMPACT, FOLLOW, PAGE, PG, SAMPLE

PG2 = {**PG, "title": "Temperature explorer", "html": PG["html"].replace("temperature", "heat")}
V1 = PAGE
V2 = {**PAGE, "stages": [{**s, "label": "Words become tokens"} if s["id"] == "tokens" else s for s in PAGE["stages"]]}
V3 = {**V2, "stages": [s for s in V2["stages"] if s["id"] != "loop"]}


# ---------- fakes ----------


def tool_use(name: str, inp: dict, i: int = 0) -> SimpleNamespace:
    d = {"type": "tool_use", "id": f"t{name}{i}", "name": name, "input": inp}
    return SimpleNamespace(**d, to_dict=lambda **k: d)


def text(t: str) -> SimpleNamespace:
    d = {"type": "text", "text": t}
    return SimpleNamespace(**d, to_dict=lambda **k: d)


def msg(*blocks, stop: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=1000, output_tokens=200), stop_reason=stop,
                           content=list(blocks))


def stream_client(*replies, sent: list | None = None):
    """Streamed Haiku: answers with the next reply. No messages.create, so the main file's AI part fails open."""
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


def finish(reply_: str = "Done.", i: int = 9, **kw) -> SimpleNamespace:
    return msg(tool_use("finish", {"reply": reply_, **kw}, i))


def report(problems: list | None = None, suggestions: list | None = None) -> SimpleNamespace:
    return msg(tool_use("report", {"problems": problems or [], "suggestions": suggestions or []}, 7))


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No node runs and no reply call."""
    monkeypatch.setattr(T, "read_playground", lambda *a, **k: None)
    monkeypatch.setattr(mf, "read_playground", lambda *a, **k: None)
    monkeypatch.setattr(reply, "write_text", lambda *a, **k: "Done, renamed it.")
    monkeypatch.delenv("FOLLOWUP_V5_FULLPAGE", raising=False)


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        s.add(User(id="u1", email="a@x.io"))
        s.flush()
        for rid in ("r1", "r2", "r3"):
            s.add(Explainer(user_id="u1", id=rid, thread="r1", status="done", title="t", data={"request": {"quality": "playground"}},
                            created_at=1, updated_at=1))
    with d.session() as s:
        for i, (rid, page) in enumerate((("r1", V1), ("r2", V2), ("r3", V3))):
            versions.save_version(s, "u1", line="r1", thread="r1", record_id=rid, parent=i or None, kind="change" if i else "new", page=page,
                                  log={"request": ["first", "rename tokens", "remove the loop"][i]})
    return d


def plan_v5(**kw) -> dict:
    return {"sections": [], "steps": [], "v5": {"agent": True, **kw}}


# ---------- the plan step: code only ----------


def test_a_long_answers_fix_list_reaches_the_agent_whole(db):
    """An answer can be 6000 characters (MESSAGE_CHARS); its last fix must survive every cap on the way to the agent after
    "Yes, make these fixes": the plan request, talk.plan, followup.parse_plan and the agent's first message."""
    fixes = "".join(f"- Fix {i}: set the readout in S{i % 8 + 1} to three decimals so it matches the text.\n" for i in range(70))
    answer = js_slice("I checked every playground.\n" + fixes, 0, agent.MESSAGE_CHARS - 40) + "\n- LAST FIX: rename S2."
    turn = f'"can you check all of it?": answered in the chat, nothing changed: "{answer}"'
    req = entry.parse_plan_request({"text": "Yes, make the fixes you listed.", "quality": "playground",
                                    "followUp": {**FOLLOW, "design": SAMPLE["design"]},
                                    "option": {"label": "Yes, make the fixes you listed."}, "history": [turn]})
    plan = talk.plan(req["text"], req.get("option"), history=req["history"])
    assert plan["v5"]["intent"] == "change"  # a picked option: no Jev call
    kept = parse_plan({"sections": [], "steps": [], "v5": plan["v5"]})["v5"]
    assert "LAST FIX: rename S2." in kept["earlier"] and "LAST FIX: rename S2." in kept["conversation"]
    sent: list = []
    run_agent(db, msg(tool_use("rename_section", {"section": "S2", "label": "x"})), finish("Done."), report(), sent=sent,
              plan={"sections": [], "steps": [], "v5": kept})
    assert "LAST FIX: rename S2." in first_text(sent)


def test_the_plan_step_is_code_only():
    assert talk.plan("undo that") == {"kind": "revert"}
    hist = ['"is S2 right?": answered in the chat, nothing changed: "S2 says 3 heads, the playground has 4."']
    out = talk.plan("fix that", history=hist)
    assert out["kind"] == "change" and not out["confirm"] and out["v5"]["agent"] is True
    assert "is S2 right?" in out["v5"]["conversation"] and out["v5"]["earlier"].startswith('"is S2 right?"')
    out = talk.plan("make it better", option={"label": "Clearer wording", "sections": [], "steps": []}, extra="in S2")
    assert out["v5"] == {"agent": True, "chose": "Clearer wording", "extra": "in S2", "intent": "change"}  # a picked option: no Jev call
    assert talk.plan("undo", option={"label": "x"})["kind"] == "change"  # a picked option is never an undo
    assert talk.handle(None, {"text": "what is S1?"})["usd"] == 0.0


def test_the_plan_route_picks_v5_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(talk, "handle", lambda client, req, db, user_id, jev_key=None: seen.update(db=db, user=user_id) or {"kind": "revert", "usd": 0})
    monkeypatch.delenv("FOLLOWUP_V5", raising=False)  # the default follow-up (Nov 19 2026), with no switch set
    follow = {**FOLLOW, "design": SAMPLE["design"]}
    req = entry.parse_plan_request({"text": "undo", "quality": "playground", "followUp": follow, "baseId": "r3"})
    assert req["baseId"] == "r3"
    assert entry.handle(None, None, req, db="DB", user_id="u1") == {"kind": "revert", "usd": 0} and seen == {"db": "DB", "user": "u1"}
    assert entry.parse_plan_request({"text": "x", "quality": "playground", "followUp": follow, "baseId": "../bad"}) == "Invalid record id."


def test_parse_plan_keeps_v5():
    p = parse_plan({"sections": [], "steps": [], "v5": {"agent": True, "conversation": "c", "chose": "o", "extra": "e", "earlier": "a",
                                                      "intent": "answer", "junk": 1}})
    assert p["v5"] == {"agent": True, "intent": "answer", "conversation": "c", "chose": "o", "extra": "e", "earlier": "a"}
    assert "intent" not in parse_plan({"sections": [], "steps": [], "v5": {"agent": True, "intent": "maybe"}})["v5"]
    p = parse_plan({"sections": [], "steps": [], "v5": {"brief": "b", "base": 2, "refs": [1, "x", True, 3], "earlier": "e"}})  # an older plan
    assert p["v5"] == {"brief": "b", "base": 2, "refs": [1, 3], "earlier": "e"}
    assert "v5" not in (parse_plan({"sections": [], "steps": [], "v5": {"agent": "yes"}}) or {})
    assert followup_run.is_agent({"followUp": {"kind": "revise", "plan": plan_v5()}})
    assert not followup_run.is_agent({"followUp": {"kind": "revise", "plan": {"sections": [], "steps": []}}})


def jev_says(monkeypatch, p: float | None, seen: list | None = None) -> None:
    """Jev's answer to the intent question (None: Jev fails)."""
    from explainer.jev import JevError

    def ask(key, state, questions):
        if seen is not None:
            seen.append(state)
        if p is None:
            raise JevError("Jev unreachable")
        return {"answers": {"wants_change": {"type": "noul", "p": p}}, "model": "jev-1.13.0", "usage": {"input_tokens": 100}}
    monkeypatch.setattr(intent, "jev_ask", ask)


def test_jev_decides_whether_the_message_asks_for_a_change(monkeypatch):
    seen: list = []
    hist = ['"check S2": answered in the chat, nothing changed: "S2 has a typo. Want me to fix it?"']
    jev_says(monkeypatch, 0.9, seen)
    assert talk.plan("yeah fix that", history=hist, jev_key="k")["v5"]["intent"] == "change"
    assert seen[0]["reader_message"] == "yeah fix that" and "Want me to fix it?" in seen[0]["previous_turn"]
    jev_says(monkeypatch, 0.55)  # unsure counts as only asking (a wrong "no" costs one "yes"; a wrong "yes" a version)
    assert talk.plan("anything to change more??", jev_key="k")["v5"]["intent"] == "answer"
    jev_says(monkeypatch, None)  # Jev failed: the agent decides
    assert "intent" not in talk.plan("anything to change more??", jev_key="k")["v5"]
    assert "intent" not in talk.plan("anything to change more??")["v5"]  # no key: no call
    out = talk.handle(None, {"text": "check every graph"}, jev_key=None)
    assert out["usd"] == 0.0


# ---------- the agent ----------


def run_agent(db, *replies, sent=None, base=V3, version=3, jev=None, plan=None, request="rename S1", effort="medium", think=False,
              answer_only=False, quality="playground", paper=None, follow=None):
    return agent.run(stream_client(*replies, sent=sent), UsageLog("x"), jev, follow or {"kind": "revise"}, base, plan or plan_v5(), request,
                     effort, think, quality, store=P.Store(db, "u1", "r1", version), log_text="The page's versions: ...",
                     answer_only=answer_only, paper=paper)


def first_text(sent: list) -> str:
    return sent[0]["messages"][0]["content"][0]["text"]


def agent_calls(sent: list) -> list[dict]:
    return [s for s in sent if "check_changes" in [t["name"] for t in s["tools"]]]


def test_an_answer_reads_first_and_changes_nothing(db, monkeypatch):
    monkeypatch.setenv("FOLLOWUP_V5_FULLPAGE", "0")
    sent: list = []
    with pytest.raises(Checked) as e:
        run_agent(db, msg(tool_use("get_section", {"section": "S1", "version": 1})), finish("It covered *tokens*."), sent=sent,
                  request="what was S1 called in version 1?", plan=plan_v5(conversation="The reader's earlier messages: - hi"))
    assert str(e.value) == "✓ It covered *tokens*."
    first = first_text(sent)
    assert "Main file of that version:" in first and "S1 · tokens" in first and "The page's versions: ..." in first
    assert "The reader's earlier messages: - hi" in first and first.endswith("The reader's new message: what was S1 called in version 1?")
    assert "The page's full text" not in first  # switched off
    result = sent[1]["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and "(version 1)" in result["content"] and "Text becomes tokens" in result["content"]
    assert [s["output_config"]["effort"] for s in sent] == ["medium", "medium"]  # the reader's Effort, reading included


def test_the_full_page_is_sent_by_default(db):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, finish("x"), sent=sent)
    assert "The page's full text:" in first_text(sent)


def test_answer_and_change_in_one_turn_at_one_effort(db):
    sent: list = []
    out = run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"})),
                    finish("S1 is about splitting text; I renamed it *Tokens*."), report(), sent=sent, think=True)
    assert out["outline"]["stages"][0]["label"] == "Tokens"
    assert out["revision"]["reply"] == "S1 is about splitting text; I renamed it *Tokens*."  # the agent's own words
    calls = agent_calls(sent)
    assert [s["output_config"]["effort"] for s in calls] == ["medium", "medium"]  # the reader's Effort throughout, no step
    assert all("thinking" not in s for s in calls)  # the page's Thinking setting, never changed mid-loop


def test_ask_before_a_change_and_not_after(db):
    with pytest.raises(editor.Asked) as e:
        run_agent(db, msg(tool_use("ask_user", {"question": "Which section?", "options": ["S1", "S2"]})))
    assert str(e.value).startswith("? ") and json.loads(str(e.value)[2:])["question"]["options"] == ["S1", "S2"]
    sent: list = []
    out = run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"}), tool_use("ask_user", {"question": "q", "options": ["a", "b"]}, 1)),
                    finish("Renamed it. Want S2 shorter too?"), report(), sent=sent)
    refused = sent[1]["messages"][2]["content"][1]
    assert refused["is_error"] and "last sentence of your reply" in refused["content"]
    assert out["revision"]["reply"].endswith("Want S2 shorter too?")


def test_a_different_topic(db):
    with pytest.raises(NewTopic) as e:
        run_agent(db, msg(tool_use("new_topic", {"reply": "That's a new topic: beam search."})))
    assert str(e.value) == "↗ That's a new topic: beam search."


def test_a_different_topic_carries_its_brief(db):
    with pytest.raises(NewTopic) as e:
        run_agent(db, msg(tool_use("new_topic", {"reply": "That's beam search.", "brief": "Beam search, kept short"})))
    assert str(e.value) == '↗+ {"reply": "That\'s beam search.", "brief": "Beam search, kept short"}'


def test_start_from_then_finish_goes_back_to_that_version(db):
    out = run_agent(db, msg(tool_use("start_from", {"version": 1})), finish("Here is version 1 again."))
    assert [s["id"] for s in out["outline"]["stages"]] == [s["id"] for s in V1["stages"]] and out["startFrom"] == 1
    assert out["revision"]["steps"] == [{"label": "Went back to version 1", "status": "done"}] and out["follow"]["outline"]
    sent: list = []
    run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "x"}), tool_use("start_from", {"version": 1}, 1)), finish("ok"), report(),
              sent=sent)
    assert "only before any change" in sent[1]["messages"][2]["content"][1]["content"]


def test_the_first_finish_gets_the_problems_and_the_agent_fixes_them(db):
    problem = {"section": "embeddings", "cause": "tokens", "what": "still says 'Text becomes tokens'"}
    sent: list = []
    out = run_agent(db, msg(tool_use("rename_section", {"section": "tokens", "label": "Tokens"})), finish("ok"), report([problem]),
                    msg(tool_use("rename_section", {"section": "embeddings", "label": "Vectors from tokens"}, 1)), finish("Renamed both.", 2), sent=sent)
    labels = {s["id"]: s["label"] for s in out["outline"]["stages"]}
    assert labels["tokens"] == "Tokens" and labels["embeddings"] == "Vectors from tokens"  # nothing put back
    refused = sent[3]["messages"][4]["content"][0]  # (the messages list is shared: index it, never [-1])
    assert refused["is_error"] and "still says 'Text becomes tokens'" in refused["content"]
    assert out["revision"]["reply"] == "Renamed both."


def write_spy(monkeypatch) -> dict:
    seen: dict = {}
    monkeypatch.setattr(agent.reply_mod, "write", lambda client, usage, request, note, entries, notes, suggestions=None:
                        seen.update(note=note, notes=notes) or "From the log.")
    return seen


@pytest.mark.parametrize("cause", ["tokens", ""])
def test_a_review_problem_left_alone_is_told_never_put_back(db, monkeypatch, cause):
    """The review's problems are feedback and notes: putting their cause back undid the fix the reader asked for."""
    seen = write_spy(monkeypatch)
    problem = {"section": "embeddings", "cause": cause, "what": "broken by the rename"}
    out = run_agent(db, msg(tool_use("rename_section", {"section": "tokens", "label": "Tokens"}),
                            tool_use("rename_section", {"section": "sampling", "label": "Picking a word"}, 1)),
                    finish("ok"), report([problem]), finish("Done anyway.", 2))
    labels = {s["id"]: s["label"] for s in out["outline"]["stages"]}
    assert labels["tokens"] == "Tokens" and labels["sampling"] == "Picking a word"  # both kept
    assert [s["status"] for s in out["revision"]["steps"]] == ["done", "done"]
    assert seen["notes"] == [f'Still to fix in "{labels["embeddings"]}": broken by the rename']
    assert seen["note"] == "Done anyway." and out["revision"]["reply"] == "From the log."  # nothing put back: its own words kept


def test_every_change_put_back_is_a_calm_reply_from_the_log_alone(db, monkeypatch):
    """Only Jev's numbers check puts a change back; then the reply comes from the log, never the agent's claim ("I've
    reverted the chart" when nothing changed)."""
    seen = write_spy(monkeypatch)
    monkeypatch.setattr(T, "playground_readings", lambda page_, st_, runs=None: [])
    monkeypatch.setattr(editor, "readings_contradict",
                        lambda key, st_, readings, usage: "the text says it always rises" if st_.get("label") == "Picking a word" else "")
    with pytest.raises(Checked) as e:
        run_agent(db, msg(tool_use("rename_section", {"section": "sampling", "label": "Picking a word"})), finish("ok"), report(),
                  finish("I've renamed it.", 1), jev="k")
    assert str(e.value) == "✓ From the log." and seen["note"] == ""


@pytest.mark.parametrize("plan, answer_only, prefix", [(plan_v5(intent="answer"), False, "✓+ "), (plan_v5(intent="answer"), True, "✓ "),
                                                        (plan_v5(), False, "✓ ")])
def test_an_answer_listing_fixes_gets_the_yes_button_only_when_it_can_change(db, plan, answer_only, prefix):
    with pytest.raises(Checked) as e:
        run_agent(db, finish("S2 says 0.9, its playground shows 0.4. I'd fix the text.", offers_fixes=True), plan=plan,
                  request="can you check S2?", answer_only=answer_only)
    assert str(e.value).startswith(prefix + "S2 says 0.9")


def test_an_answer_offer_is_a_done_answer_with_its_button(db):
    rec = {"status": "generating", "prompt": {"text": "check S2"}}
    gen = Generation(user_id="u1", record_id="x", status="error", error="✓+ " + "A long answer. " * 120, steps={}, updated_at=1)
    out = history._apply_generation(rec, gen)
    assert out["status"] == "done" and out["offersFix"] is True and out["answer"].startswith("A long answer.")
    assert len(out["answer"]) > 1000  # no longer cut to the old 1000-character column


def test_a_cut_off_reply_is_dropped_and_the_agent_goes_on_in_smaller_steps(db):
    sent: list = []
    cut = msg(tool_use("set_blocks", {"section": "S1"}), stop="max_tokens")  # its input is incomplete: never applied
    out = run_agent(db, cut, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"}, 1)), finish("Renamed."), report(), sent=sent)
    assert [e["label"] for e in out["revision"]["steps"]] == ['Renamed "Words become tokens" → "Tokens"']
    assert out["revision"]["reply"] == "Renamed."
    messages = sent[1]["messages"]  # (shared with later calls: index it, never [-1])
    assert messages[0]["content"][-1]["text"] == agent.CUT_OFF  # told after the first message
    assert messages[1]["role"] == "assistant" and messages[1]["content"][0]["name"] == "rename_section"  # the cut reply never sent back
    assert sent[0]["max_tokens"] == agent.EDIT_TOKENS == 32_000


def test_two_cut_offs_keep_the_changes_already_made(db, monkeypatch):
    seen = write_spy(monkeypatch)
    cut = lambda i: msg(tool_use("set_blocks", {"section": "S2"}, i), stop="max_tokens")
    out = run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"})), cut(1), cut(2), report())
    assert next(s for s in out["outline"]["stages"] if s["id"] == "tokens")["label"] == "Tokens"  # kept, not lost
    assert any("too big to finish in one go" in n for n in seen["notes"]) and out["revision"]["reply"] == "From the log."


def test_cut_offs_before_any_change_say_so_plainly(db):
    from explainer.llm import ReaderError
    cut = lambda i: msg(tool_use("set_blocks", {"section": "S2"}, i), stop="max_tokens")
    with pytest.raises(ReaderError) as e:
        run_agent(db, cut(0), cut(1))
    assert str(e.value) == agent.TOO_LONG and not isinstance(e.value, Checked)


def test_text_is_cut_at_a_sentence_or_word_never_mid_word():
    s = "First sentence here. " + "word " * 100
    assert P.cut(s, 36) == "First sentence here."  # a sentence end in the second half: cut there
    assert P.cut(s, 60).endswith("word…")  # too early (under half): cut at a word instead
    assert P.cut("alpha beta gamma delta", 15) == "alpha beta…" and P.cut("short", 15) == "short"
    assert P.whole("x" * 50, 60) == "x" * 50
    assert P.whole("x" * 80, 60) == "x" * 60 + "\n…(cut here: 20 more characters not shown)"  # never silently


def test_longer_questions_options_builder_instructions_and_version_log():
    q = "Which of these should I change first, given that the second section and its playground disagree? " * 5
    asked = json.loads(editor.asked_message({"text": q, "options": ["Option " + "long words " * 25, "b"]})[len(editor.ASKED):])
    assert 240 < len(asked["question"]["text"]) <= editor.QUESTION_CHARS and asked["question"]["text"].endswith("?")
    assert 100 < len(asked["question"]["options"][0]) <= editor.OPTION_CHARS + 1 and asked["question"]["options"][0].endswith("…")
    what, _ = T._builder_what({"instructions": "Add a table. " * 300}, "x")
    assert 1500 < len(what) <= T.BUILDER_CHARS and what.endswith(".")
    entry_ = {"version": 2, "parent": 1, "request": "fix it", "reply": "I fixed the table. " * 30, "suggestions": ["Want a chart? " * 20]}
    text = B.log_text([entry_], 2)
    assert ("I fixed the table. " * 30).strip() in text  # the whole reply (570 characters), no longer cut to 200
    assert ("Want a chart? " * 20).strip()[:300] in text  # nor the offer to 120


def test_undo_phrasings_need_no_ai():
    from explainer.modes.followup.shared import is_undo
    assert all(is_undo(t) for t in ("so revert the changes", "ok undo those changes", "yeah just undo it", "go back to the previous version"))
    assert not any(is_undo(t) for t in ("revert the colour change in S3", "go back to version 3", "undo the table in section 2"))


def test_the_budget_ends_with_a_text_reply(db, monkeypatch):
    monkeypatch.setitem(agent.BUDGET, "medium", (1, 1.0))
    sent: list = []
    with pytest.raises(Checked) as e:
        run_agent(db, msg(tool_use("get_section", {"section": "S1"})), msg(text("S1 explains tokens."), stop="end_turn"), sent=sent)
    assert str(e.value) == "✓ S1 explains tokens."
    assert sent[0]["tool_choice"] == {"type": "auto"} and sent[1]["tool_choice"] == {"type": "none"}
    assert "last step" in sent[1]["messages"][2]["content"][-1]["text"]
    monkeypatch.setenv("FOLLOWUP_MAX_USD", "0.5")
    assert agent.budget("medium") == (1, 0.5) and agent.budget("high") == (40, 0.5)
    monkeypatch.delenv("FOLLOWUP_MAX_USD")
    assert agent.budget("low") == (20, 0.06)  # enough rounds for "fix all" (~2 a fix); the $ cap is the real limit


def test_answer_only_refuses_every_change(db):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "x"}), tool_use("start_from", {"version": 1}, 1)),
                  finish("Changes are back tomorrow."), sent=sent, answer_only=True)
    assert "can't be changed" in first_text(sent)
    assert all(r["is_error"] and "can't be changed today" in r["content"] for r in sent[1]["messages"][2]["content"])


def test_a_message_that_only_asks_changes_nothing(db):
    sent: list = []
    with pytest.raises(Checked) as e:
        run_agent(db, msg(tool_use("try_playground", {"section": "S1"})),
                  msg(tool_use("patch_playground", {"section": "S1", "sets": [{"path": "plan.title", "value": "x"}]}, 1)),
                  finish("Its numbers are right; the dots have no key.", suggestions=["Add a key to the dot chart?"]),
                  sent=sent, request="can you check the dot chart?", plan=plan_v5(intent="answer"))
    assert str(e.value).startswith("✓ Its numbers are right") and "Add a key to the dot chart?" in str(e.value)
    assert "doesn't ask for a change" in first_text(sent)
    refused = sent[2]["messages"][4]["content"][0]
    assert refused["is_error"] and "doesn't ask for a change" in refused["content"]


def test_a_reader_page_stays_low_and_has_no_playground_tools(db):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, finish("x"), sent=sent, quality="lite-plus", effort="high", think=True)
    assert sent[0]["output_config"]["effort"] == "low" and sent[0]["thinking"] == {"type": "disabled"}
    assert "patch_playground" not in [t["name"] for t in sent[0]["tools"]]


def test_title_only_change_is_not_reviewed(db):
    sent: list = []
    out = run_agent(db, msg(tool_use("set_page_meta", {"title": "New title"})), finish("New title."), sent=sent)
    assert len(sent) == 2 and "suggestions" not in out["revision"]


def test_only_the_agents_ideas_reach_the_reader_never_the_reviews(db):
    """The review only reports what a change broke: its ideas made a new list under every reply, so "fix all" never ended."""
    out = run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"})), finish("Renamed.", suggestions=["Want a table?"]),
                    report(suggestions=["Want S2 simpler too?"]))
    assert out["revision"]["suggestions"] == ["Want a table?"]
    assert out["revision"]["reply"] == "Renamed.\n\nIdeas, if you want more:\n- Want a table?"
    assert "Want S2 simpler" not in out["revision"]["reply"]


def test_at_most_two_ideas_none_offered_before_and_cut_at_a_sentence(db):
    with db.session() as s:  # v3 offered one already
        row = s.get(PageVersion, ("u1", "r1", 3))
        row.log = {**row.log, "suggestions": ["Want S2 simpler too?"]}
    long = "Add a value table under S3. " + "It lists every cell of the grid so you can compare. " * 20
    plan = plan_v5(conversation="- tokens: answered in the chat, nothing changed: Ideas, if you want more:\n- Want a quiz on S4?")
    out = run_agent(db, msg(tool_use("rename_section", {"section": "S1", "label": "Tokens"})),
                    finish("Renamed.", suggestions=["Want S2 simpler too?", "want a QUIZ on S4", long, "Want a chart in S5?", "One more?"]),
                    report(), plan=plan)
    got = out["revision"]["suggestions"]
    assert len(got) == 2 and got[0].startswith("Add a value table under S3.") and got[1] == "Want a chart in S5?"  # 2 offered before
    assert len(got[0]) <= agent.SUGGESTION_CHARS and got[0].endswith(".")  # cut at a sentence, not mid-word


def test_check_changes_reports_without_undoing(db):
    problem = {"section": "embeddings", "cause": "tokens", "what": "needs the new name"}
    sent: list = []
    out = run_agent(db, msg(tool_use("rename_section", {"section": "tokens", "label": "Tokens"})), msg(tool_use("check_changes", {}, 1)),
                    report([problem]), msg(tool_use("rename_section", {"section": "embeddings", "label": "Token vectors"}, 2)),
                    finish("Done."), report(), sent=sent)
    assert "needs the new name" in sent[3]["messages"][4]["content"][0]["content"]
    assert {s["id"]: s["label"] for s in out["outline"]["stages"]}["tokens"] == "Tokens"


def test_jev_never_undoes_a_change_for_not_matching_the_request(db, monkeypatch):
    """Jev checks numbers only: it saw only the last message, so after "fix those" it undid the fixes the reader agreed to."""
    from explainer.modes.followup import check as jev_check
    monkeypatch.setattr(jev_check, "judge", lambda *a, **k: pytest.fail("Jev no longer judges the request"))
    out = run_agent(db, msg(tool_use("rename_section", {"section": "tokens", "label": "Tokens"})), finish("Renamed it."), report(),
                    jev="k", request="fix those")
    assert {s["id"]: s["label"] for s in out["outline"]["stages"]}["tokens"] == "Tokens"
    assert [s["status"] for s in out["revision"]["steps"]] == ["done"] and out["revision"]["reply"] == "Renamed it."


def test_the_numbers_check_still_puts_back_a_contradiction(monkeypatch):
    page = P.Page(V3, "playground")
    si, st = page.stage("sampling")
    page.set_stage(si, {**st, "blocks": [{"type": "text", "md": "It always picks 0.9."}, *st["blocks"][1:]]})
    page.record("sampling", "edit_text", "Reworded part of sampling")
    monkeypatch.setattr(T, "playground_readings", lambda page_, st_, runs=None: [])
    monkeypatch.setattr(editor, "readings_contradict",
                        lambda key, st_, readings, usage: "the text says 0.9, it shows 0.4" if "0.9" in json.dumps(st_) else "")
    found = editor.problems(page, UsageLog("x"), "k")
    assert found == [{"section": "sampling", "what": "the text and the playground disagree: the text says 0.9, it shows 0.4", "kind": "readings"}]
    editor.check_changes(page, UsageLog("x"), "k")
    assert page.stage("sampling")[1] == next(s for s in V3["stages"] if s["id"] == "sampling") and page.log[0]["status"] == "undone"


def test_a_done_event_with_start_from_sets_the_parent(db):
    with db.session() as s:
        s.add(Explainer(user_id="u1", id="r7", thread="r1", status="generating", title="t", created_at=7, updated_at=7,
                        data={"followUp": {"kind": "revise", "baseId": "r3"}}))
    req = {"mode": "concept", "text": "go back to v1", "quality": "playground",
           "followUp": {"kind": "revise", "outline": "{}", "design": {}, "plan": plan_v5()}}
    history.start_generation(db, "u1", "r7", req, "r3")
    e = {"type": "done", "spec": V1, "usage": {}, "startFrom": 1}
    history.generation_event(db, "u1", "r7", e)
    assert e["madeFrom"] == 1 and "startFrom" not in e
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", e["version"])).parent == 1


def test_a_new_topic_is_stored_as_a_picking_record():
    gen = SimpleNamespace(status="error", error="↗ That's another topic.", steps={}, updated_at=1, spec=None, usage=None, meta={})
    out = history._apply_generation({"updatedAt": 0}, gen)
    assert out["status"] == "picking"
    assert out["plan"] == {"kind": "cannot", "reason": "That's another topic.", "alternative": None, "newTopic": True}


def test_answers_still_start_past_the_days_explainers():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        s.add(User(id="u9", email="z@x.io"))
    settings = SimpleNamespace(jwt_secret="s" * 40, daily_usd=100.0, network_daily_usd=0.0, global_daily_usd=100.0,
                               daily_generations=0, max_concurrent=2)
    assert quota.over_daily(settings, d, "u9")
    with pytest.raises(Exception):
        quota.start_generation(settings, d, "u9")
    assert quota.start_generation(settings, d, "u9", allow_over=True)


def test_the_whole_generate_path_goes_back_to_an_older_version(db, monkeypatch):
    """/api/generate for a v5 agent run (run.py): the agent goes back to v1 (start_from), edits it, and the page is assembled
    from v1's own look and parts."""
    from explainer.modes.followup import run
    with db.session() as s:
        for v, page in ((1, V1), (3, V3)):
            versions.save_version(s, "u1", line="r1", thread="r1", record_id=f"r{v}", parent=None if v == 1 else 2,
                                  kind="new" if v == 1 else "change", page=spec_of(page), number=v)
    monkeypatch.setattr(run, "ask_brief", lambda *a, **k: pytest.fail("v5 needs no page brief"))
    monkeypatch.setattr(run, "write_examples", lambda client, spec, usage: spec)
    client = stream_client(msg(tool_use("start_from", {"version": 1})),
                           msg(tool_use("rename_section", {"section": "tokens", "label": "Tokens, again"}, 1)),
                           finish("Back to version 1, with S1 renamed.", suggestions=["Want S2 renamed too?"]), report())
    follow = {**FOLLOW, "design": SAMPLE["design"], "plan": plan_v5()}  # the app's copy is the latest page (v3)
    req = {"mode": "concept", "text": "go back to v1 and rename S1", "quality": "playground", "effort": "low", "followUp": follow}
    out = run.generate(client, req, lambda e: None, checks=False, db=db, user_id="u1", base_id="r3")
    spec = out["spec"]
    assert out["startFrom"] == 1
    assert [s["id"] for s in spec["stages"]] == [s["id"] for s in V1["stages"]]  # v1's sections (v3 had removed "loop")
    assert spec["stages"][0]["label"] == "Tokens, again"
    assert spec["design"]["mode"] == "dark" and spec["design"]["theme"] == SAMPLE["design"]["theme"]  # v1's own look
    sampling = next(s for s in spec["stages"] if s["id"] == "sampling")
    assert [b["html"] for b in sampling["blocks"] if b.get("type") == "widget"] == [PG["html"]]  # its playground put back
    assert spec["revision"]["suggestions"] == ["Want S2 renamed too?"] and "fromMemory" not in spec  # it had a source


# ---------- editor tools ----------


def page_on(db, base: dict = V3, version: int = 3) -> P.Page:
    return P.Page(base, "playground", "", P.Store(db, "u1", "r1", version))


def ctx() -> T.Ctx:
    return T.Ctx(None, UsageLog("x"), None)


def test_reads_any_version(db):
    pg = page_on(db)
    got = T.run_tool(pg, "get_section", {"section": "tokens", "version": 1}, ctx())
    assert not got["is_error"] and "Text becomes tokens" in got["content"]
    assert "Words become tokens" in T.run_tool(pg, "get_section", {"section": "tokens"}, ctx())["content"]
    assert T.run_tool(pg, "get_section", {"section": "loop", "version": 9}, ctx())["content"] == "There is no version 9."
    hits = json.loads(T.run_tool(pg, "search_page", {"query": "Text becomes tokens", "version": 1}, ctx())["content"])
    assert [h["id"] for h in hits] == ["tokens"]
    ch = json.loads(T.run_tool(pg, "get_change", {"version": 3}, ctx())["content"])
    assert [s["id"] for s in ch["removed"]] == ["loop"]


def test_restore_section_adds_back_or_replaces_in_place(db):
    pg = page_on(db)
    r = T.run_tool(pg, "restore_section", {"version": 1, "section": "loop", "after": "sampling"}, ctx())
    assert not r["is_error"], r
    ids = [s["id"] for s in pg.stages]
    assert ids.index("loop") == ids.index("sampling") + 1 and "loop" in pg.added
    assert next(s for s in pg.stages if s["id"] == "loop") == next(s for s in V1["stages"] if s["id"] == "loop")
    r = T.run_tool(pg, "restore_section", {"version": 1, "section": "tokens"}, ctx())  # still on the page: back in place
    assert not r["is_error"] and pg.stages[0]["label"] == next(s for s in V1["stages"] if s["id"] == "tokens")["label"]
    assert "tokens" in pg.text_changed


def test_restore_playground_copies_the_exact_html(db):
    with db.session() as s:
        v4 = {**V3, "stages": [{**st, "blocks": [b if b.get("type") != "widget" else PG2 for b in st["blocks"]]} for st in V3["stages"]]}
        versions.save_version(s, "u1", line="r1", thread="r1", record_id="r4", parent=3, kind="change", page=v4)
    pg = page_on(db, v4, 4)
    r = T.run_tool(pg, "restore_playground", {"version": 1, "section": "sampling"}, ctx())
    assert not r["is_error"], r
    st = next(s for s in pg.stages if s["id"] == "sampling")
    assert [b["html"] for b in st["blocks"] if b.get("type") == "widget"] == [PG["html"]] and "sampling" in pg.widgets_changed


def test_new_ids_avoid_every_branch(db):
    pg = page_on(db)  # v3 has no "loop", but v1 and v2 used it
    r = T.run_tool(pg, "add_section", {"after": "end", "label": "Loop", "blocks": [{"type": "text", "md": "Again and again."}]}, ctx())
    assert r["content"] == 'Added as "loop-2".'


# ---------- the base page on the server ----------


def test_follow_of_matches_the_apps_follow_up():
    icons = data.menu("ICONS")
    name = next(iter(icons))
    spec = {**PAGE, "design": {"theme": "ocean", "fontPairing": "a", "headingStyle": "b", "mode": "dark"},
            "chatbot": {"systemPrompt": "sp", "starters": ["s"], "context": "x" + B.SOURCE_MARK + "the paper"},
            "stages": [{**s, "icon": icons[name]} for s in PAGE["stages"]]}
    f = B.follow_of(spec)
    assert json.loads(f["outline"])["stages"] == COMPACT["stages"]
    assert f["widgets"] == FOLLOW["widgets"] and f["colorMode"] == "dark" and f["source"] == "the paper"
    assert f["design"]["stageIcons"][0] == {"stageId": PAGE["stages"][0]["id"], "icon": name}


def test_load_starts_from_the_plans_base(db):
    got = B.load(db, "u1", "r3", {"brief": "b", "base": 1})
    assert got["version"] == 1 and json.loads(got["follow"]["outline"])["stages"][0]["label"] == V1["stages"][0]["label"]
    assert got["store"].base == 1 and "v1 (first version)" in got["log_text"]
    assert B.load(db, "u1", "r3", {"brief": "b"})["version"] == 3
    assert B.load(None, "u1", "r3", {"brief": "b"}) is None


# ---------- the paper the page was made from (Nov 18 2026) ----------
# The page's own source is a short summary that dropped most of a results table; the agent told the reader "the paper
# doesn't give those scores" five times while the PDF had every row.

PAPER = [{"heading": "Abstract", "text": "We compare six taggers."},
         {"heading": "6.1 Results", "text": "Model Macro-F1\nCRF 0.813\nLSTM 0.819\nmBERT 0.818\nXLM-R 0.821"},
         {"heading": "", "text": "   "}]


def set_request(db, rid: str, request: dict) -> None:
    with db.session() as s:
        s.get(Explainer, ("u1", rid)).data = {"request": request}


def tool_result(sent: list, name: str) -> str:
    """What the agent got back from its first call to `name`."""
    msgs = sent[-1]["messages"]
    ids = [b["id"] for m in msgs if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use" and b["name"] == name]
    return next(b["content"] for m in msgs if m["role"] == "user" for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "tool_result" and b["tool_use_id"] == ids[0])


def test_load_reads_the_paper_from_the_lines_first_record(db):
    set_request(db, "r1", {"mode": "paper", "title": "t", "sections": PAPER})
    got = B.load(db, "u1", "r3", {"brief": "b"})
    assert got["paper"] == PAPER[:2]  # an empty section is dropped
    assert got["follow"].get("source") is None  # the page's own source (its chatbot context) is untouched


def test_a_concept_page_has_no_paper(db):
    assert B.load(db, "u1", "r3", {"brief": "b"})["paper"] == []


def test_a_retried_first_page_reads_its_new_record(db):
    """Retry saves the new record as the line's "new" version; the line's id is the old (deleted) record."""
    with db.session() as s:
        s.add(Explainer(user_id="u1", id="r9", thread="r1", status="done", title="t", created_at=9, updated_at=9,
                        data={"request": {"mode": "paper", "sections": PAPER[1:2]}}))
        v1 = s.get(PageVersion, ("u1", "r1", 1))
        v1.record_id, v1.updated_at = "r9", 10**15
        s.delete(s.get(Explainer, ("u1", "r1")))
    assert B.load(db, "u1", "r3", {"brief": "b"})["paper"] == PAPER[1:2]


def test_the_paper_goes_in_whole_and_search_reads_it(db):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, msg(tool_use("search_source", {"query": "mBERT"})), finish("mBERT scored 0.818."), sent=sent,
                  request="are all the models there?", paper=PAPER[:2],
                  follow={"kind": "revise", "source": "Summary: XLM-R 0.821 (best); CRF 0.813."})
    first = first_text(sent)
    assert agent.PAPER_NOTE in first and "mBERT 0.818" in first and "## 6.1 Results" in first
    assert "mBERT 0.818" in tool_result(sent, "search_source")  # searched in the paper, not the summary


def test_without_a_paper_search_reads_the_summary(db):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, msg(tool_use("search_source", {"query": "CRF"})), finish("x"), sent=sent,
                  follow={"kind": "revise", "source": "Summary: CRF 0.813."})
    assert agent.PAPER_NOTE not in first_text(sent)
    assert "Summary: CRF 0.813." in tool_result(sent, "search_source")


def test_a_long_paper_keeps_whole_sections_and_names_the_rest():
    secs = [{"heading": f"S{i}", "text": f"Table {i}\n" + "row 0.8\n" * 40} for i in range(6)]
    one = len(agent.paper_text(secs[:1])) + 2
    got = agent.paper_part(secs, limit=one * 2 + 5)
    assert "## S0" in got and "## S1" in got and "## S2" not in got
    assert got.count("row 0.8") == 80  # never cut inside a section
    assert '"S2", "S3", "S4", "S5"' in got and "first 2 of 6 sections" in got
    assert agent.paper_part([]) == "" and agent.paper_part(secs).count("row 0.8") == 240


def test_a_change_from_an_older_version_has_it_as_parent(db):
    with db.session() as s:
        s.add(Explainer(user_id="u1", id="r5", thread="r1", status="generating", title="t", created_at=5, updated_at=5,
                        data={"followUp": {"kind": "revise", "baseId": "r3"}}))
    req = {"mode": "concept", "text": "go back to v1 and rename", "quality": "playground",
           "followUp": {"kind": "revise", "outline": "{}", "design": {}, "plan": plan_v5(base=1)}}
    history.start_generation(db, "u1", "r5", req, "r3")
    e = {"type": "done", "spec": V1, "usage": {}}
    history.generation_event(db, "u1", "r5", e)
    assert e["version"] == 4 and e["madeFrom"] == 1
    with db.session() as s:
        assert s.get(PageVersion, ("u1", "r1", 4)).parent == 1
        assert s.get(Explainer, ("u1", "r5")).data["madeFrom"] == 1


def test_review_ids_are_checked():
    pg = P.Page(V2, "playground", "")
    T.run_tool(pg, "rename_section", {"section": "tokens", "label": "Tokens"}, ctx())
    got = review._checked(pg, {"problems": [{"section": "S2", "cause": "ghost", "what": "x"}, {"section": "nope", "cause": "", "what": "y"},
                                            {"section": "embeddings", "cause": "tokens", "what": ""}], "suggestions": ["a", 3, ""]})
    assert got == {"problems": [{"section": "embeddings", "cause": "", "what": "x"}]}  # a review's ideas are never read


def test_suggestions_land_in_the_version_log(db):
    with db.session() as s:
        s.add(Explainer(user_id="u1", id="r6", thread="r1", status="generating", title="t", created_at=6, updated_at=6, data={}))
    req = {"mode": "concept", "text": "rename", "quality": "playground", "followUp": {"kind": "revise", "outline": "{}", "design": {}, "plan": plan_v5()}}
    history.start_generation(db, "u1", "r6", req, "r3")
    history.generation_event(db, "u1", "r6", {"type": "done", "spec": {**V3, "revision": {"suggestions": ["Want S2 simpler?"]}}, "usage": {}})
    with db.session() as s:
        assert versions.version_log(s, "u1", "r1")[-1]["suggestions"] == ["Want S2 simpler?"]


def test_undo_goes_back_to_the_version_it_was_made_under(db):
    with db.session() as s:
        s.add(Explainer(user_id="u1", id="r5", thread="r1", status="generating", title="t", created_at=5, updated_at=5,
                        data={"followUp": {"kind": "revise", "baseId": "r3"}}))
    req = {"mode": "concept", "text": "go back to v1 and rename", "quality": "playground",
           "followUp": {"kind": "revise", "outline": "{}", "design": {}, "plan": plan_v5(base=1)}}
    history.start_generation(db, "u1", "r5", req, "r3")
    history.generation_event(db, "u1", "r5", {"type": "done", "spec": V1, "usage": {}})  # v4: made from v1, under v3
    with db.session() as s:
        assert versions.version_log(s, "u1", "r1")[-1]["under"] == 3
    got = history.revert_version(db, "u1", "r6", "r5")  # "undo that"
    assert got["reverted"] == 3 and got["version"] == 5
    assert "v4 (from v1, asked under v3)" in B.load(db, "u1", "r5", {"brief": "b"})["log_text"]


def spec_of(outline: dict) -> dict:
    """A saved page as the app stores it: the outline plus its look (icons by emoji, a colour mode) and its chatbot."""
    icons = data.menu("ICONS")
    d = SAMPLE["design"]
    by_stage = {x["stageId"]: icons[x["icon"]] for x in d["stageIcons"]}
    return {**outline, "design": {"theme": d["theme"], "fontPairing": d["fontPairing"], "headingStyle": d["headingStyle"], "mode": "dark"},
            "chatbot": {**d["chatbot"], "context": "page" + B.SOURCE_MARK + "the source text"},
            "stages": [{**s, "icon": by_stage.get(s["id"], icons[next(iter(icons))])} for s in outline["stages"]]}


def test_the_version_log_shows_replies_deletes_and_suggestions():
    """Every field a saved version's log can hold (a live run crashed on a reply: LOG_REPLY_CHARS was missing)."""
    entries = [{"version": 1, "parent": None, "kind": "new", "deleted": False, "request": "first"},
               {"version": 2, "parent": 1, "kind": "change", "deleted": False, "request": "fix S3", "reply": "Fixed it. " * 40,
                "changed": ["asymmetry"], "removed": ["loop"], "suggestions": ["Want S2 simpler?"]},
               {"version": 3, "parent": 2, "kind": "revert", "deleted": True, "reverted": 1},
               {"version": 4, "parent": 1, "kind": "change", "deleted": False, "under": 2, "request": "from v1"}]
    text = B.log_text(entries, 4)
    assert 'reply: "Fixed it.' in text and "changed: asymmetry; removed: loop" in text and "suggested: Want S2 simpler?" in text
    assert "v3 (from v2, back to v1): deleted" in text
    assert "v4 (from v1, asked under v2): asked \"from v1\"  <- the page this message is under" in text
    assert len(text.split("reply: ")[1].split('"')[1]) <= B.LOG_REPLY_CHARS


# ---------- looking at playgrounds (look.py, FOLLOWUP_V5_LOOK=1, Explorer only) ----------

from explainer.modes.followup.v5 import look as L  # noqa: E402

FRAME = {"label": "start (the reader's first view)", "png": "iVBORw0KGgo="}


@pytest.fixture
def looking(monkeypatch) -> dict:
    """The flag on, screenshots and the look judge faked; records what they were called with."""
    seen: dict = {"snaps": [], "reviews": []}
    monkeypatch.setenv("FOLLOWUP_V5_LOOK", "1")
    monkeypatch.setattr(L, "snap_widget", lambda html, theme=None, max_frames=None:
                        seen["snaps"].append({"theme": theme, "max": max_frames}) or [FRAME, {**FRAME, "label": "slider at its max"}])
    monkeypatch.setattr(L, "look_review", lambda client, usage, frames, plan, title, checked=None, step="", effort="medium":
                        seen["reviews"].append(effort) or ["the legend covers the line"])
    monkeypatch.setattr(T, "check_playground", lambda spec: {"problems": [], "facts": None, "layout": []})
    return seen


def look_ctx(effort: str = "medium") -> T.Ctx:
    return T.Ctx(None, UsageLog("x"), None, effort=effort)


def test_the_look_tool_is_offered_only_on_explorer_pages_with_the_flag(monkeypatch):
    names = lambda lite: [t["name"] for t in T.definitions(lite)]  # noqa: E731
    monkeypatch.setenv("FOLLOWUP_V5_LOOK", "0")
    assert "look_at_playground" not in names(False)  # flag off: the tool list (and its cache) is as before
    monkeypatch.delenv("FOLLOWUP_V5_LOOK")  # on by default (Nov 19 2026)
    assert "look_at_playground" in names(False) and "look_at_playground" not in names(True)
    page = P.Page(V3, "lite-plus")
    assert T.run_tool(page, "look_at_playground", {"section": "sampling"}, look_ctx())["is_error"]


def test_the_look_tool_returns_pictures_and_numbers_cached_and_capped_by_effort(looking, monkeypatch):
    monkeypatch.setattr(T, "playground_readings", lambda page_, st_, runs=None: [{"title": "t", "readings": "readout p = 0.42"}])
    page = P.Page(V3, "playground")
    page.look_theme = {"mode": "dark", "palette": "sunset"}
    r = T.run_tool(page, "look_at_playground", {"section": "sampling"}, look_ctx("medium"))
    assert not r["is_error"] and [b["type"] for b in r["content"]] == ["text", "text", "image", "text", "image", "text"]
    assert r["content"][2]["source"]["media_type"] == "image/png" and "readout p = 0.42" in r["content"][-1]["text"]
    assert looking["snaps"] == [{"theme": {"mode": "dark", "palette": "sunset"}, "max": L.FRAMES}]  # the page's own colours
    assert not T.run_tool(page, "look_at_playground", {"section": "sampling"}, look_ctx("medium"))["is_error"]
    assert len(looking["snaps"]) == 1  # same html and colours: taken once
    over = T.run_tool(page, "look_at_playground", {"section": "sampling"}, look_ctx("medium"))
    assert over["is_error"] and "2 look(s)" in over["content"]  # Medium: 2 a message
    low = P.Page(V3, "playground")
    T.run_tool(low, "look_at_playground", {"section": "sampling"}, look_ctx("low"))
    assert T.run_tool(low, "look_at_playground", {"section": "sampling"}, look_ctx("low"))["is_error"]  # Low: 1


def test_no_screenshots_falls_back_to_the_numbers(looking, monkeypatch):
    monkeypatch.setattr(L, "snap_widget", lambda *a, **k: None)
    monkeypatch.setattr(T, "playground_readings", lambda page_, st_, runs=None: [{"title": "t", "readings": "readout p = 0.42"}])
    r = T.run_tool(P.Page(V3, "playground"), "look_at_playground", {"section": "sampling"}, look_ctx())
    assert not r["is_error"] and r["content"][0]["type"] == "text" and len(r["content"]) == 1
    assert "Couldn't take screenshots" in r["content"][0]["text"] and "0.42" in r["content"][0]["text"]


def test_the_page_colours_come_from_the_page_and_follow_start_from():
    assert P.look_theme_of({"design": {"theme": "forest", "mode": "dark"}}) == {"mode": "dark", "palette": "forest"}
    assert P.look_theme_of({"design": {"theme": "forest"}, "colorMode": "dark"})["mode"] == "dark"
    assert P.look_theme_of({"design": {"theme": "forest", "mode": "auto"}}) == {"mode": "light", "palette": "forest"}
    assert P.look_theme_of(None) == {"mode": "light"}
    page = P.Page(V3, "playground")
    page.rebase(V1, 1, {"design": {"theme": "berry", "mode": "dark"}})
    assert page.look_theme == {"mode": "dark", "palette": "berry"}


def test_only_the_newest_screenshots_stay_in_the_conversation(db, looking):
    sent: list = []
    with pytest.raises(Checked):
        run_agent(db, msg(tool_use("look_at_playground", {"section": "sampling"})),
                  msg(tool_use("look_at_playground", {"section": "sampling"}, 1)), finish("It looks fine."), sent=sent,
                  follow={"kind": "revise", "design": {"theme": "sunset", "mode": "dark"}})
    assert looking["snaps"][0]["theme"] == {"mode": "dark", "palette": "sunset"}
    results = [b for m in sent[-1]["messages"] if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
    assert not L.has_images([results[0]]) and results[0]["content"][-1]["text"] == L.STUB
    assert L.has_images([results[1]])


def test_the_first_finish_gets_look_problems_and_one_left_alone_is_offered(db, looking):
    """The look check is feedback at the reader's Effort; never a put-back; left alone, it becomes one suggestion."""
    sent: list = []
    out = run_agent(db, msg(tool_use("patch_playground", {"section": "sampling", "sets": [{"path": "title", "value": "Heat dial"}]})),
                    finish("Renamed the playground."), report(), finish("Renamed it; it looks fine to me.", 2), sent=sent, effort="high")
    refused = sent[3]["messages"][4]["content"][0]
    assert refused["is_error"] and "how it looks (from screenshots): the legend covers the line" in refused["content"]
    assert looking["reviews"] == ["high"]  # judged once, at the reader's Effort
    assert "Heat dial" in json.dumps(out["outline"])  # kept
    assert out["revision"]["suggestions"][0].startswith('Want me to fix how the playground in "')
    assert "the legend covers the line" in out["revision"]["suggestions"][0]


def test_no_look_check_without_the_flag(db, monkeypatch):
    monkeypatch.setenv("FOLLOWUP_V5_LOOK", "0")  # on by default (Nov 19 2026)
    monkeypatch.setattr(T, "check_playground", lambda spec: {"problems": [], "facts": None, "layout": []})
    monkeypatch.setattr(L, "look_review", lambda *a, **k: pytest.fail("judged with the flag off"))
    out = run_agent(db, msg(tool_use("patch_playground", {"section": "sampling", "sets": [{"path": "title", "value": "Heat dial"}]})),
                    finish("Renamed the playground."), report())
    assert out["revision"]["reply"] == "Renamed the playground." and not out["revision"].get("suggestions")
