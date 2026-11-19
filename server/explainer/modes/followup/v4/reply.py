"""Follow-ups v4: the friendly reply under a follow-up, written from the change log (so it never claims a change that was
undone). One Haiku Low call without thinking (FOLLOWUP_REPLY_SYSTEM); a plain reply by code when it fails."""

from __future__ import annotations

import json
import logging

import anthropic

from .... import data
from ....llm import write_text
from ....pricing import UsageLog

log = logging.getLogger("explainer.followup.v4")

REPLY_CHARS = 1200


def fallback(entries: list[dict], note: str) -> str:
    done = [e["what"] for e in entries if e["status"] == "done"]
    undone = [e for e in entries if e["status"] != "done"]
    parts = []
    if done:
        parts.append("Done: " + "; ".join(done[:6]) + ".")
    if undone:
        parts.append("I put back " + "; ".join(f'{e["what"]} ({e.get("why") or "it didn't come out right"})' for e in undone[:3])
                     + ", so that part stays as it was.")
    if not parts:
        parts.append(note or "I looked at it and the page already does what you asked, so I left it as it is.")
    return " ".join(parts)


def write(client: anthropic.Anthropic | None, usage: UsageLog, request: str, note: str, entries: list[dict], notes: list[str]) -> str:
    payload = {"reader_asked": request, "editor_note": note,
               "change_log": [{k: e[k] for k in ("what", "status", "why", "label") if e.get(k)} for e in entries],
               "other_notes": notes}
    try:
        if client is None:
            raise RuntimeError("no client")
        text = write_text(client, usage, "follow-up reply", system=data.prompt("FOLLOWUP_REPLY_SYSTEM"),
                          user=json.dumps(payload, ensure_ascii=False), max_tokens=800, effort="low", thinking=False)
        text = text.strip()
    except Exception:
        log.exception("Follow-up v4: the reply couldn't be written; using the plain one")
        text = ""
    return (text or fallback(entries, note))[:REPLY_CHARS]
