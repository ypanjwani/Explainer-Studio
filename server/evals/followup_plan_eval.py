"""Follow-ups v3 understand test: does the first step (explainer/understand.py) read each follow-up right?

Plans only: no page is built, so a row costs one Haiku Low call (plus one page-map call per page, cached for the run).
It scores the same labelled rows as followup_pick_eval.py (evals/followup_pick_labels.xlsx, tab "Labels"), so the planner
can be compared with today's pick, plus evals/followup_plan_extra_cases.json for what the pick can't do (refusals,
answers, undo, moves, merges, level, page title).

From server/, with the venv (needs ANTHROPIC_API_KEY and the saved pages in ../.variety, as followup_pick_eval):
    .venv/Scripts/python -X utf8 -m evals.followup_plan_eval run --dry-run    # list the rows, no calls
    .venv/Scripts/python -X utf8 -m evals.followup_plan_eval run              # billed: about a cent per 5 rows
    .venv/Scripts/python -X utf8 -m evals.followup_plan_eval report [file]    # the last (or given) run, again

How a row is scored (the reader's own parts only; knock-on intents the app adds are not scored). The part (text or
playground) is not scored: v3's section editor decides it after seeing the section.
  scope some     kind "change" on exactly the labelled sections (section intents, or remove_section for part "remove");
                 kind "ask" counts as asked (neither right nor wrong)
  scope every    kind "change" with a page-wide step (set_level / set_tone / set_length), or an intent on every section
  scope new      kind "change" with add_section
  scope unclear  kind "ask"
  extra rows     the reply kind; for kind "change" the listed ops, and at least one section intent when "intent" is set
Runs: ../.variety/followup-plan-eval/<time>.json.
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

from explainer.modes.followup import page_index as pidx
from explainer.modes.followup import understand as U
from explainer.pricing import UsageLog

from .followup_pick_eval import CASES, VARIETY, load_page, read_labels

RUNS = VARIETY / "followup-plan-eval"
EXTRA = Path(__file__).with_name("followup_plan_extra_cases.json")


class MemStore:
    """The page-map cache for one run."""

    def __init__(self):
        self.d: dict = {}

    def get(self, key):
        return self.d.get(key)

    def put(self, key, value):
        self.d[key] = value


def rows() -> list[dict]:
    seed = json.loads(CASES.read_text(encoding="utf-8"))
    files = seed["pages"]
    out = [{**r, "quality": "lite-plus" if "lite-plus" in r["file"] else "playground"} for r in read_labels()]
    for c in json.loads(EXTRA.read_text(encoding="utf-8"))["cases"]:
        f = files[c["page"]]
        out.append({"id": c["id"], "file": f, "request": c["request"], "kind": "extra", "expect": c["expect"], "ops": c.get("ops", []),
                    "intent": c.get("intent", False), "quality": "lite-plus" if "lite-plus" in f else "playground"})
    return out


def score(row: dict, out: dict, page: dict) -> str:
    """"right", "wrong" or "asked"."""
    kind = out.get("kind") or "error"
    if row["kind"] == "extra":
        if kind != row["expect"]:
            return "wrong"
        ops = {s["op"] for s in out.get("steps") or []}
        mine = [i for i in out.get("sections") or [] if not i.get("derived")]
        return "right" if all(o in ops for o in row["ops"]) and (mine or not row.get("intent")) else "wrong"
    if row["scope"] == "unclear":
        return "right" if kind == "ask" else "wrong"
    if kind == "ask":
        return "asked"
    if kind != "change":
        return "wrong"
    steps = out.get("steps") or []
    mine = [i for i in out.get("sections") or [] if not i.get("derived")]
    ids = [st["id"] for st in page["stages"]]
    if row["scope"] == "new":
        return "right" if any(s["op"] == "add_section" for s in steps) else "wrong"
    if row["scope"] == "every":
        wide = any(s["op"] in U.PAGE_WIDE for s in steps) or {i["section"] for i in mine} >= set(ids)
        return "right" if wide else "wrong"
    want = {ids[n - 1] for n in row["sections"] if 0 < n <= len(ids)}
    if row.get("part") == "remove":
        return "right" if {s["section"] for s in steps if s["op"] == "remove_section"} == want else "wrong"
    return "right" if {i["section"] for i in mine} == want else "wrong"


def run(dry: bool) -> None:
    all_rows = rows()
    if dry:
        for r in all_rows:
            print(f'{r["id"]:7} {r["quality"]:10} {r["kind"]:18} {r["request"]}')
        print(f"{len(all_rows)} rows")
        return
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    os.environ["FOLLOWUP_PLAN"] = "1"
    store = MemStore()
    pages = {f: load_page(f) for f in {r["file"] for r in all_rows}}
    for f, p in pages.items():  # map each page once, before the rows run side by side
        pidx.get_or_build(store, client, p, UsageLog("eval"))

    def one(r: dict) -> dict:
        usage = UsageLog("eval", "low")
        t = time.time()
        try:
            out = U.plan(client, store, pages[r["file"]], r["request"], r["quality"], usage)
        except Exception as e:  # noqa: BLE001
            out = {"error": repr(e)}
        return {**r, "out": out, "result": score(r, out, pages[r["file"]]), "usd": usage.summary()["totalUsd"], "seconds": round(time.time() - t, 1)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, all_rows))
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    report(path)


def report(path: Path | None) -> None:
    if path is None:
        runs = sorted(RUNS.glob("*.json"))
        if not runs:
            sys.exit("No runs yet.")
        path = runs[-1]
    results = json.loads(path.read_text(encoding="utf-8"))
    by: dict[str, dict[str, int]] = {}
    for r in results:
        k = r["kind"] if r["kind"] != "extra" else f'extra:{r["expect"]}'
        by.setdefault(k, {"right": 0, "wrong": 0, "asked": 0})[r["result"]] += 1
    print(f"{path.name}: {len(results)} rows, ${sum(r['usd'] for r in results):.4f}")
    for k, v in sorted(by.items()):
        print(f"  {k:22} right {v['right']:3}  wrong {v['wrong']:3}  asked {v['asked']:3}")
    right = sum(1 for r in results if r["result"] == "right")
    asked = sum(1 for r in results if r["result"] == "asked")
    print(f"  all: {right}/{len(results)} right ({100 * right / len(results):.0f}%), {asked} asked")
    for r in results:
        if r["result"] == "wrong":
            o = r["out"]
            got = o.get("kind") or o.get("error")
            steps = ", ".join([*(f'{s["op"]}:{s["section"]}' for s in o.get("steps") or []),
                               *(f'intent:{i["section"]}' for i in o.get("sections") or [] if not i.get("derived"))])
            print(f'  WRONG {r["id"]}: "{r["request"]}" -> {got} {steps}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.dry_run)
    else:
        report(Path(a.file) if a.file else None)


if __name__ == "__main__":
    main()
