"""Playground 2.0 (playground-2, pgpick.py): Jev picks the playground's parts, code makes the blueprint, Haiku fills it in.
Jev and Claude are faked; the node checker is real for the end-to-end tests."""

from __future__ import annotations

import json

import pytest

from explainer import codelab, pgpick
from explainer.pricing import UsageLog
from explainer.requests import parse_generate

from test_codelab import fake_jev, marked
from test_playground import GINI, fake_haiku, needs_node, widgets

NEEDS = ["the split threshold", "the impurity of each side", "the weighted impurity"]


def yes(v: float) -> dict:
    return {"type": "noul", "p": v}


def choice(*options: str) -> dict:
    return {"type": "choice", "ranking": [{"option": o, "p": 0.9 - 0.1 * i} for i, o in enumerate(options)], "confidence": 0.8}


def answers(tools: dict[str, float] | None = None, main: str = "plotted", controls: int = 1, needs: list[str] | None = None,
            none: float = 0.05) -> dict:
    """Jev's parsed answers: every tool at 0.1 unless given, the main view, the controls score and one choice per need."""
    a = {f"tool_{t['id']}": yes((tools or {}).get(t["id"], 0.1)) for t in pgpick.TOOLS}
    a.update(main_view=choice(main), controls={"type": "score", "score": controls, "norm": controls / 2, "confidence": 0.8},
             none=yes(none))
    for i, t in enumerate(needs or []):
        a[f"need_{i}"] = choice(t)
    return a


# ---------- the questions ----------


def test_questions_ask_one_yes_no_per_tool_plus_the_view_controls_and_each_need():
    qs = pgpick.questions(NEEDS)
    assert sum(1 for k in qs if k.startswith("tool_")) == len(pgpick.TOOLS)
    assert {qs[k]["type"] for k in qs if k.startswith("tool_")} == {"noul"}
    assert qs["main_view"]["type"] == "choice" and set(qs["main_view"]["criteria"]) == {"plotted", "diagram", "both"}
    assert qs["controls"]["type"] == "score" and len(qs["controls"]["criteria"]) == len(pgpick.SLIDERS_FOR_SCORE)
    assert qs["none"]["type"] == "noul"
    for i, need in enumerate(NEEDS):
        q = qs[f"need_{i}"]
        assert q["type"] == "choice" and repr(need) in q["instructions"] and set(q["criteria"]) == set(pgpick.BY_ID)
    assert len(pgpick.TOOLS) < 255  # Jev's limit on choice options


def test_tool_catalog_is_unique_and_its_helpers_are_runtime_helpers():
    ids = [t["id"] for t in pgpick.TOOLS]
    assert len(ids) == len(set(ids))
    helpers = set(codelab.data.prompt("PLAYGROUND2_SYSTEM").split("\n")[[l.startswith("seed, rand,") for l in codelab.data.prompt("PLAYGROUND2_SYSTEM").split("\n")].index(True)].split(", "))
    for t in pgpick.TOOLS:
        assert t["group"] in ("control", "number", "plot", "structure")
        for h in t.get("helpers") or []:
            assert h in helpers, h


def test_clean_needs_trims_dedupes_and_caps():
    raw = ["  a  b ", "A B", "", 3, "x" * 300, *[f"n{i}" for i in range(10)]]
    out = pgpick.clean_needs(raw)
    assert out[0] == "a b" and len(out) == pgpick.MAX_NEEDS and len(out[1]) == pgpick.NEED_CHARS
    assert pgpick.clean_needs(None) == [] and pgpick.clean_needs("text") == []


