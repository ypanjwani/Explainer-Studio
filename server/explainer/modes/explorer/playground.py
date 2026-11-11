from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import anthropic

from ... import data
from ...core.validate import WIDGET_MAX_CHARS
from ...js import js_len
from ...json_util import extract_json
from ...llm import HAIKU, friendly_error, write_text
from ...pricing import UsageLog
from .judge import _num, _section, gate_hint, plan_text, review, scores_text
from .settings import ExplorerSettings, _lower

log = logging.getLogger(__name__)


MAX_REPAIRS = 2


# Playground: one extra repair for soft layout problems (check_playground's "layout"), kept only if it is better.
LAYOUT_REPAIRS = 1


PG_LOOK_LEAD = ("The playground works and passed every check. Fix only how it looks, as described below from screenshots, "
                "keeping the same controls, state, maths and views.")


PG_LOOK_CHECKED = ("overlapping or cut-off shapes, arrows with no room, labels cut inside shapes, link labels on shapes, links "
                   "through shapes, lines outside or capped at the y range, a mostly empty x or y range, text marks and heatmaps past "
                   "the chart's edge, the y-axis name on the plot (it is drawn above it)")


LAYOUT_LEAD = ("The playground works and passed every check. Fix only these on-screen layout problems, keeping the same "
               "controls, state, maths and views; move, resize or relabel only what is listed.")


# Nov 2: coverage problems (plan.covers items no component delivers) share the one soft repair with layout problems.
SOFT_LEAD = ("The playground works and passed every check. Fix only the problems listed below: correct the formula a failed "
             "worked-example check points to, add or fix what a plan.covers item needs, and move, resize or relabel what a "
             "layout problem names. Keep every other control, state value, formula and view as it is.")


CUTOFF = "cut off"  # check_stop's wording when a reply hit max_tokens


CHECK_TIMEOUT = 25  # seconds for one node check


CHECK_RUNS = 2  # a check that crashes is run once more; failing twice, the widget fails (fail closed)


CHECK_FAILED = "the widget check could not run, so the widget can't be trusted: keep it simple and standard"


# Code Lab 7.0's look check: screenshots of each finished widget in headless Chromium (Playwright), read by Haiku.
SNAP_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "snap-widget.mjs"


SNAP_TIMEOUT = 45  # seconds for one capture (browser start + up to 6 frames)


_SNAP_SLOTS = threading.Semaphore(2)  # sections run in parallel; at most 2 browsers at once (memory on the server)


LOOK_TOKENS = 8_000  # the answer is short; most of it is thinking


def _node() -> str | None:
    return shutil.which("node")


# On Windows each node check gets its own hidden console (CREATE_NO_WINDOW; 0 elsewhere). Attached to the server's
# console instead, node exited at once with no output and no error once the window that started the dev server was
# gone (Oct 24: every Playground check on a page came back empty within milliseconds, so every section stayed text).
_NODE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_node(args: list[str], stdin: str, timeout: float) -> subprocess.CompletedProcess:
    """One node script run for the checks: stdin in, stdout/stderr captured as text. Raises TimeoutExpired."""
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, encoding="utf-8", creationflags=_NODE_FLAGS)


def node_failure(proc: Any) -> str:
    """Why a node run gave nothing usable, for the log: its exit code (as hex on Windows, e.g. 0xc0000142) and stderr."""
    if proc is None:
        return "it did not start"
    code = getattr(proc, "returncode", None)
    shown = f"{code} ({code & 0xFFFFFFFF:#x})" if isinstance(code, int) else "unknown"
    return f"exit code {shown}; stderr: {(getattr(proc, 'stderr', '') or '')[-500:]!r}"


def _rejected(res: dict, label: str, kind: str, plan: dict, html: str, scores: dict) -> None:
    """Keep a widget Jev's review rejected (it had passed the code checks), so test runs can inspect it (debugRejected).
    kind: "first" (the planned widget), "fix" (after the reviewer's fix) or "backup". Never shown on the page."""
    res.setdefault("rejected", []).append({"section": label, "kind": kind, "title": str(plan.get("title") or ""),
                                           "lesson": str(plan.get("lesson") or ""), "html": html, "scores": scores})


