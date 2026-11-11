from __future__ import annotations

import json
import logging
import re

import anthropic
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ...core.spec import parse_outline
from ...core.validate import without_blocks
from ...js import json_parse
from ...pricing import UsageLog
from .judge import QUALITY, audit_duplicates, chart_summary, gate_one
from .playground import _design_playground, _rejected
from .settings import ExplorerSettings

log = logging.getLogger(__name__)


MAX_WORKERS = 8


# Blocks a prebuilt-widget planner would write; Code Lab strips them so Haiku's code is the only interaction.
PREBUILT = {"slider", "choice", "toggle", "presets", "plot", "chart", "bars", "readout", "samples", "heatmap", "trajectory",
            "calculator", "experiment", "examples", "sampling", "training", "scene", "widget"}


# A widget added after the section's last non-question block goes before its closing questions.
QUESTIONS = {"quiz", "predict", "reveal"}


PLANNER_NOTE = (
    "Interactive parts are added later by a separate interaction designer who reads your finished text. "
    "So write only the explanation: text, callouts, formulas (written out, no sliders), cards, tabs, tables, quizzes and diagrams. "
    "Do not write sliders, choices, toggles, plots, charts, readouts, bars, calculators, experiments or other live parts."
)


# Playground: the same marks, but for any interactive part, not only charts. PLANNER_NOTE_4 only named quantities,
# trade-offs and processes, so sections about connected parts (layers, attention, pipelines) were never marked and
# every hint read "slide X, watch Y" (Oct 30: no saved playground had a diagram or network). "kind" goes into the hint.
PLANNER_NOTE_PG = PLANNER_NOTE + (
    "\n\nAlso decide, for every stage, whether the reader needs an interactive part there, and add this field to each "
    'stage object: "interaction": {"chart": true or false, "kind": "quantity" | "process" | "compare" | "structure", '
    '"idea": "one line: what the reader explores and what it shows"} (kind and idea empty when chart is false). '
    "Interactive parts go only where exploring teaches what the text cannot: a quantity that changes with a setting, a "
    "trade-off or comparison, a process over steps, or a structure of connected parts the reader can explore (network "
    "layers and weights, tokens attending to each other, a pipeline, a tree of decisions). A static diagram in your text "
    "does not rule a section out: mark it when exploring that structure would teach more. Most sections need none. Mark "
    "at most half of the stages and at most 4 on the page. Never mark an introduction, motivation, summary, recap or "
    "cheat-sheet stage. Every idea must show something no earlier interactive part on this page shows."
)


MAX_MARKED = 4


MARK_KINDS =("quantity", "process", "compare", "structure")


def _place(outline: dict, si: int, widget: dict, after: Any) -> dict:
    """The outline with the widget in stage si: right after block `after` if that is a real block, else before the closing questions."""
    st = outline["stages"][si]
    blocks = list(st["blocks"])
    if isinstance(after, int) and not isinstance(after, bool) and 0 <= after < len(blocks):
        at = after + 1
    else:
        at = len(blocks)
        while at > 0 and blocks[at - 1].get("type") in QUESTIONS:
            at -= 1
    blocks.insert(at, widget)
    stages = list(outline["stages"])
    stages[si] = {**st, "blocks": blocks}
    return {**outline, "stages": stages}


def _safe_design(client: anthropic.Anthropic, usage: UsageLog, outline: dict, si: int, verdict: dict, jev_key: str | None = None,
                 settings: ExplorerSettings = ExplorerSettings()) -> dict:
    try:
        return _design_playground(client, usage, outline, si, verdict, jev_key, settings)
    except Exception:
        log.exception("Code Lab: section %s failed", si)
        label = outline["stages"][si].get("label", "")
        return {"notes": [f"Code Lab ({label}): something went wrong, so the section stays text."], "built": False, "repaired": 0, "backup": False, "chart": False}


def read_mark(raw: Any) -> dict | None:
    """Code Lab 4.0: a stage's "interaction" field as {"chart", "idea"}, or None when the writer left it out or it is
    malformed (that section then goes through Jev's gate, as in Code Lab 3.0)."""
    m = raw.get("interaction") if isinstance(raw, dict) else None
    if not isinstance(m, dict) or not isinstance(m.get("chart"), bool):
        return None
    idea = m.get("idea") if isinstance(m.get("idea"), str) else ""
    out = {"chart": m["chart"], "idea": re.sub(r"\s+", " ", idea).strip()[:300]}
    if m.get("kind") in MARK_KINDS:  # Playground's note (PLANNER_NOTE_PG) also says what kind of part it is
        out["kind"] = m["kind"]
    return out


