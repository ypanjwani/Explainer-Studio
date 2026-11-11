"""Steps Reader and Explorer share: reading a paper, the planner call (with the compute tool), the design, worked
examples, the hints added to the planner's request, and the spec's finishing touches.

Copied from pipeline.py (Nov 2026) so these two modes never import another mode's code. The rule for everything under
modes/: import only shared tools (no mode logic); test_modes_boundary.py checks it. pipeline.py keeps its own copy for
the other modes; when those are deleted, it goes with them.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import anthropic

from .. import data
from ..brief import brief_hint, brief_summary
from ..core.compact import parse_compact
from ..core.examples import apply_example_steps, example_briefs
from ..core.expr import eval_value, is_mat, is_num
from ..core.followup import continuation_note
from ..core.references import cut_references
from ..core.schema import parse as schema_parse
from ..core.spec import parse_design
from ..core.validate import MAX_STAGES, assemble_spec, default_design, requested_stages
from ..design import apply_brief, design_with_brief
from ..filter import filter_sections
from ..js import WS, is_finite, js_len, js_round, js_slice, js_str, js_trim, json_parse, json_stringify, round_num
from ..json_util import extract_json
from ..llm import HAIKU, ReaderError, cached_system, check_stop, text_of, write_text
from ..paper_cache import paper_key
from ..pricing import UsageLog

log = logging.getLogger(__name__)

GROUP_CHARS = 14_000  # section text per Haiku summary call
CONTEXT_CHARS = 24_000  # context kept for the chatbot
MAX_TOOL_ROUNDS = 4  # compute-tool round trips before the final answer is required
# Evals: FACTCHECK_SECTIONS=1 adds the "consistent with its own section" question to the fact check.
# Off until hand-labelled pages show it catches mismatches the other two questions miss.
FACTCHECK_SECTIONS = os.environ.get("FACTCHECK_SECTIONS") == "1"

Emit = Callable[[dict], None]


def _step(emit: Emit, step: str, status: str, detail: str | None = None) -> None:
    e = {"type": "step", "step": step, "status": status}
    if detail:
        e["detail"] = detail
    emit(e)


def _prompt(name: str) -> str:
    return data.prompt(name)


# ---------- paper summaries ----------


def group_sections(sections: list[dict]) -> list[str]:
    """Merge sections into groups of about GROUP_CHARS so long papers make few calls."""
    groups: list[str] = []
    cur = ""
    for s in sections:
        piece = f"## {s['heading']}\n{s['text']}\n\n"
        if cur and js_len(cur) + js_len(piece) > GROUP_CHARS:
            groups.append(cur)
            cur = ""
        cur += js_slice(piece, 0, int(GROUP_CHARS * 1.5)) if js_len(piece) > GROUP_CHARS * 1.5 else piece
    if cur:
        groups.append(cur)
    return groups


def summarize(client: anthropic.Anthropic, group: str, usage: UsageLog) -> str:
    return js_trim(write_text(client, usage, "summarize", system=_prompt("SUMMARIZE_SYSTEM"), user=group, max_tokens=2000, allow_cutoff=True))


# ---------- compute tool: the planner checks numbers with our math engine ----------

_COMPUTE_INPUT = {"t": "object", "shape": {"expressions": {"t": "array", "element": {"t": "object", "shape": {
    "expr": {"t": "string"},
    "vars": {"t": "optional", "inner": {"t": "array", "element": {"t": "object", "shape": {"name": {"t": "string"}, "value": {"t": "number", "int": False}}}}},
}}}}}


def show_value(v: Any) -> str:
    def n(x: Any) -> str:
        return js_str(round_num(x, 6)) if is_finite(x) else "NaN (not a valid number)"

    if is_num(v) or v is None:
        return n(v)
    if is_mat(v):
        return "[" + ", ".join("[" + ", ".join(n(x) for x in r[:8]) + "]" for r in v[:8]) + "]"
    return "[" + ", ".join(n(x) for x in v[:12]) + (f", … ({len(v)} items)" if len(v) > 12 else "") + "]"


def run_compute(tool_input: Any) -> dict:
    """One compute call; the input is validated here before any expression runs. {"content", "is_error"}."""
    value, issues = schema_parse(_COMPUTE_INPUT, tool_input)
    if not issues and len(value["expressions"]) > 40:
        issues = [{"message": "Too big: expected array to have <=40 items"}]
    if issues:
        return {"content": f"Invalid input: {issues[0]['message'] if issues else 'bad shape'}", "is_error": True}
    lines = []
    for e in value["expressions"]:
        scope: dict = {"seed": 0.0}
        for var in e.get("vars") or []:
            scope[var["name"]] = var["value"]
        lines.append(f"{e['expr']} = {show_value(eval_value(e['expr'], scope))}")
    return {"content": "\n".join(lines), "is_error": False}


def read_reply(text: str, fmt: str) -> tuple[Any, bool]:
    """The planner's reply as a raw object: JSON, or the compact format (a reply that starts with "{" is read as JSON either way)."""
    if fmt == "json" or re.match(WS + r"*(```(json)?" + WS + r"*)?\{", text):
        return extract_json(text)
    return parse_compact(text), False


