"""Follow-ups v3 (steps.run_plan, through modes.generate → modes/followup/run.py): structure by code and the page planner, each section through the
section editor (which decides text / playground / both / nothing from the evidence), knock-ons, Jev's check with a per-section
undo, and the report. A follow-up whose sections were checked and found right ends as steps.Checked, not an error. Haiku,
Jev and the playground builds are faked."""

from __future__ import annotations

import json

import pytest

from explainer import history, modes
from explainer.modes.followup import check as feedback_check, playground_edit as ce, run as R, section_editor as se, steps as S
from explainer.core.validate import default_design
from explainer.followup import parse_follow_up
from explainer.llm import ReaderError
from followup_v3_data import COMPACT, FOLLOW, IDS, OLD, PG, Store, edited, editor_reply, index_of, intent, stage_of, step


class Pipe:
    """modes.generate (a v3 follow-up) with the planner, the section editor, the playground builds and the checks faked."""

    def __init__(self, monkeypatch, editor: dict | None = None, planner=(), judge=(), checker=()):
        self.seen: dict = {"planner": [], "editor": [], "edited": [], "added": [], "gated": []}
        planner_q, editor_q, judge_q = list(planner), {k: list(v) if isinstance(v, list) else [v] for k, v in (editor or {}).items()}, list(judge)

        def write_outline(client, request, emit, usage, system, parse, effort, *a, **k):
            self.seen["planner"].append(request)
            p = parse(planner_q.pop(0))
            return {"outline": p["outline"], "warnings": p["warnings"], **({"revision": p["revision"]} if "revision" in p else {})}

        def write_text(client, usage, step, *, system, user, **k):
            payload = json.loads(user.split("\n\n# This time")[0])
            sid = payload["section"]["id"]
            self.seen["editor"].append((sid, payload))
            q = editor_q.get(sid) or [editor_reply("none", "Nothing to change.")]
            return json.dumps(q.pop(0) if len(q) > 1 else q[0])

        def edit_widget(client, usage, jev_key, outline, si, bi, what, effort="medium", think=True):
            self.seen["edited"].append((outline["stages"][si]["id"], what))
            old = outline["stages"][si]["blocks"][bi]
            return {"block": {**old, "html": old["html"] + "<!-- edited -->"}, "changed": True, "facts": None, "plan": None, "notes": []}

        monkeypatch.setattr(R, "write_outline", write_outline)
        monkeypatch.setattr(se, "write_text", write_text)
        monkeypatch.setattr(R, "clarity_check", lambda client, jev_key, spec, usage, *a, only=None, **k: (spec, "checked"))
        monkeypatch.setattr(R, "fact_check", lambda client, jev_key, spec, usage, *a, only=None, **k: (spec, "checked"))
        monkeypatch.setattr(R, "write_notes", lambda client, spec, extras, usage, kept=None: (spec, ""))
        monkeypatch.setattr(ce, "edit_widget", edit_widget)
        monkeypatch.setattr(ce, "agrees", lambda *a: None)
        monkeypatch.setattr(ce, "facts_of", lambda block: {"controls": []})
        checked = lambda spec: {"problems": list(checker), "layout": [], "facts": None}  # noqa: E731
        monkeypatch.setattr(se, "check_playground", checked)
        monkeypatch.setattr(ce, "check_playground", checked)
        monkeypatch.setattr(feedback_check, "judge", lambda *a: judge_q.pop(0) if judge_q else [])
        monkeypatch.setattr(S, "gate_one", lambda jev_key, outline, si, usage, playground=False: (self.seen["gated"].append(si) or {"open": False, "why": None}, None))

    def run(self, sections=(), steps=(), quality: str = "playground", text: str = "change it", page: dict | None = None, index: dict | None = None) -> dict:
        plan = {"sections": list(sections), "steps": list(steps)}
        follow = {**FOLLOW, "design": default_design(OLD), "plan": plan}
        if page is not None:
            follow = {"kind": "revise", "outline": json.dumps(page), "design": default_design(OLD), "plan": plan}
        req = {"mode": "concept", "text": text, "quality": quality, "extras": [], "followUp": parse_follow_up(follow)}
        assert modes.handles(req)
        return modes.generate(None, req, lambda e: None, jev_key="jev", page_index=Store(index))["spec"]