def mark_hint(mark: dict) -> str | None:
    """The hint Haiku gets from a writer's mark: its idea, led by its kind when the writer gave one."""
    idea = mark.get("idea") or ""
    if mark.get("kind") and idea:
        return f"{mark['kind']}: {idea}"
    return idea or None


def _closed(label: str = "") -> dict:
    """A section that gets no widget and costs no call (the writer marked it as text)."""
    return {"notes": [], "built": False, "repaired": 0, "backup": False, "chart": False}


def _gate_and_design(client: anthropic.Anthropic, usage: UsageLog, outline: dict, si: int, jev_key: str | None,
                     settings: ExplorerSettings = ExplorerSettings(), mark: dict | None = None) -> dict:
    """One section start to finish: the writer's mark (or, without one, Jev's gate), then the playground. Never raises."""
    if mark is not None:
        if not mark["chart"]:
            return _closed()
        return _safe_design(client, usage, outline, si, {"open": True, "why": None, "hint": mark_hint(mark)},
                            jev_key, settings)
    try:
        verdict, note = gate_one(jev_key, outline, si, usage, playground=True)
    except Exception:
        log.exception("Code Lab: gate for section %s failed", si)
        verdict, note = {"open": True, "why": None}, None
    res = _safe_design(client, usage, outline, si, verdict, jev_key, settings)
    if note:
        res["notes"].insert(0, note)
    return res


def _gate_safe(jev_key: str | None, outline: dict, si: int, usage: UsageLog) -> tuple[dict, str | None]:
    """gate_one that never raises (a failed gate opens the section, as in _gate_and_design)."""
    try:
        return gate_one(jev_key, outline, si, usage, playground=True)
    except Exception:
        log.exception("Code Lab: gate for section %s failed", si)
        return {"open": True, "why": None}, None


def _design_gated(client: anthropic.Anthropic, usage: UsageLog, outline: dict, si: int, verdict: dict, note: str | None,
                  jev_key: str | None, settings: ExplorerSettings) -> dict:
    res = _safe_design(client, usage, outline, si, verdict, jev_key, settings)
    if note:
        res["notes"].insert(0, note)
    return res


def _done(res: dict) -> Future:
    f: Future = Future()
    f.set_result(res)
    return f


def _targets(outline: dict) -> list[int]:
    return [i for i, st in enumerate(outline["stages"]) if not any(b.get("type") == "widget" for b in st["blocks"])]


def _place_all(outline: dict, targets: list[int], results: list[dict], notes: list[str], stats: dict) -> dict:
    for si, res in zip(targets, results):
        notes.extend(res["notes"])
        stats["repaired"] += res["repaired"]
        stats["backup"] += 1 if res.get("backup") else 0
        stats["charts"] += 1 if res.get("widget") and res.get("chart") else 0
        if res.get("rejected"):  # only when the review rejected something (kept for test runs, debugRejected)
            stats.setdefault("rejected", []).extend(res["rejected"])
        if res.get("look"):  # Code Lab 7.0: what the screenshots showed and whether the repair was used, for test runs
            stats.setdefault("look", []).append({"section": outline["stages"][si].get("label", ""), **res["look"]})
        if res.get("widget"):
            outline = _place(outline, si, res["widget"], res.get("after"))
            stats["built"] += 1
    return outline


class StageScanner:
    """Reads the planner's JSON reply as it streams and returns each stage object's source text as soon as it is
    complete (the objects in the top-level "stages" array). Also remembers the page's title and level if they come first."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.text = ""
        self.pos = 0
        self.depth = 0
        self.in_str = False
        self.esc = False
        self.str_start = 0
        self.key = ""
        self.after_colon = False
        self.stages_depth = 0  # depth inside the stages array, 0 = not in it
        self.done = False  # the stages array has closed
        self.start = -1  # where the current stage object began
        self.head: dict[str, str] = {}

    def feed(self, chunk: str) -> list[str]:
        self.text += chunk
        out: list[str] = []
        t = self.text
        while self.pos < len(t):
            c = t[self.pos]
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif c == "\\":
                    self.esc = True
                elif c == '"':
                    self.in_str = False
                    if self.depth == 1:
                        self._top_string(t[self.str_start:self.pos + 1])
            elif c == '"':
                self.in_str = True
                self.str_start = self.pos
            elif c in "{[":
                if c == "[" and self.depth == 1 and self.after_colon and self.key == "stages" and not self.done:
                    self.stages_depth = 2
                if c == "{" and self.stages_depth and self.depth == self.stages_depth:
                    self.start = self.pos
                self.depth += 1
            elif c in "}]":
                self.depth -= 1
                if c == "}" and self.stages_depth and self.depth == self.stages_depth and self.start >= 0:
                    out.append(t[self.start:self.pos + 1])
                    self.start = -1
                elif c == "]" and self.stages_depth and self.depth < self.stages_depth:
                    self.stages_depth = 0
                    self.done = True
            elif self.depth == 1 and c == ":":
                self.after_colon = True
            elif self.depth == 1 and c == ",":
                self.after_colon = False
            self.pos += 1
        return out

    def _top_string(self, src: str) -> None:
        try:
            s = json.loads(src)
        except ValueError:
            return
        if self.after_colon:
            if self.key in ("title", "level") and isinstance(s, str):
                self.head[self.key] = s
            self.after_colon = False
        else:
            self.key = s


def _stage_key(st: dict) -> str:
    """What a section's widget depends on, to tell whether an early start still matches the final page."""
    return json.dumps({"label": st.get("label"), "blurb": st.get("blurb"), "blocks": st.get("blocks")}, sort_keys=True, ensure_ascii=False)