def snap_widget(html: str) -> list[dict] | None:
    """Screenshots of the widget as the page shows it (scripts/snap-widget.mjs: start, each slider at min and max, a
    menu's last option, ~1 s after Play): [{"label", "png"}] (png base64), or None when they can't be taken (no node,
    no Playwright or browser, a crash). This check is an extra on top of the code checks, so it fails open: no frames =
    the widget is kept as it is."""
    node = _node()
    if not node or not SNAP_SCRIPT.exists():
        return None
    with _SNAP_SLOTS:
        try:
            proc = run_node([node, str(SNAP_SCRIPT)], html, SNAP_TIMEOUT)
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            log.exception("Code Lab: the widget screenshots failed")
            return None
    if not isinstance(out, dict) or out.get("error"):
        log.warning("Code Lab: no widget screenshots: %s", (out or {}).get("error") if isinstance(out, dict) else "no output")
        return None
    frames = [f for f in out.get("frames") or [] if isinstance(f, dict) and f.get("png")]
    return frames or None


def look_review(client: anthropic.Anthropic, usage: UsageLog, frames: list[dict], plan: dict | None, title: str,
                checked: str | None = None, step: str = "code lab look") -> list[str] | None:
    """Haiku reads the frames (in order, each with its caption) and lists what a reader would see is wrong (up to 4).
    [] = it looks fine; None = the call failed. Thinking on, medium: on saved widgets it removed the false alarms that
    thinking-off answers had, for ~$0.0004 a widget (a false alarm costs a repair of a working widget)."""
    head = {"title": title, "lesson": str((plan or {}).get("lesson") or "")}
    if checked:  # Playground: what code already measured, so the judge doesn't spend the repair on it again
        head["alreadyChecked"] = checked
    content: list[dict] = [{"type": "text", "text": json.dumps(head, ensure_ascii=False)}]
    for i, f in enumerate(frames, 1):
        content.append({"type": "text", "text": f"Frame {i}: {f.get('label', '')}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": f["png"]}})
    try:
        text = write_text(client, usage, step, system=data.prompt("CODE_LAB_LOOK_SYSTEM"), user=content,
                          max_tokens=LOOK_TOKENS, fmt=data.formats()["codeLabLook"], effort="medium", model=HAIKU, thinking=True)
        reply, _ = extract_json(text)
    except Exception:
        log.exception("Code Lab: the look check failed")
        return None
    problems = reply.get("problems") if isinstance(reply, dict) else None
    if not isinstance(problems, list):
        return None
    return [re.sub(r"\s+", " ", str(p)).strip()[:300] for p in problems if str(p).strip()][:4]


PG_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "check-playground.mjs"


PG_TOKENS = 32_000  # streamed; most of a call is thinking, the spec itself is a few thousand tokens


PG_NO_PLAYGROUND = "no_playground"  # Haiku's answer when no playground can show the section honestly


_PG_STORED = ("title", "plan", "state", "init", "components")  # what the frame needs (afterBlock is only for placing it)


def playground_html(spec: dict) -> str:
    """The widget block's html for a playground: widget.ts sees XW.playground( and loads the runtime into the frame."""
    body = {k: spec[k] for k in _PG_STORED if k in spec}
    js = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    js = js.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f'<div id="pg"></div><script>XW.playground({js})</script>'


