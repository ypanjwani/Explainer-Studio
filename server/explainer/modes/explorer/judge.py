from __future__ import annotations

import json
import logging
import math
from typing import Any

from ...jev import JevError, jev_ask
from ...js import js_slice
from ...pricing import UsageLog

log = logging.getLogger(__name__)


SECTION_CHARS = 2500


PATTERN_OF = {"varies": "sweep", "process": "play or step", "compare": "compare", "structure": "diagram"}


HINT_OF = {
    "varies": "a quantity that changes when a setting changes",
    "process": "a process that unfolds over steps, rounds or time",
    "compare": "a comparison of two settings or two methods",
    "structure": "a structure of connected parts (layers, steps, tokens linking to each other) the reader can explore",
}


FULL_SECTION_CHARS = 8000  # the whole section as text (full=True): tables and cards can be long


# Reading blocks whose words full=True passes to Jev as text, not just as "type: title".
READ_BLOCKS = ("text", "callout", "table", "cards", "tabs", "compare", "reveal", "flipcards")


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def block_text(b: dict) -> str:
    """One reading block's words as plain text, for Jev (tables row by row, cards and tabs item by item).
    Empty for blocks that aren't READ_BLOCKS."""
    t = b.get("type")
    if t == "text":
        return _s(b.get("md"))
    if t == "callout":
        return f"{_s(b.get('title'))}: {_s(b.get('md'))}".strip(": ")
    if t == "table":
        cols = [_s(c) for c in b.get("columns") or []]
        rows = [" | ".join(_s(c) for c in r) for r in b.get("rows") or [] if isinstance(r, list)]
        head = f"Table: {_s(b.get('title'))}".rstrip(": ")
        return "\n".join(x for x in [head, " | ".join(cols), *rows, _s(b.get("caption"))] if x)
    if t == "cards":
        items = []
        for it in b.get("items") or []:
            if not isinstance(it, dict):
                continue
            extra = "; ".join(f"{k}: {_s(it.get(k))}" for k in ("value", "breaksIf", "evidence") if _s(it.get(k)))
            items.append(f"- {_s(it.get('title'))}: {_s(it.get('md'))}" + (f" ({extra})" if extra else ""))
        return "\n".join(x for x in [_s(b.get("title")), *items] if x)
    if t == "tabs":
        return "\n".join(f"{_s(x.get('label'))}: {_s(x.get('md'))}" for x in b.get("tabs") or [] if isinstance(x, dict))
    if t == "compare":
        sides = [b.get("left") or {}, b.get("right") or {}]
        return "\n".join(f"{_s(s.get('title'))}: " + "; ".join(_s(p) for p in s.get("points") or []) for s in sides if isinstance(s, dict))
    if t == "reveal":
        return f"Question: {_s(b.get('prompt'))}\nAnswer: {_s(b.get('answer'))}"
    if t == "flipcards":
        return "\n".join(f"{_s(c.get('front'))}: {_s(c.get('back'))}" for c in b.get("cards") or [] if isinstance(c, dict))
    return ""


def section_text(st: dict, full: bool = True) -> str:
    """A section's words for Jev: its text and callouts, and with full=True also its tables, cards, tabs, comparisons,
    reveals and flip cards (so a term the section explains only in a table still counts as something it talks about)."""
    kinds = READ_BLOCKS if full else ("text", "callout")
    return "\n\n".join(x for x in (block_text(b) for b in st["blocks"] if b.get("type") in kinds) if x)


def gate_state(outline: dict, si: int, full: bool = False) -> dict:
    """What Jev's gate reads about one section. full=True (Code Lab) passes tables, cards and the other reading blocks
    as text too; Lab mode keeps text and callouts only, with the other blocks named by type and title."""
    st = outline["stages"][si]
    prose = section_text(st, full)
    read = READ_BLOCKS if full else ("text", "callout")
    parts = []
    for b in st["blocks"]:
        if b["type"] in read:
            continue
        if b["type"] == "formula":
            parts.append(f"formula: {b.get('latex', '')}")
        else:
            parts.append(b["type"] + (f": {b['title']}" if b.get("title") else ""))
    return {
        "explainer": {"title": outline.get("title", ""), "level": outline.get("level", "")},
        "section": {"label": st.get("label", ""), "summary": st.get("blurb", ""), "text": js_slice(prose, 0, FULL_SECTION_CHARS if full else SECTION_CHARS)},
        "section_parts": parts,
    }