class Streamed:
    """Code Lab for a new page: each section's gate, plan, build and review start as soon as the section is complete in
    the planner's stream (feed), instead of after the whole page. finish() reuses each early start whose section came
    out the same in the final outline and runs the rest. Feeding never raises, so the planner is never disturbed."""

    def __init__(self, client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, settings: "ExplorerSettings | None" = None) -> None:
        self.client, self.usage, self.jev_key = client, usage, jev_key
        self.settings = settings or ExplorerSettings()
        self.scanner = StageScanner()
        self.pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.index = 0  # the next stage's index in the current stream
        # si -> (stage key, future, kind): "design" runs the whole section; "gate" only Jev's gate (Code Lab 7.0/Playground
        # sections the writer didn't mark: finish() builds only as many of them as the page has room for)
        self.early: dict[int, tuple[str, Future, str]] = {}
        self.marks: dict[str, dict] = {}  # Code Lab 4.0: the writer's chart mark per section label (read_mark)
        self.closed = False

    def feed(self, chunk: str | None) -> None:
        if self.closed:
            return
        try:
            if chunk is None:
                self.scanner.reset()
                self.index = 0
                self.marks = {}
                return
            for src in self.scanner.feed(chunk):
                si = self.index
                self.index += 1
                self._start(si, src)
        except Exception:
            log.exception("Code Lab: reading the streamed outline failed")

    def _mark(self, label: str, raw: Any) -> dict | None:
        """Code Lab 4.0: the writer's mark for this section, with at most MAX_MARKED charts on the page (later marks
        past the limit become text). None when the writer left it out (the section then goes through Jev's gate)."""
        m = read_mark(raw)
        if m and m["chart"] and sum(1 for k, v in self.marks.items() if k != label and v["chart"]) >= MAX_MARKED:
            m = {"chart": False, "idea": m["idea"], "capped": True}
        if m is not None:
            self.marks[label] = m
        return m

    def _start(self, si: int, src: str) -> None:
        raw = json_parse(src)
        parsed = parse_outline({"title": self.scanner.head.get("title", ""), "level": self.scanner.head.get("level", ""), "stages": [raw]})
        if "outline" not in parsed:
            return
        mini = strip_prebuilt(parsed["outline"])
        st = mini["stages"][0]
        mark = self._mark(st.get("label", ""), raw)
        key = _stage_key(st)
        kind = "gate" if mark is None else "design"
        old = self.early.get(si)
        if old and old[0] == key and old[2] == kind:
            return  # a new stream wrote the same section again: keep the run already going
        if old:
            old[1].cancel()
        if kind == "gate":
            self.early[si] = (key, self.pool.submit(_gate_safe, self.jev_key, mini, 0, self.usage), kind)
        else:
            self.early[si] = (key, self.pool.submit(_gate_and_design, self.client, self.usage, mini, 0, self.jev_key, self.settings, mark), kind)

    def _build_gated(self, outline: dict, targets: list[int], gates: dict[int, Future]) -> tuple[dict[int, Future], list[tuple[int, dict, str | None]]]:
        """Code Lab 7.0/Playground: build only as many gate-opened (unmarked) sections as the page has room for after the
        writer's marks (MAX_MARKED parts in all), never a recap, clearest gate answers first. Before, every open section
        was built and _cap_unmarked threw the extras away after paying for them. Returns the started sections and the
        waiting ones (in gate order), which finish() builds only if a part failed (_refill)."""
        label = lambda si: outline["stages"][si].get("label", "")  # noqa: E731
        marked = sum(1 for si in targets if (self.marks.get(label(si)) or {}).get("chart"))
        room = max(MAX_MARKED - marked, 0)
        verdicts: dict[int, tuple[dict, str | None]] = {}
        for si, f in gates.items():
            try:
                verdicts[si] = f.result()
            except Exception:  # cancelled: run the gate again (one cheap Jev call)
                verdicts[si] = _gate_safe(self.jev_key, outline, si, self.usage)
        open_ = [si for si, (v, _) in verdicts.items() if v.get("open") and not _RECAP_LABEL.search(label(si))]
        ranked = sorted(open_, key=lambda si: (-verdicts[si][0].get("score", 0.5), si))
        chosen = set(ranked[:room])
        out: dict[int, Future] = {}
        for si, (v, note) in verdicts.items():
            if si in chosen:
                out[si] = self.pool.submit(_design_gated, self.client, self.usage, outline, si, v, note, self.jev_key, self.settings)
            elif not v.get("open") or _RECAP_LABEL.search(label(si)):
                section_notes = [note] if note else []
                if v.get("open"):
                    section_notes.append(f"Code Lab ({label(si)}): this is a recap section, so it stays text.")
                out[si] = _done({"notes": section_notes, "built": False, "repaired": 0, "backup": False, "chart": False})
        return out, [(si, verdicts[si][0], verdicts[si][1]) for si in ranked[room:]]

    def _refill(self, outline: dict, futures: dict[int, Future], waiting: list[tuple[int, dict, str | None]],
                progress: Callable[[str], None] | None) -> None:
        """The cap counts kept parts, not tries: while the page has fewer than MAX_MARKED widgets and gate-opened
        sections are waiting, build the next ones (Oct 24: a rejected or failed part still used one of the 4 places,
        so 5 open sections on 5 pages stayed text while those pages kept only about 2 parts). The rest stay text."""
        def kept() -> int:
            n = 0
            for f in futures.values():
                try:
                    n += 1 if f.result().get("widget") else 0
                except Exception:
                    pass
            return n

        while waiting and (need := MAX_MARKED - kept()) > 0:
            batch, waiting[:] = waiting[:need], waiting[need:]
            if progress:
                progress(f"building {len(batch)} more section{'s' if len(batch) > 1 else ''}")
            started = [self.pool.submit(_design_gated, self.client, self.usage, outline, si, v, note, self.jev_key, self.settings)
                       for si, v, note in batch]
            for (si, _, _), f in zip(batch, started):
                futures[si] = f
            for _ in as_completed(started):
                pass
        for si, _, note in waiting:
            label = outline["stages"][si].get("label", "")
            futures[si] = _done({"notes": ([note] if note else []) + [f"Code Lab ({label}): the page already has its {MAX_MARKED} interactive parts, so the section stays text."],
                                 "built": False, "repaired": 0, "backup": False, "chart": False})

    def finish(self, outline: dict, notes: list[str], progress: Callable[[str], None] | None = None) -> tuple[dict, dict]:
        """The outline with its widgets placed (like design_code_widgets). progress gets "k of n sections done"."""
        self.closed = True
        targets = _targets(outline)
        stats = {"sections": len(targets), "built": 0, "repaired": 0, "backup": 0, "charts": 0, "early": 0}
        futures: dict[int, Future] = {}
        gates: dict[int, Future] = {}  # Code Lab 7.0/Playground: unmarked sections, gate only for now
        for si in targets:
            mark = self.marks.get(outline["stages"][si].get("label", ""))
            kind = "gate" if mark is None else "design"
            old = self.early.get(si)
            if old and old[0] == _stage_key(outline["stages"][si]) and old[2] == kind:
                (gates if kind == "gate" else futures)[si] = old[1]
                stats["early"] += 1
            else:
                if old:
                    old[1].cancel()
                if kind == "gate":
                    gates[si] = self.pool.submit(_gate_safe, self.jev_key, outline, si, self.usage)
                else:
                    futures[si] = self.pool.submit(_gate_and_design, self.client, self.usage, outline, si, self.jev_key, self.settings, mark)
        kept = {id(f) for f in futures.values()} | {id(f) for f in gates.values()}
        for _, f, _ in self.early.values():
            if id(f) not in kept:
                f.cancel()
        waiting: list[tuple[int, dict, str | None]] = []
        if gates:
            started, waiting = self._build_gated(outline, targets, gates)
            futures.update(started)
        if targets and not self.jev_key:
            notes.append("Code Lab: no Jev key, so every section went to the plan step.")
        log.info("Code Lab: %d of %d sections started while the page text streamed", stats["early"], len(targets))
        done = 0
        for _ in as_completed(futures.values()):
            done += 1
            if progress and done < len(futures):
                progress(f"{done} of {len(futures)} sections done")
        self._refill(outline, futures, waiting, progress)
        self.pool.shutdown(wait=False, cancel_futures=True)
        results = []
        for si in targets:
            try:
                results.append(futures[si].result())
            except Exception:  # cancelled or crashed: _gate_and_design itself never raises
                results.append({"notes": [f'Code Lab ({outline["stages"][si].get("label", "")}): something went wrong, so the section stays text.'],
                                "built": False, "repaired": 0, "backup": False, "chart": False})
        # The page-level budget and repeat check, and what the writer marked.
        try:
            _cap_unmarked(outline, targets, results, self.marks)
        except Exception:
            log.exception("Code Lab: capping unmarked sections failed")
        try:
            _drop_duplicates(self.jev_key, outline, targets, results, self.usage)
        except Exception:
            log.exception("Code Lab: the duplicate audit failed")
        stats["marks"] = dict(self.marks)
        if not self.marks:
            notes.append("Code Lab: the writer marked no sections, so Jev's gate chose them.")
        return _place_all(outline, targets, results, notes, stats), stats


