"""Follow-ups v5 can look at playgrounds (Nov 18 2026, FOLLOWUP_V5_LOOK=1, Explorer pages only).

The agent otherwise sees a playground only as text (its readings and the layout check), so it can't judge how it looks
(crowding, colours, empty space). Two parts, both failing open (no node / Playwright / browser = no pictures, nothing
blocks):

tool    look_at_playground: screenshots of one playground (explorer.playground.snap_widget, in the page's own light or dark
        colours, at most FRAMES frames: the start, then a slider at its ends), each with its caption, then the readings
        (the numbers stay the truth: the picture is for how it looks). At most LOOKS[effort] calls per follow-up, by the
        Effort the reader picked. Frames are cached by html + theme (page.look_cache).
gate    the first finish's checks (agent.Gate.run) also screenshot each playground the agent changed and Haiku lists what
        looks wrong (look_review, told what the layout check already measured), at the reader's Effort: "look" problems,
        sent back as feedback like the others. Never a put-back: one left alone becomes at most one suggestion.

Only the latest screenshots stay in the agent's conversation (strip_old_images): each round resends every message.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ...explorer.playground import PG_LOOK_CHECKED, look_review, snap_widget
from .. import playground_edit as pe
from .page import Page, ToolError

log = logging.getLogger("explainer.followup.v5")

FRAMES = 3
LOOKS = {"low": 1, "medium": 2, "high": 3}  # look_at_playground calls per follow-up, by the reader's Effort
STUB = "[screenshots from earlier, no longer shown]"

TOOL = {
    "name": "look_at_playground",
    "description": (
        "See how a section's playground looks to the reader: screenshots taken in a real browser in the page's own colours "
        "(the start, then a slider at its ends), with what it shows in numbers. Use it only when the reader's message is "
        "about how a playground looks (crowded, overlapping or cut labels, colours hard to tell apart, empty space, hard to "
        "read), after you changed what a playground draws (a new chart, table, diagram or custom drawing), or when its "
        "readings hint at something odd on screen. Not to check numbers: those come from try_playground and the readings, "
        "never from reading the pictures. Limited per message. With `version`, as it is in that version."),
    "input_schema": {"type": "object", "properties": {
        "section": {"type": "string", "description": "The section's id (or its handle, S1, S2 ...)."},
        "title": {"type": "string", "description": "Only when the section has several playgrounds."},
        "version": {"type": "integer", "description": "Another version of this page (its number in the version log); leave out for the page you are editing."}},
        "required": ["section"]},
}


def _key(html: str, theme: dict) -> str:
    return hashlib.sha1((html + json.dumps(theme, sort_keys=True)).encode("utf-8")).hexdigest()


def frames_of(page: Page, html: str) -> list[dict] | None:
    """The playground's screenshots in the page's colours (cached by html + theme), or None when they can't be taken."""
    key = _key(html, page.look_theme)
    if key not in page.look_cache:
        page.look_cache[key] = snap_widget(html, page.look_theme, FRAMES)
    return page.look_cache[key]


def tool(page: Page, inp: dict, ctx: Any) -> list[dict]:
    """look_at_playground's result: content blocks (caption + image per frame, then the readings)."""
    from . import tools as T  # tools.apply calls this; imported here to avoid a cycle

    limit = LOOKS.get(ctx.effort, LOOKS["medium"])
    if page.looks >= limit:
        raise ToolError(f"You've used the {limit} look(s) this message allows: go on with the readings (try_playground).")
    st = T._read_stage(page, inp)
    bi = T._widget_in(st, inp.get("title"))
    block = st["blocks"][bi]
    if pe.widget_kind(block) != "playground":
        raise ToolError("That isn't a playground that can be shown here (an older kind).")
    page.looks += 1
    html = str(block.get("html") or "")
    readings = next((r["readings"] for r in T.playground_readings(page, {"blocks": [block]}) if r["readings"]), "")
    shown = f'"{block.get("title") or st.get("label", "")}" in "{st.get("label", "")}"'
    frames = frames_of(page, html)
    if not frames:
        return [{"type": "text", "text": f"Couldn't take screenshots of {shown} just now. What it shows:\n{readings or '(no readings either)'}"}]
    out: list[dict] = [{"type": "text", "text": f"{shown}, {page.look_theme['mode']} page colours, {len(frames)} screenshot(s):"}]
    for i, f in enumerate(frames, 1):
        out.append({"type": "text", "text": f"Screenshot {i}: {f.get('label', '')}"})
        out.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": f["png"]}})
    if readings:
        out.append({"type": "text", "text": "What it shows in numbers (the truth for any value):\n" + readings})
    return out


def problems(page: Page, client: Any, usage: Any, effort: str) -> list[dict]:
    """The gate's look check: each playground the agent changed (still on the page), screenshotted and judged by Haiku at
    the reader's Effort. [{"section", "what", "kind": "look"}]. A playground judged before (same html) isn't judged again."""
    out: list[dict] = []
    for st in page.stages:
        if st["id"] not in page.widgets_changed:
            continue
        for b in st.get("blocks") or []:
            if b.get("type") != "widget" or pe.widget_kind(b) != "playground":
                continue
            html = str(b.get("html") or "")
            key = _key(html, page.look_theme)
            if key not in page.look_judged:
                frames = frames_of(page, html)
                found = None
                if frames:
                    found = look_review(client, usage, frames, pe.read_plan(b), str(b.get("title") or ""), checked=PG_LOOK_CHECKED,
                                        step="follow-up look", effort=effort)
                page.look_judged[key] = found or []
            for p in page.look_judged[key]:
                out.append({"section": st["id"], "what": f"how it looks (from screenshots): {p}", "kind": "look"})
    return out


def text_of(content: Any) -> str:
    """A tool result's text (its text blocks when it is a list), for the log."""
    if isinstance(content, list):
        return " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def has_images(content: Any) -> bool:
    if isinstance(content, list):
        return any(isinstance(b, dict) and (b.get("type") == "image" or has_images(b.get("content"))) for b in content)
    return False


def strip_old_images(messages: list[dict]) -> None:
    """Replace every image already in the conversation with a short stub (called before a round's new screenshots go in)."""
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                kept = [x for x in b["content"] if not (isinstance(x, dict) and x.get("type") == "image")]
                if len(kept) != len(b["content"]):
                    b["content"] = [*kept, {"type": "text", "text": STUB}]
