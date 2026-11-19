"""Follow-ups v5, phase 4 (Nov 16 2026): one agent handles the reader's follow-up from start to end, in /api/generate
(run.py, a plan with v5.agent; the plan step before it is code only, talk.py).

input     the version the message is under (its main file, or the page itself without the store), the version log, the page's
          full text when it fits (FOLLOWUP_V5_FULLPAGE), the paper it was made from (whole up to PAPER_CHARS), the conversation (shared.history_text, carried in the plan), the
          reader's message and the option they picked
loop      Haiku (FOLLOWUP_V5_AGENT_SYSTEM / _LITE_SYSTEM) with every tool (tools.py): reads any version, writes, start_from,
          restores, check_changes, ask_user, new_topic, finish. Each call is applied and checked by code at once and its
          result sent back. Rounds and spend by Effort (BUDGET; FOLLOWUP_MAX_USD overrides the cap); near the end a
          wrap-up note, at the end tools off and its text is the reply.
effort    the reader's Effort from the first call to the last (Reader pages: Low). Until Nov 19 2026 it started at Low and
          stepped up at the first write, so the reading (which sections to open, which playgrounds to run, what is wrong)
          was all done at Low, and a check-only message never left Low. Thinking is the page's setting (off on Reader
          pages). Neither changes mid-loop: a thinking or effort change invalidates the prompt cache.
gate      the first finish after a change runs the checks (editor.problems: Jev's numbers check, each changed section's
          text against its playground's numbers; review.review on the whole page) and, when they find problems, is refused
          once with the list: the agent fixes them (feedback, nothing put back). check_changes runs the same checks on demand.
          Jev checks numbers only: whether a change did what the reader asked is the agent's call (it reads the conversation)
guard     after the loop, by code: Jev's numbers check again on the sections changed since the checks or failing then
          (editor.check_changes puts back each the change made contradict its playground); a review problem the agent left
          alone becomes a note in the reply, never a put-back (it undid fixes the reader asked for)
ends      a change: the outline + revision (reply: the agent's own, rewritten by reply.write only when the guard undid
          something), suggestions in the reply and the version log; start_from + finish: that version as a new one
          (startFrom, so history gives it that parent); no change: shared.Checked (the answer); ask_user before any write:
          editor.Asked; new_topic: shared.NewTopic. Answer-only (v5.intent "answer": Jev found the message only asks, or
          past the day's explainers): every write is refused, and the fixes it found are offered as suggestions.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

import anthropic

from .... import data
from ....js import js_slice
from ....llm import HAIKU, ReaderError, cached_system, check_stop, text_of
from ....pricing import UsageLog
from .. import main_file as mf
from ..cards import page_text
from ..revise import restore_kept_widgets
from ... import topic
from ..shared import CHECKED, NEW_TOPIC, OFFER, Checked, NewTopic
from . import editor, full_page, look_on
from . import look as look_mod
from . import reply as reply_mod
from . import review as review_mod
from . import tools as T
from .page import Page, Store, cut, look_theme_of

log = logging.getLogger("explainer.followup.v5")

# Rounds and $ cap per follow-up, by effort. Rounds were 10 / 20 / 30 until Nov 17 2026: "fix all 5 issues" needs ~2 a fix
# plus checking, so Low (and every Reader page) ran out part-way. The $ cap is the real limit (the dearest local run: $0.046).
BUDGET = {"low": (20, 0.06), "medium": (30, 0.12), "high": (40, 0.20)}
WRAP_UP = 0.8  # share of rounds or spend after which the agent is told to wrap up
# One reply's limit (Nov 17 2026: was 16k, and a reply cut off there failed the whole run, losing every change already
# made). Streamed, so a high cap is safe and costs only what is used. A cut-off reply is dropped and the agent told
# CUT_OFF; MAX_CUTS in a row end the run with what's done (settle), or an error when nothing was done.
EDIT_TOKENS = 32_000
MAX_CUTS = 2
CUT_OFF = ("Your last reply was too long and got cut off, so nothing in it was applied. Make the change in smaller steps: "
           "edit_text or edit_block instead of set_blocks, one section per call, and keep your thinking short.")
TOO_LONG = "That change was too big to make in one go. Try asking for one part at a time."
PAGE_CHARS = 24_000  # the page's full text is sent only when it fits
# The paper the page was made from goes in whole up to here (~15k tokens; Nov 18 2026). A longer paper keeps its first
# sections that fit, whole, and names the rest; search_source still searches all of it.
PAPER_CHARS = 60_000
PAPER_NOTE = ("The paper this page was made from, as extracted from its PDF (tables come out as lines of cells). It has more than "
              "the page and the short summary the page was built from, e.g. every row of a results table. Take facts and numbers "
              "from it, exactly as written. Never say the paper doesn't give something without looking here first.")
REPLY_CHARS = 2000
MESSAGE_CHARS = 6000  # an answer, question or new-topic reply travels as the generation's error (Text since migration 0012)
DAY_OVER = ("The page can't be changed today: the reader has made today's free explainers, and changes are back tomorrow. "
            "Answer instead, and tell the reader kindly in your reply.")
ONLY_ASKS = ("The reader's message asks, it doesn't ask for a change, so the page isn't changed now. Answer with what you found, "
             "and list in your reply each fix you'd make, so the reader can say yes to them.")
# Ideas for a next change under a reply (Nov 17 2026): the agent's own only (the review no longer sends any), at most 2, none
# already offered. 5 a reply, half from the review, made a new list under every reply, so "fix all" never ended.
MAX_SUGGESTIONS = 2
SUGGESTION_CHARS = 600
PROGRESS = {
    "get_section": "Reading a section", "get_playground": "Looking at a playground", "try_playground": "Trying a playground",
    "search_source": "Checking the source", "get_overview": "Reading another version", "get_change": "Looking at what a version changed",
    "search_page": "Searching the page", "start_from": "Going back to an earlier version", "set_page_meta": "Changing the page title",
    "set_level": "Changing the level", "rename_section": "Renaming a section", "edit_text": "Rewording", "set_blocks": "Rewriting a section",
    "add_block": "Adding to a section", "edit_block": "Changing part of a section", "remove_block": "Removing part of a section",
    "add_section": "Adding a section", "remove_section": "Removing a section", "move_section": "Moving a section",
    "patch_playground": "Adjusting a playground", "replace_playground": "Rebuilding a playground", "add_playground": "Building a playground",
    "remove_playground": "Removing a playground", "restore_section": "Bringing a section back", "restore_playground": "Bringing a playground back",
    "check_changes": "Checking the changes", "finish": "Checking the changes",
}


def budget(effort: str) -> tuple[int, float]:
    """(rounds, $ cap) for this effort; FOLLOWUP_MAX_USD, when set, overrides the cap."""
    rounds, cap = BUDGET.get(effort, BUDGET["medium"])
    try:
        cap = float(os.environ["FOLLOWUP_MAX_USD"]) if os.environ.get("FOLLOWUP_MAX_USD") else cap
    except ValueError:
        pass
    return rounds, cap


def _canon(st: Any) -> str:
    return json.dumps(st, sort_keys=True, ensure_ascii=False)


def _clip(s: Any, n: int) -> str:
    return js_slice(str(s or "").strip(), 0, n)


# ---------- what the agent reads first ----------


def main_text(client: Any, page: Page, usage: UsageLog) -> str:
    """The main file of the version the page is (saved per version), or one built now for the page; "" without the store."""
    store = page.store
    try:
        if store.on and store.base:
            main = mf.main_file_of(store.db, client, store.user_id, store.line, store.base, usage)
            if main is not None:
                return main["text"]
        if store.db and store.user_id:
            return mf.describe(store.db, client, store.user_id, page.current, version=store.base, quality=page.quality, usage=usage)["text"]
    except Exception:
        log.exception("Follow-up v5 agent: no main file")
    return ""


def reader_said(request: str, v5: dict) -> str:
    said = f"The reader's new message: {request}"
    if v5.get("chose"):
        said += f'\n\nThey were asked what they meant and chose: "{v5["chose"]}".'
    if v5.get("extra"):
        said += f" They added: {v5['extra']}"
    return said


def paper_text(sections: list[dict]) -> str:
    """The whole paper as text, one "## heading" per section (search_source searches this)."""
    return "\n\n".join(f"## {s['heading']}\n{s['text']}" if s["heading"] else s["text"] for s in sections)


def paper_part(sections: list[dict], limit: int = PAPER_CHARS) -> str:
    """The paper for the first message: whole up to `limit`; past it, the first sections that fit (whole, never cut inside
    one, so a table stays in one piece) and a note naming the ones left out. "" without a paper."""
    if not sections:
        return ""
    whole_text = paper_text(sections)
    if len(whole_text) <= limit:
        return PAPER_NOTE + "\n\n" + whole_text
    kept: list[dict] = []
    size = 0
    for s in sections:
        n = len(paper_text([s])) + 2
        if size + n > limit:
            break
        kept.append(s)
        size += n
    left = [s["heading"] or "(untitled)" for s in sections[len(kept):]]
    log.info("Follow-up v5 agent: paper of %d characters, %d of %d sections sent", len(whole_text), len(kept), len(sections))
    return (PAPER_NOTE + f" It is long, so only its first {len(kept)} of {len(sections)} sections are here; search_source searches "
            "all of it (left out here: " + ", ".join(f'"{_clip(h, 80)}"' for h in left) + ").\n\n" + paper_text(kept))


def first_message(client: Any, page: Page, usage: UsageLog, request: str, v5: dict, log_text: str, answer_only: bool,
                  only_asks: bool = False, paper: list[dict] | None = None) -> str:
    parts = [f"Mode: {page.quality}."]
    if page.store.base:
        parts.append(f"The page this message is under is version {page.store.base}.")
    main = main_text(client, page, usage)
    parts.append(("Main file of that version:\n" + main) if main else
                 ("The page:\n" + json.dumps(page.overview(), ensure_ascii=False)))
    if log_text:
        parts.append(log_text)
    if full_page():
        try:
            body = page_text(page.current)
        except Exception:
            body = ""
        if body and len(body) <= PAGE_CHARS:
            parts.append("The page's full text:\n" + body)
    if paper:
        parts.append(paper_part(paper))
    if v5.get("conversation"):
        parts.append(v5["conversation"])
    if v5.get("brief"):  # a plan made before the agent (an older record): what that step understood
        parts.append("An earlier step read this message as: " + v5["brief"])
    if v5.get("refs"):
        parts.append("Older versions that step said to read from: " + ", ".join(str(r) for r in v5["refs"]))
    if v5.get("base") and v5["base"] != page.store.base:
        parts.append(f"That step said to start from version {v5['base']} (start_from).")
    if answer_only:
        parts.append("Today the page can't be changed: the reader has made today's free explainers. Answer what you can, and say "
                     "kindly that changes are back tomorrow.")
    elif only_asks:
        parts.append("This message asks; it doesn't ask for a change. Don't change the page (every change tool is off): answer "
                     "from what you read, and list in your reply each fix you'd make, so the reader can say yes to them.")
    parts.append(reader_said(request, v5))
    return "\n\n".join(parts)


# ---------- the checks: feedback first, put-backs last ----------


class Gate:
    """The checks the agent gets as feedback (check_changes, the first finish after a change), and what the final guard needs
    from them: the sections as they were when checked, the ones that failed, and the review's problems."""

    def __init__(self, client: Any, usage: UsageLog, jev_key: str | None, page: Page, said: str, brief: str, effort: str = "medium"):
        self.client, self.usage, self.jev_key, self.page, self.said, self.brief = client, usage, jev_key, page, said, brief
        self.effort = effort  # the reader's Effort: the look check's (look.py)
        self.checked_at = -1  # len(page.log) when last checked; -1: never
        self.refused = False
        self.failed: set[str] = set()
        self.review_problems: list[dict] = []
        self.look_problems: list[dict] = []
        self.snapshot: dict[str, str] = {}

    def run(self) -> list[dict]:
        page = self.page
        found = editor.problems(page, self.usage, self.jev_key)
        rev: dict = {"problems": []}
        if review_mod.needs_review(page.before, page.current):
            rev = review_mod.review(self.client, self.usage, page, self.said, self.brief)
        # FOLLOWUP_V5_LOOK, Explorer pages: each playground the agent changed, screenshotted and judged (feedback only).
        self.look_problems = look_mod.problems(page, self.client, self.usage, self.effort) if look_on() and not page.lite else []
        self.failed = {p["section"] for p in found}
        self.review_problems = rev["problems"]
        self.snapshot = {s["id"]: _canon(s) for s in page.stages}
        self.checked_at = len(page.log)
        return [*found, *({**p, "kind": "review"} for p in rev["problems"]), *self.look_problems]

    def look_offers(self) -> list[str]:
        """A look problem the agent left alone (its section unchanged since the check): at most one, offered as a next change.
        Never a put-back and never a note: how a playground looks is a matter for the reader."""
        present = {s["id"]: s for s in self.page.stages}
        for p in self.look_problems:
            st = present.get(p["section"])
            if st is None or _canon(st) != self.snapshot.get(p["section"]):
                continue
            what = re.sub(r"^how it looks \(from screenshots\):\s*", "", p["what"])
            return [cut(f'Want me to fix how the playground in "{self.page.label(p["section"])}" looks? {what}', SUGGESTION_CHARS)]
        return []

    def text(self, found: list[dict]) -> str:
        return "\n".join(f'- {self.page.label(p["section"])} ({p["section"]}): {p["what"]}' for p in found)

    def changed_since(self) -> set[str]:
        """Sections changed after the checks ran (all changed ones when they never ran), plus those that failed then."""
        page = self.page
        if self.checked_at < 0:
            return set(page.changed_ids())
        return {s["id"] for s in page.stages if s["id"] in page.changed_ids() and _canon(s) != self.snapshot.get(s["id"])} | self.failed

    def guard(self) -> list[str]:
        """The final guard: Jev's numbers check again on what changed since the checks (a section whose text the change made
        contradict its playground goes back). A review problem the agent left alone becomes a note for the reply, never a
        put-back (Nov 17 2026: putting its cause back undid the very fix the reader asked for, e.g. a new table whose
        playground title matched another section's name). Returns notes for the reply."""
        page = self.page
        if self.checked_at < 0 and review_mod.needs_review(page.before, page.current):
            rev = review_mod.review(self.client, self.usage, page, self.said, self.brief)
            self.review_problems = rev["problems"]
            self.snapshot = {s["id"]: _canon(s) for s in page.stages}
        problems = self.review_problems
        only = self.changed_since()
        notes = editor.check_changes(page, self.usage, self.jev_key, only=only) if only else []
        present = {s["id"]: s for s in page.stages}
        for p in problems:
            st = present.get(p["section"])
            if st is not None and _canon(st) != self.snapshot.get(p["section"]):
                continue  # the agent changed that section after the check: taken as fixed (Jev checked it above)
            log.info("Follow-up v5 guard: %s is still wrong (%s); told in the reply", p["section"], p["what"])
            notes.append(f'Still to fix in "{page.label(p["section"])}": {p["what"]}')
        return notes