def _drop_duplicates(jev_key: str | None, outline: dict, targets: list[int], results: list[dict], usage: UsageLog) -> None:
    """Code Lab 4.0: after every section is done, ask Jev which finished charts repeat an earlier one and turn those
    sections back into text (the dropped chart is kept for test runs as a "duplicate")."""
    kept = [k for k, res in enumerate(results) if res.get("widget")]
    labels = [outline["stages"][targets[k]].get("label", "") for k in kept]
    for later, earlier in audit_duplicates(jev_key, labels, [results[k]["widget"] for k in kept], usage):
        res = results[kept[later]]
        w = res.pop("widget")
        res["built"] = False
        _rejected(res, labels[later], "duplicate", {"title": w.get("title", ""), "lesson": chart_summary(w)["lesson"]}, w.get("html", ""),
                  {"repeats": labels[earlier]})
        res["notes"].append(f'Code Lab ({labels[later]}): its chart repeated the one in "{labels[earlier]}", so the section stays text.')


_RECAP_LABEL = re.compile(r"\b(?:intro(?:duction)?|overview|motivation|summary|recap|cheat[\s-]?sheet|wrap[\s-]?up|putting it (?:all )?together|takeaways?|conclusion)\b", re.I)


def _review_score(widget: dict) -> float:
    """How clearly the reviewer liked a finished widget: its lowest quality score (0.5 when it wasn't reviewed)."""
    try:
        p = (json.loads(widget.get("plan") or "{}") or {}).get("review") or {}
    except (ValueError, AttributeError):
        p = {}
    scores = [float(p[k]) for k in QUALITY if isinstance(p.get(k), (int, float))]
    return min(scores) if scores else 0.5