def check_playground(spec: Any) -> dict:
    """{"problems", "facts", "layout"} from scripts/check-playground.mjs (the experiment's validator: shape, every snippet
    compiles, a dry run with no errors or NaN, every control changes state that a view shows). Fails closed: no node, a
    crash or unreadable output is a problem (CHECK_FAILED), so an unchecked playground never reaches a page. "layout"
    (only on a pass) lists soft on-screen problems: overlapping or cut-off shapes, cut labels, capped or off-range lines,
    empty space. Measured in every state the dry run reaches (a later-only problem ends with "after the controls move").
    _pg_fix_layout fixes what it can by code, then gives the rest one repair; they never fail a playground."""
    if not isinstance(spec, dict):
        return {"problems": ["the reply must be one JSON object (the playground spec)"], "facts": None}
    text = json.dumps(spec, ensure_ascii=False)
    size = js_len(playground_html(spec))  # the page drops a widget block over WIDGET_MAX_CHARS
    if size > WIDGET_MAX_CHARS:
        return {"problems": [f"the spec is too long ({size} characters, max {WIDGET_MAX_CHARS}): use fewer components, less "
                             "inline data (generate it in init) and shorter snippets"], "facts": None}
    node = _node()
    if not node or not PG_SCRIPT.exists():
        log.error("Playground: node or %s is missing, so playgrounds can't be checked", PG_SCRIPT.name)
        return {"problems": [CHECK_FAILED], "facts": None}
    for attempt in range(CHECK_RUNS):
        proc = None
        try:
            proc = run_node([node, str(PG_SCRIPT)], text, CHECK_TIMEOUT)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except subprocess.TimeoutExpired:
            return {"problems": ["checking it took too long: a snippet may loop forever; keep loops bounded"], "facts": None}
        except Exception:
            log.error("Playground: the check failed to run (attempt %d): %s", attempt + 1, node_failure(proc))
            continue
        if not isinstance(result, dict):
            continue
        if result.get("ok") is True:
            facts = result.get("facts")
            layout = [str(m) for m in result.get("layout") or [] if m][:10]
            coverage = [str(m) for m in result.get("coverage") or [] if m][:10]
            checks = [str(m) for m in result.get("checks") or [] if m][:4]
            return {"problems": [], "facts": facts if isinstance(facts, dict) else None, "layout": layout, "coverage": coverage,
                    "checks": checks}
        errors = [str(e) for e in result.get("errors") or []]
        return {"problems": errors or ["the checker rejected it without saying why: keep it simple and standard"], "facts": None}
    return {"problems": [CHECK_FAILED], "facts": None}


def playground_plan(spec: dict) -> dict:
    """What we keep and what Jev reads about a playground, in Code Lab's plan words: lesson (the spec's insight),
    readerDoes, and its controls and visuals (so chart_summary and review_state work unchanged)."""
    p = spec.get("plan") if isinstance(spec.get("plan"), dict) else {}
    comps = [c for c in spec.get("components") or [] if isinstance(c, dict)]
    controls = []
    for c in comps:
        if c.get("type") in ("slider", "select", "toggle", "button", "play") and c.get("action") != "reset":
            ctl = {"id": str(c.get("id") or ""), "kind": c["type"], "label": str(c.get("label") or c.get("id") or "")[:80]}
            if c["type"] == "slider" and _num(c.get("min")) and _num(c.get("max")):
                ctl.update(min=c["min"], max=c["max"])
            controls.append(ctl)
        elif c.get("type") == "chart" and (c.get("onDrag") or c.get("onClick")):
            controls.append({"id": str(c.get("id") or ""), "kind": "drag on chart" if c.get("onDrag") else "click on chart",
                             "label": str(c.get("title") or c.get("id") or "")[:80]})
    visuals = [{"what": str(c.get("title") or c.get("label") or c.get("id") or "")[:120]} for c in comps if c.get("type") in ("chart", "readout")]
    after = spec.get("afterBlock")
    return {"lesson": str(p.get("insight") or "")[:600], "readerDoes": str(p.get("user_changes") or "")[:400],
            "mechanism": str(p.get("mechanism") or "")[:600], "reacts": str(p.get("reacts") or "")[:400],
            "title": str(spec.get("title") or "")[:80], "afterBlock": after if isinstance(after, int) and not isinstance(after, bool) else None,
            "controls": controls[:12], "visuals": visuals[:12], "covers": _covers(p), "backup": ""}


def _covers(p: dict) -> list[dict]:
    """The spec's plan.covers, short, for the stored plan text (what the builder meant each part to deliver)."""
    items = p.get("covers") if isinstance(p.get("covers"), list) else []
    return [{"need": str(it.get("need") or "")[:80], "by": [str(x) for x in it.get("by") or []][:4] if isinstance(it.get("by"), list) else []}
            for it in items[:12] if isinstance(it, dict)]


def _pg_ask(client: anthropic.Anthropic, usage: UsageLog, step: str, payload: dict, settings: ExplorerSettings, why: list[str],
            repair: dict | None = None, effort: str | None = None, edit: dict | None = None) -> Any:
    """One Haiku call: the section's playground spec (a first build, a repair or, with `edit`, a change the reader asked
    for to a finished playground: codelab_edit.py). None if the call failed or the reply has no JSON (the reason goes into `why`)."""
    system = (data.prompt("PLAYGROUND_SYSTEM") + ("\n\n" + data.prompt("PLAYGROUND_REPAIR") if repair else "")
              + ("\n\n" + data.prompt("PLAYGROUND_EDIT") if edit else ""))
    user = {**payload, **(repair or {}), **(edit or {})}
    try:
        text = write_text(client, usage, step, system=system, user=json.dumps(user, ensure_ascii=False), max_tokens=PG_TOKENS,
                          effort=effort or settings.build, model=HAIKU, stream=True, thinking=settings.think)
        spec, _ = extract_json(text)
    except Exception as e:
        log.exception("Playground: Haiku call failed (%s)", step)
        why.append(friendly_error(e))
        return None
    return _keep_checks(spec, payload)


