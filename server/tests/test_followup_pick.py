"""Typed follow-ups on Playground and Quick read pages (followup_pick.py): Jev picks the sections from code-built cards,
the Haiku finder decides (or asks the reader) when Jev isn't sure, and the pipeline changes only the picked parts. A change of
the whole page is a text change of every section. Claude, Jev and the edits are faked."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from explainer import codelab as cl, codelab_edit as ce, followup_pick as FP, pipeline, targets as T
from explainer.core.validate import default_design
from explainer.followup import parse_follow_up, parse_pick
from explainer.llm import ReaderError
from explainer.pricing import UsageLog

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"))
OLD = SAMPLE["outline"]
IDS = [s["id"] for s in OLD["stages"]]


def playground(title: str) -> dict:
    spec = {"title": title, "plan": {}, "state": {}, "components": [{"id": "s1", "type": "slider", "label": "temperature"}, {"id": "c1", "type": "chart"}]}
    return {"type": "widget", "title": title, "html": f'<div id="pg"></div><script>XW.playground({json.dumps(spec)});</script>',
            "plan": json.dumps({"lesson": "temperature reshapes the odds"})}


PG = playground("Temperature explorer")
PAGE = {**OLD, "stages": [{**s, "blocks": [*s["blocks"], PG]} if s["id"] == "sampling" else s for s in OLD["stages"]]}
COMPACT = {**OLD, "stages": [{**s, "blocks": [*s["blocks"], {"type": "text", "md": "(interactive widget: Temperature explorer)"}]}
                             if s["id"] == "sampling" else s for s in OLD["stages"]]}
FOLLOW = {"kind": "revise", "outline": json.dumps(COMPACT), "widgets": {"sampling": [PG]}}


def target(part: str, stage: str = "", what: str = "change it", after: str = "") -> dict:
    return {"part": part, "stage": stage, "after": after, "what": what}


def jev_answers(scope: str, p_scope: float, sections: dict[str, float]) -> dict:
    """Jev's normalised answers: the scope ranking and s<N> for each section (0.05 when not given)."""
    a = {"scope": {"type": "choice", "ranking": [{"option": scope, "p": p_scope}], "confidence": 0.9}}
    for i, sid in enumerate(IDS):
        a[f"s{i + 1}"] = {"type": "noul", "p": sections.get(sid, 0.05)}
    return a


def decide(answers: dict) -> dict | None:
    from explainer.feedback_pick import section_cards
    return FP.decide(answers, section_cards(PAGE))


# ---------- Jev narrows down the sections ----------


def test_jev_is_asked_the_scope_and_one_question_per_section():
    from explainer.feedback_pick import section_cards
    q = FP.jev_questions(section_cards(PAGE))
    assert set(q["scope"]["criteria"]) == {"some", "every", "new", "unclear"}
    assert sorted(q) == sorted(["scope", *(f"s{i + 1}" for i in range(len(IDS)))])
    assert "playground" in q["s1"]["instructions"]


def test_a_sure_pick_names_the_sections_in_page_order():
    assert decide(jev_answers("some", 0.9, {"sampling": 0.95, "tokens": 0.9})) == {"stages": ["tokens", "sampling"]}


def test_a_whole_page_change_is_page_wide():
    assert decide(jev_answers("every", 0.8, {})) == {"pageWide": True}


@pytest.mark.parametrize("answers", [
    jev_answers("some", 0.9, {"sampling": 0.5}),  # a section Jev isn't sure about
    jev_answers("some", 0.9, {}),  # no section picked
    jev_answers("some", 0.5, {"sampling": 0.95}),  # the scope itself is unsure
    jev_answers("unclear", 0.9, {"sampling": 0.95}),
    jev_answers("new", 0.9, {}),  # where and what: the Haiku finder decides
    {},
])
def test_jev_not_sure_leaves_it_to_haiku(answers):
    assert decide(answers) is None


# ---------- pick: Jev, then Haiku ----------


def fake_client(*replies, sent: list | None = None):
    """Haiku: answers with the next reply (an Exception is raised); each request's user text goes into `sent`."""
    queue = list(replies)

    def create(**kw):
        if sent is not None:
            sent.append(kw["messages"][0]["content"])
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=900, output_tokens=60),
                               content=[SimpleNamespace(type="text", text=json.dumps(reply))])
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def jev_says(monkeypatch, answers: dict) -> None:
    monkeypatch.setattr(FP, "jev_ask", lambda key, state, q: {"answers": answers, "model": "jev", "usage": {"input_tokens": 10}})


