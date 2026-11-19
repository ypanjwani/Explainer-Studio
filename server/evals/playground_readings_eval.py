"""Playground readings eval (Nov 13 2026): what saved Explorer playgrounds really show, and whether Jev finds their
section's text contradicting it (explorer.judge.readings_contradict, used by follow-ups v4).

For each finished Explorer page in the dev database (newest first), each section with a playground: the default readings
(explorer.playground.read_playground: the start, each slider's ends, options, Play, buttons), then Jev's check of the
section's text against them. Writes a Markdown table to playground_readings_results.md next to this file.

Run from server/:
    .venv/Scripts/python -m evals.playground_readings_eval --no-jev          # readings only, free
    .venv/Scripts/python -m evals.playground_readings_eval --limit 20        # + Jev (real calls; needs JEV_API_KEY)
    .venv/Scripts/python -m evals.playground_readings_eval --id vequ1450mwervos3
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from explainer.modes.explorer.judge import readings_contradict, section_text
from explainer.modes.explorer.playground import read_playground, readings_text
from explainer.modes.followup import playground_edit as pe
from explainer.pricing import UsageLog

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "instance" / "dev.db"
OUT = HERE / "playground_readings_results.md"


def cell(s: str, n: int = 400) -> str:
    s = " ".join(str(s or "").split())
    s = s if len(s) <= n else s[: n - 1] + "…"
    return s.replace("|", "\\|")


def pages(conn: sqlite3.Connection, only: str | None, limit: int) -> list[dict]:
    q = "select data from explainers where status = 'done'" + (" and (id = ? or thread = ?)" if only else "") + " order by created_at desc"
    out = []
    for (raw,) in conn.execute(q, (only, only) if only else ()):
        rec = json.loads(raw) if isinstance(raw, str) else raw
        spec = rec.get("spec") if isinstance(rec, dict) else None
        if not isinstance(spec, dict) or not spec.get("stages"):
            continue
        if (rec.get("request") or {}).get("quality") != "playground":
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-jev", action="store_true", help="readings only (free)")
    ap.add_argument("--limit", type=int, default=15, help="pages, newest first")
    ap.add_argument("--id", help="one explainer (its id, or a conversation's thread id)")
    args = ap.parse_args()
    load_dotenv(HERE.parent.parent / ".env")
    key = None if args.no_jev else os.environ.get("JEV_API_KEY")
    if not args.no_jev and not key:
        raise SystemExit("JEV_API_KEY is not set (or use --no-jev)")
    usage = UsageLog("readings-eval", "low")
    conn = sqlite3.connect(DB)
    rows = ["| page | section | playground | Jev | readings (start of) |", "|---|---|---|---|---|"]
    counts = {"sections": 0, "no_readings": 0, "contradicts": 0}
    for rec in pages(conn, args.id, args.limit):
        for st in rec["spec"]["stages"]:
            pgs = [b for b in st.get("blocks") or [] if b.get("type") == "widget" and pe.widget_kind(b) == "playground"]
            if not pgs:
                continue
            counts["sections"] += 1
            shown = []
            for b in pgs:
                spec = pe.read_playground_spec(b.get("html", ""))
                shown.append({"title": str(b.get("title") or ""), "readings": readings_text(read_playground(spec)) if spec else ""})
            if not any(r["readings"] for r in shown):
                counts["no_readings"] += 1
            verdict = "—" if args.no_jev else (readings_contradict(key, st, shown, usage) or "agrees")
            if verdict not in ("—", "agrees"):
                counts["contradicts"] += 1
            print(f"{rec['id']} / {st.get('label', '')}: {verdict}")
            rows.append(f"| {cell(rec.get('title'), 60)} ({rec['id']}) | {cell(st.get('label'), 60)} | {cell(shown[0]['title'], 60)} | "
                        f"{cell(verdict, 120)} | {cell(' / '.join(r['readings'] for r in shown), 600)} |")
            if args.id:
                print(section_text(st)[:1500], "\n---\n", "\n\n".join(r["readings"] for r in shown), "\n")
    total = usage.summary()["totalUsd"]
    head = (f"# Playground readings eval\n\nSections with a playground: {counts['sections']}; no readings: {counts['no_readings']}; "
            f"Jev found a contradiction: {counts['contradicts']}{' (Jev not run)' if args.no_jev else ''}. Jev cost: ${total:.4f}.\n\n")
    OUT.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{counts} -> {OUT}")


if __name__ == "__main__":
    main()