def _no_playground(spec: Any) -> bool:
    return isinstance(spec, dict) and spec.get("error") == PG_NO_PLAYGROUND


# Checker errors code can fix without a Haiku call (messages from shared/playground/validator.cjs). Seen Oct 24: a
# draggable point that started just below the chart, and a Step button whose onClick returned nothing; both cost the
# section its playground after the repairs.
_NUM = r"(-?[\d.]+(?:e[-+]?\d+)?)"


_PG_DRAG_OUT = re.compile(rf"^([\w-]+): draggable mark .*? starts at \({_NUM}, {_NUM}\), outside the chart range")


_PG_BAR_OUT = re.compile(rf"^([\w-]+): a bar has value {_NUM}, outside the chart's y range")


_PG_DEAD = re.compile(r"^([\w-]+) (?:never changes state|changes \[.*\] but no chart or readout depends on that)")


def _widen(rng: Any, v: float) -> list | None:
    """rng ([lo, hi]) grown to hold v with a 10% margin, or None when rng isn't a numeric range."""
    if not (isinstance(rng, list) and len(rng) == 2 and all(_num(x) for x in rng) and rng[0] < rng[1]):
        return None
    lo, hi = float(rng[0]), float(rng[1])
    pad = 0.1 * (max(hi, v) - min(lo, v))
    return [round(v - pad, 6) if v < lo else lo, round(v + pad, 6) if v > hi else hi]


def _pg_autofix(spec: dict, problems: list[str]) -> dict | None:
    """A copy of spec with the checker's mechanical problems fixed by code: a chart range widened to hold a draggable
    point or a bar, a control that changes nothing removed (only while another control stays). None if nothing applied."""
    out = json.loads(json.dumps(spec))
    comps = out.get("components") if isinstance(out.get("components"), list) else []
    by_id = {c.get("id"): c for c in comps if isinstance(c, dict)}
    # Counted like the validator does: a Reset button is not a control, so it can't be the one that "stays".
    controls = lambda: [c for c in comps if isinstance(c, dict) and (c.get("type") in ("slider", "select", "toggle", "play")  # noqa: E731
                                                                    or (c.get("type") == "button" and c.get("action") != "reset")
                                                                    or (c.get("type") == "chart" and (c.get("onDrag") or c.get("onClick"))))]
    changed = False
    for msg in problems:
        if m := _PG_DRAG_OUT.match(msg):
            c = by_id.get(m.group(1))
            if c is not None:
                x, y = _widen(c.get("x"), float(m.group(2))), _widen(c.get("y"), float(m.group(3)))
                if x and y:
                    c["x"], c["y"], changed = x, y, True
        elif m := _PG_BAR_OUT.match(msg):
            c = by_id.get(m.group(1))
            y = _widen(c.get("y"), float(m.group(2))) if c is not None else None
            if y:
                c["y"], changed = y, True
        elif m := _PG_DEAD.match(msg):
            c = by_id.get(m.group(1))
            if c is not None and c.get("type") in ("button", "play", "slider", "select", "toggle") and len(controls()) > 1:
                comps.remove(c)
                by_id.pop(m.group(1), None)
                changed = True
    return out if changed else None


# Layout problems code can fix in the spec itself (messages from layoutProblems in shared/playground/validator.cjs).
# Seen in 167 saved playgrounds (Oct 31): a mostly empty x or y range, a probability curve
# running along the top edge of [0, 1], a line just outside y. Text past the edge is fixed by the renderer (placeText).
_PG_EMPTY = re.compile(rf"^([\w-]+): the data never fills more than \d+% of the ([xy]) range \[{_NUM},{_NUM}\].*?narrow \2 to about \[{_NUM}, {_NUM}\]")


