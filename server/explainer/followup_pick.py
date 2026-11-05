"""Which sections a typed follow-up on a Playground or Quick read page is about (POST /api/followup/pick), decided before
anything is rebuilt, so an unclear request can be asked about first.

1. Code builds a short card of every section from the page's JSON (feedback_pick.section_cards: no AI call).
2. Jev, one call, narrows down WHICH sections: the request's scope (particular sections / the whole page / a new section /
   unclear) and one yes/no per section.
3. Particular sections, Jev sure -> one small Haiku call (targets.find_targets_ask) that sees ONLY those sections and writes
   each one's own change: playground or text, and what to do there. (Jev only answers questions, so it can't write the
   per-section instruction, and readers call a playground many names.) The whole page, Jev sure -> no Haiku call.
4. Jev unsure, a new section, unclear, a Jev error, or nothing usable from step 3 -> the same Haiku finder on the whole page,
   which may instead return a question with 2-4 options for the reader.

The answer is {"targets", "pageWide"}, {"question"} or {"error"}. The app sends the chosen targets back with the revise
follow-up (FollowUp.pick) and the pipeline changes only those (targets.apply_targets). A change of the whole page (pageWide)
becomes a "text" target on every section in the pipeline, and the title, key intuition and level may change too, so these
pages never take the old whole-page path through the Interactive planner.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from . import data
from .feedback_pick import cards_text, section_cards
from .followup import parse_follow_up
from .jev import JevError, jev_ask
from .js import js_len, js_trim, json_parse
from .pricing import UsageLog
from .revise import restore_kept_widgets
from .targets import find_targets_ask

log = logging.getLogger("explainer.followup_pick")

MODES = ("playground", "lite-plus")  # the pages whose typed follow-ups are picked here (not Playground 2.0)
SCOPES = {
    "some": "reader_request asks to change one or a few particular sections, or the playground, wording, chart, "
            "formula or example of particular sections",
    "every": "reader_request asks for one clear change across the whole page: the reader level or audience, the tone or "
             "style throughout, the language, or making every section simpler, more detailed or more formal",
    "new": "reader_request asks to add a new section or topic that the page doesn't have",
    "unclear": "reader_request doesn't say what should change, or it could reasonably mean quite different changes "
               "(for example: shorten the wording, or drop whole sections?)",
}
PICK_SURE = 0.6  # the scope Jev picks must be at least this likely
SECTION_IN = 0.7  # a section is in at or above this
SECTION_OUT = 0.3  # and out at or below this; anything between means Jev isn't sure
SCOPED_PARTS = ("widget", "text", "section", "remove")  # what the scoped Haiku call may do to a picked section


def _noul(instructions: str, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


def jev_state(page: dict, text: str, cards: list[dict]) -> dict:
    return {"explainer": page.get("title") or "", "reader_request": text, "sections": cards_text(cards)}


def jev_questions(cards: list[dict]) -> dict[str, dict]:
    q: dict[str, dict] = {"scope": {"type": "choice", "criteria": SCOPES, "instructions": (
        "The reader typed reader_request under a finished explainer page whose sections are listed in `sections`. "
        "What kind of change does reader_request ask for?")}}
    for c in cards:
        n, label = c["number"], c["label"]
        q[f"s{n}"] = _noul(
            f'reader_request is about Section {n} "{label}": it names or describes this section, its wording, its playground '
            "(interactive widget) or one of its other parts.",
            f"reader_request asks to change something in Section {n} (its text, formula, example, chart or playground).",
            "reader_request is about other sections, or about none in particular.")
    return q


def decide(answers: dict[str, dict], cards: list[dict]) -> dict | None:
    """{"stages": [picked ids]} or {"pageWide": True} when Jev is sure, else None (the Haiku finder then decides)."""
    scope = answers.get("scope")
    if not scope or not scope["ranking"]:
        return None
    top = scope["ranking"][0]
    if top["option"] not in ("some", "every") or top["p"] < PICK_SURE:
        return None
    if top["option"] == "every":
        return {"pageWide": True}
    picked: list[str] = []
    for c in cards:
        a = answers.get(f"s{c['number']}")
        if not a or SECTION_OUT < a["p"] < SECTION_IN:
            return None
        if a["p"] >= SECTION_IN:
            picked.append(c["id"])
    return {"stages": picked} if picked else None


def _scores(answers: dict[str, dict], cards: list[dict]) -> str:
    """Jev's answers for the log: the scope ranking and every section's score."""
    scope = ", ".join("%s %.2f" % (x["option"], x["p"]) for x in (answers.get("scope") or {}).get("ranking", []))
    found = [(c["id"], answers.get("s%d" % c["number"])) for c in cards]
    return "scope [%s]; sections [%s]" % (scope, ", ".join("%s %.2f" % (sid, a["p"]) for sid, a in found if a))


def jev_pick(jev_key: str | None, page: dict, text: str, usage: UsageLog) -> dict | None:
    """Jev's narrowing ({"stages"} or {"pageWide"}), or None when Jev isn't sure, can't be asked or fails."""
    if not jev_key or not page["stages"]:
        return None
    cards = section_cards(page)
    try:
        r = jev_ask(jev_key, jev_state(page, text, cards), jev_questions(cards))
    except JevError:
        log.exception("Follow-up pick: Jev failed; the Haiku finder decides")
        return None
    usage.add("jev follow-up pick", r["model"], {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": 0})
    out = decide(r["answers"], cards)
    log.info("Follow-up pick (Jev): %s -> %s", _scores(r["answers"], cards), out or "not sure")
    return out


def only_stages(page: dict, ids: list[str]) -> dict:
    """The page with just the picked stages (ids unchanged), for the scoped Haiku call."""
    return {**page, "stages": [st for st in page["stages"] if st["id"] in ids]}


def scoped_targets(client: anthropic.Anthropic, page: dict, ids: list[str], text: str, usage: UsageLog) -> dict | None:
    """Haiku, shown only the sections Jev picked: each one's own change ({"targets"}), or a question. None when unusable."""
    found = find_targets_ask(client, usage, only_stages(page, ids), text)
    if found and found.get("question"):
        return {"question": found["question"]}
    targets = [t for t in (found or {}).get("targets") or [] if t["part"] in SCOPED_PARTS and t["stage"] in ids]
    log.info("Follow-up pick (Haiku on %s): %s", ids, targets or "nothing usable")
    return {"targets": targets} if targets else None


def pick(client: anthropic.Anthropic, jev_key: str | None, page: dict, text: str, usage: UsageLog) -> dict:
    """{"targets", "pageWide", "by"} | {"question", "by"} | {"error"}: Jev narrows and Haiku writes each picked section's
    change; when Jev isn't sure (or that gives nothing usable), Haiku decides on the whole page."""
    jev = jev_pick(jev_key, page, text, usage)
    if jev and jev.get("pageWide"):
        return {"targets": [], "pageWide": True, "by": "jev"}
    if jev:
        scoped = scoped_targets(client, page, jev["stages"], text, usage)
        if scoped and scoped.get("question"):
            return {"question": scoped["question"], "by": "jev+haiku"}
        if scoped:
            return {"targets": scoped["targets"], "pageWide": False, "by": "jev+haiku"}
    found = find_targets_ask(client, usage, page, text)
    if found and found.get("question"):
        return {"question": found["question"], "by": "haiku"}
    if found and (found.get("pageWide") or found.get("targets")):
        return {"targets": found["targets"], "pageWide": found["pageWide"], "by": "haiku"}
    return {"error": "I couldn't tell which part of the page you mean. Name the section or the playground and try again."}


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


def handle(client: anthropic.Anthropic, jev_key: str | None, req: dict) -> dict:
    """The /api/followup/pick reply: pick() plus its cost ("usd")."""
    usage = UsageLog("followup-pick", "low")
    out = pick(client, jev_key, req["page"], req["text"], usage)
    return {**out, "usd": usage.summary()["totalUsd"]}
