"""Request validation for /api/generate and /api/chat (the same rules and messages as before the move to Flask)."""

from __future__ import annotations

from typing import Any

from . import data
from .followup import parse_follow_up
from .js import js_len, js_slice, js_string, js_trim
from .modes.followup.shared import HISTORY_CHARS, MAX_HISTORY

# The two modes the server runs (modes/). Any other quality (an old mode's page, an old app tab, a script) is kept as
# sent, so /api/generate can refuse it with a clear message (400 mode_off).
QUALITIES = ("lite-plus", "playground")


def _get(body: Any, key: str) -> Any:
    return body.get(key) if isinstance(body, dict) else None


def parse_generate(body: Any) -> dict | str:
    """A GenerateRequest, or the error to show."""
    limits = data.limits()
    # Reader (lite-plus) and Explorer (playground). Anything else stays as sent (cut short) for the refusal.
    q = _get(body, "quality")
    quality = q if q in QUALITIES else js_slice(q, 0, 40) if isinstance(q, str) and q else "none"
    opts: dict = {}
    effort = _get(body, "effort")
    if effort in ("low", "medium", "high"):  # absent = server default (PLAN_EFFORT or medium)
        opts["effort"] = effort
    interactivity = _get(body, "interactivity")
    if interactivity in ("reading", "hands-on"):  # absent or "balanced" = the planner's usual mix
        opts["interactivity"] = interactivity
    level = _get(body, "level")
    if isinstance(level, str) and level != "auto" and level in data.menu("LEVEL_CHOICES"):  # absent/"auto" = Jev or the planner picks it
        opts["level"] = level
    # "What do you want to add to each section?" (Explorer only): known keys, in menu order; "auto" can't be combined
    # with the others. An empty list is kept: it means None (no worked examples), unlike a missing field (not asked).
    extras = _get(body, "extras")
    if quality == "playground" and isinstance(extras, list):
        picked = [k for k in data.menu("EXTRAS") if k in extras]
        opts["extras"] = ["auto"] if "auto" in picked else picked
    # Explorer only: plan and build playgrounds with Haiku's thinking on (True) or off (False). Absent = on.
    think = _get(body, "planThinking")
    if quality == "playground" and isinstance(think, bool):
        opts["planThinking"] = think
    # Page colours (the composer's Options): the page opens and exports in light or dark. Absent = follow the reader's setting.
    color = _get(body, "colorMode")
    if color in ("light", "dark"):
        opts["colorMode"] = color
    # Explorer test runs (scripts/variety.mjs): the playgrounds Jev's review rejected, in the done event. The app never sends it.
    if quality == "playground" and _get(body, "debugRejected") is True:
        opts["debugRejected"] = True
    # Retry (the app's "↻ Retry"): what the explainer being replaced had, so the fresh one takes a different route.
    avoid = _parse_avoid(_get(body, "avoid"))
    if avoid:
        opts["avoid"] = avoid
    # The first-message step (modes/intake.py): the conversation so far when it has no page yet (shaped by history_text),
    # and direct: build without the step (the reader already chose to build).
    hist = _get(body, "history")
    if isinstance(hist, list):
        turns = [js_slice(h, 0, HISTORY_CHARS) for h in hist[-MAX_HISTORY:] if isinstance(h, str) and h.strip()]
        if turns:
            opts["history"] = turns
    if _get(body, "direct") is True:
        opts["direct"] = True
    # A follow-up in a conversation: revise the explainer above, or a related new one.
    f = parse_follow_up(_get(body, "followUp"))
    if isinstance(f, str):
        return f

    mode = _get(body, "mode")
    if mode == "concept":
        text = js_trim(body["text"]) if isinstance(body.get("text"), str) else ""
        if not text:
            return "Describe the concept you want explained."
        if js_len(text) > limits["conceptChars"]:
            return f"Please keep the description under {limits['conceptChars']} characters."
        return {"mode": "concept", "text": text, "quality": quality, **_order(opts), **({"followUp": f} if f else {})}
    if mode == "paper":
        sections = body.get("sections") if isinstance(body.get("sections"), list) else []
        if not sections or len(sections) > limits["sections"]:
            return "The paper has no readable sections."
        total = 0
        for s in sections:
            if not isinstance(s, dict) or not isinstance(s.get("heading"), str) or not isinstance(s.get("text"), str):
                return "Malformed paper sections."
            total += js_len(s["text"])
        if total > limits["paperChars"]:
            return "The paper is too long."
        title = body.get("title")
        note = body.get("note")
        return {
            "mode": "paper",
            "title": js_slice(js_string("Untitled paper" if title is None else title), 0, 300),
            "note": js_slice(js_string("" if note is None else note), 0, 1000),
            "sections": [{"heading": js_slice(s["heading"], 0, 200), "text": s["text"]} for s in sections],
            "quality": quality,
            **_order(opts),
            **({"followUp": f} if f and f["kind"] == "new" else {}),  # a new paper can't "revise" the explainer above
        }
    return "Unknown request."


AVOID_TITLES = 12  # at most this many section titles of the replaced page
AVOID_TITLE_CHARS = 120


def _parse_avoid(v: Any) -> dict | None:
    """{"titles": [...], "theme"?: name}, or None when there is nothing usable. Bad parts are dropped, not errors."""
    if not isinstance(v, dict) or not isinstance(v.get("titles"), list):
        return None
    titles = [js_slice(js_trim(t), 0, AVOID_TITLE_CHARS) for t in v["titles"] if isinstance(t, str) and js_trim(t)][:AVOID_TITLES]
    if not titles:
        return None
    theme = v.get("theme")
    return {"titles": titles, **({"theme": theme} if isinstance(theme, str) and theme in data.menu("THEMES") else {})}


def _order(opts: dict) -> dict:
    return {k: opts[k] for k in ("effort", "interactivity", "level", "extras", "planThinking", "colorMode", "avoid", "debugRejected", "history",
                                    "direct") if k in opts}


def parse_chat(body: Any) -> dict | str:
    limits = data.limits()
    msgs = _get(body, "messages")
    if not isinstance(msgs, list) or not msgs or len(msgs) > limits["chatMessages"]:
        return "Invalid messages."
    chars = 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            return "Invalid messages."
        chars += js_len(m["content"])
    if msgs[-1]["role"] != "user":
        return "Last message must be from the user."
    # body.systemPrompt is ignored: the server builds the system prompt (audit F03).
    ctx = body.get("context")
    context = js_slice(js_string("" if ctx is None else ctx), 0, 30_000)
    if chars + js_len(context) > limits["chatChars"] + 30_000:
        return "Conversation too long."
    return {"context": context, "messages": [{"role": m["role"], "content": m["content"]} for m in msgs]}