def test_pick_sends_the_section_and_needs_and_fails_open(monkeypatch):
    out = marked({"chart": True, "idea": "x"})
    seen = {}

    def jev_ask(key, state, qs):
        seen.update(state=state, qs=qs)
        return {"answers": answers(), "model": "jev-test", "usage": {"input_tokens": 50}}

    monkeypatch.setattr(pgpick, "jev_ask", jev_ask)
    usage = UsageLog("playground-2", "low")
    assert pgpick.pick("k", out, 0, NEEDS, usage) is not None
    assert seen["state"]["needs"] == NEEDS and seen["state"]["section"]["label"] == "S0" and "need_2" in seen["qs"]
    assert pgpick.pick(None, out, 0, NEEDS, usage) is None  # no key: no call

    def boom(*a):
        raise pgpick.JevError("Jev unreachable")

    monkeypatch.setattr(pgpick, "jev_ask", boom)
    assert pgpick.pick("k", out, 0, NEEDS, usage) is None


# ---------- the blueprint ----------


def test_blueprint_maps_each_need_to_a_part_and_writes_covers():
    bp = pgpick.blueprint(answers({"slider": 0.9, "bars": 0.8, "readout": 0.9}, needs=["slider", "bars", "readout"]), NEEDS)
    types = {c["id"]: c["type"] for c in bp["components"]}
    assert types == {"s1": "slider", "c1": "chart", "r1": "readout"}
    assert [c["type"] for c in bp["components"]] == ["slider", "chart", "readout"]  # controls, charts, readouts
    assert next(c for c in bp["components"] if c["id"] == "c1")["marks"] == ["bars"]
    assert bp["covers"] == [{"need": NEEDS[0], "kind": "control", "by": ["s1"]},
                            {"need": NEEDS[1], "kind": "mark", "by": ["c1"]},
                            {"need": NEEDS[2], "kind": "number", "by": ["r1"]}]


def test_blueprint_thresholds_in_optional_and_out():
    bp = pgpick.blueprint(answers({"slider": 0.8, "line": 0.75, "points": 0.5, "toggle": 0.45, "pie": 0.3}), [])
    marks = next(c for c in bp["components"] if c["type"] == "chart")["marks"]
    assert marks == ["line"]
    assert {o["tool"] for o in bp["optional"]} == {"points", "toggle"}
    assert all(o["tool"] != "pie" for o in bp["optional"])


def test_blueprint_puts_structures_on_their_own_diagram_chart():
    bp = pgpick.blueprint(answers({"slider": 0.9, "line": 0.8, "sequence": 0.85, "matrix": 0.8}, main="both"), [])
    charts = {c["id"]: c["marks"] for c in bp["components"] if c["type"] == "chart"}
    assert charts == {"c1": ["line"], "d1": ["sequence", "matrix"]}


def test_blueprint_main_view_forces_a_view_of_that_kind():
    bp = pgpick.blueprint(answers({"slider": 0.9, "line": 0.8, "graph": 0.5}, main="diagram"), [])
    assert {c["id"] for c in bp["components"] if c["type"] == "chart"} == {"c1", "d1"}
    only = pgpick.blueprint(answers({"slider": 0.9, "shapes": 0.6}, main="diagram"), [])
    assert [c["marks"] for c in only["components"] if c["type"] == "chart"] == [["shapes"]]


def test_blueprint_always_has_a_control_and_a_view():
    bp = pgpick.blueprint(answers({"play": 0.3, "bars": 0.2}), [])
    assert any(c["type"] == "play" for c in bp["components"])
    assert any(c["type"] == "chart" for c in bp["components"])


def test_drag_and_click_go_on_the_main_chart():
    bp = pgpick.blueprint(answers({"drag": 0.9, "points": 0.9, "tree": 0.8}, main="diagram", needs=["click"]), ["pick a branch"])
    d1 = next(c for c in bp["components"] if c["id"] == "d1")
    assert d1.get("drag") and d1.get("click")
    assert bp["covers"] == [{"need": "pick a branch", "kind": "control", "by": ["d1"]}]
    assert not any(c["type"] == "slider" for c in bp["components"])  # drag is the control: no slider added


