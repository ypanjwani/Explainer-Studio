"""Follow-ups v5 phase 2 (.claude/followup-v5-plan.md §3): a version's **main file**, a short description of one version of
a page, enough for Haiku to know what is where and pick what to open (it never answers or edits from it alone).

For each section, in page order:
- code part (exact, no AI): handle and id together ("S3 · attention"), label, blurb, one line per block (a quiz lists its
  questions), and the playground: controls with ranges and defaults, what it shows, what it teaches;
- readings: what the playground shows at the start (explorer.playground.read_playground; no AI, fails open);
- AI part (one Haiku Low call, MAIN_FILE_SYSTEM): what the section introduces, uses and claims. Each section is described
  on its own, so it stays right wherever the section moves.

All three depend only on the section's own content, so they're cached per user in section_notes by a hash of it:
building a new version's main file runs readings and the AI only for new or changed sections. A main file is built the
first time something needs it (main_file_of), never in the background, and saved in its version's row
(page_versions.main_file). Its cost lands on the caller's UsageLog. MAIN_FILE_FORMAT changes make old notes miss and old
saved main files rebuild.

A shared follow-up file: no v3 / v4 imports (test_modes_boundary.py).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy import delete, select

from ... import data, versions
from ...db import Database, Explainer, PageVersion, SectionNote, now_ms
from ...js import json_parse
from ...llm import write_text
from ...pricing import UsageLog
from ..explorer.playground import read_playground, readings_text
from . import playground_edit as pe
from .cards import _clip, block_full, block_line, playground_visuals

log = logging.getLogger("explainer")

MAIN_FILE_FORMAT = "1"  # bump when the main file's shape or MAIN_FILE_SYSTEM changes
NOTES_MAX_DAYS = 60  # a user's section notes unused this long are deleted on their next build
READINGS_CHARS = 600  # short readings at the start
AI_SECTION_CHARS = 2500  # each section's text sent to the AI part
AI_MAX_TOKENS = 3000
AI_BATCH = 8  # sections per AI call, so a long page's reply is never cut off
NO_AI = {"introduces": [], "uses": [], "claims": []}  # a section the AI was asked about and skipped: not asked again
LIST_MAX, ITEM_CHARS, CLAIMS_MAX, CLAIM_CHARS = 6, 80, 4, 200
MODES = {"playground": "Explorer", "lite-plus": "Reader"}


# ---------- code part ----------


def note_key(stage: dict) -> str:
    return hashlib.sha256((MAIN_FILE_FORMAT + "\n" + versions.canonical(stage)).encode("utf-8")).hexdigest()


def _num(x: Any) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _playground(stage: dict) -> dict | None:
    """The section's (first) playground block."""
    for b in stage.get("blocks") or []:
        if isinstance(b, dict) and b.get("type") == "widget" and pe.widget_kind(b) == "playground":
            return b
    return None


def controls(spec: dict) -> list[str]:
    """A playground's controls as the reader sees them: sliders with their range and default, selects with their options."""
    state = spec.get("state") if isinstance(spec.get("state"), dict) else {}
    out = []
    for c in spec.get("components") or []:
        if not isinstance(c, dict) or c.get("action") == "reset":
            continue
        t, key = c.get("type"), c.get("key")
        label = _clip(c.get("label") or key or c.get("id"), 60)
        default = f"default {_num(state[key])}" if key in state and not isinstance(state[key], (dict, list)) else ""
        if t == "slider":
            rng = f" {_num(c.get('min'))}–{_num(c.get('max'))}" if c.get("min") is not None and c.get("max") is not None else ""
            out.append(f"{label}{rng}" + (f" ({default})" if default else ""))
        elif t == "select":
            opts = [_clip(o.get("label", o.get("value")) if isinstance(o, dict) else o, 30) for o in c.get("options") or []]
            out.append(f"{label} ({' / '.join(opts[:8])}" + (f"; {default}" if default else "") + ")")
        elif t == "toggle":
            out.append(f"{label} (on/off" + (f"; {default}" if default else "") + ")")
        elif t in ("button", "play"):
            out.append(f"{label} ({t})" if t == "play" else label)
        elif t == "chart" and (c.get("onDrag") or c.get("onClick")):
            out.append(f"{'drag' if c.get('onDrag') else 'click'} on the chart {_clip(c.get('title') or c.get('id'), 40)}")
    return out[:12]