GATE_QUESTIONS: dict[str, dict] = {
    "varies": {
        "type": "noul",
        "instructions": "Does `section` describe a quantity whose value changes when a setting, input or parameter changes?",
        "criteria": {
            "true": "`section` explains how some number (an error, a probability, a score, a size) depends on a setting the reader could vary, for example through a formula or a described relationship.",
            "false": "`section` names no quantity that depends on a setting; it defines terms, gives facts or gives advice.",
        },
    },
    "process": {
        "type": "noul",
        "instructions": "Does `section` describe a process that unfolds over steps, rounds, iterations or time?",
        "criteria": {
            "true": "`section` describes something done repeatedly or in stages (training rounds, iterations, a sequence of updates, an algorithm's steps) whose state changes from step to step.",
            "false": "`section` describes nothing that proceeds step by step.",
        },
    },
    "compare": {
        "type": "noul",
        "instructions": "Does `section` compare two or more settings, methods or cases whose results differ in a way that could be shown side by side?",
        "criteria": {
            "true": "`section` contrasts methods or settings (for example two algorithms, a small and a large value) and says how their results differ.",
            "false": "`section` does not compare methods or settings, or only lists their features without results that differ.",
        },
    },
    "static": {
        "type": "noul",
        "instructions": "Is `section` mainly definitions, history, a summary or practical advice?",
        "criteria": {
            "true": "Most of `section` is definitions, background, a recap or when-to-use advice, with no mechanism to explore.",
            "false": "`section` explains a mechanism, a formula or a process that the reader could explore.",
        },
    },
}


# Playground only (codelab.gate_one(playground=True)): sections about connected parts were closed by the three questions
# above, so Playground never drew a network, attention links or a pipeline (Oct 30).
PG_GATE_QUESTIONS: dict[str, dict] = {
    **GATE_QUESTIONS,
    "structure": {
        "type": "noul",
        "instructions": "Does `section` describe a structure of connected parts, such as layers of a network, tokens that attend to each other, the stages of a pipeline or a tree of decisions?",
        "criteria": {
            "true": "`section` explains how parts are connected or how something flows between them (an architecture, layers and weights, links between tokens, a pipeline, a tree), which the reader could explore in a diagram.",
            "false": "`section` describes no connected parts; it is about a single quantity, facts or advice.",
        },
    },
}


