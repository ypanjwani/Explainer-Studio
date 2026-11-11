"""Follow-ups v3, steps 2-3 (section_editor.py): the evidence gathered before editing, the editor's decision (text, playground,
both, nothing) cleaned and carried out with the existing tools. Haiku, Jev and the playground builds are faked."""

from __future__ import annotations

import json

from explainer.modes.followup import playground_edit as ce, section_editor as se
from explainer.pricing import UsageLog
from followup_v3_data import OLD, PAGE, PG, edited, editor_reply, index_of, intent, playground, stage_of


def test_the_editor_sees_the_section_its_playground_and_what_was_found(monkeypatch):
    monkeypatch.setattr(se, "check_playground", lambda spec: {"problems": ["c1 stays empty"], "layout": ["a label is cut"], "facts": None})
    monkeypatch.setattr(ce, "agrees", lambda *a: "a control sets something the text never mentions")
    monkeypatch.setattr(ce, "facts_of", lambda b: {"controls": []})
    ev = se.evidence(PAGE, "sampling", index_of(PAGE, sampling={"playgroundLevel": "advanced"}), "jev", UsageLog("x"))
    pg = ev["playground"]
    assert pg["spec"]["components"][0]["label"] == "temperature" and pg["checker"] == ["c1 stays empty", "a label is cut"]
    assert pg["agreement"].startswith("a control") and pg["level"] == "advanced" and pg["kind"] == "playground"
    assert {"type": "text", "md": "(interactive widget: Temperature explorer)"} in ev["section"]["blocks"]  # no html in the section
    assert [n["id"] for n in ev["neighbours"]] == ["attention", "loop"]
    assert se.evidence(PAGE, "tokens", index_of(PAGE), None, UsageLog("x"))["playground"] is None


def test_the_editors_decision_is_cleaned():
    st = edited("tokens")
    assert se.clean_result(editor_reply("none", "Looks right.", st, "edit", "x"), "playground") == editor_reply("none", "Looks right.", None, "keep", "")
    assert se.clean_result(editor_reply("change", "r", None, "edit", ""), "playground")["decision"] == "none"  # nothing to do
    assert se.clean_result(editor_reply("change", "r", st, "edit", "add a slider"), "lite-plus")["playground"]["action"] == "keep"  # Quick read
    out = se.clean_result(editor_reply("change", "r", None, "edit", "fix c1's range"), "playground")
    assert out["decision"] == "change" and out["playground"] == {"action": "edit", "instructions": "fix c1's range"}
    assert se.clean_result("not a dict", "playground")["decision"] == "none"


def test_one_editor_call_with_thinking_gets_everything(monkeypatch):
    seen: dict = {}

    def write_text(client, usage, step, *, system, user, max_tokens, effort, stream, thinking, **k):
        seen.update(system=system, user=user, thinking=thinking, step=step)
        return "Here you go: " + json.dumps(editor_reply("none", "Its playground and text agree.", None))

    monkeypatch.setattr(se, "write_text", write_text)
    ev = {"section": stage_of(PAGE, "sampling"), "playground": {"checker": [], "agreement": "agree"}, "neighbours": []}
    out = se.edit_section(None, UsageLog("x"), "PLANNER", PAGE, "sampling", intent("sampling", "check it, fix if wrong", because="b"), "check the last one",
                          "playground", ev, "medium")
    assert out["decision"] == "none" and out["reason"] == "Its playground and text agree."
    assert seen["thinking"] is True and seen["system"] == "PLANNER" and seen["step"] == "follow-up editor"
    user = json.loads(seen["user"].split("\n\n# This time")[0])
    assert user["intent"] == "check it, fix if wrong" and user["because"] == "b" and user["playground"] == {"checker": [], "agreement": "agree"}
    assert "Decide what this section needs" in seen["user"] and se.PLANNER_NOTE in seen["user"]
    monkeypatch.setattr(se, "write_text", lambda *a, **k: "no json at all")
    assert "failed" in se.edit_section(None, UsageLog("x"), "P", PAGE, "sampling", intent("sampling"), "x", "playground", ev)


def test_a_quick_read_editor_gets_the_reader_rules(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(se, "write_text", lambda *a, user, **k: seen.update(user=user) or json.dumps(editor_reply("none")))
    se.edit_section(None, UsageLog("x"), "P", OLD, "tokens", intent("tokens"), "x", "lite-plus", {"section": {}, "playground": None})
    assert "Quick read page" in seen["user"] and "Keep each stage short" in seen["user"]


def test_new_text_replaces_only_its_section_and_keeps_the_playground():
    st = edited("sampling", "Sampling, said more clearly.")
    out, changed, warnings = se.apply_text(PAGE, "sampling", editor_reply(stage=st), prose=False)
    assert changed and stage_of(out, "sampling")["blocks"][0]["md"] == "Sampling, said more clearly." and PG in stage_of(out, "sampling")["blocks"]
    assert [s for s in out["stages"] if s["id"] != "sampling"] == [s for s in PAGE["stages"] if s["id"] != "sampling"]
    out, changed, warnings = se.apply_text(PAGE, "tokens", editor_reply(stage={"id": "tokens", "blocks": "nope"}), prose=False)
    assert not changed and out == PAGE and warnings


def test_a_playground_page_keeps_explanation_only():
    slider = next(b for s in OLD["stages"] for b in s["blocks"] if b["type"] == "slider")
    st = edited("tokens")
    st["blocks"].append(slider)
    out, changed, _ = se.apply_text(PAGE, "tokens", editor_reply(stage=st), prose=True)
    assert changed and "slider" not in [b["type"] for b in stage_of(out, "tokens")["blocks"]]


def test_playgrounds_are_removed_edited_and_added_as_the_editor_decided(monkeypatch):
    calls: dict = {"edit": [], "add": []}

    def edit_widget(client, usage, jev_key, outline, si, bi, what, effort="medium", think=True):
        calls["edit"].append((outline["stages"][si]["id"], what))
        old = outline["stages"][si]["blocks"][bi]
        return {"block": {**old, "html": old["html"] + "<!-- edited -->"}, "changed": True, "facts": None, "plan": None, "notes": []}

    def add_widget(client, usage, jev_key, outline, si, what, kind, effort="medium", think=True):
        calls["add"].append((outline["stages"][si]["id"], what, kind))
        stages = list(outline["stages"])
        stages[si] = {**stages[si], "blocks": [*stages[si]["blocks"], playground("New one")]}
        return {"outline": {**outline, "stages": stages}, "built": True, "facts": None, "plan": None, "notes": []}

    monkeypatch.setattr(ce, "edit_widget", edit_widget)
    monkeypatch.setattr(ce, "add_widget", add_widget)
    page = {**PAGE, "stages": [{**s, "blocks": [*s["blocks"], playground("Loop demo")]} if s["id"] == "loop" else s for s in PAGE["stages"]]}
    results = {"sampling": editor_reply(action="edit", instructions="fix c1's range"), "loop": editor_reply(action="remove"),
               "tokens": editor_reply(action="add", instructions="a slider for vocabulary size")}
    out, changed, notes = se.apply_playgrounds(None, UsageLog("x"), "jev", page, results, "playground", "medium", True)
    assert calls["edit"] == [("sampling", "fix c1's range")] and calls["add"] == [("tokens", "a slider for vocabulary size", "playground")]
    assert stage_of(out, "sampling")["blocks"][-1]["html"].endswith("<!-- edited -->")
    assert not any(b.get("type") == "widget" for b in stage_of(out, "loop")["blocks"]) and changed == {"sampling", "loop", "tokens"}