_PG_CAPPED = re.compile(rf"^([\w-]+): .*? sits on the top edge of the chart \(y = {_NUM}\)")


_PG_Y_OUT = re.compile(rf"^([\w-]+): .*? lies outside the y range \[{_NUM},{_NUM}\] for \d+ of \d+ points \(values {_NUM} to {_NUM}\)")


_PG_HEAT_OUT = re.compile(rf"^([\w-]+): the heatmap covers ([xy]) .*?set the chart's \2 to \[{_NUM},{_NUM}\]")


PG_WIDEN_MAX = 3  # a line outside y is fitted only if y grows at most 3x; more would squash the rest (left to Haiku)


def _range(v: Any) -> list[float] | None:
    ok = isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v) and v[0] < v[1]
    return [float(v[0]), float(v[1])] if ok else None


def _pg_layout_autofix(spec: dict, layout: list[str]) -> dict | None:
    """A copy of spec with the layout problems code can fix applied (see _PG_EMPTY...), or None if none applied.
    The caller keeps it only if every check still passes and fewer layout problems are left."""
    out = json.loads(json.dumps(spec))
    comps = out.get("components") if isinstance(out.get("components"), list) else []
    charts = {c.get("id"): c for c in comps if isinstance(c, dict) and c.get("type") == "chart"}
    changed = False
    for msg in layout:
        if m := _PG_EMPTY.match(msg):  # x or y
            c, axis = charts.get(m.group(1)), m.group(2)
            old = _range(c.get(axis)) if c is not None else None
            if old is None:
                continue
            lo, hi = float(m.group(5)), float(m.group(6))
            if old[0] >= 0 > lo:  # a range that started at 0 or above doesn't get negative space
                lo = old[0]
            if lo < hi:
                c[axis], changed = [round(lo, 6), round(hi, 6)], True
        elif m := _PG_CAPPED.match(msg):
            c = charts.get(m.group(1))
            y = _range(c.get("y")) if c is not None else None
            # only probabilities and percentages that really reach 1 or 100; a clamped value stays with Haiku
            if y and y[1] == float(m.group(2)) and y[1] in (1.0, 100.0) and y[0] >= 0:
                c["y"], changed = [y[0], round(y[1] + 0.05 * (y[1] - y[0]), 6)], True
        elif m := _PG_Y_OUT.match(msg):
            c = charts.get(m.group(1))
            y = _range(c.get("y")) if c is not None else None
            new = _widen(y, float(m.group(4))) if y else None
            new = _widen(new, float(m.group(5))) if new else None
            if y and new and new[1] - new[0] <= PG_WIDEN_MAX * (y[1] - y[0]):
                c["y"], changed = new, True
        elif m := _PG_HEAT_OUT.match(msg):
            c = charts.get(m.group(1))
            lo, hi = float(m.group(3)), float(m.group(4))
            if c is not None and lo < hi:
                c[m.group(2)], changed = [lo, hi], True
    return out if changed else None


def _pg_until_clean(client: anthropic.Anthropic, usage: UsageLog, payload: dict, spec: Any, res: dict, settings: ExplorerSettings,
                    why: list[str]) -> tuple[dict | None, dict]:
    """(spec, report) once the checker passes it, repairing with its exact errors up to MAX_REPAIRS times; (None, report)
    if it still fails. A repair that answers {"error": "no_playground"} ends it too. Mechanical problems (_pg_autofix)
    are fixed by code first, before any repair call."""
    def by_code(spec: Any, report: dict) -> tuple[Any, dict]:
        if not report["problems"] or report["problems"] == [CHECK_FAILED] or not isinstance(spec, dict):
            return spec, report
        fixed = _pg_autofix(spec, report["problems"])
        if fixed is None:
            return spec, report
        again = check_playground(fixed)
        return (spec, report) if again["problems"] == [CHECK_FAILED] else (fixed, again)

    spec, report = by_code(spec, check_playground(spec))
    for _ in range(MAX_REPAIRS):
        if not report["problems"]:
            break
        if report["problems"] == [CHECK_FAILED]:  # the checker itself can't run: a repair can't help
            break
        res["repaired"] += 1
        fixed = _pg_ask(client, usage, "playground repair", payload, settings, why,
                        {"yourPreviousSpec": spec, "problems": report["problems"]}, effort=settings.fix)
        if fixed is None or _no_playground(fixed):
            break
        spec, report = by_code(fixed, check_playground(fixed))
    if report["problems"]:
        return None, report
    return _pg_fix_layout(client, usage, payload, spec, report, res, settings, why)


