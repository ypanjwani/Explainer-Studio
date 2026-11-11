from __future__ import annotations

import re
from typing import Any

from ... import data
from ...followup import parse_follow_up
from ...js import js_len, js_slice, js_trim, json_parse
from . import playground_edit as pe
from ..explorer.judge import block_text
from .revise import restore_kept_widgets


# from feedback_pick.py
SNIPPET = 120


# from feedback_pick.py
CONTROL_TYPES = ("slider", "select", "toggle", "button", "play")  # the playground library's controls (app/public/viewer.js)


# from feedback_pick.py
def _clip(s: Any, n: int = SNIPPET) -> str:
    return js_slice(re.sub(r"\s+", " ", str(s or "")).strip(), 0, n)


# from feedback_pick.py
def _labels(items: Any, key: str = "label") -> list[str]:
    return [_clip(x.get(key), 60) for x in items or [] if isinstance(x, dict) and _clip(x.get(key), 60)]


# from feedback_pick.py
# What a playground chart draws, from the helpers its draw code calls (the library's bars(), curve(), point(), arrow(), matrix()...).
DRAWS = (("bar", "bar"), ("curve", "line"), ("line", "line"), ("path", "line"), ("area", "line"), ("matrix", "grid"),
         ("grid", "grid"), ("heat", "grid"), ("point", "point"), ("arrow", "arrow"))


# from feedback_pick.py
def chart_kind(draw: Any) -> str:
    """ "bar", "line and point" etc. for a playground chart's draw code; "" when it can't be told."""
    called = {w.lower() for w in re.findall(r"\b([A-Za-z]+)\s*\(", str(draw or ""))}
    kinds = list(dict.fromkeys(k for prefix, k in DRAWS if any(w.startswith(prefix) for w in called)))
    return " and ".join(kinds[:3])


# from feedback_pick.py
def playground_visuals(b: dict) -> list[str]:
    """Each chart of a playground as the reader sees it: its title and what it draws, e.g. 'Per-head dimension (bar chart)'."""
    spec = pe.read_playground_spec(b.get("html", "")) or {}
    out = []
    for c in spec.get("components") or []:
        if isinstance(c, dict) and c.get("type") == "chart":
            kind = chart_kind(c.get("draw"))
            title = _clip(c.get("title") or c.get("id"), 60)
            out.append(f"{title} ({kind} chart)" if kind else f"{title} (chart)")
        elif isinstance(c, dict) and c.get("type") == "readout" and c.get("label"):
            out.append(f"{_clip(c['label'], 40)} (readout)")
    return out


# from feedback_pick.py
def _playground_controls(b: dict) -> tuple[list[str], bool, bool] | None:
    """(the controls a reader can press or slide, whether a chart can be dragged or clicked, whether it's a playground), from
    the playground spec in the html; None when it isn't a playground."""
    if pe.widget_kind(b) != "playground":
        return None
    spec = pe.read_playground_spec(b.get("html", "")) or {}
    comps = [c for c in spec.get("components") or [] if isinstance(c, dict)]
    controls = [f"{c.get('label') or c.get('id')} ({c['type']})" if c.get("type") != "slider" else str(c.get("label") or c.get("id"))
                for c in comps if c.get("type") in CONTROL_TYPES and c.get("action") != "reset"]
    drag = any(c.get("type") == "chart" and (c.get("onDrag") or c.get("onClick")) for c in comps)
    return controls, drag, True


# from feedback_pick.py
def widget_line(b: dict) -> str:
    """A widget's controls and what it shows: for a playground from its spec (plus the Reset the library always adds),
    otherwise from its stored plan."""
    plan = pe.read_plan(b)
    shows = _labels(plan.get("visuals"), "what")
    pg = _playground_controls(b)
    if pg is not None:
        shows = playground_visuals(b) or shows
        controls, drag, _ = pg
        items = controls + (["dragging on a chart"] if drag else []) + ["Reset (added automatically)"]
        flag = "" if controls else "; its only button is Reset" + (": the reader can only drag on its chart" if drag else ": nothing else to press")
    else:
        items = [f"{c.get('label')} ({c.get('kind')})" if c.get("kind") and c.get("kind") != "slider" else str(c.get("label"))
                 for c in plan.get("controls") or [] if isinstance(c, dict) and c.get("label")]
        flag = ""
    out = f'interactive widget "{_clip(b.get("title"), 80)}"'
    if items:
        out += f"; controls: {', '.join(items[:10])}"
    out += flag
    if shows:
        out += f"; shows: {', '.join(shows[:8])}"
    if plan.get("lesson"):
        out += f"; teaches: {_clip(plan['lesson'])}"
    return out


# from feedback_pick.py
def block_line(b: dict) -> str:
    """One block, as the reader would recognise it on the page."""
    t = b.get("type")
    if t == "widget":
        return widget_line(b)
    if t == "chart":
        series = _labels(b.get("series"))
        return f'line chart "{_clip(b.get("title"), 80)}"' + (f" (lines: {', '.join(series[:4])})" if series else "")
    if t == "bars":
        return f'bar chart "{_clip(b.get("title"), 80)}"' + (f" (bars: {', '.join(str(x) for x in (b.get('labels') or [])[:6])})" if b.get("labels") else "")
    if t == "slider":
        return f'slider "{_clip(b.get("label"), 60)}"'
    if t == "readout":
        return "readout: " + ", ".join(_labels(b.get("items"))[:4])
    if t == "formula":
        return "formula: " + _clip(b.get("latex"), 80)
    words = block_text(b)
    if words:
        return f"{t}: {_clip(words)}"
    title = b.get("title") or b.get("prompt") or b.get("label")
    return f"{t}" + (f' "{_clip(title, 80)}"' if title else "")


# from followup_pick.py
MODES = ("playground", "lite-plus")  # the pages whose typed follow-ups are picked here (not Playground 2.0)


# from followup_pick.py
def parse_pick_request(body: Any) -> dict | str:
    """POST /api/followup/pick: {text, quality, followUp (the revise follow-up the app builds)}; a string is the error to show."""
    body = body if isinstance(body, dict) else {}
    text = js_trim(body.get("text") if isinstance(body.get("text"), str) else "")
    if not text or js_len(text) > data.limits()["conceptChars"]:
        return "Describe what you want to change."
    if body.get("quality") not in MODES:
        return "This page's follow-ups don't use this step."
    f = parse_follow_up(body.get("followUp"))
    if not isinstance(f, dict) or f.get("kind") != "revise":
        return f if isinstance(f, str) else "The previous explainer couldn't be read for this revision."
    try:
        outline = json_parse(f["outline"])
    except ValueError:
        return "The previous explainer couldn't be read for this revision."
    if not isinstance(outline, dict) or not isinstance(outline.get("stages"), list) or not outline["stages"]:
        return "The previous explainer couldn't be read for this revision."
    page = restore_kept_widgets(outline, f["widgets"]) if f.get("widgets") else outline
    return {"text": text, "page": page, "quality": body["quality"]}
