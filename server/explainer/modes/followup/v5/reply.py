"""Follow-ups v5 (copied from v4's reply.py, then changed): the friendly reply under a follow-up, written from the change
log (so it never claims a change that was undone). One Haiku Low call without thinking (FOLLOWUP_V5_REPLY_SYSTEM); a plain
reply by code when it fails. v5: the agent's suggestions (ideas for a next change) are added by code."""

from __future__ import annotations

import json
import logging

import anthropic

from .... import data
from ....llm import write_text
from ....pricing import UsageLog
from .page import cut

log = logging.getLogger("explainer.followup.v5")

REPLY_CHARS = 3000  # was 1200 (and 800 tokens): a reply rewritten after a put-back stopped mid-sentence (Nov 17 2026)
REPLY_TOKENS = 2000


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


def with_suggestions(text: str, suggestions: list[str]) -> str:
    """The reply plus the agent's ideas for a next change, as offers the reader can answer with "yes, do that". Headed as
    ideas, not "Something else I noticed": they read like a new list of problems under every reply."""
    if not suggestions:
        return text
    lines = "\n".join(f"- {s}" for s in suggestions)
    return f"{text}\n\nIdeas, if you want more:\n{lines}"


def write(client: anthropic.Anthropic | None, usage: UsageLog, request: str, note: str, entries: list[dict], notes: list[str],
          suggestions: list[str] | None = None) -> str:
    payload = {"reader_asked": request, "editor_note": note,
               "change_log": [{k: e[k] for k in ("what", "status", "why", "label") if e.get(k)} for e in entries],
               "other_notes": notes}
    try:
        if client is None:
            raise RuntimeError("no client")
        text = write_text(client, usage, "follow-up reply", system=data.prompt("FOLLOWUP_V5_REPLY_SYSTEM"),
                          user=json.dumps(payload, ensure_ascii=False), max_tokens=REPLY_TOKENS, effort="low", thinking=False)
        text = text.strip()
    except Exception:
        log.exception("Follow-up v5: the reply couldn't be written; using the plain one")
        text = ""
    return with_suggestions(cut(text or fallback(entries, note), REPLY_CHARS), suggestions or [])
