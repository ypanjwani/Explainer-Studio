"""Follow-ups in a conversation: validating the FollowUp a request carries (revise or new, and a revise
follow-up's plan). Which one a typed message is, the plan step decides (POST /api/followup/plan,
modes/followup/entry.py); the old router (POST /api/route) was removed on Nov 13 2026."""

from __future__ import annotations

from typing import Any

from . import data
from .core.color import is_hex
from .core.spec import parse_design
from .core.validate import WIDGET_MAX_CHARS
from .js import js_len, js_slice, json_stringify


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
    v5 = p.get("v5")
    # Follow-ups v5: the one agent (agent: true, with the conversation, the option the reader picked and the answer they got
    # just before), or an older plan's brief (a record planned before the agent: the agent reads it as the earlier plan).
    if isinstance(v5, dict) and (v5.get("agent") is True or _str(v5.get("brief")).strip()):
        out5: dict = {}
        if v5.get("agent") is True:
            out5["agent"] = True
        if v5.get("intent") in ("answer", "change"):  # Jev's call in the plan step (v5/intent.py)
            out5["intent"] = v5["intent"]
        # conversation: shared.history_text (HISTORY_TOTAL 24000 + its header); earlier: one whole turn (EARLIER_ANSWER_CHARS).
        for k, n in (("brief", 2000), ("conversation", 26000), ("chose", 200), ("extra", 300)):
            if _str(v5.get(k)).strip():
                out5[k] = js_slice(_str(v5[k]).strip(), 0, n)
        if isinstance(v5.get("base"), int) and not isinstance(v5["base"], bool) and v5["base"] >= 1:
            out5["base"] = v5["base"]
        refs = [r for r in v5.get("refs") or [] if isinstance(r, int) and not isinstance(r, bool) and r >= 1] if isinstance(v5.get("refs"), list) else []
        if refs:
            out5["refs"] = refs[:5]
        if _str(v5.get("earlier")).strip():
            out5["earlier"] = js_slice(_str(v5["earlier"]).strip(), 0, 8000)
        return {"sections": sections, "steps": steps, "v5": out5}
    v4 = p.get("v4")
    if isinstance(v4, dict) and _str(v4.get("brief")).strip():  # follow-ups v4: one brief for the tool-using editor
        out = {"brief": js_slice(_str(v4["brief"]).strip(), 0, 2000)}
        if _str(v4.get("earlier")).strip():  # the answer the reader got just before asking for this change
            out["earlier"] = js_slice(_str(v4["earlier"]).strip(), 0, 4000)
        return {"sections": sections, "steps": steps, "v4": out}
    return {"sections": sections, "steps": steps} if sections or steps else None


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
        if f.get("plan") is not None:  # /api/followup/plan's steps
            pl = parse_plan(f["plan"])
            if pl is None:
                return "The change couldn't be read. Send your request again."
            out["plan"] = pl
        return out
    if f.get("kind") == "new":
        previous = _str(f.get("previous"))
        if not previous:
            return "The earlier explainers couldn't be read for this follow-up."
        if js_len(previous) + js_len(source) > limit:
            return "This conversation is too large to continue. Start a new one instead."
        return {"kind": "new", "previous": previous, **({"source": source} if source else {})}
    return "Unknown follow-up."