# Compute-tool round trips allowed per plan attempt. Each round resends the whole conversation.
FULL_BUDGET = {"rounds": MAX_TOOL_ROUNDS, "retryRounds": MAX_TOOL_ROUNDS}
# Lite+: at most 2 rounds; a retry gets none and is handed the numbers the first attempt checked.
LITE_BUDGET = {"rounds": 2, "retryRounds": 0}


def write_outline(
    client: anthropic.Anthropic, request: str | list[dict], emit: Emit, usage: UsageLog, system: str,
    parse: Callable[[Any], dict], effort: str, fmt: str = "json", budget: dict = FULL_BUDGET,
    whole: str = "the whole outline", on_text: Callable[[str | None], None] | None = None,
) -> dict:
    """{"outline", "warnings", "revision"?} from the planner. One attempt normally; a second (full rewrite) only when
    the reply is unusable even after JSON repair and outline defaults. whole: what the reply holds, for the nudge
    after the last compute round ("your reply" for a revision made as changes). "revision" is passed through
    from parse (revise.parse_revision). on_text, if given, sees the reply as it streams: None when a new stream starts
    (a compute round or a retry), then each text delta (Code Lab starts a section's widget as soon as it is written)."""
    last_error = ""
    checked: list[str] = []  # every compute result, so a retry without the tool can still quote checked numbers
    compute_tool = data.formats()["computeTool"]
    for attempt in range(2):
        label = "plan (retry)" if attempt else "plan"
        max_rounds = budget["retryRounds"] if attempt else budget["rounds"]
        carried = ("\n\nThe compute tool is off for this reply. Numbers you already checked:\n" + js_slice("\n".join(checked), 0, 4000)) if attempt and max_rounds == 0 and checked else ""
        if last_error:
            again = "Reply again with the whole outline in the compact format." if fmt == "compact" else "Reply again with one valid JSON object."
            note = f"Your previous reply could not be used ({last_error}). {again}{carried}"
            # A block request (Quality 2.0's improve) keeps its cached page block first.
            content = [*request, {"type": "text", "text": note}] if isinstance(request, list) else f"{request}\n\n{note}"
        else:
            content = request
        messages: list[dict] = [{"role": "user", "content": content}]
        chars = last_report = checks = 0
        final = None

        for rnd in range(max_rounds + 1):
            try:
                with client.beta.messages.stream(
                    model=HAIKU,
                    max_tokens=48_000,
                    output_config={"effort": effort},
                    system=cached_system(system),
                    # Automatic caching of the conversation so far (the request, which for a revision or an
                    # addition carries the page, plus earlier compute rounds): each compute round reads it at a
                    # tenth of the price instead of paying for it again. The system prompt keeps its own marker.
                    cache_control={"type": "ephemeral"},
                    tools=[compute_tool],
                    # After the last allowed round, the model must answer.
                    tool_choice={"type": "none"} if rnd == max_rounds else {"type": "auto"},
                    messages=messages,
                ) as stream:
                    if on_text:
                        on_text(None)
                    # Report progress as the reply streams in (also keeps the SSE connection busy).
                    for t in stream.text_stream:
                        if on_text:
                            on_text(t)
                        chars += len(t)
                        if chars - last_report > 1500:
                            last_report = chars
                            _step(emit, "plan", "running", f"Writing lesson… {js_str(js_round(chars / 1000))}k characters")
                    msg = stream.get_final_message()
            except anthropic.APIError:
                # API errors (rate limit, auth, overload…) still fail the run.
                raise
            except Exception:
                # Anything else is the SDK failing to read a malformed tool call: retry instead.
                log.exception("plan stream failed")
                last_error = "a malformed compute tool call"
                break
            usage.add_message(f"{label} (after compute)" if rnd else label, msg)
            check_stop(msg, "lesson outline")
            if msg.stop_reason != "tool_use":
                final = msg
                break

            calls = [b for b in msg.content if b.type == "tool_use"]
            # Seen once (Oct 2026): stop_reason "tool_use" with no tool call. Answering would send an
            # empty user turn, which the API rejects (400), so retry the plan instead.
            if not calls:
                last_error = "a tool call was announced but missing"
                break
            # Append the full response (thinking blocks included) and answer every tool call.
            messages.append({"role": "assistant", "content": [b.to_dict(exclude_none=True) for b in msg.content]})
            results = []
            for c in calls:
                r = run_compute(c.input) if c.name == "compute" else {"content": "Unknown tool.", "is_error": True}
                if not r["is_error"]:
                    checked.append(r["content"])
                results.append({"type": "tool_result", "tool_use_id": c.id, **r})
            # The next round has the tool switched off. Unless told, Haiku tries to keep checking and
            # ends with an empty reply (stop=end_turn, 0 chars), which cost a full retry.
            if rnd + 1 == max_rounds:
                results.append({"type": "text", "text": f"That was the last compute check. Write {whole} now" + (" in the compact format." if fmt == "compact" else " as one JSON object.")})
            messages.append({"role": "user", "content": results})
            checks += len(calls)
            _step(emit, "plan", "running", f"Checking numbers… ({checks})")
        if final is None:
            last_error = last_error or "no final answer after checking numbers"
            continue

        reply = text_of(final)
        try:
            value, repaired = read_reply(reply, fmt)
            parsed = parse(value)
            if "outline" in parsed and parsed["outline"]["stages"]:
                notes = []
                if attempt:
                    notes.append(f"Plan retried once: the first reply could not be used ({last_error}).")
                if repaired:
                    notes.append("The plan's JSON had a syntax slip and was repaired automatically.")
                return {"outline": parsed["outline"], "warnings": [*notes, *parsed["warnings"]],
                        **({"revision": parsed["revision"]} if "revision" in parsed else {})}
            last_error = js_slice(parsed["error"], 0, 600) if "error" in parsed else "no stages"
        except Exception as e:
            last_error = js_slice(str(e), 0, 300) or "invalid JSON"
        # Why a plan was unusable (a cut-off reply, prose instead of JSON, a broken object…).
        log.warning(
            "plan attempt %d unusable: %s | stop=%s | after %d compute calls | content [%s] | out %d tokens | %d chars | starts: %s | ends: %s",
            attempt + 1, last_error, final.stop_reason, checks, ", ".join(b.type for b in final.content), final.usage.output_tokens,
            len(reply), reply[:200], reply[-200:],
        )
    raise ReaderError("Couldn't produce a valid lesson outline. Please try again.")