def gate_hint(why: str | None) -> str:
    return f"{HINT_OF[why]} (a {PATTERN_OF[why]} lab may fit)" if why in HINT_OF else "a quantity that varies or a process that unfolds"


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def plan_text(plan: dict, review: dict | None = None, math_scores: dict | None = None) -> str:
    """The plan as compact JSON (plus Jev's review scores), kept on the widget block so a weak widget can be traced."""
    keep = ("lesson", "readerDoes", "controls", "visuals", "covers", "expects", "contrast", "backup", "checks", "claims", "sanity")
    out = {k: plan[k] for k in keep if k in plan}
    if review:
        out["review"] = review
    if math_scores:
        out["math"] = math_scores
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def gate_one(jev_key: str | None, outline: dict, si: int, usage: UsageLog, playground: bool = True) -> tuple[dict, str | None]:
    """({"open", "why"}, note or None) for one section. Without a key, or if Jev fails, the section is open (Haiku's
    plan can still decline). Also asks about a structure of connected parts (PG_GATE_QUESTIONS). `playground` is
    accepted for the old call shape and ignored."""
    if not jev_key:
        return {"open": True, "why": None}, None
    try:
        r = jev_ask(jev_key, gate_state(outline, si, full=True), PG_GATE_QUESTIONS)
    except JevError:
        r = None
    if r is None or not r["answers"]:
        return {"open": True, "why": None}, f'Code Lab ({outline["stages"][si].get("label", "")}): Jev didn\'t answer, so the section went to the plan step.'
    usage.add("jev code lab gate", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    verdict = codelab_gate_verdict(r["answers"])
    if verdict["why"]:  # how clearly the section has something to explore, to rank open sections (Code Lab 7.0/Playground)
        verdict["score"] = r["answers"].get(verdict["why"], {}).get("p", 0.5)
    return verdict, None


GATE_CLOSED_STATIC = 0.7  # mostly definitions, history, a recap or advice: a clear yes closes the section


GATE_CLOSED_NONE = 0.3  # no quantity that varies, no process and no comparison: all three a clear no closes it


GATE_OPEN_ANYWAY = 0.7  # ... but a clear yes on any of the three keeps it open even if it also reads as advice


def codelab_gate_verdict(answers: dict) -> dict:
    """{"open", "why"}: Code Lab closes a section only on a clear no (the planner can still decline an unsure one),
    unlike Lab mode's gate_verdict, which opens only on a clear yes."""
    p = {k: answers[k]["p"] for k in PG_GATE_QUESTIONS if k in answers}
    # "structure" is only asked in Playground (PG_GATE_QUESTIONS); elsewhere it is absent and never wins.
    best = max(("varies", "process", "compare", "structure"), key=lambda k: p.get(k, -1))
    # "Mostly definitions or advice" closes only when nothing to explore is a clear yes: a section can be judged both
    # advice-like and clearly comparing two settings, and that conflict is for the planner to settle.
    closed = (p.get("static", 0) >= GATE_CLOSED_STATIC and p.get(best, 0) < GATE_OPEN_ANYWAY) or p.get(best, 1) <= GATE_CLOSED_NONE
    return {"open": not closed, "why": None if closed else best}


REVIEW_HIGH = 0.7  # at or above: a clear yes


REVIEW_LOW = 0.3  # at or below: a clear no


REVIEW_SECTION_CHARS = 12_000


REVIEW_QUESTIONS: dict[str, dict] = {
    "controls_on_topic": {
        "type": "noul",
        "instructions": "The controls in `widget.controls` set quantities that `section` talks about.",
        "criteria": {
            "true": "Each control in `widget.controls` (by its label) is a setting, input or parameter that `section` names or describes.",
            "false": "A control in `widget.controls` sets something `section` never mentions.",
        },
    },
    "change_shows_lesson": {
        "type": "noul",
        "instructions": "What `widget.changes` says changes when a control moves is the thing `plan.lesson` says the reader should see.",
        "criteria": {
            "true": "The parts that change (their ids, the before → after samples, a redrawn chart) are the quantity or picture `plan.lesson` is about.",
            "false": "The parts that change are unrelated to `plan.lesson`, or nothing that `plan.lesson` is about changes.",
        },
    },
    "uses_section_terms": {
        "type": "noul",
        "instructions": "The labels in `widget.labels` and `widget.controls` use the names and symbols that `section` uses.",
        "criteria": {
            "true": "The labels name things the way `section` does (the same terms and symbols).",
            "false": "The labels use names or symbols that `section` does not use for these things.",
        },
    },
    "same_idea": {
        "type": "noul",
        "instructions": "`widget` shows the same idea as `plan.lesson`, and only labels, readouts or small details are missing.",
        "criteria": {
            "true": "The controls and the changing parts of `widget` are about `plan.lesson`; what is wrong is a missing label, readout, line or highlight.",
            "false": "`widget` is about a different idea than `plan.lesson`.",
        },
    },
    "backup_direct": {
        "type": "noul",
        "instructions": "`plan.backup` names one control and one visual that directly show `plan.lesson`.",
        "criteria": {
            "true": "`plan.backup` says which control the reader moves and what is drawn, and that drawing is what `plan.lesson` is about.",
            "false": "`plan.backup` is empty, vague, or describes something other than `plan.lesson`.",
        },
    },
}


QUALITY = ("controls_on_topic", "change_shows_lesson", "uses_section_terms")


REVIEW_PROBLEMS = {
    "controls_on_topic": "A reviewer judged that a control sets something the section never talks about. Make every control a "
                         "setting the section describes, labeled the way the section labels it.",
    "change_shows_lesson": "A reviewer judged that what changes when the controls move is not what the lesson (plan.lesson) is about. "
                           "Make the controls change the visual that shows the lesson directly.",
    "uses_section_terms": "A reviewer judged that the labels don't use the section's names and symbols. Label controls, axes and "
                          "readouts with exactly the section's terms and symbols.",
}


# Playground mode's review. Today's questions judged every control, so a Play, Step or Resample button "set something the
# section never mentions", and a label like "Routing skew" for the section's imbalance was "not the section's term":
# offline on 11 rejected playgrounds (Oct 24), all of them on topic, controls_on_topic was 0.12-0.73 and
# uses_section_terms 0.14-0.78. With these questions (buttons that only run the playground marked role "runs it"): 0.67-0.96
# and 0.37-0.83, while the same playgrounds checked against another topic's section still scored at most 0.04. So
# PG_REVIEW_LOW sits between the two.
PG_REVIEW_LOW = 0.2


PG_REVIEW_QUESTIONS: dict[str, dict] = {
    **REVIEW_QUESTIONS,
    "controls_on_topic": {
        "type": "noul",
        "instructions": 'The setting controls in `widget.controls` (role "setting"; ignore role "runs it") change quantities that `section` is about.',
        "criteria": {
            "true": "Each setting control changes a quantity, input or parameter that `section` names, describes, or uses in its formulas or "
                    "worked example (a made-up value of something `section` discusses counts).",
            "false": "A setting control changes something unrelated to what `section` discusses.",
        },
    },
    "uses_section_terms": {
        "type": "noul",
        "instructions": "The labels in `widget.labels` and `widget.controls` refer to things `section` discusses, using its names, symbols or short forms of them.",
        "criteria": {
            "true": "Each label names something `section` discusses, with the section's term, its symbol, or a short or plain form of it.",
            "false": "A label names something `section` never discusses, or uses a name that contradicts the section's.",
        },
    },
}


PG_RUNS = ("button", "play")  # control kinds that run the playground (Play, Step, Resample) rather than set a quantity


def pg_review_problems(failed: list[str], controls: list[dict]) -> list[str]:
    """The reviewer's problems for a playground repair. An off-topic control can't be fixed by renaming it, so the
    message names the setting controls and asks for a different one where needed."""
    settings = [c.get("label", "") for c in controls if c.get("kind") not in PG_RUNS]
    out = []
    for k in failed:
        if k == "controls_on_topic":
            out.append(f"A reviewer judged that a setting control ({', '.join(settings) or 'none'}) changes something the section doesn't "
                       "discuss. Replace that control with a quantity the section describes (named with its term or symbol); "
                       "renaming it is not enough.")
        else:
            out.append(REVIEW_PROBLEMS[k])
    return out


def review_state(outline: dict, si: int, plan: dict, facts: dict, playground: bool = True) -> dict:
    """What the reviewer reads: the whole section (its text, callouts, tables, cards, tabs, comparisons, reveals and flip
    cards, and its formulas), the plan's intent and what the test run saw. Before Oct 2026 only text and callouts were
    sent, so a control the section explained only in a table or card was judged "never talked about"."""
    st = outline["stages"][si]
    prose = section_text(st)
    formulas = [b.get("latex", "") for b in st["blocks"] if b["type"] == "formula" and b.get("latex")]
    controls = [dict(c) for c in facts.get("controls") or [] if isinstance(c, dict)]
    if playground:  # say which controls only run the playground (see PG_REVIEW_QUESTIONS); the follow-up agree check doesn't
        for c in controls:
            c["role"] = "runs it" if c.get("kind") in PG_RUNS else "setting"
    return {
        "section": {"label": st.get("label", ""), "summary": st.get("blurb", ""), "text": prose[:REVIEW_SECTION_CHARS], "formulas": formulas},
        "plan": {k: plan.get(k, "") for k in ("lesson", "readerDoes", "backup")},
        "widget": {"controls": controls, "labels": facts.get("labels") or [], "changes": facts.get("changes") or []},
    }


def review(jev_key: str | None, outline: dict, si: int, plan: dict, facts: dict | None, usage: UsageLog, full: bool = True,
           fix_first: bool = False, playground: bool = True) -> dict | None:
    """Jev's verdict on a widget that passed the code checks: {"reject", "fix", "backup", "unsure", "problems", "p"}, or
    None when there is no key, no facts (the widget couldn't run) or Jev didn't answer: the widget is then kept.
    full=False asks only the quality questions (a re-review). fix_first: see review_verdict (Code Lab 4.0).
    Explorer's questions and threshold (PG_REVIEW_QUESTIONS, PG_REVIEW_LOW); `playground` is accepted and ignored."""
    if not jev_key or not facts:
        return None
    base = PG_REVIEW_QUESTIONS
    questions = base if full else {k: base[k] for k in QUALITY}
    try:
        r = jev_ask(jev_key, review_state(outline, si, plan, facts), questions)
    except JevError:
        return None
    a = r["answers"]
    if not all(k in a for k in QUALITY):
        return None
    usage.add("jev code lab review", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    v = review_verdict(a, bool(str(plan.get("backup") or "").strip()), fix_first, PG_REVIEW_LOW)
    if v["reject"]:
        failed = [k for k in QUALITY if v["p"].get(k, 1) <= PG_REVIEW_LOW]
        v["problems"] = pg_review_problems(failed, facts.get("controls") or [])
    log.info("Code Lab review (%s): %s", outline["stages"][si].get("label", ""), scores_text(v["p"]))
    return v


def review_verdict(answers: dict, has_backup: bool, fix_first: bool = False, low: float = REVIEW_LOW) -> dict:
    """Rejected only on a clear no (a quality score at or below REVIEW_LOW). Then: one fix if it is clearly the same
    idea, else the backup if it clearly fits, else text. Anything else is kept ("unsure" if a score is below REVIEW_HIGH).
    Off-topic controls are never sent to a fix: the plan chose them and a repair must keep the plan's controls (the
    first live run's fixes were rejected again for exactly this), so that case goes to the backup's fresh design.
    fix_first (Code Lab 4.0): every rejected chart gets one fix first (the reviewer now reads the whole section, so most
    rejections are labels to rename or a control to refocus); the backup, when it clearly fits, is the next try.
    low: the clear-no line (Playground mode uses PG_REVIEW_LOW)."""
    p = {k: round(float(answers[k]["p"]), 3) for k in REVIEW_QUESTIONS if k in answers}
    failed = [k for k in QUALITY if p.get(k, 1) <= low]
    reject = bool(failed)
    if fix_first:
        fix = reject
        backup = reject and has_backup and p.get("backup_direct", 0) >= REVIEW_HIGH
        unsure = not reject and any(p.get(k, 1) < REVIEW_HIGH for k in QUALITY)
        return {"reject": reject, "fix": fix, "backup": backup, "unsure": unsure, "problems": [REVIEW_PROBLEMS[k] for k in failed], "p": p}
    fix = reject and "controls_on_topic" not in failed and p.get("same_idea", 0) >= REVIEW_HIGH
    backup = reject and not fix and has_backup and p.get("backup_direct", 0) >= REVIEW_HIGH
    unsure = not reject and any(p.get(k, 1) < REVIEW_HIGH for k in QUALITY)
    return {"reject": reject, "fix": fix, "backup": backup, "unsure": unsure, "problems": [REVIEW_PROBLEMS[k] for k in failed], "p": p}


def _noul(instructions: str, true: str, false: str) -> dict:
    """A yes/no question for Jev (copied from codelab._noul)."""
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


def scores_text(p: dict) -> str:
    return ", ".join(f"{k} {v:.2f}" for k, v in p.items())


def _section(outline: dict, si: int) -> dict:
    st = outline["stages"][si]
    return {
        "page": outline.get("title", ""), "level": outline.get("level", ""),
        "section": {"label": st.get("label", ""), "summary": st.get("blurb", ""), "blocks": [{"index": i, **b} for i, b in enumerate(st["blocks"])]},
    }


_BIND_KIND = {"range": "slider", "select": "select", "radio": "select", "checkbox": "checkbox"}


def is_full_vega(spec: dict | None) -> bool:
    """A full Vega spec (Code Lab 3.0) rather than Vega-Lite: by its $schema, or by its shape when that is missing
    (the same rule as XW.vega in shared/widget-kit.js)."""
    if not isinstance(spec, dict):
        return False
    schema = str(spec.get("$schema") or "")
    if "/schema/vega-lite/" in schema:
        return False
    if "/schema/vega/" in schema:
        return True
    return isinstance(spec.get("marks"), list) and not any(k in spec for k in ("mark", "layer", "hconcat", "vconcat", "concat"))


def controls_from_spec(spec: dict | None) -> list[dict]:
    """Code Lab 3.0/4.0: the plan's controls, read from the chart's top-level params (Vega-Lite) or signals (full Vega)
    that the reader can set (those with a bind), so the plan can't disagree with the chart. A slider keeps its min, max
    and step; every control its default."""
    out: list[dict] = []
    spec = spec if isinstance(spec, dict) else {}
    found = spec.get("signals") if is_full_vega(spec) else spec.get("params")
    for p in found if isinstance(found, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]:
            continue
        bind = p.get("bind")
        kind = _BIND_KIND.get(bind.get("input")) if isinstance(bind, dict) else None
        if not kind:
            continue
        c: dict = {"id": p["name"], "kind": kind, "label": str(bind.get("name") or p["name"]).strip()}
        for k in ("min", "max", "step"):
            v = bind.get(k)
            if kind == "slider" and isinstance(v, (int, float)) and not isinstance(v, bool):
                c[k] = v
        if "value" in p:
            c["value"] = p["value"]
        out.append(c)
    return out


def chart_summary(widget: dict) -> dict:
    """What Jev reads about a finished chart (Code Lab 4.0's duplicate audit): its title, lesson, controls (from the
    spec's params) and axis and legend titles. Built by code from the chart, never the raw spec."""
    html = str(widget.get("html") or "")
    spec = None
    i = html.find("XW.vega(")
    if i >= 0:
        start, end = html.find("{", i), html.rfind(")</script>")
        try:
            spec = json.loads(html[start:end].replace("<\\/", "</")) if 0 <= start < end else None
        except ValueError:
            spec = None
    titles: list[str] = []

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "title" and isinstance(x, str) and key in ("x", "y", "color", "x2", "y2", "theta", "size", "axes", "legends") and x.strip():
                    titles.append(x.strip())
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)

    walk(spec)
    try:
        plan = json.loads(widget.get("plan") or "{}")
    except ValueError:
        plan = {}
    plan = plan if isinstance(plan, dict) else {}
    lesson = plan.get("lesson", "")
    if spec is not None:
        controls = controls_from_spec(spec)
    else:
        # Code Lab 5.0's own code: no spec to read, so the plan it was built and checked against (its controls and
        # what each visual draws) says what the reader moves and sees.
        controls = [c for c in plan.get("controls") or [] if isinstance(c, dict) and isinstance(c.get("label"), str)]
        titles = [str(v["what"])[:120] for v in plan.get("visuals") or [] if isinstance(v, dict) and isinstance(v.get("what"), str) and v["what"].strip()]

    def rng(c: dict) -> str:
        lo, hi = c.get("min"), c.get("max")
        ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi))
        return f" ({lo:g} to {hi:g})" if ok else ""

    return {"title": str(widget.get("title") or ""), "lesson": str(lesson or ""),
            "controls": [f'{c["label"]}{rng(c)}' for c in controls],
            "axes": list(dict.fromkeys(titles))[:8]}


