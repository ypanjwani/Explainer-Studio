"""The first-message step (FIRST_TALK=1, modes/intake.py): every message that would start a new explainer is read first and
ends in answer (a reply, no page, not counted) or new_topic (built from its brief). Claude is faked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conftest import signup
from explainer import history
from explainer.modes import explorer, intake, reader, topic
from explainer.modes.followup.v5 import tools as v5_tools
from explainer.requests import parse_generate
from explainer.routes import ai


def call(name: str, inp: dict) -> SimpleNamespace:
    return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=500, output_tokens=80),
                           content=[SimpleNamespace(type="tool_use", id="t1", name=name, input=inp)])


@pytest.fixture
def first(monkeypatch):
    """FIRST_TALK on; the step's Haiku call answers with `state["reply"]` and records what it was sent. The pipelines are
    faked: each records the request it built."""
    monkeypatch.setenv("FIRST_TALK", "1")
    state: dict = {"reply": call("new_topic", {"brief": "LoRA for fine-tuning"}), "sent": [], "built": []}

    def fake_call(client, usage, content):
        state["sent"].append(content)
        r = state["reply"]
        if isinstance(r, Exception):
            raise r
        usage.add_message("first message", r)
        return r

    def build(client, req, emit, **kw):
        state["built"].append(req)
        return {"spec": {"title": "T", "stages": []},
                "usage": {"quality": req["quality"], "effort": "low", "steps": [], "totalUsd": 0.0, "seconds": 1}}

    monkeypatch.setattr(intake, "_call", fake_call)
    monkeypatch.setattr(reader, "generate", build)
    monkeypatch.setattr(explorer.pipeline, "generate", build)
    return state


def events(res) -> list[dict]:
    return [json.loads(line[6:]) for line in res.get_data(as_text=True).splitlines() if line.startswith("data: ")]


def used(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).get_json()["allowance"]["generationsUsed"]


CONCEPT = {"mode": "concept", "text": "LoRA", "quality": "playground"}


@pytest.mark.parametrize("quality", ["lite-plus", "playground"])
def test_a_topic_is_built_from_its_brief(client, first, quality):
    h = signup(client)
    ev = events(client.post("/api/generate", json={**CONCEPT, "quality": quality}, headers=h))
    assert ev[-1]["type"] == "done"
    assert first["built"][0]["text"] == "LoRA for fine-tuning"
    assert [e["status"] for e in ev if e.get("step") == "intake"] == ["running", "done"]
    assert used(client, h) == 1


def test_an_answer_builds_nothing_and_isnt_counted(client, first):
    first["reply"] = call("answer", {"reply": "A page on KL would cover: …", "offer": "KL divergence for beginners"})
    h = signup(client)
    ev = events(client.post("/api/generate", json={**CONCEPT, "text": "what topics would a KL explainer include?"}, headers=h))
    assert first["built"] == []
    assert ev[-1]["type"] == "error" and ev[-1]["message"].startswith(topic.ANSWER)
    assert json.loads(ev[-1]["message"][len(topic.ANSWER):]) == {"reply": "A page on KL would cover: …", "offer": "KL divergence for beginners"}
    assert used(client, h) == 0


def test_the_record_shows_the_answer_and_its_offer():
    gen = SimpleNamespace(status="error", error=topic.answer_message("Hi! Name an AI topic.", "Attention for beginners"),
                          steps={}, updated_at=1, spec=None, usage=None, meta={})
    out = history._apply_generation({"updatedAt": 0}, gen)
    assert out["status"] == "done" and out["answer"] == "Hi! Name an AI topic." and out["offer"] == "Attention for beginners"
    assert "error" not in out
    gen.error = topic.answer_message("Hello!")
    assert "offer" not in history._apply_generation({"updatedAt": 0}, gen)


def test_a_new_topic_with_a_brief_is_a_picking_record_with_its_brief():
    gen = SimpleNamespace(status="error", error=topic.new_topic_message("That's another topic.", "Beam search, shorter"),
                          steps={}, updated_at=1, spec=None, usage=None, meta={})
    out = history._apply_generation({"updatedAt": 0}, gen)
    assert out["status"] == "picking"
    assert out["plan"] == {"kind": "cannot", "reason": "That's another topic.", "alternative": None, "newTopic": True,
                           "brief": "Beam search, shorter"}


def test_the_conversation_and_paper_reach_the_step(client, first):
    h = signup(client)
    paper = {"mode": "paper", "title": "Attention Is All You Need", "note": "", "quality": "playground",
             "sections": [{"heading": "Abstract", "text": "We propose the Transformer."}],
             "history": ['"what would a page on it cover?": answered in the chat, nothing changed: "…"']}
    first["reply"] = call("new_topic", {"brief": "The whole paper"})
    events(client.post("/api/generate", json=paper, headers=h))
    sent = first["sent"][0]
    assert "Attention Is All You Need" in sent and "We propose the Transformer." in sent and "what would a page on it cover?" in sent
    built = first["built"][0]
    assert built["note"] == "The whole paper" and built["sections"] == paper["sections"]


@pytest.mark.parametrize("body", [{**CONCEPT, "direct": True},
                                  {**CONCEPT, "followUp": {"kind": "new", "previous": "1. \"T\""}, "direct": True}])
def test_direct_skips_the_step(client, first, body):
    events(client.post("/api/generate", json=body, headers=signup(client)))
    assert first["sent"] == [] and first["built"][0]["text"] == "LoRA"


def test_on_by_default_off_with_zero(client, first, monkeypatch):
    monkeypatch.delenv("FIRST_TALK")  # on by default (Nov 19 2026)
    assert intake.on()
    monkeypatch.setenv("FIRST_TALK", "0")
    events(client.post("/api/generate", json=CONCEPT, headers=signup(client)))
    assert first["sent"] == [] and first["built"][0]["text"] == "LoRA"


def test_never_for_a_revise_follow_up(monkeypatch):
    monkeypatch.setenv("FIRST_TALK", "1")
    assert not intake.runs({**CONCEPT, "followUp": {"kind": "revise", "outline": "{}"}})
    assert intake.runs(CONCEPT)


@pytest.mark.parametrize("reply", [RuntimeError("down"), call("answer", {"reply": ""}), call("new_topic", {"brief": "  "})])
def test_a_failed_or_empty_call_builds_as_asked(client, first, reply):
    first["reply"] = reply
    ev = events(client.post("/api/generate", json=CONCEPT, headers=signup(client)))
    assert ev[-1]["type"] == "done" and first["built"][0]["text"] == "LoRA"


def test_past_the_days_explainers_it_still_answers(client, first, monkeypatch):
    monkeypatch.setattr(ai.quota, "over_daily", lambda *a: True)
    h = signup(client)
    ev = events(client.post("/api/generate", json=CONCEPT, headers=h))
    assert first["built"] == [] and "today's free explainers" in ev[-1]["message"]
    assert "Today no page can be built" in first["sent"][0]
    first["reply"] = call("answer", {"reply": "Dropout drops units.", "offer": "Dropout"})
    ev = events(client.post("/api/generate", json=CONCEPT, headers=h))
    assert json.loads(ev[-1]["message"][len(topic.ANSWER):]) == {"reply": "Dropout drops units."}  # no offer it can't keep


def test_history_and_direct_are_parsed():
    req = parse_generate({**CONCEPT, "history": ["a", "", 3, "b"], "direct": True})
    assert req["history"] == ["a", "b"] and req["direct"] is True
    req = parse_generate({**CONCEPT, "history": "x", "direct": "yes"})
    assert "history" not in req and "direct" not in req


def test_apply_brief():
    assert topic.apply_brief(CONCEPT, "LoRA, light maths")["text"] == "LoRA, light maths"
    paper = {"mode": "paper", "title": "T", "note": "", "sections": [{"heading": "", "text": "x"}]}
    out = topic.apply_brief(paper, "Its results")
    assert out["note"] == "Its results" and out["sections"] == paper["sections"]


def test_v5_new_topic_asks_for_a_brief():
    t = next(d for d in v5_tools._END if d["name"] == "new_topic")
    assert t["input_schema"]["required"] == ["reply", "brief"]
