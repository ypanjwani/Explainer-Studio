"""POST /api/followup/plan (routes/ai.py): the request checked, then follow-ups v5's talk step (the default; FOLLOWUP_V5=0
turns it off), else v4's when FOLLOWUP_V4=1, else v3's understand step. One of the two places (with run.py) that knows the versions exist; each
is imported only when chosen, so deleting v3/ or v4/ means removing its branch here and in run.py (v5 never loads them).
"""

from __future__ import annotations

from typing import Any

import os
import re

import anthropic

from ...js import js_slice
from . import page_index as pidx
from .shared import HISTORY_CHARS, MAX_HISTORY, MAX_INTENTS, MAX_STEPS, _clip

SOURCE_CHARS = 20_000
RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # schemas.RECORD_ID (a record id from the browser)


def _on(name: str) -> bool:
    return os.environ.get(name, "") == "1"


def _v5_on() -> bool:
    """Follow-ups v5 are the default (Nov 19 2026): on unless FOLLOWUP_V5=0 (v5/__init__.on, not imported here so this
    file loads no v5 code for a v3 or v4 run)."""
    return os.environ.get("FOLLOWUP_V5", "1") != "0"


def parse_plan_request(body: Any) -> dict | str:
    """POST /api/followup/plan: {text, quality, followUp, option?, extra?, history?, baseId?}. Reuses the pick request's
    checks. baseId: the record the follow-up is under (follow-ups v5 finds its version in the store)."""
    from .cards import parse_pick_request
    req = parse_pick_request(body)
    if isinstance(req, str):
        return req
    body = body if isinstance(body, dict) else {}
    opt = body.get("option")
    if isinstance(opt, dict):
        req["option"] = {"label": _clip(opt.get("label"), 200),  # v5's options are up to 200 (v5/editor.OPTION_CHARS)
                         "sections": opt["sections"][:MAX_INTENTS] if isinstance(opt.get("sections"), list) else [],
                         "steps": opt["steps"][:MAX_STEPS] if isinstance(opt.get("steps"), list) else []}
    extra = body.get("extra")
    req["extra"] = _clip(extra, 1000) if isinstance(extra, str) else ""
    hist = body.get("history")
    req["history"] = [_clip(h, HISTORY_CHARS) for h in hist[-MAX_HISTORY:] if isinstance(h, str)] if isinstance(hist, list) else []  # shaped by history_text
    base_id = body.get("baseId")
    if base_id is not None:
        if not isinstance(base_id, str) or not RECORD_ID.match(base_id):
            return "Invalid record id."
        req["baseId"] = base_id
    f = body.get("followUp") if isinstance(body.get("followUp"), dict) else {}
    if isinstance(f.get("source"), str) and f["source"].strip():
        req["source"] = js_slice(f["source"], 0, SOURCE_CHARS)
    return req


def handle(client: anthropic.Anthropic, store: pidx.PageIndexStore | None, req: dict, db: Any = None, user_id: str | None = None,
           jev_key: str | None = None) -> dict:
    """The /api/followup/plan reply plus its cost ("usd"): v5's plan step (the default, unless FOLLOWUP_V5=0; code only: the agent in
    /api/generate does the rest; jev_key: whether the message asks for a change), v4's talk step under FOLLOWUP_V4=1,
    otherwise v3's understand."""
    if _v5_on():
        from .v5 import talk as v5_talk
        return v5_talk.handle(client, req, db, user_id, jev_key)
    if _on("FOLLOWUP_V4"):
        from .v4 import talk
        return talk.handle(client, store, req)
    from .v3 import understand
    return understand.handle(client, store, req)