def _block_line(b: dict) -> str:
    t = b.get("type")
    if t == "quiz":
        qs = [_clip(q.get("q"), 100) for q in b.get("questions") or [] if isinstance(q, dict)]
        return "quiz: " + " | ".join(f"Q: {q}" for q in qs) if qs else "quiz"
    if t == "predict":
        return f"predict: Q: {_clip(b.get('question'), 100)}"
    if t == "reveal":
        return f"reveal: Q: {_clip(b.get('prompt'), 100)}"
    if t == "widget" and pe.widget_kind(b) == "playground":
        return f'playground "{_clip(b.get("title"), 80)}"'
    return block_line(b)


def section_code(stage: dict) -> dict:
    """The exact part of a section's description, built by code."""
    out: dict[str, Any] = {"id": str(stage.get("id") or ""), "label": _clip(stage.get("label"), 120),
                           "blurb": _clip(stage.get("blurb"), 200),
                           "blocks": [_block_line(b) for b in stage.get("blocks") or [] if isinstance(b, dict)][:16]}
    b = _playground(stage)
    if b is not None:
        spec = pe.read_playground_spec(b.get("html", "")) or {}
        parts = []
        ctl = controls(spec)
        if ctl:
            parts.append("controls: " + "; ".join(ctl))
        shows = playground_visuals(b)
        if shows:
            parts.append("shows: " + ", ".join(shows[:8]))
        lesson = pe.read_plan(b).get("lesson")
        if lesson:
            parts.append("teaches: " + _clip(lesson, 200))
        out["playground"] = f'"{_clip(b.get("title"), 80)}"' + (": " + "; ".join(parts) if parts else "")
    return out


def section_readings(stage: dict) -> str | None:
    """What the section's playground shows at the start: "" with no playground, None when it couldn't be run just now
    (no node, a timeout, a crash), so a passing failure is never kept and the run is tried again next time."""
    b = _playground(stage)
    spec = pe.read_playground_spec(b.get("html", "")) if b is not None else None
    if not spec:
        return ""
    try:
        r = read_playground(spec, [{"label": "at the start"}], READINGS_CHARS)
    except Exception:
        log.exception("Main file: couldn't read a playground")
        return None
    text = readings_text(r).strip()
    if r is None or not r.get("runs") or text.startswith("Couldn't run it"):
        return None
    text = re.sub(r"^- at the start:\s*", "", text)  # the run's own heading; the main file says "at the start"
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# ---------- AI part ----------


def _ai_input(items: list[tuple[dict, dict, str]]) -> str:
    out = []
    for stage, code, readings in items:
        text = "\n".join(block_full(b) for b in stage.get("blocks") or [] if isinstance(b, dict)
                         and not (b.get("type") == "widget" and pe.widget_kind(b) == "playground"))
        part = [f'Section id "{code["id"]}": "{code["label"]}" ({code["blurb"]})', text[:AI_SECTION_CHARS]]
        if code.get("playground"):
            part.append("Playground: " + code["playground"])
        if readings:
            part.append("What the playground shows at the start:\n" + readings)
        out.append("\n".join(part))
    return "\n\n---\n\n".join(out)


def _items(x: Any, n: int, chars: int) -> list[str]:
    return [_clip(v, chars) for v in (x if isinstance(x, list) else []) if isinstance(v, str) and v.strip()][:n]


