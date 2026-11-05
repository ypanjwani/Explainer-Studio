"""Follow-up pick test: how sure must Jev be before followup_pick.py trusts its section pick?

The three cut-offs in explainer/followup_pick.py (PICK_SURE, SECTION_IN, SECTION_OUT) are chosen from data here:
labelled requests on saved pages, Jev's raw scores, and an offline sweep of every setting.

From server/, with the venv:
    .venv/Scripts/python -m evals.followup_pick_eval build            # cases JSON -> label sheet (for review)
    .venv/Scripts/python -m evals.followup_pick_eval run [--dry-run]  # Jev twice per row (billed, cents)
    .venv/Scripts/python -m evals.followup_pick_eval sweep            # thresholds on the tune rows (no calls)
    .venv/Scripts/python -m evals.followup_pick_eval check            # the chosen setting on the check rows (no calls)
    .venv/Scripts/python -m evals.followup_pick_eval chain            # Haiku after the pick (billed, cents)

Labels: evals/followup_pick_labels.xlsx, tab "Labels" (the source of truth once reviewed; followup_pick_cases.json only
seeds it). A row whose note starts with "drop" is skipped. Runs: ../.variety/followup-pick-eval/<time>.json.
Results are written back into the sheet as tabs "Jev scores", "Sweep" and "Result".
Jev gets exactly what the product sends (section_cards, jev_state, jev_questions), so this measures what runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from explainer import followup_pick as fp
from explainer.feedback_pick import section_cards
from explainer.jev import JevError, jev_ask
from explainer.pricing import UsageLog

ROOT = Path(__file__).resolve().parents[2]  # explainer-studio/
CASES = Path(__file__).with_name("followup_pick_cases.json")
SHEET = Path(__file__).with_name("followup_pick_labels.xlsx")
VARIETY = ROOT / ".variety"
RUNS = VARIETY / "followup-pick-eval"
REPEATS = 2  # Jev runs per row, to see how much its scores move

SCOPES = ("some", "every", "new", "unclear")
PARTS = ("playground", "chart", "text", "remove", "-")
COLS = ["ID", "Page", "Mode", "Sections", "Request", "Kind", "Right scope", "Right sections", "Right part", "Set", "Your note", "Page file"]
WIDTHS = [8, 34, 11, 60, 48, 18, 12, 14, 12, 8, 34, 30]

# The sweep's grid (step 0.05) and the rule agreed with the user: no wrong-and-sure picks, then the fewest handoffs.
GRID_SURE = [round(0.40 + 0.05 * i, 2) for i in range(11)]  # 0.40 .. 0.90
GRID_IN = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 .. 0.95
GRID_OUT = [round(0.05 + 0.05 * i, 2) for i in range(10)]  # 0.05 .. 0.50
MAX_WRONG = 1  # allowed only when no setting has 0


# ---------- pages and labels ----------

def load_page(name: str) -> dict:
    d = json.loads((VARIETY / f"{name}.json").read_text(encoding="utf-8"))
    return d.get("spec", d)


def mode_of(name: str) -> str:
    return "Quick read" if "lite-plus" in name else "Playground"


def sections_text(page: dict) -> str:
    out = []
    for i, st in enumerate(page["stages"]):
        pg = " [playground]" if any(b.get("type") == "widget" for b in st.get("blocks") or []) else ""
        out.append(f"{i + 1}. {st.get('label') or st['id']}{pg}")
    return "\n".join(out)


def parse_sections(v) -> list[int]:
    if v is None:
        return []
    return sorted({int(x) for x in str(v).replace(";", ",").split(",") if x.strip().isdigit()})


def read_labels() -> list[dict]:
    if not SHEET.exists():
        sys.exit(f"No label sheet yet: run `build` first ({SHEET.name}).")
    ws = load_workbook(SHEET, read_only=True)["Labels"]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        x = dict(zip(head, r))
        if not x.get("ID"):
            continue
        note = str(x.get("Your note") or "").strip()
        if note.lower().startswith("drop"):
            continue
        scope = str(x.get("Right scope") or "").strip().lower()
        if scope not in SCOPES:
            sys.exit(f"{x['ID']}: 'Right scope' must be one of {', '.join(SCOPES)} (got {scope!r}).")
        secs = parse_sections(x.get("Right sections"))
        if scope == "some" and not secs:
            sys.exit(f"{x['ID']}: scope 'some' needs section numbers.")
        out.append({"id": str(x["ID"]), "file": str(x["Page file"]), "request": str(x["Request"]), "kind": str(x.get("Kind") or ""),
                    "scope": scope, "sections": secs if scope == "some" else [],
                    "part": str(x.get("Right part") or "-").strip().lower(), "set": str(x.get("Set") or "tune").strip().lower()})
    return out


def write_tab(name: str, header: list[str], rows: list[list], widths: list[int] | None = None) -> None:
    """Replace one tab of the sheet (the Labels tab is never touched here)."""
    wb = load_workbook(SHEET)
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)
    ws.append(header)
    for r in rows:
        ws.append(r)
    _style(ws, widths or [16] * len(header))
    try:
        wb.save(SHEET)
    except PermissionError:
        sys.exit(f"Can't write {SHEET.name}: close it in Excel and run again.")


def _style(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="C2410C")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"


# ---------- build ----------

def build() -> None:
    if SHEET.exists() and "--force" not in sys.argv:
        sys.exit(f"{SHEET.name} exists (it may hold your edits). Use `build --force` to overwrite it.")
    seed = json.loads(CASES.read_text(encoding="utf-8"))
    pages = {k: load_page(v) for k, v in seed["pages"].items()}
    wb = Workbook()
    ws = wb.active
    ws.title = "Labels"
    ws.append(COLS)
    for c in seed["cases"]:
        key = c["id"].split("-")[0]
        page, file = pages[key], seed["pages"][key]
        n = len(page["stages"])
        if any(s < 1 or s > n for s in c["sections"]):
            sys.exit(f"{c['id']}: a section number is outside 1..{n}")
        ws.append([c["id"], page["title"], mode_of(file), sections_text(page), c["request"], c["kind"], c["scope"],
                   ", ".join(map(str, c["sections"])), c["part"], c["set"], c.get("note", ""), file])
    last = ws.max_row
    for col, options in (("G", SCOPES), ("I", PARTS), ("J", ("tune", "check"))):
        dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=False)
        dv.add(f"{col}2:{col}{last}")
        ws.add_data_validation(dv)
    for r in range(2, last + 1):  # the columns to check, shaded
        for col in "GHI":
            ws[f"{col}{r}"].fill = PatternFill("solid", fgColor="FFF7ED")
    _style(ws, WIDTHS)
    about = wb.create_sheet("About")
    for line in [
        ["What this is"],
        ["Typed follow-ups on 8 saved pages, with the answer the section pick should give. Jev's scores on these rows set the three cut-offs in followup_pick.py."],
        [""],
        ["What to do"],
        ["Check the shaded columns: Right scope, Right sections, Right part. Change any you disagree with."],
        ["Use 'Your note' for anything else. A note starting with 'drop' removes the row. You can also add rows (new ID, same columns, Page file copied from a row of that page)."],
        ["Save the file and close it before the run (the script writes results into new tabs)."],
        [""],
        ["Columns"],
        ["Right scope: some = particular sections · every = one change across the whole page · new = add a section the page doesn't have · unclear = the reader should be asked."],
        ["Right sections: section numbers, comma-separated (only for 'some'). Removing a section is 'some' with part 'remove'."],
        ["Right part: playground (change/add the interactive part) · chart (Quick read chart) · text · remove · - (not used). Scored only in the Haiku step after the pick."],
        ["Set: tune = the cut-offs are chosen on these · check = scored once at the end, on the chosen setting, to see it holds."],
        [""],
        ["How the cut-offs are chosen"],
        ["Wrong and sure (Jev sure, but the wrong sections or scope) must be 0 (at most 1 if 0 is impossible). Then the fewest handoffs to Haiku. Then the setting in the middle of the safe zone, so small score changes don't flip it. Each row is asked twice; a row counts as right only if both runs are."],
    ]:
        about.append(line)
    about.column_dimensions["A"].width = 140
    for row in about.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if c.value in ("What this is", "What to do", "Columns", "How the cut-offs are chosen"):
                c.font = Font(bold=True)
    wb.save(SHEET)
    kinds = sorted({c["kind"] for c in seed["cases"]})
    for s in ("tune", "check"):
        rows = [c for c in seed["cases"] if c["set"] == s]
        missing = [k for k in kinds if k not in {c["kind"] for c in rows}]
        print(f"{s}: {len(rows)} rows" + (f" (kinds missing: {', '.join(missing)})" if missing else " (every kind)"))
    print(f"Wrote {SHEET} ({len(seed['cases'])} rows, {len(pages)} pages)")


# ---------- run (Jev) ----------

def jev_input(row: dict) -> tuple[dict, dict, list[dict]]:
    page = load_page(row["file"])
    cards = section_cards(page)
    return fp.jev_state(page, row["request"], cards), fp.jev_questions(cards), cards


def run(dry: bool) -> None:
    rows = read_labels()
    if dry:
        state, qs, _ = jev_input(rows[0])
        print(json.dumps({"row": rows[0]["id"], "state": state, "questions": list(qs)}, ensure_ascii=False, indent=2))
        print(f"\n{len(rows)} rows x {REPEATS} runs would be sent.")
        return
    load_dotenv(ROOT / ".env")
    key = os.environ.get("JEV_API_KEY") or sys.exit("JEV_API_KEY is not set (explainer-studio/.env).")
    usage = UsageLog("followup-pick-eval", "low")
    jobs = [(r, k) for r in rows for k in range(REPEATS)]

    def ask(job):
        r, _ = job
        state, qs, _ = jev_input(r)
        try:
            return jev_ask(key, state, qs)
        except JevError as e:
            return {"error": str(e)}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ask, jobs))
    out: dict[str, dict] = {r["id"]: {"file": r["file"], "runs": []} for r in rows}
    tokens, model = 0, "jev"
    for (r, _), res in zip(jobs, results):
        if "error" in res:
            out[r["id"]]["runs"].append({"error": res["error"]})
            continue
        tokens += res["usage"].get("input_tokens", 0)
        model = res["model"]
        out[r["id"]]["runs"].append(res["answers"])
    usage.add("jev follow-up pick eval", model, {"input_tokens": tokens, "output_tokens": 0})
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / time.strftime("%Y%m%d-%H%M%S.json")
    usd = usage.summary()["totalUsd"]
    path.write_text(json.dumps({"rows": out, "usd": usd, "seconds": round(time.time() - t0)}, indent=1), encoding="utf-8")
    errors = sum(1 for v in out.values() for x in v["runs"] if "error" in x)
    print(f"Saved {path.name}: {len(jobs)} Jev calls, {errors} errors, ${usd:.4f}, {round(time.time() - t0)} s")
    scores_tab(rows, out)


def latest_run() -> dict:
    files = sorted(RUNS.glob("*.json")) if RUNS.exists() else []
    if not files:
        sys.exit("No Jev run yet: run `run` first.")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def scores_tab(rows: list[dict], out: dict) -> None:
    table = []
    for r in rows:
        for k, ans in enumerate(out[r["id"]]["runs"]):
            if "error" in ans:
                table.append([r["id"], k + 1, r["scope"], ", ".join(map(str, r["sections"])), ans["error"], ""])
                continue
            scope = ", ".join(f"{x['option']} {x['p']:.2f}" for x in ans["scope"]["ranking"])
            secs = ", ".join(f"{q[1:]}: {a['p']:.2f}" for q, a in ans.items() if q.startswith("s") and q[1:].isdigit())
            table.append([r["id"], k + 1, r["scope"], ", ".join(map(str, r["sections"])), scope, secs])
    write_tab("Jev scores", ["ID", "Run", "Right scope", "Right sections", "Jev scope (ranked)", "Jev per section"], table,
              [8, 6, 12, 14, 50, 70])


# ---------- sweep / check ----------

def outcome(answers: dict, row: dict, cards: list[dict], sure: float, s_in: float, s_out: float) -> str:
    """right | wrong | handoff for one Jev run under one setting (the product's own decide(), with the numbers set)."""
    old = fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT
    fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT = sure, s_in, s_out
    try:
        d = fp.decide(answers, cards)
    finally:
        fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT = old
    if d is None:
        return "handoff"
    if d.get("pageWide"):
        return "right" if row["scope"] == "every" else "wrong"
    want = {cards[n - 1]["id"] for n in row["sections"]}
    return "right" if row["scope"] == "some" and set(d["stages"]) == want else "wrong"


def row_outcome(runs: list, row: dict, cards: list[dict], setting: tuple) -> str:
    """Both runs together: wrong if either is wrong, right only if both are, else handoff (Jev errors count as handoffs)."""
    got = [outcome(a, row, cards, *setting) if "error" not in a else "handoff" for a in runs]
    return "wrong" if "wrong" in got else "right" if all(g == "right" for g in got) else "handoff"


def score(rows: list[dict], run_data: dict, setting: tuple) -> dict:
    res = {"right": 0, "wrong": 0, "handoff": 0, "wrong_ids": []}
    for r in rows:
        cards = section_cards(load_page(r["file"]))
        o = row_outcome(run_data["rows"][r["id"]]["runs"], r, cards, setting)
        res[o] += 1
        if o == "wrong":
            res["wrong_ids"].append(r["id"])
    return res


def settings():
    return [(a, b, c) for a in GRID_SURE for b in GRID_IN for c in GRID_OUT if c < b]


def sweep() -> None:
    rows = [r for r in read_labels() if r["set"] == "tune"]
    data = latest_run()
    missing = [r["id"] for r in rows if r["id"] not in data["rows"]]
    if missing:
        sys.exit(f"Rows not in the latest run (run `run` again): {', '.join(missing)}")
    results = {s: score(rows, data, s) for s in settings()}
    best_wrong = min(v["wrong"] for v in results.values())
    allowed = 0 if best_wrong == 0 else MAX_WRONG
    ok = {s: v for s, v in results.items() if v["wrong"] <= allowed}
    if not ok:
        print(f"No setting has at most {MAX_WRONG} wrong-and-sure picks (best: {best_wrong}). Jev shouldn't decide alone here.")
    fewest = min(v["handoff"] for v in ok.values()) if ok else None

    def neighbours(s):  # how many grid neighbours are just as good: the middle of the safe zone wins ties
        a, b, c = s
        near = [(round(a + da, 2), round(b + db, 2), round(c + dc, 2)) for da in (-.05, 0, .05) for db in (-.05, 0, .05) for dc in (-.05, 0, .05)]
        return sum(1 for n in near if n in ok and ok[n]["handoff"] <= fewest)

    ranked = sorted(results.items(), key=lambda kv: (kv[1]["wrong"] > allowed, kv[1]["wrong"], kv[1]["handoff"], -neighbours(kv[0]) if kv[0] in ok else 0))
    current = (0.6, 0.7, 0.3)
    cur = results[current]
    table = [[*s, v["wrong"], v["handoff"], v["right"], neighbours(s) if s in ok else "", ", ".join(v["wrong_ids"])] for s, v in ranked[:25]]
    table.append(["now: 0.6", 0.7, 0.3, cur["wrong"], cur["handoff"], cur["right"], "", ", ".join(cur["wrong_ids"])])
    write_tab("Sweep", ["PICK_SURE", "SECTION_IN", "SECTION_OUT", "Wrong + sure", "Handoff", "Right", "Safe neighbours", "Wrong rows"], table,
              [11, 11, 12, 13, 9, 7, 15, 40])
    chosen = ranked[0][0] if ok else None
    print(f"{len(rows)} tune rows, {len(results)} settings tried.")
    print(f"Now (0.6/0.7/0.3): {cur['right']} right, {cur['wrong']} wrong and sure {cur['wrong_ids']}, {cur['handoff']} handoffs")
    if chosen:
        v = results[chosen]
        print(f"Chosen {chosen}: {v['right']} right, {v['wrong']} wrong and sure {v['wrong_ids']}, {v['handoff']} handoffs "
              f"({round(100 * v['handoff'] / len(rows))}% go to Haiku)")
        (RUNS / "chosen.json").write_text(json.dumps({"setting": chosen, "tune": v}), encoding="utf-8")


def check() -> None:
    path = RUNS / "chosen.json"
    if not path.exists():
        sys.exit("Run `sweep` first.")
    chosen = tuple(json.loads(path.read_text(encoding="utf-8"))["setting"])
    rows = [r for r in read_labels() if r["set"] == "check"]
    data = latest_run()
    v, now = score(rows, data, chosen), score(rows, data, (0.6, 0.7, 0.3))
    print(f"{len(rows)} check rows. Chosen {chosen}: {v['right']} right, {v['wrong']} wrong and sure {v['wrong_ids']}, {v['handoff']} handoffs")
    print(f"Now (0.6/0.7/0.3): {now['right']} right, {now['wrong']} wrong and sure {now['wrong_ids']}, {now['handoff']} handoffs")
    tune = json.loads(path.read_text(encoding="utf-8"))["tune"]
    write_tab("Result", ["", "PICK_SURE / IN / OUT", "Rows", "Right", "Wrong + sure", "Handoff", "Wrong rows"], [
        ["Chosen, tune rows", " / ".join(map(str, chosen)), tune["right"] + tune["wrong"] + tune["handoff"], tune["right"], tune["wrong"], tune["handoff"], ", ".join(tune["wrong_ids"])],
        ["Chosen, check rows", " / ".join(map(str, chosen)), len(rows), v["right"], v["wrong"], v["handoff"], ", ".join(v["wrong_ids"])],
        ["Now, check rows", "0.6 / 0.7 / 0.3", len(rows), now["right"], now["wrong"], now["handoff"], ", ".join(now["wrong_ids"])],
        ["Jev run cost", f"${data['usd']:.4f}", "", "", "", "", ""],
    ], [20, 20, 7, 7, 13, 9, 40])


# ---------- chain (Haiku after the pick) ----------

def part_ok(targets: list[dict], want: str) -> bool:
    got = {t["part"] for t in targets}
    return {"playground": "widget" in got, "remove": "remove" in got, "text": bool(got & {"text", "section"}),
            "chart": bool(got & {"text", "section", "widget"}), "-": True}.get(want, True)


def chain() -> None:
    import anthropic

    path = RUNS / "chosen.json"
    if not path.exists():
        sys.exit("Run `sweep` first.")
    load_dotenv(ROOT / ".env")
    chosen = tuple(json.loads(path.read_text(encoding="utf-8"))["setting"])
    rows, data = read_labels(), latest_run()
    client, usage = anthropic.Anthropic(), UsageLog("followup-pick-eval", "low")
    old = fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT

    def one(r: dict) -> list:
        page = load_page(r["file"])
        cards = section_cards(page)
        first = data["rows"][r["id"]]["runs"][0]
        fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT = chosen
        d = fp.decide(first, cards) if "error" not in first else None
        want_ids = {cards[n - 1]["id"] for n in r["sections"]}
        if d and d.get("pageWide"):
            return [r["id"], r["set"], "jev", "whole page", "", r["scope"] == "every"]
        if d:
            s = fp.scoped_targets(client, page, d["stages"], r["request"], usage)
            if not s:
                return [r["id"], r["set"], "jev+haiku", "nothing usable -> whole-page finder", "", ""]
            if s.get("question"):
                return [r["id"], r["set"], "jev+haiku", "asked: " + str(s["question"].get("text", ""))[:80], "", r["scope"] == "unclear"]
            parts = "; ".join(f"{t['part']} {t['stage']}" for t in s["targets"])
            return [r["id"], r["set"], "jev+haiku", parts, r["part"], part_ok(s["targets"], r["part"])]
        f = fp.find_targets_ask(client, usage, page, r["request"])
        if not f:
            return [r["id"], r["set"], "haiku", "failed", "", False]
        if f.get("question"):
            return [r["id"], r["set"], "haiku", "asked: " + str(f["question"].get("text", ""))[:80], "", r["scope"] == "unclear"]
        if f.get("pageWide"):
            return [r["id"], r["set"], "haiku", "whole page", "", r["scope"] == "every"]
        ts = f.get("targets") or []
        parts = "; ".join(f"{t['part']} {t['stage'] or t.get('after', '')}" for t in ts)
        if r["scope"] == "new":
            return [r["id"], r["set"], "haiku", parts, "new", any(t["part"] == "new-section" for t in ts)]
        right = r["scope"] == "some" and {t["stage"] for t in ts} == want_ids and part_ok(ts, r["part"])
        return [r["id"], r["set"], "haiku", parts, r["part"], right]

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            table = list(pool.map(one, rows))
    finally:
        fp.PICK_SURE, fp.SECTION_IN, fp.SECTION_OUT = old
    usd = usage.summary()["totalUsd"]
    for who in ("jev", "jev+haiku", "haiku"):
        got = [t for t in table if t[2] == who and t[5] != ""]
        if got:
            print(f"{who}: {sum(1 for t in got if t[5] is True)}/{len(got)} right")
    print(f"Haiku cost ${usd:.4f}")
    write_tab("Chain", ["ID", "Set", "Decided by", "What came back", "Right part", "Right?"],
              [[*t[:5], "" if t[5] == "" else ("yes" if t[5] else "NO")] for t in table], [8, 7, 11, 70, 11, 8])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "run", "sweep", "check", "chain"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.step == "build":
        build()
    elif a.step == "run":
        run(a.dry_run)
    else:
        {"sweep": sweep, "check": check, "chain": chain}[a.step]()