# ---------- design and worked examples ----------


def design_brief(outline: dict, request: str) -> str:
    stages = "\n".join(f"- {s['id']}: {s['label']} — {s['blurb']}" for s in outline["stages"])
    return "\n".join([
        f"Title: {outline['title']}",
        f"Subtitle: {outline['subtitle']}",
        f"Level: {outline['level']}",
        f"Key intuition: {outline['keyIntuition']}",
        f"Stages:\n{stages}",
        f"Original request (excerpt): {js_slice(request, 0, 1500)}",
    ])


def choose_design(client: anthropic.Anthropic, outline: dict, request: str, usage: UsageLog) -> dict | None:
    try:
        msg = client.messages.create(
            model=HAIKU,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": data.formats()["design"]}},
            system=cached_system(_prompt("DESIGN_SYSTEM")),
            messages=[{"role": "user", "content": design_brief(outline, request)}],
        )
        usage.add_message("design", msg)
        check_stop(msg, "design")
        ok, design = parse_design(json_parse(text_of(msg)))
        if not ok:
            raise ValueError(design[0]["message"])
        return design
    except anthropic.APIError:
        log.exception("design step failed")
        return None  # assemble_spec falls back to the default design
    except Exception:
        log.exception("design step failed")
        return None


_STEPS_OUT = {"t": "object", "shape": {"examples": {"t": "array", "element": {"t": "object", "shape": {
    "key": {"t": "string"}, "steps": {"t": "array", "element": {"t": "string"}}}}}}}