COVERS_MISSING = "plan.covers is missing"  # validator.cjs coverageProblems' message when there is no list


def _soft(report: dict) -> list[str]:
    """A passing playground's soft problems for the repair: worked-example checks first (wrong numbers), then coverage
    (what is missing), then layout (how it looks). A missing covers list alone isn't sent: writing the list afterwards
    adds nothing to the playground, and older saved playgrounds (follow-up edits) have none."""
    cov = [m for m in report.get("coverage") or [] if not m.startswith(COVERS_MISSING)]
    return [*(report.get("checks") or []), *cov, *(report.get("layout") or [])]


_SECTION_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _decimals(x: float | str) -> int:
    t = repr(x) if isinstance(x, float) else str(x)
    return len(t.split(".")[1]) if "." in t and "e" not in t.lower() else 0


def _close(expect: float, written: float, text: str) -> bool:
    """expect is a number written in the section: equal at the section's precision, at most 2 decimals finer (0.357
    matches a written 0.36), never coarser (0 doesn't match 0.36), and a whole number in the section matches only a whole
    expect (3.4 doesn't match 3)."""
    exp_dec = 0 if expect == int(expect) else _decimals(expect)
    dec = _decimals(text)
    if exp_dec < dec or exp_dec > dec + 2 or (dec == 0 and exp_dec > 0):
        return False
    return abs(expect - written) <= 0.5 * 10 ** -dec + 1e-9


def _keep_checks(spec: Any, payload: dict) -> Any:
    """spec with only the worked-example checks whose expected number the section itself writes (so a check tests
    Haiku's maths against the section, never against Haiku's own numbers). Nov 2."""
    if not isinstance(spec, dict) or not isinstance(spec.get("checks"), list):
        return spec
    text = json.dumps(payload.get("section") or {}, ensure_ascii=False)
    text = re.sub(r'"index": \d+', "", text)  # the block numbers aren't the section's numbers
    text = re.sub(r"(\d),(?=\d{3}(?!\d))", r"\1", text)  # 1,024 -> 1024
    written = [(float(t), t) for t in _SECTION_NUM.findall(text)]
    kept = [c for c in spec["checks"] if isinstance(c, dict) and _num(c.get("expect"))
            and any(_close(float(c["expect"]), w, t) or _close(float(c["expect"]) * 100, w, t) for w, t in written)]
    if len(kept) < len(spec["checks"]):
        log.info("Playground: %d of %d worked-example checks dropped (expected number not in the section)",
                 len(spec["checks"]) - len(kept), len(spec["checks"]))
    return {**spec, "checks": kept}


