"""Follow-ups in a conversation: validating the FollowUp a request carries, and POST /api/route,
which decides whether a follow-up message changes the explainer above ("revise") or asks for a
new one ("new"), or can't tell ("unclear")."""

from __future__ import annotations

import re
from typing import Any

import anthropic

from . import data
from .core.color import is_hex
from .core.spec import parse_design
from .core.validate import WIDGET_MAX_CHARS
from .js import js_len, js_slice, js_trim, json_parse, json_stringify
from .llm import HAIKU, cached_system, text_of
from .pricing import price_of


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def parse_kept(k: Any) -> dict | None:
    """FollowUp.kept, cleaned: only well-formed entries, and nothing at all past LIMITS.keptChars (it is an
    optimization: without it the server just writes those parts again)."""
    if not isinstance(k, dict):
        return None
    steps = {key: [s for s in v if isinstance(s, str)][:6] for key, v in (k.get("steps") or {}).items()
             if isinstance(key, str) and key.count(":") >= 2 and isinstance(v, list)} if isinstance(k.get("steps"), dict) else {}
    notes = {sid: {f: n[f] for f in ("deeper", "another") if isinstance(n.get(f), str) and n[f].strip()}
             for sid, n in (k.get("notes") or {}).items() if isinstance(sid, str) and isinstance(n, dict)} if isinstance(k.get("notes"), dict) else {}
    out = {**({"steps": {key: v for key, v in steps.items() if v}} if any(steps.values()) else {}),
           **({"notes": {sid: n for sid, n in notes.items() if n}} if any(notes.values()) else {})}
    if not out or js_len(json_stringify(out)) > data.limits()["keptChars"] + 1000:
        return None
    return out


def parse_widgets(w: Any) -> dict | None:
    """FollowUp.widgets, cleaned: well-formed widget blocks by stage id, each as valid as a freshly written one
    (title, a script, within WIDGET_MAX_CHARS), and no more than LIMITS.widgetChars of HTML in all (the rest stay
    placeholders)."""
    if not isinstance(w, dict):
        return None
    out: dict[str, list[dict]] = {}
    chars = 0
    for sid, blocks in w.items():
        if not isinstance(sid, str) or not isinstance(blocks, list):
            continue
        for b in blocks:
            if not (isinstance(b, dict) and b.get("type") == "widget" and isinstance(b.get("title"), str)
                    and isinstance(b.get("html"), str) and "<script" in b["html"].lower()):
                continue
            size = js_len(b["html"])
            if not b["html"].strip() or size > WIDGET_MAX_CHARS or chars + size > data.limits()["widgetChars"]:
                continue
            chars += size
            out.setdefault(sid, []).append({"type": "widget", "title": b["title"], "html": b["html"],
                                            **({"plan": b["plan"]} if isinstance(b.get("plan"), str) else {})})
    return out or None


FEEDBACK_PARTS = ("widget", "text", "section", "new-section")
FEEDBACK_ID = re.compile(r"^[0-9a-f]{32}$")
FEEDBACK_TARGETS = 6  # like targets.MAX_TARGETS
FEEDBACK_WHAT = 300


def parse_targets(raw: Any, parts: tuple[str, ...]) -> list[dict]:
    """Well-formed targets only (part in `parts`, a change described); checked against the page later (targets.clean_targets)."""
    targets = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict) or t.get("part") not in parts:
            continue
        what = js_trim(js_slice(" ".join(_str(t.get("what")).split()), 0, FEEDBACK_WHAT))
        if what:
            targets.append({"part": t["part"], "stage": js_slice(_str(t.get("stage")), 0, 80),
                            "after": js_slice(_str(t.get("after")), 0, 80), "what": what})
    return targets


PICK_PARTS = (*FEEDBACK_PARTS, "remove")
PICK_TARGETS = 20  # like feedback_pick.MAX_CARD_SECTIONS


def parse_pick(p: Any) -> dict | None:
    """FollowUp.pick: the parts a typed follow-up on a Playground or Quick read page changes, as /api/followup/pick (or the
    reader's answer to its question) chose them: {targets, pageWide}. None when unusable."""
    if not isinstance(p, dict):
        return None
    page_wide = p.get("pageWide") is True
    targets = [] if page_wide else parse_targets(p.get("targets"), PICK_PARTS)[:PICK_TARGETS]
    if not page_wide and not targets:
        return None
    return {"targets": targets, "pageWide": page_wide}


PLAN_STEPS = 20  # like understand.MAX_STEPS
PLAN_INTENTS = 30  # understand.MAX_INTENTS, plus knock-ons
# understand.OPS without "revert" (an undo never reaches the pipeline: the app copies a version).
PLAN_OPS = ("add_section", "remove_section", "move_section", "split_section", "merge_sections", "set_level", "set_tone", "set_length",
            "edit_page_meta")