# ---------- the loop ----------


def _call(client: anthropic.Anthropic, usage: UsageLog, system: str, tools: list[dict], messages: list[dict], effort: str, think: bool,
          final: bool) -> Any:
    params: dict = dict(model=HAIKU, max_tokens=EDIT_TOKENS, output_config={"effort": effort}, system=cached_system(system), tools=tools,
                        tool_choice={"type": "none"} if final else {"type": "auto"}, messages=messages)
    if not think:
        params["thinking"] = {"type": "disabled"}
    with client.messages.stream(**params) as s:
        msg = s.get_final_message()
    usage.add_message("follow-up agent", msg)
    return msg


def _block(b: Any) -> dict:
    return b.to_dict(exclude_none=True) if hasattr(b, "to_dict") else dict(b)


def _suggestions(raw: Any) -> list[str]:
    """finish's suggestions, each cut at a sentence; capped at MAX_SUGGESTIONS in settle, after the ones offered before go."""
    return [cut(x, SUGGESTION_CHARS) for x in raw if isinstance(x, str) and x.strip()][:8] if isinstance(raw, list) else []


def _words(s: str) -> str:
    return " ".join(re.findall(r"\w+", str(s or "").lower()))


def fresh(suggestions: list[str], offered: list[str]) -> list[str]:
    """The suggestions not offered before (word for word in an earlier reply or the version log), without repeats."""
    seen = [f" {w} " for w in (_words(o) for o in offered) if w]
    out: list[str] = []
    for s in suggestions:
        w = f" {_words(s)} "
        if w.strip() and not any(w in o for o in seen):
            out.append(s)
            seen.append(w)
    return out