def _pg_fix_layout(client: anthropic.Anthropic, usage: UsageLog, payload: dict, spec: dict, report: dict, res: dict,
                   settings: ExplorerSettings, why: list[str]) -> tuple[dict, dict]:
    """A working playground with soft problems: plan.covers items no component delivers ("coverage") and on-screen layout
    problems. Code fixes what layout it can first (_pg_layout_autofix), then everything left gets LAYOUT_REPAIRS Haiku
    tries in ONE call (Nov 2: coverage joined the layout repair, so a playground never gets two soft repairs). Each step
    is kept only if it still passes every check and has fewer soft problems; otherwise the version before stays, so a
    soft problem never costs a working playground."""
    seen = report.get("layout") or []
    if report.get("coverage"):
        res.setdefault("coverage_seen", []).extend(report["coverage"])
    if report.get("checks"):
        res.setdefault("checks_seen", []).extend(report["checks"])
    if seen:
        res.setdefault("layout_seen", []).extend(seen)  # first-draft messages, for test-run stats
        fixed = _pg_layout_autofix(spec, seen)
        again = check_playground(fixed) if fixed is not None else None
        if again is not None and not again["problems"] and len(again.get("layout") or []) < len(seen):
            res["layout_code_fixed"] = res.get("layout_code_fixed", 0) + 1
            spec, report = fixed, again
        elif again is not None:
            log.info("Playground: layout code fix not kept (%d layout problems before, %s after)", len(seen),
                     "a failed check" if again["problems"] else len(again.get("layout") or []))
    for _ in range(LAYOUT_REPAIRS):
        soft = _soft(report)
        if not soft:
            break
        cov = [m for m in report.get("coverage") or [] if not m.startswith(COVERS_MISSING)]
        res["layout_repairs"] = res.get("layout_repairs", 0) + 1
        lead = SOFT_LEAD if cov or report.get("checks") else LAYOUT_LEAD
        fixed = _pg_ask(client, usage, "playground layout", payload, settings, why,
                        {"yourPreviousSpec": spec, "problems": [lead, *soft]}, effort=settings.fix)
        if fixed is None or _no_playground(fixed) or not isinstance(fixed, dict):
            break
        again = check_playground(fixed)
        if again["problems"] or len(_soft(again)) >= len(soft):
            log.info("Playground: soft repair not kept (%d soft problems before, %s after)", len(soft),
                     "a failed check" if again["problems"] else len(_soft(again)))
            break
        if len(again.get("layout") or []) < len(report.get("layout") or []):
            res["layout_fixed"] = res.get("layout_fixed", 0) + 1
        if len([m for m in again.get("coverage") or [] if not m.startswith(COVERS_MISSING)]) < len(cov):
            res["coverage_fixed"] = res.get("coverage_fixed", 0) + 1
        if len(again.get("checks") or []) < len(report.get("checks") or []):
            res["checks_fixed"] = res.get("checks_fixed", 0) + 1
        spec, report = fixed, again
    if report.get("coverage"):
        res.setdefault("coverage_left", []).extend(report["coverage"])
    if report.get("checks"):  # kept anyway (a soft problem never costs a working playground), but logged
        res.setdefault("checks_failed", []).extend(report["checks"])
        log.warning("Playground: worked-example checks still fail after the repair: %s", report["checks"])
    return spec, report


def _control_labels(facts: dict | None) -> list[str]:
    return sorted(str(c.get("label", "")) for c in (facts or {}).get("controls") or [] if isinstance(c, dict))


def _pg_look(client: anthropic.Anthropic, usage: UsageLog, payload: dict, spec: dict, plan: dict, res: dict,
             settings: ExplorerSettings, why: list[str], label: str) -> tuple[dict, dict]:
    """Playground's screenshot check (settings.look, PG_LOOK): the playground about to be kept is screenshotted like Code
    Lab 7.0's look_check and Haiku lists what looks wrong, told what the layout check already measured. Problems get ONE
    repair, kept only if it passes every check, has no more layout problems, keeps the same controls (so Jev's verdict
    still holds) and its screenshots show fewer problems. Fails open: no frames or no judge answer = kept as it is."""
    if not settings.look:
        return spec, plan
    frames = snap_widget(playground_html(spec))
    if not frames:
        res["look"] = {"checked": False}
        return spec, plan
    title = str(spec.get("title") or "")
    problems = look_review(client, usage, frames, plan, title, checked=PG_LOOK_CHECKED, step="playground look")
    look = {"checked": problems is not None, "frames": len(frames), "problems": problems or [], "repaired": False}
    res["look"] = look
    if not problems:
        return spec, plan
    res["repaired"] += 1
    fixed = _pg_ask(client, usage, "playground look repair", payload, settings, why,
                    {"yourPreviousSpec": spec, "problems": [PG_LOOK_LEAD, *[f"How it looks (from screenshots): {p}" for p in problems]]},
                    effort=settings.fix)
    if not isinstance(fixed, dict) or _no_playground(fixed):
        res["notes"].append(f"Playground ({label}): the screenshots showed problems and the repair failed, so it is kept as it was. ({problems[0]})")
        return spec, plan
    before, after_check = check_playground(spec), check_playground(fixed)
    same_controls = _control_labels(before.get("facts")) == _control_labels(after_check.get("facts"))
    if after_check["problems"] or len(after_check.get("layout") or []) > len(before.get("layout") or []) or not same_controls:
        why_not = "it failed a check" if after_check["problems"] else "it changed the controls" if not same_controls else "it had more layout problems"
        res["notes"].append(f"Playground ({label}): the screenshots showed problems but the repair wasn't kept ({why_not}). ({problems[0]})")
        return spec, plan
    frames2 = snap_widget(playground_html(fixed))
    after = look_review(client, usage, frames2, plan, title, checked=PG_LOOK_CHECKED, step="playground look") if frames2 else None
    look["after"] = after
    if after is None or len(after) >= len(problems):
        res["notes"].append(f"Playground ({label}): the screenshots showed problems and the repair didn't look better, so it is kept as it was. ({problems[0]})")
        return spec, plan
    look["repaired"] = True
    res["notes"].append(f"Playground ({label}): the screenshots showed {len(problems)} problem(s); the repaired playground shows {len(after)}, so it was used.")
    return fixed, playground_plan(fixed)