def _parse_format(node: dict, text: str) -> Any:
    value, issues = schema_parse(node, json_parse(text))
    if issues:
        raise ValueError(issues[0]["message"])
    return value


def write_examples(client: anthropic.Anthropic, spec: dict, usage: UsageLog) -> dict:
    """Haiku writes worked-example steps around numbers our code computed. Failures keep the "focus" line only."""
    briefs = example_briefs(spec)
    if not briefs:
        return spec
    try:
        text = write_text(client, usage, "examples", system=_prompt("EXAMPLES_SYSTEM"),
                          user=f"Explainer: {spec['title']} (level: {spec['level']})\n\nExamples:\n{json_stringify(briefs, indent=1)}",
                          max_tokens=6000, fmt=data.formats()["steps"])
        out = _parse_format(_STEPS_OUT, text)
        return apply_example_steps(spec, {e["key"]: e["steps"] for e in out["examples"]})
    except Exception:
        log.exception("examples step failed")
        return spec


# ---------- the planner's request: hints ----------


def outline_context(o: dict) -> str:
    lines = [f"Explainer: {o['title']} — {o['subtitle']}", f"Key intuition: {o['keyIntuition']}"]
    for s in o["stages"]:
        lines.append(f"\n## {s['label']}\n{s['blurb']}")
        for b in s["blocks"]:
            if b["type"] in ("text", "callout"):
                lines.append(b["md"])
            elif b["type"] == "formula":
                lines.append(f"Formula: {b['latex']} ({b['caption']})")
            elif b["type"] == "cards":
                lines.extend(f"{it['title']}: {it['md']}" for it in b["items"])
    lines.append("\nGlossary:\n" + "\n".join(f"- {g['term']}: {g['definition']}" for g in o["glossary"]))
    return "\n".join(lines)


def interactivity_hint(i: str | None) -> str:
    """The composer's Mostly reading | Balanced | Hands-on toggle, as a line in the planner's request (the user turn,
    so the cached system prompts stay byte-stable). Balanced adds nothing."""
    if i == "reading":
        return "The reader chose a mostly-reading page: clear, well-structured explanation with only a few visuals or interactive parts, placed where they help most. Keep the quizzes."
    if i == "hands-on":
        return "The reader chose a hands-on page: give most stages an interactive part (sliders with live charts, experiments, predict-then-reveal and the like) wherever the topic allows, so they learn by trying."
    return ""


def avoid_hint(avoid: dict | None) -> str:
    """Retry: the replaced page's section titles, so the planner writes a fresh take instead of the same page again.
    Dormant since Nov 4 2026: the app no longer sends `avoid` (Retry runs from scratch; the growing list shrank pages)."""
    if not avoid or not avoid.get("titles"):
        return ""
    titles = "; ".join(f'"{t}"' for t in avoid["titles"])
    return ("This request was explained before, and the reader asked for a completely fresh explainer. The earlier one had these "
            f"sections: {titles}. Take a different route: a different opening analogy, different examples, and a different order "
            "or angle where the topic allows. Don't reuse those section titles. Keep every fact correct.")


def other_theme(theme: str, avoid: dict | None) -> str:
    """Retry: a theme other than the replaced page's, so the fresh page looks new too (the next one in menu order)."""
    old = (avoid or {}).get("theme")
    if not old or theme != old:
        return theme
    names = list(data.menu("THEMES"))
    return names[(names.index(old) + 1) % len(names)] if old in names else theme


def drops_examples(extras: list[str] | None) -> bool:
    """The reader answered "What do you want to add to each section?" without worked examples (None included).
    Absent (not asked: older apps, scripts) keeps the planner's usual examples, and so does auto."""
    return extras is not None and "examples" not in extras and "auto" not in extras


def extras_hint(extras: list[str] | None) -> str:
    """The worked-examples part of the answer, as a line in the planner's request. Deeper explanation and other
    wording are written after the plan (notes.py) and web search runs on click, so neither changes the plan."""
    if extras is None or "auto" in extras:
        return ""
    if "examples" in extras:
        return ("The reader asked for worked examples: give every stage that has numbers, a formula or a procedure an "
                "examples block with one or two worked examples.")
    return "The reader chose no worked examples: write no examples blocks."