def test_sliders_are_capped_by_the_controls_score():
    needs = [f"setting {i}" for i in range(6)]
    bp = pgpick.blueprint(answers({"line": 0.9}, controls=0, needs=["slider"] * 6), needs)
    sliders = [c for c in bp["components"] if c["type"] == "slider"]
    assert len(sliders) == 1 and all(c["by"] == ["s1"] for c in bp["covers"])
    bp5 = pgpick.blueprint(answers({"line": 0.9}, controls=2, needs=["slider"] * 6), needs)
    assert len([c for c in bp5["components"] if c["type"] == "slider"]) == pgpick.MAX_SLIDERS


def test_components_are_capped_and_covers_only_name_kept_ones():
    needs = [f"n{i}" for i in range(6)]
    tools = {t["id"]: 0.9 for t in pgpick.TOOLS}
    bp = pgpick.blueprint(answers(tools, main="both", needs=["readout"] * 6), needs)
    kept = {c["id"] for c in bp["components"]}
    assert len(bp["components"]) <= pgpick.MAX_COMPONENTS
    assert all(b in kept for c in bp["covers"] for b in c["by"])


def test_nothing_fits_or_no_answers_means_no_blueprint():
    assert pgpick.blueprint(answers({"slider": 0.9}, none=0.8), []) is None
    assert pgpick.blueprint(None, []) is None
    assert pgpick.blueprint({}, []) is None


# ---------- merge and deviations ----------


def test_merge_adds_needs_the_spec_left_out():
    bp = {"components": [], "covers": [{"need": "A", "kind": "control", "by": ["s1"]}, {"need": "B", "kind": "number", "by": ["r1"]}]}
    spec = {"plan": {"insight": "i", "covers": [{"need": "a", "kind": "control", "by": ["x"]}]}, "components": []}
    out = pgpick.merge(spec, bp)
    assert [c["need"] for c in out["plan"]["covers"]] == ["a", "B"] and out["plan"]["insight"] == "i"
    assert spec["plan"]["covers"] == [{"need": "a", "kind": "control", "by": ["x"]}]  # not changed in place
    assert pgpick.merge({"error": "no_playground"}, bp) == {"error": "no_playground"}
    assert pgpick.merge(spec, None) is spec


def test_deviations_find_missing_parts_marks_and_haikus_own_notes():
    bp = {"components": [{"id": "s1", "type": "slider"}, {"id": "c1", "type": "chart", "marks": ["bars", "rules"], "drag": True},
                         {"id": "r1", "type": "readout"}], "covers": []}
    spec = {"plan": {"deviations": [{"id": "r1", "change": "dropped", "why": "the chart shows it"}]},
            "components": [{"id": "s1", "type": "select"}, {"id": "c1", "type": "chart", "draw": "return [bars(state.v), hline(0.5)];"}]}
    dev = pgpick.deviations(spec, bp)
    assert "s1 is a select, not a slider" in dev and "r1 (readout) is missing" in dev and "c1 has no onDrag" in dev
    assert not any("doesn't draw" in d for d in dev)  # bars and hline (rules) are both drawn
    assert any(d.startswith("Haiku: r1: dropped") for d in dev)
    spec["components"][1]["draw"] = "return [line(state.v)];"
    assert {"c1 doesn't draw bars", "c1 doesn't draw rules"} <= set(pgpick.deviations(spec, bp))


# ---------- the mode ----------


def test_playground_2_is_a_request_and_its_settings_pick_with_jev():
    req = parse_generate({"mode": "concept", "text": "Attention", "quality": "playground-2", "planThinking": False, "reviewRounds": 2,
                          "debugRejected": True})
    assert req["quality"] == "playground-2" and req["planThinking"] is False and req["reviewRounds"] == 2 and req["debugRejected"] is True
    s = codelab.CodeLabSettings.for_effort("low", playground=True, jev_pick=True)
    assert s.playground and s.jev_pick and s.marks
    assert not codelab.CodeLabSettings.for_effort("low", playground=True).jev_pick