def _pg_accept(res: dict, spec: dict, plan: dict, scores: dict | None = None) -> dict:
    title = re.sub(r"\s+", " ", str(spec.get("title") or "")).strip()[:80] or "Playground"
    res.update(widget={"type": "widget", "title": title, "html": playground_html(spec), "plan": plan_text(plan, scores)},
               after=plan.get("afterBlock"), built=True)
    return res


def _design_playground(client: anthropic.Anthropic, usage: UsageLog, outline: dict, si: int, verdict: dict, jev_key: str | None,
                       settings: ExplorerSettings) -> dict:
    """One section's playground: Haiku writes the spec, the checker dry-runs it (repairs with its errors), then Jev's
    review (one fix on a clear no, re-checked and re-reviewed). Falls back to text, with a note, at any step."""
    label = outline["stages"][si].get("label", "")
    res: dict = {"notes": [], "built": False, "repaired": 0, "backup": False, "chart": False}
    if not verdict.get("open"):
        return res
    why: list[str] = []
    payload = {**_section(outline, si), "hint": verdict.get("hint") or gate_hint(verdict.get("why")) or ""}
    spec = _pg_ask(client, usage, "playground write", payload, settings, why)
    lower = _lower(settings.build)
    if spec is None and why and CUTOFF in why[-1] and lower:  # the whole budget went on thinking: once more, thinking less
        why.clear()
        spec = _pg_ask(client, usage, "playground write", payload, settings, why, effort=lower)
    if spec is None:
        res["notes"].append(f"Playground ({label}): the builder failed, so the section stays text. ({why[0] if why else 'no answer'})")
        return res
    if _no_playground(spec):
        res["notes"].append(f"Playground ({label}): the builder found nothing to play with here, so the section stays text.")
        return res
    spec, report = _pg_until_clean(client, usage, payload, spec, res, settings, why)
    if spec is None and report["problems"] == [CHECK_FAILED]:
        res["notes"].append(f"Playground ({label}): the playground was written, but the server couldn't run its checker (node), so it "
                            "wasn't shown and the section stays text. The server log says why.")
        return res
    if spec is None:
        res["notes"].append(f"Playground ({label}): the playground failed its checks and couldn't be repaired, so the section stays text. "
                            f"({(report['problems'] or ['no answer'])[0]})")
        return res
    plan = playground_plan(spec)
    rv = review(jev_key, outline, si, plan, report.get("facts"), usage, fix_first=True, playground=True)
    if rv is None or not rv["reject"]:
        spec, plan = _pg_look(client, usage, payload, spec, plan, res, settings, why, label)
        return _pg_accept(res, spec, plan, rv["p"] if rv else None)
    _rejected(res, label, "first", plan, playground_html(spec), rv["p"])
    scores = scores_text(rv["p"])
    res["repaired"] += 1
    fixed = _pg_ask(client, usage, "playground repair", payload, settings, why,
                    {"yourPreviousSpec": spec, "problems": rv["problems"]}, effort=settings.fix)
    if fixed is not None and not _no_playground(fixed):
        spec2, report2 = _pg_until_clean(client, usage, payload, fixed, res, settings, why)
        if spec2 is not None:
            plan2 = playground_plan(spec2)
            again = review(jev_key, outline, si, plan2, report2.get("facts"), usage, full=False, playground=True)
            if again is None or not again["reject"]:
                spec2, plan2 = _pg_look(client, usage, payload, spec2, plan2, res, settings, why, label)
                return _pg_accept(res, spec2, plan2, again["p"] if again else rv["p"])
            _rejected(res, label, "fix", plan2, playground_html(spec2), again["p"])
    res["notes"].append(f"Playground ({label}): the reviewer rejected the playground and its fix didn't pass, so the section stays text. "
                        f"({rv['problems'][0]} Scores: {scores})")
    return res
