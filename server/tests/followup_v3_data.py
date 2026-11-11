"""Shared test data for follow-ups v3 (test_understand.py, test_section_editor.py, test_steps.py): the sample page with one
playground, the revise follow-up the app sends for it, and fakes for Haiku and the page map cache."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from explainer.modes.followup import page_index as pidx

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"))
OLD = SAMPLE["outline"]
IDS = [s["id"] for s in OLD["stages"]]


def playground(title: str, label: str = "temperature") -> dict:
    spec = {"title": title, "plan": {}, "state": {}, "components": [{"id": "s1", "type": "slider", "label": label}, {"id": "c1", "type": "chart"}]}
    return {"type": "widget", "title": title, "html": f'<div id="pg"></div><script>XW.playground({json.dumps(spec)});</script>',
            "plan": json.dumps({"lesson": "temperature reshapes the odds"})}


PG = playground("Temperature explorer")
PAGE = {**OLD, "stages": [{**s, "blocks": [*s["blocks"], PG]} if s["id"] == "sampling" else s for s in OLD["stages"]]}
COMPACT = {**OLD, "stages": [{**s, "blocks": [*s["blocks"], {"type": "text", "md": "(interactive widget: Temperature explorer)"}]}
                             if s["id"] == "sampling" else s for s in OLD["stages"]]}
FOLLOW = {"kind": "revise", "outline": json.dumps(COMPACT), "widgets": {"sampling": [PG]}}


def step(op: str, section: str = "", what: str = "change it", quote: str = "", **kw) -> dict:
    return {"op": op, "section": section, "other": kw.get("other", ""), "after": kw.get("after", ""), "level": kw.get("level", ""),
            "what": what, "quote": quote}


def intent(section: str, text: str = "make it clearer", quote: str = "", **kw) -> dict:
    return {"section": section, "intent": text, "quote": quote, **kw}


def index_of(page: dict, **ai: dict) -> dict:
    """The page map's code part, with AI fields per section id."""
    idx = pidx.code_index(page)
    for s in idx["sections"]:
        s.update(ai.get(s["id"], {}))
    return idx


class Store:
    """The page map cache, in memory."""

    def __init__(self, index: dict | None = None):
        self.index = index

    def get(self, key):
        return self.index

    def put(self, key, value):
        self.index = value


def fake_client(*replies, sent: list | None = None):
    """Haiku with structured output: answers with the next reply (an Exception is raised)."""
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


def understood(kind: str = "change", sections=(), steps=(), question=None, reason: str = "", alternative=None) -> dict:
    return {"kind": kind, "sections": list(sections), "steps": list(steps), "question": question or {"text": "", "options": []},
            "reason": reason, "alternative": alternative or {"label": "", "sections": [], "steps": []}}


def stage_of(page: dict, sid: str) -> dict:
    return next(s for s in page["stages"] if s["id"] == sid)


def edited(sid: str, words: str | None = None, page: dict = COMPACT) -> dict:
    """The section as the editor would return it: its first block reworded."""
    import copy
    st = copy.deepcopy(stage_of(page, sid))
    st["blocks"][0] = {"type": "text", "md": words or f"Clearer {sid}."}
    return st


def editor_reply(decision: str = "change", reason: str = "Done.", stage: dict | None = None, action: str = "keep", instructions: str = "") -> dict:
    return {"decision": decision, "reason": reason, "stage": stage, "playground": {"action": action, "instructions": instructions}}