def blocks_of(spec: dict, sid: str) -> list[dict]:
    return stage_of(spec, sid)["blocks"]


CHECK = "check the last playground; if it is correct leave it, otherwise fix it"


# ---------- the editor decides ----------


def test_a_checked_section_that_is_right_is_a_reply_not_an_error(monkeypatch):
    """The Nov 9 failure: "check the last playground, fix it if wrong". The editor sees the playground and the checker's
    findings (none), decides nothing needs changing, and the follow-up ends as Checked with its reason."""
    pipe = Pipe(monkeypatch, editor={"sampling": editor_reply("none", "Its playground matches the text: the temperature slider reshapes the odds.")})
    with pytest.raises(S.Checked) as e:
        pipe.run([intent("sampling", "check that its playground and text are correct; fix anything wrong")], text=CHECK)
    assert str(e.value).startswith(S.CHECKED) and "matches the text" in str(e.value) and 'Section 4 "Picking the next token"' in str(e.value)
    sid, payload = pipe.seen["editor"][0]
    assert sid == "sampling" and payload["playground"]["spec"]["components"][0]["label"] == "temperature" and payload["playground"]["checker"] == []
    assert pipe.seen["edited"] == []


def test_a_checked_section_with_a_broken_playground_gets_it_fixed(monkeypatch):
    pipe = Pipe(monkeypatch, checker=["c1 stays empty in every state"],
                editor={"sampling": editor_reply("change", "Its chart was empty.", None, "edit", "c1 stays empty: draw the probability bars from the temperature")})
    spec = pipe.run([intent("sampling", "check it, fix anything wrong")], text=CHECK)
    assert "c1 stays empty in every state" in pipe.seen["editor"][0][1]["playground"]["checker"]  # the editor saw the problem
    assert pipe.seen["edited"] == [("sampling", "c1 stays empty: draw the probability bars from the temperature")]
    assert blocks_of(spec, "sampling")[-1]["html"].endswith("<!-- edited -->") and [s["id"] for s in spec["stages"]] == IDS
    assert spec["revision"]["steps"][0]["status"] == "done" and spec["revision"]["steps"][0]["why"] == "Its chart was empty."


def test_the_editor_may_change_the_text_and_the_playground_together(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"sampling": editor_reply("change", "r", edited("sampling", "Temperature, step by step."), "edit", "add a top-p slider")})
    spec = pipe.run([intent("sampling", "explain temperature better")])
    assert blocks_of(spec, "sampling")[0]["md"] == "Temperature, step by step." and blocks_of(spec, "sampling")[-1]["html"].endswith("<!-- edited -->")
    assert spec["revision"]["steps"][0]["status"] == "done" and spec["revision"]["replaced"] == 1 and spec["revision"]["charts"] == 1
    assert pipe.seen["edited"] == [("sampling", "add a top-p slider")]


def test_untouched_sections_stay_exactly_as_they_were(monkeypatch):
    """run_plan itself (before the pipeline's spec assembly normalises numbers): every section the plan didn't touch is
    byte-identical, the playground included."""
    from explainer.pricing import UsageLog
    Pipe(monkeypatch, editor={"tokens": editor_reply(stage=edited("tokens"))})
    r = S.run_plan(None, UsageLog("x"), None, FOLLOW, COMPACT, {"sections": [intent("tokens", "simpler")], "steps": []}, "simpler", None,
                   "SYSTEM", "medium", True, "playground", True, Store(None))
    page = {**COMPACT, "stages": [{**s, "blocks": [*s["blocks"][:-1], PG]} if s["id"] == "sampling" else s for s in COMPACT["stages"]]}
    assert [s for s in r["outline"]["stages"] if s["id"] != "tokens"] == [s for s in page["stages"] if s["id"] != "tokens"]
    assert stage_of(r["outline"], "tokens")["blocks"][0]["md"] == "Clearer tokens." and r["revision"]["changed"] == ["tokens"]


