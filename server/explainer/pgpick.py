"""Playground 2.0 (playground-2): Jev picks a playground's parts, code turns the picks into a blueprint, Haiku fills it in.

Jev answers only yes/no, choice and score questions, so the picks are many questions in ONE request (the docs'
"speculative fan-out": questions run in parallel and in isolation, so more of them barely change the time):
- one yes/no per tool in TOOLS (controls, readouts, plotted marks, diagram structures),
- the main view (plotted chart / diagram / both) and how many settings the reader changes,
- one choice per item of the planner's "needs" list (which tool delivers it),
- a companion yes/no "nothing here fits" (the docs' companion-noul pattern).
blueprint() applies thresholds, caps and the rules between tools (pure code); merge() keeps the blueprint's covers in
the spec Haiku wrote and lists where the spec left the blueprint. Everything fails open: no key, a Jev error or an
unusable answer means no blueprint, and the section is built like Playground (PLAYGROUND_SYSTEM).

Questions and tool descriptions stay topic-neutral (no worked example of one AI area: the model would copy it).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .jev import JevError, jev_ask
from .labplan import gate_state
from .pricing import UsageLog

log = logging.getLogger("explainer")

PICK_IN = 0.7  # at or above: the tool is in the blueprint
PICK_MAYBE = 0.4  # at or above (and below PICK_IN): offered to Haiku as optional
NONE_FITS = 0.7  # "nothing here fits" at or above: no blueprint
MAX_NEEDS = 6
NEED_CHARS = 100
MAX_SLIDERS = 5
MAX_COMPONENTS = 16
SLIDERS_FOR_SCORE = (1, 3, 5)  # the "controls" score levels: one setting, two or three, four or five

# One entry per tool: id (as the blueprint names it), group, what Jev reads, and the helpers that draw it (for merge's
# check that a chart draws its listed marks). Controls also name their component type.
TOOLS: list[dict] = [
    {"id": "slider", "group": "control", "type": "slider", "what": "a slider that sets one number the section names (a rate, a size, a weight, a threshold, a count)"},
    {"id": "select", "group": "control", "type": "select", "what": "a dropdown that switches between named options, methods or inputs the section compares"},
    {"id": "toggle", "group": "control", "type": "toggle", "what": "an on/off switch between two modes or settings the section names"},
    {"id": "button", "group": "control", "type": "button", "what": "a button that takes one step, draws a new sample or adds one item"},
    {"id": "play", "group": "control", "type": "play", "what": "a play button that runs a process step by step over time"},
    {"id": "drag", "group": "control", "what": "points or shapes on a chart that the reader drags to move an input directly"},
    {"id": "click", "group": "control", "what": "clicking on a chart or on its parts (cells, nodes, tokens) to add, pick or highlight one"},
    {"id": "readout", "group": "number", "type": "readout", "what": "a big live number for a result or an intermediate value the section computes"},
    {"id": "line", "group": "plot", "helpers": ["line"], "what": "a line or curve of one quantity against another or over steps"},
    {"id": "area", "group": "plot", "helpers": ["area"], "what": "a filled area under a curve or between two curves"},
    {"id": "points", "group": "plot", "helpers": ["points", "point"], "what": "a scatter of data points or samples"},
    {"id": "bars", "group": "plot", "helpers": ["bars"], "what": "bars comparing values across categories, options or positions"},
    {"id": "heatmap", "group": "plot", "helpers": ["heatmap"], "what": "a grid of coloured cells for a 2D map of values (scores, similarities, regions)"},
    {"id": "pie", "group": "plot", "helpers": ["pie"], "what": "a pie of shares of a whole"},
    {"id": "kde", "group": "plot", "helpers": ["kde"], "what": "a smooth density curve of samples"},
    {"id": "countplot", "group": "plot", "helpers": ["countplot"], "what": "bars counting how often each category occurs"},
    {"id": "rules", "group": "plot", "helpers": ["hline", "vline"], "what": "horizontal or vertical marker lines for a threshold, an average or a target"},
    {"id": "arrow", "group": "plot", "helpers": ["arrow"], "what": "arrows for directions, vectors or updates"},
    {"id": "rect", "group": "plot", "helpers": ["rect"], "what": "rectangles marking regions or intervals"},
    {"id": "text", "group": "plot", "helpers": ["text"], "what": "short value labels drawn on a chart"},
    {"id": "shapes", "group": "structure", "helpers": ["node", "box", "circle", "diamond", "link"], "what": "a hand-laid diagram of named parts joined by arrows (the stages of a pipeline, the components of a system)"},
    {"id": "graph", "group": "structure", "helpers": ["graph"], "what": "a graph of nodes joined by weighted edges"},
    {"id": "mlpGraph", "group": "structure", "helpers": ["mlpGraph"], "what": "a neural network drawn as layers of units with weighted connections"},
    {"id": "tree", "group": "structure", "helpers": ["tree", "treeNodes"], "what": "a tree of decisions or branches"},
    {"id": "sequence", "group": "structure", "helpers": ["sequence"], "what": "a row of tokens or items, with links showing which one connects to or attends to which"},
    {"id": "matrix", "group": "structure", "helpers": ["matrix"], "what": "a matrix or vector of numbers in brackets (weights, vectors, a product)"},
    {"id": "grid", "group": "structure", "helpers": ["grid"], "what": "a grid world of cells with an agent moving in it"},
]
BY_ID = {t["id"]: t for t in TOOLS}
ID_PREFIX = {"slider": "s", "select": "sel", "toggle": "t", "button": "b", "play": "p", "readout": "r"}
MARK_GROUPS = ("plot", "structure")
COVER_KIND = {"control": "control", "number": "number", "plot": "mark", "structure": "mark"}

MAIN_VIEW = {
    "type": "choice",
    "instructions": "Which kind of view shows what `section` explains best?",
    "criteria": {
        "plotted": "plotted charts of numbers: curves, bars, points or coloured maps",
        "diagram": "a diagram of connected parts: shapes joined by links, a network, a tree, a row of tokens, a matrix",
        "both": "a plotted chart and a diagram side by side",
    },
}
CONTROLS = {
    "type": "score",
    "instructions": "How many separate settings does `section` give the reader to change?",
    "criteria": ["one setting", "two or three settings", "four or five settings"],
}
NONE = {
    "type": "noul",
    "instructions": "Is `section` something none of the parts in this list could show (only definitions, history or advice)?",
    "criteria": {"true": "`section` has nothing to change, read or compare.", "false": "`section` has something a reader could explore."},
}


def tool_question(t: dict) -> dict:
    return {"type": "noul", "instructions": f"Would a reader exploring `section` need {t['what']}?",
            "criteria": {"true": f"`section` describes something best explored with {t['what']}.",
                         "false": f"`section` does not need {t['what']}."}}


def need_question(need: str) -> dict:
    return {"type": "choice",
            "instructions": "Which one part best lets the reader see or do this item from `section`: " + repr(need) + "?",
            "criteria": {t["id"]: t["what"] for t in TOOLS}}


def clean_needs(raw: Any) -> list[str]:
    """The planner's needs list: up to MAX_NEEDS short strings, duplicates dropped."""
    out: list[str] = []
    for x in raw if isinstance(raw, list) else []:
        s = re.sub(r"\s+", " ", x).strip()[:NEED_CHARS] if isinstance(x, str) else ""
        if s and s.lower() not in (o.lower() for o in out):
            out.append(s)
    return out[:MAX_NEEDS]