def test_jev_narrows_and_haiku_writes_each_sections_own_change(monkeypatch):
    jev_says(monkeypatch, jev_answers("some", 0.9, {"tokens": 0.9, "sampling": 0.95}))
    sent: list = []
    reply = haiku([target("text", "tokens", "use a simpler example"), target("widget", "sampling", "add a top-p slider"),
                   target("text", "loop", "not picked")])  # a section Jev didn't pick: dropped
    out = FP.pick(fake_client(reply, sent=sent), "jev", PAGE, "simpler tokens example and a top-p slider in sampling", UsageLog("x"))
    assert out == {"targets": [target("text", "tokens", "use a simpler example"), target("widget", "sampling", "add a top-p slider")],
                   "pageWide": False, "by": "jev+haiku"}
    assert len(sent) == 1 and 'Section id "tokens"' in sent[0] and 'Section id "sampling"' in sent[0]
    assert all(f'Section id "{sid}"' not in sent[0] for sid in IDS if sid not in ("tokens", "sampling"))  # only the picked ones


def test_a_playground_question_on_a_picked_section_becomes_a_playground_change(monkeypatch):
    jev_says(monkeypatch, jev_answers("some", 0.9, {"sampling": 0.95}))
    reply = haiku([target("widget", "sampling", "show how top-p cuts the tail")])
    out = FP.pick(fake_client(reply), "jev", PAGE, "can the sampling playground be improved?", UsageLog("x"))
    assert out["targets"] == [target("widget", "sampling", "show how top-p cuts the tail")] and out["by"] == "jev+haiku"


def test_the_scoped_call_failing_falls_back_to_the_whole_page(monkeypatch):
    jev_says(monkeypatch, jev_answers("some", 0.9, {"sampling": 0.95}))
    sent: list = []
    out = FP.pick(fake_client(RuntimeError("down"), haiku([target("text", "loop")]), sent=sent), "jev", PAGE, "x", UsageLog("x"))
    assert out == {"targets": [target("text", "loop")], "pageWide": False, "by": "haiku"}
    assert len(sent) == 2 and all(f'Section id "{sid}"' in sent[1] for sid in IDS)  # the second call saw every section


def test_a_question_from_the_scoped_call_is_passed_on(monkeypatch):
    jev_says(monkeypatch, jev_answers("some", 0.9, {"tokens": 0.9, "sampling": 0.95}))
    q = {"text": "Which part?", "options": [{"label": "Tokens text", "targets": [target("text", "tokens")], "pageWide": False},
                                            {"label": "Sampling playground", "targets": [target("widget", "sampling")], "pageWide": False}]}
    out = FP.pick(fake_client(haiku(question=q)), "jev", PAGE, "fix it", UsageLog("x"))
    assert out["by"] == "jev+haiku" and [o["label"] for o in out["question"]["options"]] == ["Tokens text", "Sampling playground"]


def haiku(targets=(), page_wide=False, question=None) -> dict:
    return {"targets": list(targets), "pageWide": page_wide, "question": question or {"text": "", "options": []}}


def test_jev_sure_needs_no_haiku_call(monkeypatch):
    jev_says(monkeypatch, jev_answers("every", 0.9, {}))
    out = FP.pick(fake_client(RuntimeError("not called")), "jev", PAGE, "explain it to a beginner", UsageLog("x"))
    assert out == {"targets": [], "pageWide": True, "by": "jev"}


def test_jev_unsure_then_haiku_targets(monkeypatch):
    jev_says(monkeypatch, jev_answers("unclear", 0.9, {}))
    out = FP.pick(fake_client(haiku([target("widget", "sampling", "show the top-p cut")])), "jev", PAGE, "x", UsageLog("x"))
    assert out == {"targets": [target("widget", "sampling", "show the top-p cut")], "pageWide": False, "by": "haiku"}


def test_haiku_asks_the_reader_when_it_could_mean_different_changes():
    q = {"text": "Shorter how?", "options": [
        {"label": "Trim every section", "targets": [], "pageWide": True},
        {"label": "Drop the last two sections", "targets": [target("remove", IDS[-1]), target("remove", IDS[-2])], "pageWide": False},
        {"label": "Ghost", "targets": [target("remove", "ghost")], "pageWide": False},  # matches nothing: dropped
    ]}
    out = FP.pick(fake_client(haiku(question=q)), None, PAGE, "make it shorter", UsageLog("x"))
    assert out["by"] == "haiku" and out["question"]["text"] == "Shorter how?"
    assert [o["label"] for o in out["question"]["options"]] == ["Trim every section", "Drop the last two sections"]
    assert out["question"]["options"][1]["targets"] == [target("remove", IDS[-1]), target("remove", IDS[-2])]