def section_tools(extras: list[str] | None) -> list[str]:
    """The buttons that call the server when clicked (POST /api/section). auto offers web search: it costs nothing
    until clicked."""
    e = extras or []
    return ["search"] if "search" in e or "auto" in e else []


# Saved preferences (copied from feedback.prefs_hint, whose module also imports other modes' code).
PREF_MAX = 200
MAX_ACTIVE_PREFS = 8


def prefs_hint(prefs: Any) -> str:
    """The planner's note for a reader with saved preferences; "" without any (the request is then unchanged)."""
    prefs = [p for p in prefs if isinstance(p, str) and p.strip()] if isinstance(prefs, list) else []
    if not prefs:
        return ""
    lines = "\n".join(f"- {js_slice(' '.join(p.split()), 0, PREF_MAX)}" for p in prefs[:MAX_ACTIVE_PREFS])
    return ("This reader's saved preferences (follow them where they fit this topic; the rules above always come first):\n"
            f"{lines}")


# ---------- one run: the parts both modes share ----------


def planner_hint(req: dict, follow: dict | None, early_warnings: list[str], interactivity: bool) -> str:
    """The lines added to the planner's request: How hands-on (not for Reader), worked examples, a Retry's old titles,
    a stage count the reader typed, and saved preferences."""
    # A stage count the reader typed ("14 stages") beats the prompt's 4-8 default.
    asked = requested_stages(req["note"] if req["mode"] == "paper" else req["text"])
    if asked and asked["clamped"]:
        early_warnings.append(f"Explainers have at most {MAX_STAGES} stages, so this one was planned with {MAX_STAGES}.")
    return "\n\n".join(x for x in [
        interactivity_hint(req.get("interactivity")) if interactivity else "",
        extras_hint(req.get("extras")),
        avoid_hint(req.get("avoid")),
        f"The reader asked for {asked['count']} stages: write exactly {asked['count']}." if asked else "",
        "" if follow and follow.get("feedback") else prefs_hint(req.get("prefs")),
    ] if x)


def read_source(client: anthropic.Anthropic, req: dict, follow: dict | None, hint: str, emit: Emit, usage: UsageLog,
                jev_key: str | None, paper_cache: Any, early_warnings: list[str], pool: ThreadPoolExecutor,
                brief_future: Any) -> tuple[str, str]:
    """(the planner's request, the source context kept for the chatbot) for a new page or a "new" follow-up.
    A paper: references cut, Jev's section filter, Haiku's summaries (or all three from the paper cache)."""

    def brief_line() -> str:
        b = brief_future.result() if brief_future else None
        return brief_hint(b) if b else ""

    if req["mode"] == "paper":
        refs = cut_references(req["sections"])
        sections = refs["sections"]
        ref_note = f" · references cut ({js_str(js_round(refs['removed'] / 1000))}k chars)" if refs["removed"] else ""
        # The same paper read before: its filter result and summaries come from the cache (paper_cache.py).
        key = paper_key(req["title"], sections) if paper_cache is not None else ""
        hit = paper_cache.get(key) if paper_cache is not None else None
        if hit:
            if hit["skipped"]:
                skipped = ", ".join(f'"{js_slice(h, 0, 50)}"' for h in hit["skipped"])
                early_warnings.append(f"Jev skipped {len(hit['skipped'])} of {len(sections)} sections as side material: {skipped}.")
            _step(emit, "read", "done", f"kept {hit['kept']} of {len(sections)} sections (read before){ref_note}")
            _step(emit, "summarize", "done", f"{hit['groups']} parts (from cache)")
            useful = hit["summaries"]
        else:
            _step(emit, "read", "running", f"Jev is checking {len(sections)} sections{ref_note}")
            f = filter_sections(jev_key, req["title"], sections, usage)
            kept = f["sections"]
            if f["skipped"]:
                skipped = ", ".join(f'"{js_slice(h, 0, 50)}"' for h in f["skipped"])
                early_warnings.append(f"Jev skipped {len(f['skipped'])} of {len(sections)} sections as side material: {skipped}.")
            if f.get("note"):
                early_warnings.append(f"Section filter: {f['note']}.")
            _step(emit, "read", "done", f"kept {len(kept)} of {len(sections)} sections (Jev){ref_note}")
            groups = group_sections(kept)
            _step(emit, "summarize", "running", f"0 / {len(groups)} parts")
            done = [0]
            lock = threading.Lock()

            def summ(g: str) -> str:
                s = summarize(client, g, usage)
                with lock:
                    done[0] += 1
                    n = done[0]
                _step(emit, "summarize", "running", f"{n} / {len(groups)} parts")
                return s

            summaries = list(pool.map(summ, groups))
            useful = [s for s in summaries if s and not s.startswith("(no content)")]
            _step(emit, "summarize", "done", f"{len(groups)} parts")
            # Only a full read is cached: a filter that fell back (no Jev key, Jev errors) or empty summaries aren't.
            if paper_cache is not None and not f.get("note") and useful:
                paper_cache.put(key, {"skipped": f["skipped"], "kept": len(kept), "groups": len(groups), "summaries": useful})
        source_context = "\n\n".join(useful)
        request = "\n\n".join(x for x in [
            f'Create an interactive explainer for this AI research paper: "{req["title"]}".',
            f"The reader's note: {req['note']}" if req["note"] else "",
            hint,
            brief_line(),
            continuation_note(follow) if follow and follow["kind"] == "new" else "",
            f"Section summaries:\n\n{source_context}",
        ] if x)
        return request, source_context
    request = "\n\n".join(x for x in [f"Create an interactive explainer for this AI concept request:\n\n{req['text']}", hint, brief_line(),
                                      continuation_note(follow) if follow else ""] if x)
    source_context = "\n\n".join(x for x in [f"Reader's request: {req['text']}", (follow or {}).get("source") or ""] if x)
    return request, source_context


