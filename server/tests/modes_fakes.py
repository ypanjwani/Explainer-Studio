"""Fakes shared by the modes tests: Haiku at the client, Jev at its HTTP call, a few outlines and playground specs.
Imports nothing from other modes' code, so test_modes_boundary.py can use it with every other mode blocked."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from explainer import data
from explainer.core.validate import default_design
from explainer.llm import HAIKU

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "playground"
GINI = json.loads((FIXTURES / "gini-impurity.json").read_text(encoding="utf-8"))
# A slider whose key isn't in state: the checker rejects it with a fixable message (a repair round).
BROKEN = {"title": "Broken", "state": {"a": 1},
          "components": [{"id": "s", "type": "slider", "label": "B", "key": "b", "min": 0, "max": 5},
                         {"id": "r", "type": "readout", "label": "A", "value": "return state.a;"}]}
# Passes the checker, but the fake reviewer rejects any control labelled "Alpha" (a fix round).
ALPHA = copy.deepcopy(GINI)
ALPHA["components"][0]["label"] = "Alpha"

TEXT = "Gini impurity measures how mixed a group is: 0 when every item has one class, higher as the classes mix."


def stage(i: int, label: str, mark: dict | None, extra: list[dict] | None = None) -> dict:
    st = {"id": f"s{i}", "label": label, "phase": "p", "blurb": f"About {label.lower()}.",
          "blocks": [{"type": "text", "md": f"{label}: {TEXT}"}, *(extra or [])]}
    if mark is not None:
        st["interaction"] = mark
    return st


def mark(on: bool, idea: str = "") -> dict:
    return {"chart": on, "kind": "quantity" if on else "", "idea": idea}


def page(stages: list[dict]) -> dict:
    return {"title": "Gini impurity", "subtitle": "How decision trees pick a split", "level": "student", "layout": "story",
            "keyIntuition": "A good split leaves each side mostly one class.", "phases": [{"id": "p", "label": "Basics"}],
            "glossary": [{"term": "Gini", "definition": "A measure of how mixed a group is."}], "stages": stages}


# Explorer, all marked: one kept, one repaired after the checker, one fixed after the review, one with nothing to play with.
MARKED = page([stage(0, "Intro", mark(False)), stage(1, "Kept part", mark(True, "move the split, watch the impurity")),
               stage(2, "Checker part", mark(True, "drag the split point")), stage(3, "Rejected part", mark(True, "compare two splits")),
               stage(4, "Nothing part", mark(True, "explore the formula")), stage(5, "Summary", mark(False))])
# Explorer, few marks: unmarked sections go through Jev's gate ("Open part" opens, "Closed part" stays closed).
GATED = page([stage(0, "Kept part", mark(True, "move the split")), stage(1, "Open part", None), stage(2, "Closed part", None),
              stage(3, "Summary", None)])
# Reader: a quiz and a reveal the code must strip (LITE_DROPPED), a callout it keeps.
LITE = page([stage(0, "First idea", None, [{"type": "callout", "md": "Zero means pure."},
                                           {"type": "quiz", "questions": [{"q": "Pure means?", "options": ["0", "1"], "answer": 0, "why": "w"}]}]),
             stage(1, "Second idea", None, [{"type": "reveal", "prompt": "Guess", "md": "It rises."}])])


def system_of(params: dict) -> str:
    s = params["system"]
    return s[0]["text"] if isinstance(s, list) else s


def user_of(params: dict) -> str:
    c = params["messages"][0]["content"]
    return c if isinstance(c, str) else json.dumps(c)


def haiku_reply(params: dict) -> str:
    """Haiku's answer, chosen by the system prompt and the request's content."""
    system, user = system_of(params), user_of(params)
    p = data.prompt
    # Follow-ups v3: what the follow-up asks, the page map, and the section editor.
    if system == p("UNDERSTAND_SYSTEM"):
        return json.dumps({"kind": "change", "sections": [{"section": "tokens", "intent": "make it clearer", "quote": "clearer"}], "steps": [],
                           "question": {"text": "", "options": []}, "reason": "", "alternative": {"label": "", "sections": [], "steps": []}})
    if system == p("INDEX_SYSTEM"):
        return "{}"
    if p("SECTION_EDIT_NOTE") in user:
        section = json.loads(user.split("\n\n" + p("SECTION_EDIT_NOTE"))[0])["section"]
        blocks = [{**b, "md": "Clearer: " + b["md"]} if i == 0 and b.get("type") == "text" else b for i, b in enumerate(section["blocks"])]
        return json.dumps({"decision": "change", "reason": "Reworded the opening.", "stage": {**section, "blocks": blocks},
                           "playground": {"action": "keep", "instructions": ""}})
    if system == p("SUMMARIZE_SYSTEM"):
        return "Summary: " + user.splitlines()[0][:60]
    if system == p("LITE_SYSTEM"):
        return json.dumps(LITE)
    if system == p("OUTLINE_SYSTEM"):
        return json.dumps(GATED if "gated" in user else MARKED)
    if system == p("DESIGN_SYSTEM"):
        return json.dumps(default_design(MARKED))
    if system.startswith(p("PLAYGROUND_SYSTEM")):
        if p("PLAYGROUND_REPAIR") in system:
            return json.dumps(GINI)
        if p("PLAYGROUND_EDIT") in system:  # a follow-up's change to a finished playground
            return json.dumps(ALPHA if "Alpha" in user else GINI)
        for label, spec in (("Checker part", BROKEN), ("Rejected part", ALPHA), ("Nothing part", {"error": "no_playground"})):
            if label in user:
                return json.dumps(spec)
        return json.dumps(GINI)
    return "{}"  # anything else (clarity, fact check fixes): unusable, handled by each step's own fallback


class _Msg:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.model = HAIKU
        self.usage = {"input_tokens": 100, "output_tokens": len(text) // 4}
        self.stop_reason = "end_turn"


class _Stream:
    def __init__(self, text: str):
        self.msg = _Msg(text)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        t = self.msg.content[0].text
        return (t[i:i + 40] for i in range(0, len(t), 40))

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[str] = []

        def create(**params):
            return self._log(_Msg(haiku_reply(params)), params)

        def stream(**params):
            return self._log(_Stream(haiku_reply(params)), params)

        self.messages = SimpleNamespace(create=create, stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))

    def _log(self, out, params):
        with self.lock:
            self.calls.append(system_of(params)[:40])
        return out


GOOD = {"controls_on_topic": 0.9, "change_shows_lesson": 0.9, "uses_section_terms": 0.9, "same_idea": 0.5, "backup_direct": 0.1}
FIXABLE = {**GOOD, "change_shows_lesson": 0.1, "same_idea": 0.9}


def jev_answers(state: dict, questions: dict) -> dict:
    """Jev's answers, chosen by the questions and the state."""
    if "controls_on_topic" in questions:  # the playground review
        labels = [c.get("label") for c in state["widget"]["controls"]]
        vals = FIXABLE if "Alpha" in labels else GOOD
        return {k: {"noul": vals.get(k, 0.5)} for k in questions}
    if "varies" in questions:  # the gate
        yes = state["section"]["label"].startswith("Open")
        return {k: {"noul": (0.9 if yes else 0.1) if k == "varies" else (0.1 if yes else 0.9) if k == "static" else 0.1} for k in questions}
    if any(k.startswith("shape_") for k in questions):  # the page brief
        out = {}
        for k, q in questions.items():
            if q["type"] == "choice":
                opts = list(q["criteria"])
                out[k] = {"probabilities": {o: (0.9 if i == 0 else 0.05) for i, o in enumerate(opts)}, "confidence": 0.9}
            elif q["type"] == "noul":
                out[k] = {"noul": 0.2}
        return out
    return {}  # filter, clarity, fact check, repeat check: no answer (each falls back)


def jev_post(url, headers=None, json=None, timeout=None):
    """Stands in for httpx.post in explainer.jev."""
    body = {"answers": jev_answers(json["state"], json["questions"]), "model": "jev-test", "usage": {"input_tokens": 50, "output_tokens": 0}}
    return SimpleNamespace(status_code=200, json=lambda: body)


def run(fn, req: dict) -> dict:
    events: list[dict] = []
    lock = threading.Lock()

    def emit(e: dict) -> None:
        with lock:
            events.append(e)

    client = FakeClient()
    out = fn(client, copy.deepcopy(req), emit, jev_key="k", plan_effort="medium")
    done = sorted((e["step"], e.get("detail") or "") for e in events if e.get("type") == "step" and e["status"] == "done")
    steps = sorted(s["step"] for s in out["usage"]["steps"])
    return {"out": out, "done": done, "steps": steps, "calls": sorted(client.calls), "quality": out["usage"]["quality"],
            "effort": out["usage"]["effort"]}