def test_a_question_with_one_usable_option_is_no_question():
    q = {"text": "Which?", "options": [{"label": "A", "targets": [target("text", "tokens")], "pageWide": False}]}
    out = FP.pick(fake_client(haiku([target("text", "sampling")], question=q)), None, PAGE, "x", UsageLog("x"))
    assert out == {"targets": [target("text", "sampling")], "pageWide": False, "by": "haiku"}


def test_nothing_usable_is_an_error():
    assert "error" in FP.pick(fake_client(RuntimeError("down")), None, PAGE, "x", UsageLog("x"))
    assert "error" in FP.pick(fake_client(haiku()), None, PAGE, "x", UsageLog("x"))


def test_the_request_is_checked():
    body = {"text": "make it simpler", "quality": "playground", "followUp": {**FOLLOW, "design": default_design(OLD)}}
    req = FP.parse_pick_request(body)
    assert req["text"] == "make it simpler" and req["page"]["stages"][IDS.index("sampling")]["blocks"][-1] == PG
    assert isinstance(FP.parse_pick_request({**body, "quality": "playground-2"}), str)
    assert isinstance(FP.parse_pick_request({**body, "text": ""}), str)
    assert isinstance(FP.parse_pick_request({**body, "followUp": {"kind": "new", "previous": "x"}}), str)


def test_follow_up_pick_is_parsed():
    assert parse_pick({"targets": [], "pageWide": True}) == {"targets": [], "pageWide": True}
    assert parse_pick({"targets": [target("remove", "tokens"), target("bogus", "x")], "pageWide": False}) == {"targets": [target("remove", "tokens")], "pageWide": False}
    assert parse_pick({"targets": [], "pageWide": False}) is None
    f = parse_follow_up({**FOLLOW, "design": default_design(OLD), "pick": {"targets": [target("text", "tokens")], "pageWide": False}})
    assert f["pick"] == {"targets": [target("text", "tokens")], "pageWide": False}
    assert isinstance(parse_follow_up({**FOLLOW, "design": default_design(OLD), "pick": {"targets": []}}), str)


# ---------- the pipeline: only the picked parts change ----------


class Pipe:
    """pipeline.generate with the planner, edits, checks and extras faked."""

    def __init__(self, monkeypatch, *replies: dict):
        self.seen: dict = {"planner": [], "edited": [], "finder": 0}
        replies_ = list(replies)

        def write_outline(client, request, emit, usage, system, parse, effort, *a, **k):
            self.seen["planner"].append(request)
            p = parse(replies_.pop(0))
            return {"outline": p["outline"], "warnings": p["warnings"], **({"revision": p["revision"]} if "revision" in p else {})}

        def edit_widget(client, usage, jev_key, outline, si, bi, what, effort="medium", think=True):
            self.seen["edited"].append(outline["stages"][si]["id"])
            old = outline["stages"][si]["blocks"][bi]
            return {"block": {**old, "html": old["html"] + "<!-- edited -->"}, "changed": True, "facts": {"controls": []}, "plan": ce.read_plan(old), "notes": []}

        monkeypatch.setattr(pipeline, "write_outline", write_outline)
        monkeypatch.setattr(pipeline, "find_targets", lambda *a: self.seen.update(finder=self.seen["finder"] + 1) or None)
        monkeypatch.setattr(pipeline, "clarity_check", lambda client, jev_key, spec, usage, *a, only=None, **k: (spec, "checked"))
        monkeypatch.setattr(pipeline, "fact_check", lambda client, jev_key, spec, usage, *a, only=None, **k: (spec, "checked"))
        monkeypatch.setattr(pipeline, "write_notes", lambda client, spec, extras, usage, kept=None: (spec, ""))
        monkeypatch.setattr(ce, "edit_widget", edit_widget)
        monkeypatch.setattr(ce, "agrees", lambda *a: None)
        monkeypatch.setattr(ce, "facts_of", lambda block: {"controls": []})

    def run(self, pick: dict | None, quality: str = "playground", text: str = "change it") -> dict:
        follow = {**FOLLOW, "design": default_design(OLD), **({"pick": pick} if pick else {})}
        req = {"mode": "concept", "text": text, "quality": quality, "extras": [], "followUp": parse_follow_up(follow)}
        return pipeline.generate(None, req, lambda e: None, jev_key="jev")["spec"]


