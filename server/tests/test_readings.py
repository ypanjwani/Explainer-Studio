"""Playground readings (Nov 13 2026): explorer.playground.read_playground runs scripts/read-playground.mjs for real and
says what a playground shows; Jev's text-vs-readings check (explorer.judge.readings_contradict) is faked."""

from __future__ import annotations

import shutil

import pytest

from explainer.modes.explorer import judge
from explainer.modes.explorer import playground as P
from explainer.pricing import UsageLog

SPEC = {
    "title": "Error by degree", "state": {"d": 3},
    "init": "return {noise: randn(0, 1)}",
    "components": [
        {"id": "deg", "type": "slider", "key": "d", "min": 1, "max": 9, "label": "Degree"},
        {"id": "err", "type": "readout", "label": "Test error", "value": "return 0.1 * state.d * state.d + 0 * state.noise"},
        {"id": "c", "type": "chart", "title": "Error vs degree",
         "draw": "const p = []; for (let i = 1; i <= 9; i++) p.push([i, (i - 4) * (i - 4)]); return [line(p, {label: 'test'}), vline(state.d)]"},
    ],
}

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_readings_show_the_numbers_at_each_setting_and_are_the_same_each_time():
    r = P.read_playground(SPEC)
    labels = [x["label"] for x in r["runs"]]
    assert labels == ["at the start", "Degree = 1 (min)", "Degree = 9 (max)"]
    assert r["runs"][2]["readouts"] == {"Test error": 8.1}
    assert "dips at x=4" in r["text"] and "vertical line at x=9" in r["text"]
    assert P.read_playground(SPEC) == r  # seeded: the same numbers every run


@needs_node
def test_chosen_runs_and_a_broken_run():
    r = P.read_playground(SPEC, [{"set": {"d": 5}}, {"set": {"nope": 1}}])
    assert r["runs"][0]["readouts"] == {"Test error": 2.5}
    assert "not a state key" in r["runs"][1]["error"]
    assert P.read_playground({"title": "x", "state": {}, "components": [{"id": "a", "type": "readout", "value": "nope("}]})["text"].startswith("Couldn't run it")


def test_no_node_means_no_readings(monkeypatch):
    monkeypatch.setattr(P, "_node", lambda: None)
    assert P.read_playground(SPEC) is None and P.readings_text(None) == ""


def test_jevs_text_vs_readings_check(monkeypatch):
    st = {"label": "Overfitting", "blocks": [{"type": "text", "md": "Test error rises past degree 4."}]}
    shown = [{"title": "Error", "readings": "- at the start: ..."}]
    asked = {}

    def fake(key, state, questions):
        asked.update(state=state)
        return {"answers": {"numbers_agree": {"p": 0.9}, "trends_agree": {"p": 0.1}}, "model": "jev", "usage": {"input_tokens": 10}}
    monkeypatch.setattr(judge, "jev_ask", fake)
    assert judge.readings_contradict("k", st, shown, UsageLog("x")) == judge.READINGS_PROBLEMS["trends_agree"]
    assert asked["state"]["playgrounds"][0]["readings"] == "- at the start: ..." and "rises" in asked["state"]["section"]["text"]
    assert judge.readings_contradict(None, st, shown, UsageLog("x")) is None
    assert judge.readings_contradict("k", st, [{"title": "x", "readings": ""}], UsageLog("x")) is None

    def down(*a):
        raise judge.JevError("Jev unreachable")
    monkeypatch.setattr(judge, "jev_ask", down)
    assert judge.readings_contradict("k", st, shown, UsageLog("x")) is None