def end_tool(page: Page, gate: Gate, name: str, inp: Any, over: bool) -> dict:
    """check_changes, new_topic and finish (the rest is tools.run_tool). over: past the spend cap, no checks at finish."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "check_changes":
        if not page.did_anything():
            return {"content": "You haven't changed anything yet.", "is_error": False}
        found = gate.run()
        return {"content": ("The checks found:\n" + gate.text(found)) if found else "The checks found no problems.", "is_error": False}
    if name == "new_topic":
        if page.writes:
            return {"content": "You already changed this page: finish instead, and say it in your reply.", "is_error": True}
        reply = _clip(inp.get("reply"), REPLY_CHARS)
        if not reply:
            return {"content": "Write the reply the reader sees.", "is_error": True}
        page.new_topic, page.finished = reply, True
        page.new_topic_brief = topic.clip(inp.get("brief"), topic.BRIEF_CHARS)
        return {"content": "Done.", "is_error": False}
    reply = _clip(inp.get("reply"), REPLY_CHARS)
    if not reply:
        return {"content": "Write the reply the reader sees in `reply`.", "is_error": True}
    if page.did_anything() and not gate.refused and gate.checked_at != len(page.log) and not over:
        found = gate.run()
        if found:
            gate.refused = True
            return {"content": "Not finished yet: the checks found these problems in what you changed.\n" + gate.text(found)
                    + "\nFix them now. Text left disagreeing with its playground makes that change go back as it was; any other "
                    "problem you leave, say plainly in your reply.", "is_error": True}
    page.reply, page.suggestions, page.finished = reply, _suggestions(inp.get("suggestions")), True
    page.offers_fixes = inp.get("offers_fixes") is True
    return {"content": "Done.", "is_error": False}


def run(client: anthropic.Anthropic, usage: UsageLog, jev_key: str | None, follow: dict, base: dict, plan: dict, request: str,
        effort: str, think: bool, quality: str, progress: Callable[[str], None] | None = None, store: Store | None = None,
        log_text: str = "", answer_only: bool = False, paper: list[dict] | None = None) -> dict:
    """{"outline", "warnings", "revision", "pageFields", "startFrom"?, "follow"?} for a change; raises Checked (an answer),
    editor.Asked or NewTopic otherwise. paper: the sections of the paper the page was made from (base.paper_of; [] or None
    for a concept page): it goes in the first message, and search_source searches it instead of the summary."""
    page_outline = restore_kept_widgets(base, follow["widgets"]) if follow.get("widgets") else base
    page = Page(page_outline, quality, paper_text(paper) if paper else follow.get("source") or "", store)
    page.look_theme = look_theme_of(follow)  # look.py's screenshots in the page's own colours
    v5 = plan.get("v5") or {}
    run_effort = "low" if page.lite else effort
    think = think and not page.lite
    rounds, cap = budget(run_effort)
    only_asks = v5.get("intent") == "answer"  # intent.py: Jev found the message only asks
    ctx = T.Ctx(client, usage, jev_key, run_effort, think, DAY_OVER if answer_only else ONLY_ASKS if only_asks else "")
    said = reader_said(request, v5)
    brief = said + (f"\n\nWhat the reader was told just before: {v5['earlier']}" if v5.get("earlier") else "")
    gate = Gate(client, usage, jev_key, page, said, brief, run_effort)
    system = data.prompt("FOLLOWUP_V5_AGENT_LITE_SYSTEM" if page.lite else "FOLLOWUP_V5_AGENT_SYSTEM")
    tools = T.definitions(page.lite)
    if progress:
        progress("Reading your message…")
    first = first_message(client, page, usage, request, v5, log_text, answer_only, only_asks, paper)
    messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": first, "cache_control": {"type": "ephemeral"}}]}]
    warned = False
    cut = 0  # replies cut off at EDIT_TOKENS in a row
    for rnd in range(rounds + 1):
        final = rnd == rounds or usage.summary()["totalUsd"] > cap
        msg = _call(client, usage, system, tools, messages, run_effort, think, final)
        if msg.stop_reason == "max_tokens":
            # Cut off: its last tool call is incomplete, so nothing in it is applied (it isn't sent back either). The agent
            # is told to work in smaller steps; a second cut in a row ends the run with what's done, never losing it.
            cut += 1
            log.warning("Follow-up v5 agent: reply cut off at %d tokens (%d in a row)", EDIT_TOKENS, cut)
            if cut >= MAX_CUTS or final:
                page.cut_off = True
                break
            messages[-1]["content"].append({"type": "text", "text": CUT_OFF})
            continue
        cut = 0
        check_stop(msg, "follow-up")  # a refusal
        calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if not calls:
            page.reply = page.reply or _clip(text_of(msg), REPLY_CHARS)
            break
        messages.append({"role": "assistant", "content": [_block(b) for b in msg.content]})
        results: list[dict] = []
        for c in calls:
            if page.finished:  # an end tool came earlier in this reply: the rest is not applied
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": "Not applied: your turn already ended.", "is_error": True})
                continue
            if progress and c.name in PROGRESS:
                progress(PROGRESS[c.name] + "…")
            if c.name in T.END_TOOLS:
                r = end_tool(page, gate, c.name, c.input, usage.summary()["totalUsd"] > cap)
            else:
                r = T.run_tool(page, c.name, c.input, ctx)
            log.info("Follow-up v5 agent %s: %s%s", c.name, "refused: " if r["is_error"] else "", look_mod.text_of(r["content"])[:160])
            results.append({"type": "tool_result", "tool_use_id": c.id, **r})
        if page.finished:
            break
        if look_mod.has_images(results):  # only the newest screenshots stay: every round resends the whole conversation
            look_mod.strip_old_images(messages)
        spent = usage.summary()["totalUsd"]
        if rnd + 1 == rounds or spent > cap:
            results.append({"type": "text", "text": "That was your last step for this message. Reply now with what you did, anything left "
                                                    "undone and why, as the reader will read it."})
        elif not warned and (rnd + 1 >= WRAP_UP * rounds or spent > WRAP_UP * cap):
            warned = True
            results.append({"type": "text", "text": "You are close to the limit for this message: wrap up and finish soon with what's done."})
        messages.append({"role": "user", "content": results})
    offered = [v5.get("conversation") or "", v5.get("earlier") or "", *page.store.offered()]
    return settle(client, usage, page, gate, said, progress, offered, can_offer=only_asks and not answer_only)


def settle(client: Any, usage: UsageLog, page: Page, gate: Gate, said: str, progress: Callable[[str], None] | None,
           offered: list[str] | None = None, can_offer: bool = False) -> dict:
    """The run's end: the final guard and the reply for a change, or the right exception for anything else. offered: what
    the reader was offered before (the conversation, the version log's suggestions), so no idea is offered twice.
    can_offer: the message only asked (intent "answer", not past the day's explainers), so an answer that lists fixes
    (finish's offers_fixes) comes with a "Yes, make these fixes" button (OFFER)."""
    if page.asked and not page.writes:
        raise editor.Asked(editor.asked_message(page.asked))
    if page.new_topic and not page.writes:
        if page.new_topic_brief:  # the button builds from the brief, not the reader's raw message
            raise NewTopic(topic.new_topic_message(page.new_topic, page.new_topic_brief))
        raise NewTopic(NEW_TOPIC + _clip(page.new_topic, MESSAGE_CHARS))
    if page.cut_off and not page.writes:  # cut off before changing anything: nothing to keep
        raise ReaderError(TOO_LONG)
    notes: list[str] = []
    undone = sum(e["status"] != "done" for e in page.log)
    if page.did_anything():
        if progress:
            progress("Checking the changes…")
        notes = gate.guard()
    if page.cut_off:  # the changes made before it stay (checked above); the reply says the rest wasn't finished
        notes.append("The last part was too big to finish in one go, so it wasn't done: ask for it again, one part at a time.")
    looks = gate.look_offers() if page.did_anything() else []  # a look problem left alone: offered first (look.py)
    suggestions = fresh([*looks, *page.suggestions], offered or [])[:MAX_SUGGESTIONS]
    put_back = sum(e["status"] != "done" for e in page.log) > undone
    went_back = page.started_from is not None and not page.writes
    # When anything was put back, the reply is written from the log alone: the agent's own reply claims what it did before
    # the put-back ("I've reverted the chart"), and the writer repeated it (Nov 17 2026).
    if not page.did_anything() and not went_back:
        if page.writes:  # every change was put back: a calm reply from the log
            raise Checked(CHECKED + _clip(reply_mod.write(client, usage, said, "", page.log, notes, suggestions), MESSAGE_CHARS))
        text = page.reply or "I looked at it, but I'm not sure what you'd like changed. Could you say a bit more?"
        prefix = OFFER if can_offer and page.offers_fixes and page.reply else CHECKED
        raise Checked(prefix + _clip(reply_mod.with_suggestions(text, suggestions), MESSAGE_CHARS))
    if page.reply and not put_back and not notes:
        text = reply_mod.with_suggestions(page.reply, suggestions)
    else:
        text = reply_mod.write(client, usage, said, "" if put_back else page.reply, page.log, notes, suggestions)
    revision = page.revision()
    if went_back:
        revision["steps"] = [{"label": f"Went back to version {page.started_from}", "status": "done"}]
    revision = {**revision, "reply": text, **({"suggestions": suggestions} if suggestions else {})}
    out: dict = {"outline": page.current, "warnings": [], "revision": revision, "pageFields": page.page_fields}
    if page.started_from is not None:
        out["startFrom"], out["follow"] = page.started_from, page.follow
    return out