def test_a_quick_read_page_edit_never_touches_playgrounds(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"tokens": editor_reply("change", "r", edited("tokens", page=OLD), "add", "a slider")})
    spec = pipe.run([intent("tokens", "show the vocabulary chart")], quality="lite-plus", page=OLD)
    assert pipe.seen["edited"] == [] and blocks_of(spec, "tokens")[0]["md"] == "Clearer tokens."
    assert pipe.seen["editor"][0][1]["page"]["mode"] == "lite-plus"


# ---------- structure, page-wide, knock-ons ----------


def test_a_removal_and_its_knock_on_run_together(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"attention": editor_reply(stage=edited("attention"))})
    derived = intent("attention", 'The section "Tokens become vectors" is removed: reword any mention of it.', derived=True, because="it mentions it")
    spec = pipe.run([derived], [step("remove_section", "embeddings")])
    assert [s["id"] for s in spec["stages"]] == [i for i in IDS if i != "embeddings"] and spec["revision"]["removed"] == 1
    assert blocks_of(spec, "attention")[0]["md"] == "Clearer attention."
    assert [r["status"] for r in spec["revision"]["steps"]] == ["done", "done"] and spec["revision"]["steps"][1]["derived"]


def test_a_section_that_still_names_a_removed_one_gets_one_more_pass(monkeypatch):
    mention = edited("attention", "Building on Tokens become vectors, attention mixes context.")
    pipe = Pipe(monkeypatch, editor={"attention": [editor_reply(stage=mention), editor_reply(stage=edited("attention", "Attention mixes context."))]})
    spec = pipe.run([intent("attention", "shorter")], [step("remove_section", "embeddings")])
    assert blocks_of(spec, "attention")[0]["md"] == "Attention mixes context." and len(pipe.seen["editor"]) == 2


def test_moving_a_section_is_done_by_code(monkeypatch):
    pipe = Pipe(monkeypatch)
    spec = pipe.run([], [step("move_section", "summary", after="start")])
    assert [s["id"] for s in spec["stages"]] == ["summary", *IDS[:-1]] and pipe.seen["planner"] == [] and pipe.seen["editor"] == []
    assert spec["revision"]["moved"] == 1 and spec["revision"]["steps"][0]["status"] == "done"


def test_a_new_level_opens_every_section_and_fits_mismatched_playgrounds(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"tokens": editor_reply(stage=edited("tokens")), "sampling": editor_reply("change", "r", None, "edit", "fewer controls")})
    spec = pipe.run([], [step("set_level", level="beginner", what="plainer words")], index=index_of(COMPACT, sampling={"playgroundLevel": "advanced"}))
    opened = {sid: p for sid, p in pipe.seen["editor"]}
    assert set(opened) == set(IDS) and "beginner" in opened["tokens"]["intent"]
    assert "Its playground is advanced-level" in opened["sampling"]["intent"] and "Its playground" not in opened["tokens"]["intent"]
    assert spec["level"] == "beginner" and pipe.seen["edited"] == [("sampling", "fewer controls")]


def test_a_merge_writes_the_kept_section_then_removes_the_other(monkeypatch):
    pipe = Pipe(monkeypatch, planner=[{"changes": {"replace": [edited("loop")]}}])
    spec = pipe.run([], [step("merge_sections", "loop", "fold it in", other="summary")])
    assert 'stage "loop"' in pipe.seen["planner"][0] and '"id":"summary"' in pipe.seen["planner"][0].replace(" ", "")
    assert [s["id"] for s in spec["stages"]] == IDS[:-1] and spec["revision"]["removed"] == 1