def _same_question(a: str, b: str) -> dict:
    return {
        "type": "noul",
        "instructions": f"`charts.{b}` shows the same quantity changing with the same kind of setting as `charts.{a}`.",
        "criteria": {
            "true": f"`charts.{a}` and `charts.{b}` plot the same quantity (their lessons and axes are about the same thing) and the reader moves the same kind of setting.",
            "false": f"`charts.{a}` and `charts.{b}` plot different quantities, or the reader moves a different kind of setting.",
        },
    }


def audit_duplicates(jev_key: str | None, labels: list[str], widgets: list[dict], usage: UsageLog) -> list[tuple[int, int]]:
    """Code Lab 4.0: (later, earlier) index pairs where Jev clearly says the later chart repeats the earlier one
    (p >= REVIEW_HIGH). One Jev call with one literal question per pair. No key, one chart, or a Jev error: nothing."""
    if not jev_key or len(widgets) < 2:
        return []
    names = [f"c{i + 1}" for i in range(len(widgets))]
    state = {"charts": {n: {"section": labels[i], **chart_summary(w)} for i, (n, w) in enumerate(zip(names, widgets))}}
    pairs = [(a, b) for b in range(len(widgets)) for a in range(b)]
    questions = {f"same_{names[a]}_{names[b]}": _same_question(names[a], names[b]) for a, b in pairs}
    try:
        r = jev_ask(jev_key, state, questions)
    except JevError:
        return []
    usage.add("jev code lab duplicates", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    out: list[tuple[int, int]] = []
    dropped: set[int] = set()
    for a, b in pairs:
        ans = r["answers"].get(f"same_{names[a]}_{names[b]}")
        if b in dropped or a in dropped or not isinstance(ans, dict):
            continue
        if float(ans.get("p", 0)) >= REVIEW_HIGH:
            out.append((b, a))
            dropped.add(b)
    return out