def _cap_unmarked(outline: dict, targets: list[int], results: list[dict], marks: dict[str, dict]) -> None:
    """Code Lab 7.0/Playground: sections the writer didn't mark went through Jev's gate, which opens far more of them (seen: a
    page with no marks got 5 charts, 3 about the same beta). Keep the page within MAX_MARKED: never on an intro or
    summary, and the best-reviewed first. The rest become text (kept for test runs as "capped")."""
    built = [k for k, res in enumerate(results) if res.get("widget")]
    label = lambda k: outline["stages"][targets[k]].get("label", "")  # noqa: E731
    unmarked = [k for k in built if label(k) not in marks]
    room = MAX_MARKED - (len(built) - len(unmarked))
    drop = [k for k in unmarked if _RECAP_LABEL.search(label(k))]
    rest = sorted((k for k in unmarked if k not in drop), key=lambda k: -_review_score(results[k]["widget"]))
    drop += rest[max(room, 0):]
    for k in drop:
        res = results[k]
        w = res.pop("widget")
        res["built"] = False
        _rejected(res, label(k), "capped", {"title": w.get("title", ""), "lesson": chart_summary(w)["lesson"]}, w.get("html", ""), {})
        res["notes"].append(f"Code Lab ({label(k)}): the page already has its {MAX_MARKED} interactive parts"
                            + (" and this is a recap section" if _RECAP_LABEL.search(label(k)) else "") + ", so the section stays text.")


def strip_prebuilt(outline: dict) -> dict:
    """The planner's outline without prebuilt interactive blocks (a safety net for the PLANNER_NOTE)."""
    return without_blocks(outline, PREBUILT)