def plan_detail(follow: dict | None, brief: dict | None) -> str | None:
    """What the "Planning" step says while the plan is written."""
    if follow:
        return "Building on the conversation"
    return (f"Jev: {brief_summary(brief)}" if brief else "") or None


def level_from_brief(outline: dict, brief: dict | None) -> dict:
    """The brief's level wins over the planner's, so the page badge and the chatbot match it."""
    if brief and brief.get("level") and outline["level"] != brief["level"]:
        return {**outline, "level": brief["level"]}
    return outline


def finish_spec(client: anthropic.Anthropic, req: dict, follow: dict | None, outline: dict, request: str, brief: dict | None,
                source_context: str, warnings: list[str], usage: UsageLog, emit: Emit, mode_fields: dict) -> dict:
    """The design (Haiku's pick, Jev's brief, a Retry's other theme), then the spec assembled with the mode's own
    fields (mode_fields, e.g. Reader's noAsk/noControls) and the reader's choices, then worked examples."""
    _step(emit, "design", "running")
    design = choose_design(client, outline, request, usage)
    if brief:
        design = design_with_brief(design or default_design(outline), brief)
    if req.get("avoid"):
        d = design or default_design(outline)
        design = {**d, "theme": other_theme(d["theme"], req["avoid"])}
    jev_pick = " (Jev)" if brief and (brief.get("theme") or brief.get("fontPairing")) else ""
    _step(emit, "design", "done", f"{design['theme']} · {design['fontPairing']}{jev_pick}" if design else "default design")

    _step(emit, "build", "running")
    context = js_slice(f"{outline_context(outline)}\n\nSource material:\n{source_context}", 0, CONTEXT_CHARS)
    spec = assemble_spec(outline, design, context, warnings)
    if mode_fields:
        spec = {**spec, **mode_fields}
    tools = section_tools(req.get("extras"))
    if tools:
        spec = {**spec, "sectionTools": tools}
    # No paper text behind it (a follow-up may carry an earlier paper's), so its quoted figures are recalled, not read.
    if req["mode"] == "concept" and not (follow and follow.get("source")):
        spec = {**spec, "fromMemory": True}
    if brief:
        spec = apply_brief(spec, brief)
    # Page colours chosen in the composer: the page opens and exports in it.
    if req.get("colorMode") in ("light", "dark"):
        spec = {**spec, "design": {**spec["design"], "mode": req["colorMode"]}}
    _step(emit, "build", "done")

    if example_briefs(spec):
        _step(emit, "examples", "running")
        spec = write_examples(client, spec, usage)
        _step(emit, "examples", "done")
    return spec