def merge_ai(raw: Any, ids: set[str]) -> dict[str, dict]:
    """The AI part checked: known ids only, short lists of short phrases."""
    out: dict[str, dict] = {}
    for e in (raw or {}).get("sections") or [] if isinstance(raw, dict) else []:
        if not isinstance(e, dict) or e.get("id") not in ids:
            continue
        out[e["id"]] = {"introduces": _items(e.get("introduces"), LIST_MAX, ITEM_CHARS),
                        "uses": _items(e.get("uses"), LIST_MAX, ITEM_CHARS),
                        "claims": _items(e.get("claims"), CLAIMS_MAX, CLAIM_CHARS)}
    return out


def ai_part(client: Any, items: list[tuple[dict, dict, str | None]], usage: UsageLog) -> dict[str, dict]:
    """Haiku Low calls for these sections, AI_BATCH at a time. A batch that answers covers every section in it (one the
    reply skipped gets NO_AI, so it's never asked about again); a batch that fails gives nothing and is tried again next
    time. {} with no client (the code part is used alone)."""
    if client is None or not items:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(items), AI_BATCH):
        batch = items[i:i + AI_BATCH]
        try:
            reply = write_text(client, usage, "follow-up main file", system=data.prompt("MAIN_FILE_SYSTEM"),
                               user=_ai_input(batch), max_tokens=AI_MAX_TOKENS, fmt=data.formats()["mainFileNotes"],
                               effort="low", thinking=False)
            got = merge_ai(json_parse(reply), {code["id"] for _, code, _ in batch})
        except Exception:
            log.exception("Main file: the AI part failed; using the code part alone")
            continue
        out.update({code["id"]: got.get(code["id"], NO_AI) for _, code, _ in batch})
    return out


# ---------- notes cache ----------


