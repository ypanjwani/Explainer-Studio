"""/api/generate sends a new Reader or Explorer page to its own pipeline (explainer/modes/) and everything else to the
old one (pipeline.generate). MODES_SPLIT=0 sends everything to the old one."""

from __future__ import annotations

import json

import pytest

from conftest import signup
from explainer.routes import ai


def events(res) -> list[dict]:
    return [json.loads(line[6:]) for line in res.get_data(as_text=True).splitlines() if line.startswith("data: ")]


@pytest.fixture
def which(monkeypatch) -> list[str]:
    """Fakes both pipelines; each run records which one it went to."""
    went: list[str] = []

    def fake(name):
        def generate(client, req, emit, **kw):
            went.append(name)
            return {"spec": {"title": "T", "stages": []},
                    "usage": {"quality": req["quality"], "effort": "low", "steps": [], "totalUsd": 0.0, "seconds": 1}}
        return generate

    monkeypatch.setattr(ai, "generate", fake("old"))
    monkeypatch.setattr(ai.modes, "generate", fake("modes"))
    return went


CONCEPT = {"mode": "concept", "text": "How does dropout work?"}


@pytest.mark.parametrize("quality, expected", [("lite-plus", "modes"), ("playground", "modes"), ("quality-plus", "old"), ("playground-2", "old")])
def test_new_page_goes_to_its_mode(client, which, quality, expected):
    events(client.post("/api/generate", json={**CONCEPT, "quality": quality}, headers=signup(client)))
    assert which == [expected]


def test_kill_switch_sends_everything_to_the_old_pipeline(client, which, monkeypatch):
    monkeypatch.setenv("MODES_SPLIT", "0")
    a = signup(client)
    for q in ("lite-plus", "playground"):
        events(client.post("/api/generate", json={**CONCEPT, "quality": q}, headers=a))
    assert which == ["old", "old"]


# ---------- follow-ups ----------

def revise_body(quality: str, **extra) -> dict:
    from explainer.core.validate import default_design
    from followup_v3_data import FOLLOW, OLD, intent
    follow = {**FOLLOW, "design": default_design(OLD), **extra}
    if extra.get("plan") is True:
        follow["plan"] = {"sections": [intent("tokens", "simpler")], "steps": []}
    return {"mode": "concept", "text": "make it simpler", "quality": quality, "followUp": follow}


@pytest.mark.parametrize("quality", ["lite-plus", "playground"])
def test_a_v3_follow_up_goes_to_its_mode(client, which, quality):
    events(client.post("/api/generate", json=revise_body(quality, plan=True), headers=signup(client)))
    assert which == ["modes"]


@pytest.mark.parametrize("extra", [{}, {"add": True}, {"plan": True, "add": True},
                                   {"pick": {"targets": [{"stage": "tokens", "part": "text", "what": "simpler"}], "pageWide": False}}],
                         ids=["no-plan", "add-section", "add-with-plan", "old-pick"])
def test_other_follow_ups_stay_on_the_old_pipeline(client, which, extra):
    """Add section, the old section pick and a follow-up without a plan (an old tab, a script) are legacy for now."""
    events(client.post("/api/generate", json=revise_body("playground", **extra), headers=signup(client)))
    assert which == ["old"]


def test_follow_ups_of_other_modes_stay_on_the_old_pipeline(client, which):
    events(client.post("/api/generate", json=revise_body("quality-plus", plan=True), headers=signup(client)))
    assert which == ["old"]