def blocks_of(spec: dict, sid: str) -> list[dict]:
    return next(s for s in spec["stages"] if s["id"] == sid)["blocks"]


def simpler(sid: str) -> dict:
    st = copy.deepcopy(next(s for s in COMPACT["stages"] if s["id"] == sid))
    st["blocks"][0] = {"type": "text", "md": f"Simpler {sid}."}
    return st


def test_a_page_wide_pick_on_a_playground_page_changes_every_section_and_keeps_the_playground(monkeypatch):
    slider = next(b for s in OLD["stages"] for b in s["blocks"] if b["type"] == "slider")
    st = simpler("tokens")
    st["blocks"].append(slider)  # a prebuilt control the planner wrote anyway
    pipe = Pipe(monkeypatch, {"changes": {"replace": [st, simpler("sampling")], "level": "practitioner"}})
    spec = pipe.run({"targets": [], "pageWide": True}, text="make it more advanced")
    request = pipe.seen["planner"][0]
    assert pipe.seen["finder"] == 0 and len(pipe.seen["planner"]) == 1  # one planner call for all sections, no finder
    assert "Change ONLY these parts" in request and all(f'stage "{sid}"' in request for sid in IDS)
    assert blocks_of(spec, "tokens")[0]["md"] == "Simpler tokens." and "slider" not in [b["type"] for b in blocks_of(spec, "tokens")]
    assert PG in blocks_of(spec, "sampling")  # the playground is put back, not frozen as text or lost
    assert spec["level"] == "practitioner"  # a whole-page change may change the level


def test_a_playground_pick_edits_only_that_playground(monkeypatch):
    pipe = Pipe(monkeypatch)
    spec = pipe.run({"targets": [target("widget", "sampling", "it shows nothing")], "pageWide": False})
    assert pipe.seen["edited"] == ["sampling"] and pipe.seen["planner"] == []
    assert any(b.get("html", "").endswith("<!-- edited -->") for b in blocks_of(spec, "sampling"))
    assert [s["id"] for s in spec["stages"]] == IDS


def test_a_remove_pick_drops_only_that_section(monkeypatch):
    pipe = Pipe(monkeypatch)
    spec = pipe.run({"targets": [target("remove", "sampling")], "pageWide": False})
    assert [s["id"] for s in spec["stages"]] == [i for i in IDS if i != "sampling"] and pipe.seen["planner"] == []
    assert spec["revision"]["removed"] == 1


def test_a_pick_that_matches_nothing_is_an_error(monkeypatch):
    with pytest.raises(ReaderError):
        Pipe(monkeypatch).run({"targets": [target("text", "ghost")], "pageWide": False})


def test_an_old_client_without_a_pick_still_never_takes_the_whole_page_path(monkeypatch):
    pipe = Pipe(monkeypatch)
    with pytest.raises(ReaderError):  # the finder couldn't place it: an error, not an Interactive rewrite
        pipe.run(None)
    assert pipe.seen["finder"] == 1 and pipe.seen["planner"] == []


def test_a_page_keeps_at_least_one_section():
    one = {**OLD, "stages": OLD["stages"][:1]}
    assert T.remove_stages(one, {IDS[0]}) == one


def test_a_playground_edit_tells_haiku_what_the_checker_finds(monkeypatch):
    sent = []
    monkeypatch.setattr(cl, "check_playground", lambda spec: {"problems": ["the chart stays empty"], "facts": None, "layout": ["a label is cut"]})
    monkeypatch.setattr(cl, "_pg_ask", lambda client, usage, step, payload, st, why, *a, edit=None, **k: sent.append(edit) or None)
    r = ce.edit_widget(None, UsageLog("x"), None, PAGE, IDS.index("sampling"), len(PAGE["stages"][IDS.index("sampling")]["blocks"]) - 1, "it shows nothing")
    assert r["changed"] is False  # the fake build wrote nothing: the old playground is kept
    assert "the chart stays empty" in sent[0]["request"] and "a label is cut" in sent[0]["request"] and sent[0]["request"].startswith("it shows nothing")