def test_the_writers_note_asks_for_needs_and_read_mark_keeps_them():
    assert '"needs"' in codelab.PLANNER_NOTE_PG2 and codelab.PLANNER_NOTE_PG2.startswith(codelab.PLANNER_NOTE_PG)
    m = codelab.read_mark({"interaction": {"chart": True, "kind": "quantity", "idea": "x", "needs": [" a ", "a", "b"]}})
    assert m["needs"] == ["a", "b"]
    assert "needs" not in codelab.read_mark({"interaction": {"chart": True, "idea": "x"}})


def run2(monkeypatch, replies: list, jev_answers: dict | None, needs: list[str] = NEEDS):
    sent = fake_haiku(monkeypatch, replies)
    asked = fake_jev(monkeypatch)
    picks: list[dict] = []

    def jev_ask(key, state, qs):
        picks.append({"state": state, "questions": sorted(qs)})
        if jev_answers is None:
            raise pgpick.JevError("Jev unreachable")
        return {"answers": jev_answers, "model": "jev-test", "usage": {"input_tokens": 80}}

    monkeypatch.setattr(pgpick, "jev_ask", jev_ask)
    out = marked({"chart": True, "kind": "quantity", "idea": "move the split and watch the impurity", "needs": needs})
    st = codelab.Streamed(None, UsageLog("playground-2", "low"), "k",
                          codelab.CodeLabSettings.for_effort("low", think=False, playground=True, jev_pick=True))
    st.feed(json.dumps(out))
    notes: list[str] = []
    result, stats = st.finish(out, notes)
    return result, stats, notes, sent, picks


@needs_node
def test_playground_2_sends_the_blueprint_to_haiku_and_keeps_its_needs(monkeypatch):
    jev = answers({"slider": 0.9, "select": 0.8, "button": 0.8, "points": 0.9, "bars": 0.8, "rect": 0.7, "readout": 0.9},
                  needs=["slider", "bars", "readout"])
    result, stats, notes, sent, picks = run2(monkeypatch, [GINI] * 4, jev)
    assert len(picks) == 1 and picks[0]["state"]["needs"] == NEEDS
    first = sent[0]
    assert first["step"] == "playground write" and "## Your blueprint" in first["system"] and "## Your views" not in first["system"]
    bp = first["user"]["blueprint"]
    assert {c["id"] for c in bp["components"]} >= {"s1", "c1", "r1"} and [c["need"] for c in bp["covers"]] == NEEDS
    # every Haiku call for this section (write and any repair) carries the blueprint and the Playground 2.0 prompt
    assert all("blueprint" in s["user"] and "## Your blueprint" in s["system"] for s in sent)
    (info,) = stats["blueprint"]
    assert info["needs"] == NEEDS and "s1: slider" in info["picked"]
    if widgets(result):  # GINI uses its own ids, so the spec leaves the blueprint: logged, never fatal
        assert info["built"] and any("missing" in d for d in info["deviations"])


@needs_node
def test_playground_2_without_jev_is_built_like_playground(monkeypatch):
    result, stats, notes, sent, picks = run2(monkeypatch, [GINI], None)
    assert len(picks) == 1 and "blueprint" not in sent[0]["user"]
    assert "You design the interactive playground" in sent[0]["system"] and "## Your views" in sent[0]["system"]
    assert any(n.startswith("Playground 2.0 (S0): Jev didn't answer") for n in notes)
    assert len(widgets(result)) == 1


def test_playground_prompt_is_unchanged_by_playground_2():
    pg, pg2 = codelab.data.prompt("PLAYGROUND_SYSTEM"), codelab.data.prompt("PLAYGROUND2_SYSTEM")
    assert "## Your views" in pg and "7. covers:" in pg and "## Your blueprint" not in pg
    assert "## Your views" not in pg2 and "7. covers:" not in pg2 and "Choose freely" not in pg2
    # the reference parts are shared word for word (one cached prompt each, same rules for both modes)
    ref = pg[pg.index("## Spec format"):pg.index("- Choose freely")]
    assert ref in pg2
