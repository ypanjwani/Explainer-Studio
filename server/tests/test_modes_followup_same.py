"""v3 follow-ups on their own path (explainer/modes/followup/) give the same result as on the old one.

1. Every scenario of test_steps.py runs on the old run (pipeline.generate's v3 branch) and the new one (modes/followup/run.py);
   the page, the report, the error and everything the fakes saw must match. (Before the v3 files moved, on Nov 11 2026,
   this also ran the old v3 files on codelab / codelab_edit / feedback_check and matched; since the move both runs share
   the v3 files, so this checks the run itself.)
2. The playground edit / add, the text-playground agreement check and the playground's facts: codelab_edit vs its copy
   playground_edit, against the real checker, with Haiku and Jev faked at their calls; what Jev is asked must match too.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from explainer import codelab as cl, codelab_edit as ce, modes, pipeline
from explainer.core.validate import default_design
from explainer.followup import parse_follow_up
from explainer.llm import ReaderError
from explainer.modes.followup import check as n_check, playground_edit as n_pe, run as n_run, section_editor as n_se, steps as n_steps
from explainer.pricing import UsageLog

import modes_fakes as F
from followup_v3_data import COMPACT, FOLLOW, IDS, OLD, Store, edited, editor_reply, index_of, intent, step

needs_node = pytest.mark.skipif(not shutil.which("node") or not cl.PG_SCRIPT.exists(), reason="node not available")


def install(m, side: str, seen: dict, editor: dict | None, planner, judge, checker):
    """The fakes of test_steps.Pipe, on the old modules or on the new ones."""
    planner_q, editor_q, judge_q = list(planner), {k: list(v) if isinstance(v, list) else [v] for k, v in (editor or {}).items()}, list(judge)

    def write_outline(client, request, emit, usage, system, parse, effort, *a, **k):
        seen["planner"].append(request)
        p = parse(planner_q.pop(0))
        return {"outline": p["outline"], "warnings": p["warnings"], **({"revision": p["revision"]} if "revision" in p else {})}

    def write_text(client, usage, step_, *, system, user, **k):
        payload = json.loads(user.split("\n\n# This time")[0])
        sid = payload["section"]["id"]
        seen["editor"].append((sid, payload))
        q = editor_q.get(sid) or [editor_reply("none", "Nothing to change.")]
        return json.dumps(q.pop(0) if len(q) > 1 else q[0])

    def edit_widget(client, usage, jev_key, outline, si, bi, what, effort="medium", think=True):
        seen["edited"].append((outline["stages"][si]["id"], what))
        old = outline["stages"][si]["blocks"][bi]
        return {"block": {**old, "html": old["html"] + "<!-- edited -->"}, "changed": True, "facts": None, "plan": None, "notes": []}

    checked = lambda spec: {"problems": list(checker), "layout": [], "facts": None}  # noqa: E731
    gate = lambda jev_key, outline, si, usage, playground=False: (seen["gated"].append(si) or {"open": False, "why": None}, None)  # noqa: E731
    keep = lambda client, jev_key, spec, usage, *a, only=None, **k: (spec, "checked")  # noqa: E731
    se, edit_mod, check_mod = n_se, n_pe, n_check
    run_mod = pipeline if side == "old" else n_run  # the old run (pipeline.generate) or the new one
    m.setattr(n_se, "check_playground", checked)
    m.setattr(n_pe, "check_playground", checked)
    m.setattr(n_steps, "gate_one", gate)
    m.setattr(run_mod, "write_outline", write_outline)
    m.setattr(se, "write_text", write_text)
    m.setattr(run_mod, "clarity_check", keep)
    m.setattr(run_mod, "fact_check", keep)
    m.setattr(run_mod, "write_notes", lambda client, spec, extras, usage, kept=None: (spec, ""))
    m.setattr(edit_mod, "edit_widget", edit_widget)
    m.setattr(edit_mod, "agrees", lambda *a: None)
    m.setattr(edit_mod, "facts_of", lambda block: {"controls": []})
    m.setattr(check_mod, "judge", lambda *a: judge_q.pop(0) if judge_q else [])


def run_side(monkeypatch, side: str, sections=(), steps=(), quality="playground", text="change it", page=None, index=None,
             editor=None, planner=(), judge=(), checker=()) -> dict:
    seen: dict = {"planner": [], "editor": [], "edited": [], "gated": []}
    plan = {"sections": list(sections), "steps": list(steps)}
    follow = {**FOLLOW, "design": default_design(OLD), "plan": plan}
    if page is not None:
        follow = {"kind": "revise", "outline": json.dumps(page), "design": default_design(OLD), "plan": plan}
    req = {"mode": "concept", "text": text, "quality": quality, "extras": [], "followUp": parse_follow_up(follow)}
    events: list = []
    with monkeypatch.context() as m:
        install(m, side, seen, copy.deepcopy(editor), planner, judge, checker)
        fn = pipeline.generate if side == "old" else modes.generate
        if side == "new":
            assert modes.handles(req)
        try:
            out = fn(None, copy.deepcopy(req), events.append, jev_key="jev", page_index=Store(copy.deepcopy(index)))
            result = {"spec": out["spec"], "usage": (out["usage"]["quality"], out["usage"]["effort"], sorted(s["step"] for s in out["usage"]["steps"]))}
        except ReaderError as e:  # steps.Checked included
            result = {"error": (type(e).__name__, str(e))}
    done = [(e["step"], e.get("detail") or "") for e in events if e.get("type") == "step" and e["status"] == "done"]
    return {**result, "seen": seen, "done": done}


CHECK = "check the last playground; if it is correct leave it, otherwise fix it"
MENTION = edited("attention", "Building on Tokens become vectors, attention mixes context.")
NEW = {**edited("tokens"), "id": "extra", "label": "Beam search"}

SCENARIOS = {
    "checked-is-a-reply": dict(sections=[intent("sampling", "check that its playground and text are correct; fix anything wrong")], text=CHECK,
                               editor={"sampling": editor_reply("none", "Its playground matches the text: the temperature slider reshapes the odds.")}),
    "broken-playground-fixed": dict(sections=[intent("sampling", "check it, fix anything wrong")], text=CHECK, checker=["c1 stays empty in every state"],
                                    editor={"sampling": editor_reply("change", "Its chart was empty.", None, "edit", "c1 stays empty: draw the bars")}),
    "text-and-playground": dict(sections=[intent("sampling", "explain temperature better")],
                                editor={"sampling": editor_reply("change", "r", edited("sampling", "Temperature, step by step."), "edit", "add a top-p slider")}),
    "reader-never-touches-playgrounds": dict(sections=[intent("tokens", "show the vocabulary chart")], quality="lite-plus", page=OLD,
                                             editor={"tokens": editor_reply("change", "r", edited("tokens", page=OLD), "add", "a slider")}),
    "reader-text-edit": dict(sections=[intent("tokens", "simpler"), intent("loop", "simpler")], quality="lite-plus", page=OLD,
                             editor={"tokens": editor_reply(stage=edited("tokens", page=OLD)), "loop": editor_reply(stage=edited("loop", page=OLD))}),
    "removal-and-knock-on": dict(sections=[intent("attention", 'The section "Tokens become vectors" is removed: reword any mention of it.', derived=True, because="it mentions it")],
                                 steps=[step("remove_section", "embeddings")], editor={"attention": editor_reply(stage=edited("attention"))}),
    "still-names-removed-one": dict(sections=[intent("attention", "shorter")], steps=[step("remove_section", "embeddings")],
                                    editor={"attention": [editor_reply(stage=MENTION), editor_reply(stage=edited("attention", "Attention mixes context."))]}),
    "move-by-code": dict(steps=[step("move_section", "summary", after="start")]),
    "new-level": dict(steps=[step("set_level", level="beginner", what="plainer words")], index=index_of(COMPACT, sampling={"playgroundLevel": "advanced"}),
                      editor={"tokens": editor_reply(stage=edited("tokens")), "sampling": editor_reply("change", "r", None, "edit", "fewer controls")}),
    "merge": dict(steps=[step("merge_sections", "loop", "fold it in", other="summary")], planner=[{"changes": {"replace": [edited("loop")]}}]),
    "add-section-gated": dict(steps=[step("add_section", what="beam search", after="sampling")], planner=[{"changes": {"add": [{"after": "sampling", "stage": NEW}]}}]),
    "fails-check-twice-put-back": dict(sections=[intent("tokens", "simpler"), intent("loop", "simpler")], judge=[["fixes"], ["fixes"], []],
                                       editor={"tokens": [editor_reply(stage=edited("tokens")), editor_reply(stage=edited("tokens", "Still unclear."))],
                                               "loop": editor_reply(stage=edited("loop"))}),
    "kept-with-reason": dict(sections=[intent("tokens", "simpler"), intent("loop", "add an example")],
                             editor={"tokens": editor_reply(stage=edited("tokens")), "loop": editor_reply("none", "It already has an example.")}),
    "calm-reply-after-two-fails": dict(sections=[intent("tokens", "make it a table")], judge=[["fixes", "not_worse"], ["fixes", "not_worse"]],
                                       editor={"tokens": [editor_reply(stage=edited("tokens")), editor_reply(stage=edited("tokens", "Again."))]}),
    "unusable-version": dict(sections=[intent("tokens", "simpler")], editor={"tokens": editor_reply(stage={"id": "tokens", "blocks": []})}),
    "whole-page-intents": dict(sections=[intent(s, "use simpler words") for s in IDS], editor={s: editor_reply(stage=edited(s)) for s in IDS}),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_follow_up_runs_the_same_on_both_paths(monkeypatch, name):
    kw = SCENARIOS[name]
    old, new = run_side(monkeypatch, "old", **kw), run_side(monkeypatch, "new", **kw)
    assert new.get("error") == old.get("error")
    assert new.get("spec") == old.get("spec")
    assert new.get("usage") == old.get("usage")
    assert new["seen"] == old["seen"]
    assert new["done"] == old["done"]


def test_a_real_failure_is_the_same_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the AI service is down")
    seen = {}
    for side in ("old", "new"):
        se = n_se
        with monkeypatch.context() as m:
            install(m, side, {"planner": [], "editor": [], "edited": [], "gated": []}, None, (), (), ())
            m.setattr(se, "write_text", boom)
            follow = parse_follow_up({**FOLLOW, "design": default_design(OLD), "plan": {"sections": [intent("tokens", "simpler")], "steps": []}})
            req = {"mode": "concept", "text": "simpler", "quality": "playground", "extras": [], "followUp": follow}
            fn = pipeline.generate if side == "old" else modes.generate
            with pytest.raises(ReaderError) as e:
                fn(None, req, lambda e: None, jev_key="jev", page_index=Store(None))
            seen[side] = (type(e.value).__name__, str(e.value))
    assert seen["new"] == seen["old"] and "the change couldn't be written" in seen["new"][1]


# ---------- the playground edit, add and checks, with the real checker ----------

PG_BLOCK = {"type": "widget", "title": "Gini explorer", "html": cl.playground_html(F.GINI),
            "plan": json.dumps({"lesson": "a good split leaves each side mostly one class", "readerDoes": "moves the split"})}


def pg_page(with_widget: bool = True) -> dict:
    page = copy.deepcopy(F.MARKED)
    if with_widget:
        page["stages"][1]["blocks"].append(copy.deepcopy(PG_BLOCK))
    return page


@pytest.fixture
def jev_log(monkeypatch) -> list:
    log: list = []

    def post(url, headers=None, json=None, timeout=None):
        log.append({"state": json["state"], "questions": sorted(json["questions"])})
        return F.jev_post(url, headers, json, timeout)

    monkeypatch.setattr("explainer.jev.httpx.post", post)
    monkeypatch.setattr(cl, "snap_widget", lambda html: None)
    return log


def both(jev_log, fn_old, fn_new):
    """Runs each side with a fresh fake client; returns (old result, new result), each with the Jev requests and Haiku calls."""
    out = []
    for fn in (fn_old, fn_new):
        jev_log.clear()
        client = F.FakeClient()
        r = fn(client)
        out.append({"result": r, "jev": list(jev_log), "calls": sorted(client.calls)})
    return out


@needs_node
@pytest.mark.parametrize("what", ["make the split easier to see", "rename the split slider to Alpha"], ids=["kept", "review-fix"])
def test_playground_edit_is_the_same(jev_log, what):
    page = pg_page()
    old, new = both(jev_log, lambda c: ce.edit_widget(c, UsageLog("x"), "k", page, 1, len(page["stages"][1]["blocks"]) - 1, what),
                    lambda c: n_pe.edit_widget(c, UsageLog("x"), "k", page, 1, len(page["stages"][1]["blocks"]) - 1, what))
    assert new == old
    assert old["result"]["changed"] is True


@needs_node
@pytest.mark.parametrize("label", ["Kept part", "Checker part", "Nothing part"])
def test_playground_add_is_the_same(jev_log, label):
    page = pg_page(with_widget=False)
    si = next(i for i, s in enumerate(page["stages"]) if s["label"] == label)
    old, new = both(jev_log, lambda c: ce.add_widget(c, UsageLog("x"), "k", page, si, "let the reader move the split", "playground"),
                    lambda c: n_pe.add_widget(c, UsageLog("x"), "k", page, si, "let the reader move the split", "playground"))
    assert new == old


@needs_node
def test_agreement_and_facts_are_the_same(jev_log):
    page = pg_page()
    facts_old, facts_new = ce.facts_of(PG_BLOCK), n_pe.facts_of(PG_BLOCK)
    assert facts_new == facts_old and facts_old
    plan = ce.read_plan(PG_BLOCK)
    old, new = both(jev_log, lambda c: ce.agrees("k", page, 1, plan, facts_old, UsageLog("x")),
                    lambda c: n_pe.agrees("k", page, 1, plan, facts_new, UsageLog("x")))
    assert new == old and old["jev"], "the agreement check should ask Jev the same thing on both paths"


def test_page_kind_and_widget_kind_match():
    page = pg_page()
    for q in ("playground", "lite-plus", None):
        assert n_pe.page_kind(page, q) == ce.page_kind(page, q)
        assert n_pe.page_kind(pg_page(False), q) == ce.page_kind(pg_page(False), q)
    assert n_pe.widget_kind(PG_BLOCK) == ce.widget_kind(PG_BLOCK) == "playground"
    assert n_pe.read_playground_spec(PG_BLOCK["html"]) == ce.read_playground_spec(PG_BLOCK["html"])
    assert n_pe.viewed(page) == ce.viewed(page)