def test_a_new_section_is_offered_a_playground_by_the_gate(monkeypatch):
    new = {**edited("tokens"), "id": "extra", "label": "Beam search"}
    pipe = Pipe(monkeypatch, planner=[{"changes": {"add": [{"after": "sampling", "stage": new}]}}])
    spec = pipe.run([], [step("add_section", what="beam search", after="sampling")])
    assert "extra" in [s["id"] for s in spec["stages"]] and pipe.seen["gated"] == [[s["id"] for s in spec["stages"]].index("extra")]


# ---------- verify and report ----------


def test_a_section_that_fails_its_check_twice_is_put_back_alone(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"tokens": [editor_reply(stage=edited("tokens")), editor_reply(stage=edited("tokens", "Still unclear."))],
                                     "loop": editor_reply(stage=edited("loop"))},
                judge=[["fixes"], ["fixes"], []])  # tokens fails, fails again after its retry; loop passes
    spec = pipe.run([intent("tokens", "simpler"), intent("loop", "simpler")])
    assert blocks_of(spec, "tokens") == stage_of(OLD, "tokens")["blocks"] and blocks_of(spec, "loop")[0]["md"] == "Clearer loop."
    report = {r["label"].split('"')[1]: r for r in spec["revision"]["steps"]}
    assert report["Text becomes tokens"]["status"] == "undone" and report["Generate and repeat"]["status"] == "done"


def test_a_section_kept_as_it_was_is_reported_with_the_editors_reason(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"tokens": editor_reply(stage=edited("tokens")), "loop": editor_reply("none", "It already has an example.")})
    spec = pipe.run([intent("tokens", "simpler"), intent("loop", "add an example")])
    kept = next(r for r in spec["revision"]["steps"] if "Generate and repeat" in r["label"])
    assert kept == {"label": kept["label"], "status": "kept", "why": "It already has an example."}


def test_a_change_that_fails_its_check_twice_ends_as_a_calm_reply(monkeypatch):
    """Nov 9: "make it look like a table" was tried twice, put back by the check, and shown as "Something went wrong". Nothing
    went wrong: the page was kept on purpose, so it is a reply (Checked) that says what was tried and why it was kept."""
    pipe = Pipe(monkeypatch, editor={"tokens": [editor_reply(stage=edited("tokens")), editor_reply(stage=edited("tokens", "Again."))]},
                judge=[["fixes", "not_worse"], ["fixes", "not_worse"]])
    with pytest.raises(S.Checked) as e:
        pipe.run([intent("tokens", "make it a table")])
    msg = str(e.value)
    assert msg.startswith(S.CHECKED + "I left the page as it was.") and 'I tried to change Section 1 "Text becomes tokens"' in msg
    assert "didn't pass the quality check (" in msg and "so I kept it as it was" in msg and "send the request again" in msg
    assert "Something went wrong" not in msg


def test_a_new_version_the_page_cant_use_is_a_calm_reply_too(monkeypatch):
    pipe = Pipe(monkeypatch, editor={"tokens": editor_reply(stage={"id": "tokens", "blocks": []})})
    with pytest.raises(S.Checked) as e:
        pipe.run([intent("tokens", "simpler")])
    assert 'I couldn\'t make a clean new version of Section 1 "Text becomes tokens"' in str(e.value)


def test_a_real_failure_is_still_an_error_with_the_reason(monkeypatch):
    pipe = Pipe(monkeypatch)
    monkeypatch.setattr(se, "write_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the AI service is down")))
    with pytest.raises(ReaderError) as e:
        pipe.run([intent("tokens", "simpler")])
    assert not isinstance(e.value, S.Checked) and "the change couldn't be written" in str(e.value)


def test_a_checked_follow_up_is_saved_as_a_reply():
    from types import SimpleNamespace
    gen = SimpleNamespace(status="error", error=S.CHECKED + "I checked it.", steps={}, updated_at=5, spec=None, usage=None)
    out = history._apply_generation({"status": "generating", "updatedAt": 1}, gen)
    assert out["status"] == "done" and out["answer"] == "I checked it." and "error" not in out
    gen.error = "Something failed."
    assert history._apply_generation({"status": "generating", "updatedAt": 1}, gen)["status"] == "error"
