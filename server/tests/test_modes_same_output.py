"""Reader and Explorer's own pipelines (explainer/modes/) make the same page as the old shared one (pipeline.generate)
for the same AI answers. Haiku is faked at the client and Jev at its HTTP call, with replies chosen by prompt and
content (never by call order, so the parallel steps can't change them). The playground checker (node) is real.
No API calls, no cost. The fakes are in modes_fakes.py."""

from __future__ import annotations

import shutil

import pytest

from explainer import codelab, modes, pipeline
from explainer.modes.explorer import playground as explorer_playground
from explainer.requests import parse_generate

from modes_fakes import jev_post, run

needs_node = pytest.mark.skipif(not shutil.which("node") or not codelab.PG_SCRIPT.exists(), reason="node not available")


@pytest.fixture
def fake_jev(monkeypatch):
    monkeypatch.setattr("explainer.jev.httpx.post", jev_post)
    monkeypatch.setattr(codelab, "snap_widget", lambda html: None)
    monkeypatch.setattr(explorer_playground, "snap_widget", lambda html: None)


def same(req: dict) -> dict:
    """Runs the old and the new pipeline on req and checks every observable result matches."""
    assert modes.handles(req)
    old, new = run(pipeline.generate, req), run(modes.generate, req)
    assert new["out"]["spec"] == old["out"]["spec"]
    for k in ("rejected", "labMarks", "labLook", "labMath", "labBlueprint"):
        assert new["out"].get(k) == old["out"].get(k), k
    assert new["done"] == old["done"]
    assert new["steps"] == old["steps"]
    assert new["calls"] == old["calls"]
    assert (new["quality"], new["effort"]) == (old["quality"], old["effort"])
    return new


CONCEPT = {"mode": "concept", "text": "Explain Gini impurity in decision trees."}
PAPER = {"mode": "paper", "title": "Splitting criteria", "note": "", "sections": [
    {"heading": "Method", "text": "We measure Gini impurity at each split. " * 20},
    {"heading": "Results", "text": "Lower impurity gives purer leaves. " * 20},
    {"heading": "References", "text": "[1] Breiman et al. 1984."}]}
NEW_FOLLOW = {"kind": "new", "previous": "1. Entropy explained", "source": "Entropy is the expected surprise."}


def req(body: dict) -> dict:
    r = parse_generate(body)
    assert isinstance(r, dict), r
    return r


@pytest.mark.parametrize("body", [
    {**CONCEPT, "quality": "lite-plus"},
    {**CONCEPT, "quality": "lite-plus", "effort": "high", "colorMode": "dark", "extras": []},
    {**CONCEPT, "quality": "lite-plus", "text": "Explain Gini impurity in 3 stages", "followUp": NEW_FOLLOW},
    {**PAPER, "quality": "lite-plus"},
], ids=["concept", "options", "new-follow-up", "paper"])
def test_reader_same_page(fake_jev, body):
    r = same(req(body))
    spec = r["out"]["spec"]
    assert spec["noAsk"] and spec["noControls"]
    assert not any(b["type"] in ("quiz", "reveal") for st in spec["stages"] for b in st["blocks"])


@needs_node
@pytest.mark.parametrize("body", [
    {**CONCEPT, "quality": "playground"},
    {**CONCEPT, "quality": "playground", "text": "Explain Gini impurity, gated", "planThinking": False, "effort": "low"},
    {**CONCEPT, "quality": "playground", "extras": [], "colorMode": "light", "interactivity": "hands-on", "followUp": NEW_FOLLOW},
    {**PAPER, "quality": "playground"},
], ids=["marked", "gated", "options-new-follow-up", "paper"])
def test_explorer_same_page(fake_jev, body):
    r = same(req(body))
    widgets = [b for st in r["out"]["spec"]["stages"] for b in st["blocks"] if b["type"] == "widget"]
    assert widgets, "the fakes should produce at least one playground"


@needs_node
def test_explorer_paths_covered(fake_jev):
    """The marked page goes through every playground path: kept, repaired after the checker, fixed after the review, none."""
    r = same(req({**CONCEPT, "quality": "playground"}))
    spec = r["out"]["spec"]
    built = {st["label"] for st in spec["stages"] if any(b["type"] == "widget" for b in st["blocks"])}
    assert built == {"Kept part", "Checker part", "Rejected part"}
    assert "playground repair" in r["steps"]
    assert any("nothing to play with" in w for w in spec["warnings"])


def test_revise_follow_ups_stay_on_the_old_path():
    revise = {"kind": "revise", "baseId": "x"}
    assert not modes.handles({"quality": "playground", "followUp": revise})
    assert not modes.handles({"quality": "lite-plus", "followUp": revise})
    assert not modes.handles({"quality": "playground", "reviewRounds": 2})
    assert not modes.handles({"quality": "quality-plus"})
    assert not modes.handles({"quality": "playground-2"})
    assert modes.handles({"quality": "playground", "followUp": {"kind": "new"}})


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("MODES_SPLIT", "0")
    assert not modes.handles({"quality": "playground"})
