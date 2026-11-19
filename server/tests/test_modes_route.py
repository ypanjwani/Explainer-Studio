"""/api/generate runs a new Reader or Explorer page, and a v3 follow-up on one, on their own pipelines (explainer/modes/).
Everything else (an old mode's page, review rounds, Add section, the old section pick, a follow-up without a v3 plan) is
refused (400 mode_off) and not counted."""

from __future__ import annotations

import json

import pytest

from conftest import signup
from explainer.routes import ai


def events(res) -> list[dict]:
    return [json.loads(line[6:]) for line in res.get_data(as_text=True).splitlines() if line.startswith("data: ")]


@pytest.fixture
def which(monkeypatch) -> list[str]:
    """Fakes Reader and Explorer's pipeline; each run records that it went there."""
    went: list[str] = []

    def generate(client, req, emit, **kw):
        went.append("modes")
        return {"spec": {"title": "T", "stages": []},
                "usage": {"quality": req["quality"], "effort": "low", "steps": [], "totalUsd": 0.0, "seconds": 1}}

    monkeypatch.setattr(ai.modes, "generate", generate)
    return went


CONCEPT = {"mode": "concept", "text": "How does dropout work?"}


def allowance(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).get_json()["allowance"]["generationsUsed"]


def refused(client, body, headers) -> None:
    """The request is refused with mode_off before anything runs, and isn't one of the day's explainers."""
    res = client.post("/api/generate", json=body, headers=headers)
    assert res.status_code == 400
    assert res.get_json()["code"] == "mode_off"
    assert allowance(client, headers) == 0


@pytest.mark.parametrize("quality", ["lite-plus", "playground"])
def test_new_page_goes_to_its_mode(client, which, quality):
    events(client.post("/api/generate", json={**CONCEPT, "quality": quality}, headers=signup(client)))
    assert which == ["modes"]


@pytest.mark.parametrize("quality", ["quality-plus", "playground-2", "quality-deep", "freeform-plus"])
def test_other_modes_are_refused(client, which, quality):
    refused(client, {**CONCEPT, "quality": quality}, signup(client))
    assert which == []


def test_review_rounds_are_ignored(client, which):
    """An old app tab may still send review rounds: the page is built as a plain Explorer page."""
    events(client.post("/api/generate", json={**CONCEPT, "quality": "playground", "reviewRounds": 2}, headers=signup(client)))
    assert which == ["modes"]


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


@pytest.mark.parametrize("extra", [{}, {"add": True},
                                   {"pick": {"targets": [{"stage": "tokens", "part": "text", "what": "simpler"}], "pageWide": False}}],
                         ids=["no-plan", "add-section", "old-pick"])
def test_other_follow_ups_are_refused(client, which, extra):
    """Add section, the old section pick and a follow-up without a plan (an old tab, a script)."""
    refused(client, revise_body("playground", **extra), signup(client))
    assert which == []


def test_old_flags_on_a_planned_follow_up_are_ignored(client, which):
    """An old tab's Add section / Feedback flags are dropped; a follow-up with a v3 plan runs as usual."""
    events(client.post("/api/generate", json=revise_body("playground", plan=True, add=True, feedback={"id": "x"}), headers=signup(client)))
    assert which == ["modes"]



def test_follow_ups_on_old_mode_pages_are_refused(client, which):
    refused(client, revise_body("quality-plus", plan=True), signup(client))
    assert which == []


def test_removed_routes_are_gone(client):
    """The old section pick, the Feedback button, saved preferences and Edit chat were removed (Nov 2026)."""
    a = signup(client)
    for method, path in [("post", "/api/followup/pick"), ("post", "/api/feedback"), ("post", "/api/feedback/x/confirm"),
                         ("get", "/api/preferences"), ("put", "/api/preferences/x"), ("delete", "/api/preferences/x"), ("post", "/api/edit")]:
        assert getattr(client, method)(path, json={}, headers=a).status_code in (404, 405), path