def _get_notes(db: Database, user_id: str, keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    now = now_ms()
    with db.session() as s:
        rows = s.scalars(select(SectionNote).where(SectionNote.user_id == user_id, SectionNote.hash.in_(keys))).all()
        for r in rows:
            r.used_at = now
        return {r.hash: dict(r.note) for r in rows}


def _put_notes(db: Database, user_id: str, notes: dict[str, dict]) -> None:
    now = now_ms()
    with db.session() as s:
        for key, note in notes.items():
            r = s.get(SectionNote, (user_id, key))
            if r is None:
                s.add(SectionNote(user_id=user_id, hash=key, note=note, created_at=now, used_at=now))
            else:
                r.note, r.used_at = note, now
        s.execute(delete(SectionNote).where(SectionNote.user_id == user_id,
                                            SectionNote.used_at < now - NOTES_MAX_DAYS * 24 * 3600 * 1000))


# ---------- the main file ----------


def describe(db: Database, client: Any, user_id: str, page: dict, *, version: int | None, quality: str | None,
             usage: UsageLog) -> dict:
    """The main file of a page: cached section notes, readings and AI calls for what's new. "complete" is False when some
    section has no AI part (the AI failed or there's no client) or a playground couldn't be run, so it isn't saved and
    what's missing is tried again next time."""
    stages = [st for st in page.get("stages") or [] if isinstance(st, dict)]
    keys = [note_key(st) for st in stages]
    cached = _get_notes(db, user_id, keys)
    notes: dict[str, dict] = {}
    changed: dict[str, dict] = {}
    for st, key in zip(stages, keys):
        n = cached.get(key)
        if n is None:
            n = changed[key] = {"code": section_code(st), "readings": section_readings(st), "ai": None}
        elif n.get("readings") is None:  # the run failed last time: try again
            readings = section_readings(st)
            if readings is not None:
                n = changed[key] = {**n, "readings": readings}
        notes[key] = n
    todo = [(st, notes[key]["code"], notes[key]["readings"]) for st, key in zip(stages, keys) if notes[key].get("ai") is None]
    got = ai_part(client, todo, usage)
    for st, key in zip(stages, keys):
        sid = notes[key]["code"]["id"]
        if notes[key].get("ai") is None and sid in got:
            notes[key] = {**notes[key], "ai": got[sid]}
            changed[key] = notes[key]
    if changed:
        try:
            _put_notes(db, user_id, changed)
        except Exception:
            log.exception("Main file: couldn't save section notes")
    sections = []
    for i, key in enumerate(keys):
        n = notes[key]
        sec = {"handle": f"S{i + 1}", **n["code"], **(n.get("ai") or {"introduces": [], "uses": [], "claims": []})}
        if n.get("readings"):
            sec["readings"] = n["readings"]
        sections.append(sec)
    main = {"format": MAIN_FILE_FORMAT, "version": version, "title": _clip(page.get("title"), 200),
            "subtitle": _clip(page.get("subtitle"), 200), "level": str(page.get("level") or ""),
            "mode": MODES.get(str(quality or ""), str(quality or "")), "sections": sections,
            "complete": all(notes[k].get("ai") is not None and notes[k].get("readings") is not None for k in keys)}
    main["text"] = render(main)
    return main


def render(main: dict) -> str:
    head = f'Page: "{main["title"]}"' + (f' — {main["subtitle"]}' if main.get("subtitle") else "")
    head += f'. Level: {main.get("level") or "not set"}.'
    if main.get("mode"):
        head += f' Mode: {main["mode"]}.'
    if main.get("version"):
        head += f' Version {main["version"]}.'
    lines = [head]
    for sec in main["sections"]:
        lines.append(f'{sec["handle"]} · {sec["id"]} — "{sec["label"]}"' + (f': {sec["blurb"]}' if sec.get("blurb") else ""))
        lines += [f"  - {b}" for b in sec.get("blocks") or [] if not b.startswith("playground ")]
        if sec.get("playground"):
            lines.append(f'  playground {sec["playground"]}')
        if sec.get("readings"):
            lines.append("  at the start it shows: " + re.sub(r"\s*\n\s*", "; ", sec["readings"]))
        meaning = [f"{k}: " + "; ".join(sec[k]) for k in ("introduces", "uses", "claims") if sec.get(k)]
        if meaning:
            lines.append("  " + " | ".join(meaning))
    return "\n".join(lines)


def _unpack(blob: bytes | None) -> dict | None:
    try:
        return json.loads(gzip.decompress(blob).decode("utf-8")) if blob else None
    except (OSError, ValueError):
        return None


def main_file_of(db: Database, client: Any, user_id: str, line: str, version: int, usage: UsageLog) -> dict | None:
    """A version's main file: the saved one, or built now (and saved once every section has its AI part). None for a
    missing or deleted version."""
    with db.session() as s:
        v = versions.row(s, user_id, line, version)
        if v is None or v.deleted or not v.payload:
            return None
        saved = _unpack(v.main_file)
        if saved is not None and saved.get("format") == MAIN_FILE_FORMAT:
            return saved
        page = versions.unpack(v.payload)
        rec = s.get(Explainer, (user_id, v.record_id))
        request = (rec.data or {}).get("request") if rec is not None and isinstance(rec.data, dict) else None
        quality = request.get("quality") if isinstance(request, dict) else None
    main = describe(db, client, user_id, page, version=version, quality=quality, usage=usage)
    if main["complete"]:
        with db.session() as s:
            v = s.get(PageVersion, (user_id, line, version))
            if v is not None and not v.deleted and v.payload:
                v.main_file = versions.pack(main)
                v.size = len(v.payload) + len(v.main_file)
    return main


def handle_of(main: dict, ref: Any) -> str | None:
    """The section id a handle ("S3", "s3", "3") or an id names in **this** main file; None when there's no such section."""
    ref = str(ref or "").strip()
    secs = main.get("sections") or []
    for sec in secs:
        if sec.get("id") == ref:
            return sec["id"]
    m = re.fullmatch(r"[Ss]?(\d+)", ref)
    if m and 1 <= int(m.group(1)) <= len(secs):
        return secs[int(m.group(1)) - 1].get("id")
    return None