def questions(needs: list[str]) -> dict[str, dict]:
    qs: dict[str, dict] = {f"tool_{t['id']}": tool_question(t) for t in TOOLS}
    qs.update(main_view=MAIN_VIEW, controls=CONTROLS, none=NONE)
    for i, need in enumerate(needs):
        qs[f"need_{i}"] = need_question(need)
    return qs


def pick(jev_key: str | None, outline: dict, si: int, needs: list[str], usage: UsageLog) -> dict | None:
    """Jev's answers (parse_jev_answers form) for one section, or None (no key, an error, no answers)."""
    if not jev_key:
        return None
    state = {**gate_state(outline, si, full=True), "needs": needs}
    try:
        r = jev_ask(jev_key, state, questions(needs))
    except JevError as e:
        log.warning("Playground 2.0: Jev pick failed: %s", e)
        return None
    except Exception:
        log.exception("Playground 2.0: Jev pick failed")
        return None
    usage.add("jev playground pick", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    return r["answers"] or None


def _p(answers: dict, qid: str) -> float:
    """A yes/no answer's probability (parse_jev_answers' {"type": "noul", "p"}); 0 when missing."""
    a = answers.get(qid) or {}
    v = a.get("p")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _top(answers: dict, qid: str) -> str | None:
    """A choice answer's most likely option (its ranking is sorted, most likely first); None when missing."""
    a = answers.get(qid) or {}
    ranking = a.get("ranking")
    return ranking[0].get("option") if isinstance(ranking, list) and ranking and isinstance(ranking[0], dict) else None


def blueprint(answers: dict | None, needs: list[str]) -> dict | None:
    """The playground's parts from Jev's answers (pure code), or None when Jev says nothing fits or gave no answers:
    {"mainView", "components": [{"id", "type", "marks"?, "drag"?, "click"?}], "optional": [...], "covers": [...]}."""
    if not answers or _p(answers, "none") >= NONE_FITS:
        return None
    p = {t["id"]: _p(answers, f"tool_{t['id']}") for t in TOOLS}
    if not any(p.values()):
        return None
    main = _top(answers, "main_view") or "plotted"
    score = (answers.get("controls") or {}).get("score")
    slider_cap = SLIDERS_FOR_SCORE[int(score)] if isinstance(score, (int, float)) and 0 <= score < len(SLIDERS_FOR_SCORE) else 3
    need_tool = {i: _top(answers, f"need_{i}") for i in range(len(needs))}
    need_tool = {i: t for i, t in need_tool.items() if t in BY_ID}

    chosen = {t for t, v in p.items() if v >= PICK_IN} | set(need_tool.values())
    by_group = lambda g: [t["id"] for t in TOOLS if t["group"] == g and t["id"] in chosen]  # noqa: E731
    best = lambda g: max((t["id"] for t in TOOLS if t["group"] == g), key=lambda i: p[i])  # noqa: E731
    plot, struct = by_group("plot"), by_group("structure")
    if main in ("plotted", "both") and not plot:
        plot = [best("plot")]
    if main in ("diagram", "both") and not struct:
        struct = [best("structure")]
    chosen |= set(plot) | set(struct)

    comps: list[dict] = []
    if plot:
        comps.append({"id": "c1", "type": "chart", "marks": plot})
    if struct:
        comps.append({"id": "d1", "type": "chart", "marks": struct})
    chart_of = {m: c["id"] for c in comps for m in c["marks"]}
    main_chart = comps[0] if main != "diagram" or len(comps) == 1 else comps[1]

    counts: dict[str, int] = {}

    def new(kind: str) -> dict:
        counts[kind] = counts.get(kind, 0) + 1
        c = {"id": f"{ID_PREFIX[kind]}{counts[kind]}", "type": kind}
        comps.append(c)
        return c

    covers: list[dict] = []
    for i, need in enumerate(needs):
        tool = need_tool.get(i)
        if tool is None:
            continue
        group = BY_ID[tool]["group"]
        if tool in ("drag", "click"):
            main_chart[tool] = True
            by = main_chart["id"]
        elif group in MARK_GROUPS:
            by = chart_of[tool]
        elif tool == "slider" and counts.get("slider", 0) >= min(slider_cap, MAX_SLIDERS):
            by = "s1"  # past the cap: the need shares the first slider (Haiku may record a deviation)
        else:
            by = new(BY_ID[tool]["type"])["id"]
        covers.append({"need": need, "kind": COVER_KIND[group], "by": [by]})
    # Tools Jev wants that no need brought in: one of each (a slider only when no need made one)
    for t in ("slider", "select", "toggle", "button", "play", "readout"):
        if t in chosen and not counts.get(t):
            new(t)
    for t in ("drag", "click"):
        if t in chosen:
            main_chart[t] = True
    has_control = any(c["type"] in ("slider", "select", "toggle", "button", "play") or c.get("drag") or c.get("click") for c in comps)
    if not has_control:
        top = max((t["id"] for t in TOOLS if t["group"] == "control"), key=lambda i: p[i])
        if top in ("drag", "click"):
            main_chart[top] = True
        else:
            new(BY_ID[top]["type"])
    comps = _ordered(comps)[:MAX_COMPONENTS]
    kept = {c["id"] for c in comps}
    covers = [c for c in covers if all(b in kept for b in c["by"])]
    optional = [{"tool": t["id"], **({"type": t["type"]} if t.get("type") else {}), "p": round(p[t["id"]], 2)}
                for t in TOOLS if t["id"] not in chosen and PICK_MAYBE <= p[t["id"]] < PICK_IN
                and not (t["id"] in ("drag", "click") and main_chart.get(t["id"]))]
    return {"mainView": main, "components": comps, "optional": optional, "covers": covers}


def _ordered(comps: list[dict]) -> list[dict]:
    """Controls first, then charts, then readouts (the order the reader meets them)."""
    rank = {"slider": 0, "select": 0, "toggle": 0, "button": 0, "play": 0, "chart": 1, "readout": 2}
    return sorted(comps, key=lambda c: rank.get(c["type"], 1))


def picked(bp: dict) -> list[str]:
    """Short names of a blueprint's parts, for notes and test-run stats."""
    out = []
    for c in bp.get("components") or []:
        if c["type"] == "chart":
            out.append(f"{c['id']}: " + "+".join(c.get("marks") or []) + (" (drag)" if c.get("drag") else "") + (" (click)" if c.get("click") else ""))
        else:
            out.append(f"{c['id']}: {c['type']}")
    return out


def merge(spec: Any, bp: dict | None) -> Any:
    """spec with every blueprint covers item it left out added back to plan.covers (so the coverage check reports a
    need no component delivers). A need is matched by its text, ignoring case."""
    if not bp or not isinstance(spec, dict) or spec.get("error"):
        return spec
    plan = spec.get("plan") if isinstance(spec.get("plan"), dict) else {}
    covers = [c for c in plan.get("covers") or [] if isinstance(c, dict)] if isinstance(plan.get("covers"), list) else []
    have = {str(c.get("need") or "").strip().lower() for c in covers}
    added = [dict(c) for c in bp.get("covers") or [] if c["need"].strip().lower() not in have]
    if not added:
        return spec
    return {**spec, "plan": {**plan, "covers": [*covers, *added]}}


def deviations(spec: Any, bp: dict | None) -> list[str]:
    """Where the spec left the blueprint: a missing component or a changed type, a chart that doesn't call one of its
    marks or lacks its onDrag/onClick, plus Haiku's own plan.deviations."""
    if not bp or not isinstance(spec, dict):
        return []
    comps = {c.get("id"): c for c in spec.get("components") or [] if isinstance(c, dict)}
    out: list[str] = []
    for want in bp.get("components") or []:
        got = comps.get(want["id"])
        if got is None:
            out.append(f"{want['id']} ({want['type']}) is missing")
            continue
        if got.get("type") != want["type"]:
            out.append(f"{want['id']} is a {got.get('type')}, not a {want['type']}")
            continue
        draw = str(got.get("draw") or "")
        for m in want.get("marks") or []:
            helpers = BY_ID[m].get("helpers") or [m]
            if not re.search(r"\b(?:" + "|".join(map(re.escape, helpers)) + r")\s*\(", draw):
                out.append(f"{want['id']} doesn't draw {m}")
        if want.get("drag") and not got.get("onDrag"):
            out.append(f"{want['id']} has no onDrag")
        if want.get("click") and not got.get("onClick"):
            out.append(f"{want['id']} has no onClick")
    plan = spec.get("plan") if isinstance(spec.get("plan"), dict) else {}
    for d in plan.get("deviations") or [] if isinstance(plan.get("deviations"), list) else []:
        if isinstance(d, dict):
            out.append(f"Haiku: {d.get('id', '?')}: {str(d.get('change') or '')[:80]} ({str(d.get('why') or '')[:80]})")
    return out[:12]
