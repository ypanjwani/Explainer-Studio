"""Follow-ups v5: the base version as a revise follow-up, built on the server from the version store.

follow_of(spec) is a Python port of shared/followup.ts buildFollowUp("revise", spec) (compactOutline, designOf, keptOf,
widgetsOf, sourceOf): run.py then edits a version the server loaded (any version, not only the one the app holds), with
that version's own look, playgrounds and worked-example steps. Keep it in step with shared/followup.ts
(test_followup_v5.py compares the shapes).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .... import data, versions
from ....db import Explainer, PageVersion
from .page import Store, cut

SOURCE_MARK = "\n\nSource material:\n"  # shared/followup.ts SOURCE_MARK
# Each version's reply and offers in the version log the agent reads (were 200 / 120, cut mid-word, so it couldn't see what
# it had offered and repeated or forgot offers; Nov 17 2026).
LOG_REPLY_CHARS = 800
LOG_SUGGESTION_CHARS = 400


def _block(b: dict) -> dict:
    if b.get("type") == "widget":
        return {"type": "text", "md": f"(interactive widget: {b.get('title')})"}
    if b.get("type") == "examples":
        return {**b, "items": [{k: v for k, v in it.items() if k != "steps"} for it in b.get("items") or []]}
    return b


def compact_outline(spec: dict) -> dict:
    return {"title": spec.get("title"), "subtitle": spec.get("subtitle"), "level": spec.get("level"), "layout": spec.get("layout"),
            "keyIntuition": spec.get("keyIntuition"), "phases": spec.get("phases"),
            "stages": [{"id": s.get("id"), "label": s.get("label"), "phase": s.get("phase"), "blurb": s.get("blurb"),
                        "blocks": [_block(b) for b in s.get("blocks") or []]} for s in spec.get("stages") or []],
            "glossary": spec.get("glossary")}


def source_of(spec: dict) -> str:
    ctx = str(((spec.get("chatbot") or {}).get("context")) or "")
    i = ctx.find(SOURCE_MARK)
    return "" if i < 0 else ctx[i + len(SOURCE_MARK):].strip()


def design_of(spec: dict) -> dict:
    d = spec.get("design") or {}
    names = {emoji: name for name, emoji in data.menu("ICONS").items()}
    chatbot = spec.get("chatbot") or {}
    out: dict[str, Any] = {"design": {
        "theme": "ocean" if d.get("theme") == "custom" else d.get("theme"),
        "fontPairing": d.get("fontPairing"), "headingStyle": d.get("headingStyle"),
        "stageIcons": [{"stageId": s.get("id"), "icon": names[s.get("icon")]} for s in spec.get("stages") or [] if s.get("icon") in names],
        "chatbot": {"systemPrompt": chatbot.get("systemPrompt"), "starters": chatbot.get("starters")}}}
    if d.get("theme") == "custom" and d.get("accent"):
        out["accent"] = d["accent"]
    if d.get("mode"):
        out["colorMode"] = d["mode"]
    return out


def kept_of(spec: dict) -> dict | None:
    steps: dict[str, list] = {}
    for st in spec.get("stages") or []:
        for bi, b in enumerate(st.get("blocks") or []):
            if b.get("type") == "examples":
                for ii, it in enumerate(b.get("items") or []):
                    if it.get("steps"):
                        steps[f"{st.get('id')}:{bi}:{ii}"] = it["steps"]
    notes = spec.get("sectionNotes") or None
    for kept in ({"steps": steps, "notes": notes}, {"steps": steps}):
        k = {**({"steps": kept["steps"]} if kept["steps"] else {}), **({"notes": kept["notes"]} if kept.get("notes") else {})}
        if not k:
            return None
        if len(json.dumps(k, ensure_ascii=False, separators=(",", ":"))) <= data.limits()["keptChars"]:
            return k
    return None


def widgets_of(spec: dict) -> dict | None:
    out: dict[str, list] = {}
    chars = 0
    for st in spec.get("stages") or []:
        for b in st.get("blocks") or []:
            if b.get("type") != "widget" or chars + len(b.get("html") or "") > data.limits()["widgetChars"]:
                continue
            chars += len(b.get("html") or "")
            out.setdefault(st.get("id"), []).append(b)
    return out or None


def follow_of(spec: dict) -> dict:
    """The revise follow-up for this version (without a plan): {kind, outline (JSON string), design, accent?, colorMode?,
    source?, kept?, widgets?}. Never refused for size: the server holds the page already."""
    outline = json.dumps(compact_outline(spec), ensure_ascii=False, separators=(",", ":"))
    source = source_of(spec)
    room = data.limits()["followUpChars"] - len(outline)
    kept, widgets = kept_of(spec), widgets_of(spec)
    return {"kind": "revise", "outline": outline, **design_of(spec),
            **({"source": source[:max(room, 0)]} if source and room > 500 else {}),
            **({"kept": kept} if kept else {}), **({"widgets": widgets} if widgets else {})}


def _clip(s: Any, n: int) -> str:
    return cut(" ".join(str(s or "").split()), n)


def log_text(entries: list[dict], here: int | None) -> str:
    """The version log as text, one line per version, oldest first (talk step and editor); `here`: the version the message is under."""
    if not entries:
        return ""
    lines = []
    for e in entries:
        made = f"from v{e['parent']}" if e.get("parent") else "first version"
        if e.get("under"):
            made += f", asked under v{e['under']}"
        head = f"v{e['version']} ({made}{', back to v' + str(e['reverted']) if e.get('reverted') else ''})"
        if e.get("deleted"):
            lines.append(f"{head}: deleted")
            continue
        what = f'asked "{_clip(e.get("request"), 160)}"' if e.get("request") else ""
        if e.get("changed") or e.get("removed"):
            what += f"; changed: {', '.join(e.get('changed') or [])}" + (f"; removed: {', '.join(e['removed'])}" if e.get("removed") else "")
        if e.get("reply"):
            what += f'; reply: "{_clip(e["reply"], LOG_REPLY_CHARS)}"'
        if e.get("suggestions"):
            what += "; suggested: " + " | ".join(_clip(x, LOG_SUGGESTION_CHARS) for x in e["suggestions"])
        mark = "  <- the page this message is under" if e["version"] == here else ""
        lines.append(f"{head}: {what}{mark}")
    return "The page's versions (oldest first):\n" + "\n".join(lines)


def load(db: Any, user_id: str | None, base_id: str | None, plan: dict) -> dict | None:
    """The version a v5 change starts from (plan["base"], else the base record's own version), as the server holds it:
    {"follow", "store", "log_text", "version", "paper"} (paper: paper_of, [] for a concept page). None when it can't be
    loaded (run.py then uses the app's copy)."""
    if not (db and user_id and base_id):
        return None
    with db.session() as s:
        row = versions.of_record(s, user_id, base_id) or versions.backfill_line(s, user_id, base_id)
        if row is None or row.deleted:
            return None
        start = plan.get("base") if isinstance(plan.get("base"), int) else row.version
        spec = versions.load(s, user_id, row.line, start)
        if spec is None:
            start, spec = row.version, versions.load(s, user_id, row.line, row.version)
        if spec is None:
            return None
        entries = versions.version_log(s, user_id, row.line)
        line = row.line
        paper = paper_of(s, user_id, line)
    return {"follow": follow_of(spec), "store": Store(db, user_id, line, start), "log_text": log_text(entries, start), "version": start,
            "paper": paper}


# ---------- the paper the page was made from (Nov 18 2026) ----------
# The page keeps only the summarize step's summary as its source (chatbot.context), and that summary drops most of a
# paper's tables: the agent kept telling a reader "the paper doesn't give LSTM / BiLSTM / mBERT scores" when the PDF's
# results tables had every row. The paper's full text is already saved with the line's first record (request.sections),
# so the agent gets it from there; nothing new is stored, and old pages get it too.


def paper_of(s: Any, user_id: str, line: str) -> list[dict]:
    """The paper's sections ({heading, text}) for this line's first page, [] for a concept page or a missing record.
    The line's id is its first record's id; a Retry of that page saves its new record under the same line as a "new"
    version, so those records are tried first (newest first), then the line's own id."""
    ids = list(s.scalars(select(PageVersion.record_id).where(PageVersion.user_id == user_id, PageVersion.line == line,
                                                             PageVersion.kind == "new").order_by(PageVersion.updated_at.desc())))
    for rid in [*ids, line]:
        rec = s.get(Explainer, (user_id, rid))
        req = (rec.data or {}).get("request") if rec is not None and isinstance(rec.data, dict) else None
        if not isinstance(req, dict) or req.get("mode") != "paper":
            continue
        secs = [{"heading": str(x.get("heading") or "").strip(), "text": str(x.get("text") or "").strip()}
                for x in req.get("sections") or [] if isinstance(x, dict)]
        secs = [x for x in secs if x["text"]]
        if secs:
            return secs
    return []