def parse_plan(p: Any) -> dict | None:
    """FollowUp.plan (follow-ups v3, understand.py): the sections the reader's follow-up is about, each with their intent, and
    its structural steps; well-formed only (the pipeline then checks them against the page, understand.clean_plan). None when
    there is nothing usable."""
    if not isinstance(p, dict):
        return None
    steps = []
    for s in (p.get("steps") if isinstance(p.get("steps"), list) else [])[:PLAN_STEPS]:
        if not isinstance(s, dict) or s.get("op") not in PLAN_OPS:
            continue
        step = {k: js_slice(_str(s.get(k)), 0, 800 if k == "what" else 200) for k in ("section", "other", "after", "level", "what", "quote")}
        step["op"] = s["op"]
        if _str(s.get("unsure")).strip():  # understand.flag: the reader confirmed a part it wasn't sure of
            step["unsure"] = js_slice(_str(s["unsure"]), 0, 200)
        steps.append(step)
    sections = []
    for i in (p.get("sections") if isinstance(p.get("sections"), list) else [])[:PLAN_INTENTS]:
        if not isinstance(i, dict) or not _str(i.get("section")) or not _str(i.get("intent")):
            continue
        item = {"section": js_slice(_str(i["section"]), 0, 200), "intent": js_slice(_str(i["intent"]), 0, 1600), "quote": js_slice(_str(i.get("quote")), 0, 200)}
        if i.get("derived") is True:
            item["derived"] = True
            item["because"] = js_slice(_str(i.get("because")), 0, 200)
        if _str(i.get("unsure")).strip():
            item["unsure"] = js_slice(_str(i["unsure"]), 0, 200)
        sections.append(item)
    return {"sections": sections, "steps": steps} if sections or steps else None


def parse_feedback_plan(fb: Any) -> dict | None:
    """FollowUp.feedback (Feedback v2): the change the reader confirmed, {id, targets, keep, pageWide}. Only well-formed
    targets are kept; the pipeline then checks them against the page (targets.clean_targets). None when unusable."""
    if not isinstance(fb, dict) or not isinstance(fb.get("id"), str) or not FEEDBACK_ID.match(fb["id"]):
        return None
    targets = parse_targets(fb.get("targets"), FEEDBACK_PARTS)
    keep = [js_slice(k, 0, 80) for k in fb.get("keep") if isinstance(k, str) and k.strip()][:20] if isinstance(fb.get("keep"), list) else []
    page_wide = fb.get("pageWide") is True
    if not page_wide and not targets:
        return None
    return {"id": fb["id"], "targets": [] if page_wide else targets[:FEEDBACK_TARGETS], "keep": keep, "pageWide": page_wide}


def parse_follow_up(f: Any) -> dict | None | str:
    """A request's followUp, validated: None when absent, a string when invalid (the error to show)."""
    if f is None:
        return None
    f = f if isinstance(f, dict) else {}
    source = _str(f.get("source"))
    limit = data.limits()["followUpChars"] + 1000
    if f.get("kind") == "revise":
        outline = _str(f.get("outline"))
        ok, design = parse_design(f.get("design"))
        if not outline or not ok:
            return "The previous explainer couldn't be read for this revision."
        if js_len(outline) + js_len(source) > limit:
            return "The previous explainer is too large to revise. Start a new conversation instead."
        out: dict = {"kind": "revise", "outline": outline, "design": design}
        if is_hex(f.get("accent")):
            out["accent"] = f["accent"].lower()
        if isinstance(f.get("colorMode"), str) and f["colorMode"] in data.menu("COLOR_MODES"):
            out["colorMode"] = f["colorMode"]
        if source:
            out["source"] = source
        kept = parse_kept(f.get("kept"))
        if kept:
            out["kept"] = kept
        widgets = parse_widgets(f.get("widgets"))
        if widgets:
            out["widgets"] = widgets
        if f.get("add") is True:  # "Add section": only add to the page (addsection.py; Quality+ only)
            out["add"] = True
        if f.get("feedback") is not None and not out.get("add"):  # Feedback v2: the change the reader confirmed
            fb = parse_feedback_plan(f["feedback"])
            if fb is None:
                return "The feedback's change couldn't be read. Send the feedback again."
            out["feedback"] = fb
        if f.get("plan") is not None and not out.get("add") and not out.get("feedback"):  # /api/followup/plan's steps
            pl = parse_plan(f["plan"])
            if pl is None:
                return "The change couldn't be read. Send your request again."
            out["plan"] = pl
        if f.get("pick") is not None and not out.get("add") and not out.get("feedback") and not out.get("plan"):  # /api/followup/pick's choice
            pk = parse_pick(f["pick"])
            if pk is None:
                return "The part to change couldn't be read. Send your request again."
            out["pick"] = pk
        return out
    if f.get("kind") == "new":
        previous = _str(f.get("previous"))
        if not previous:
            return "The earlier explainers couldn't be read for this follow-up."
        if js_len(previous) + js_len(source) > limit:
            return "This conversation is too large to continue. Start a new one instead."
        return {"kind": "new", "previous": previous, **({"source": source} if source else {})}
    return "Unknown follow-up."


def parse_route(body: Any) -> dict | str:
    body = body if isinstance(body, dict) else {}
    text = js_trim(_str(body.get("text")))
    if not text or js_len(text) > data.limits()["conceptChars"]:
        return "Describe what you want."
    stages = body.get("stages")
    stages = [js_slice(s, 0, 80) for s in stages if isinstance(s, str)][:20] if isinstance(stages, list) else []
    return {"text": text, "title": js_slice(_str(body.get("title")), 0, 200), "stages": stages}


def route_follow_up(client: anthropic.Anthropic, req: dict) -> dict:
    """One tiny structured-output call (about $0.0001). Anything unusable counts as "unclear", so the reader is asked."""
    msg = client.messages.create(
        model=HAIKU,
        max_tokens=200,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": data.formats()["route"]}},
        system=cached_system(data.prompt("ROUTE_SYSTEM")),
        messages=[{"role": "user", "content": f'Explainer above: "{req["title"]}"\nIts steps: {"; ".join(req["stages"])}\n\nThe reader\'s new message: {req["text"]}'}],
    )
    usd = price_of(msg.model, msg.usage)
    try:
        kind = json_parse(text_of(msg)).get("kind")
        return {"kind": kind if kind in ("revise", "new") else "unclear", "usd": usd}
    except Exception:
        return {"kind": "unclear", "usd": usd}
