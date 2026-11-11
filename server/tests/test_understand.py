"""Follow-ups v3, step 1 (understand.py): the follow-up as sections with the reader's intent and structural steps, a question,
a refusal, an answer or an undo; knock-on intents from the page map; the budget check; the route. Haiku is faked."""

from __future__ import annotations

from explainer.modes.followup import understand as U
from explainer.core.validate import default_design
from explainer.followup import parse_follow_up, parse_plan
from explainer.pricing import UsageLog
from followup_v3_data import FOLLOW, IDS, OLD, PAGE, Store, fake_client, index_of, intent, step, understood


def plan_of(reply, text="x", quality="playground", **kw):
    return U.plan(fake_client(reply) if not isinstance(reply, list) else fake_client(*reply), Store(index_of(PAGE)), PAGE, text, quality, UsageLog("x"), **kw)


# ---------- shortcuts and cleaning ----------


def test_undo_and_remove_section_need_no_ai_call():
    assert U.shortcuts("Undo that", PAGE) == {"kind": "revert"}
    assert U.shortcuts("go back to the previous version", PAGE) == {"kind": "revert"}
    out = U.shortcuts("remove section 2", PAGE)
    assert out["plan"]["steps"][0] == {**step("remove_section", IDS[1], "Remove this section."), "quote": "remove section 2"}
    assert U.shortcuts("remove section 99", PAGE) is None and U.shortcuts("undo the tokens part and add more", PAGE) is None
    out = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "undo", "playground", UsageLog("x"))
    assert out == {"kind": "revert"}


def test_intents_and_steps_are_checked_against_the_page():
    raw = {"sections": [intent("tokens", "simpler", "simpler"), intent("ghost"), intent("loop", ""), intent("tokens", "shorter too", "shorter")],
           "steps": [step("remove_section", "ghost"), step("move_section", "loop", after="loop"), step("set_level", level="expert"),
                     step("set_level", level="advanced", what=""), step("add_section", after="nowhere", what="a new part")]}
    out = U.clean_plan(raw, PAGE)
    assert out["sections"] == [intent("tokens", "simpler Also: shorter too", "simpler")]
    assert [(s["op"], s["after"]) for s in out["steps"]] == [("set_level", "")]  # "nowhere" names no section: plan() asks
    out = U.clean_plan({"sections": [], "steps": [step("add_section", what="a new part")]}, PAGE)
    assert out["steps"][0]["after"] == IDS[-1]  # no place given: the end of the page


def test_a_removed_section_keeps_no_intent_and_undo_stands_alone():
    out = U.clean_plan({"sections": [intent("loop"), intent("tokens")], "steps": [step("remove_section", "loop")]}, PAGE)
    assert [i["section"] for i in out["sections"]] == ["tokens"]
    out = U.clean_plan({"sections": [intent("tokens")], "steps": [step("revert"), step("remove_section", "loop")]}, PAGE)
    assert out == {"sections": [], "steps": [step("revert", what="change it")]}


def test_a_part_not_in_the_readers_words_is_kept_and_flagged_never_dropped():
    said = "a simpler tokens example please"
    out = U.flag({"sections": [intent("tokens", quote="simpler tokens example"), intent("sampling", quote="add confetti everywhere")], "steps": []}, PAGE, said)
    assert [(i["section"], i.get("unsure", "")) for i in out["sections"]] == [("tokens", ""), ("sampling", U.NOT_QUOTED)]
    plan = U.clean_plan(out, PAGE)
    assert plan["sections"][1]["unsure"] == U.NOT_QUOTED and U.needs_confirm(plan, PAGE)
    assert U.summary(plan, PAGE)[1].endswith(f"({U.NOT_QUOTED})")


def test_the_quote_check_accepts_rewording_other_scripts_and_earlier_messages():
    assert U.quote_ok("make section 4 simpler", "fix the chart in section 2 and also simplify section 4")
    assert U.quote_ok("the fourth section", "shorten section 4")
    assert U.quote_ok("आसान बनाओ", "सेक्शन 2 को आसान बनाओ")
    assert not U.quote_ok("add confetti everywhere", "simplify section 4")
    said = 'do the same for section 5 "add a worked example to section 3": made version 2'
    out = U.flag({"sections": [intent("loop", quote="add a worked example")], "steps": []}, PAGE, said)
    assert "unsure" not in out["sections"][0]


def test_a_quote_naming_another_section_is_flagged():
    said = "simplify section 4"
    out = U.flag({"sections": [intent("tokens", quote="simplify section 4")], "steps": []}, PAGE, said)
    assert out["sections"][0]["unsure"] == "you mentioned section 4: check this is the one you meant"
    out = U.flag({"sections": [intent(IDS[3], quote="simplify section 4")], "steps": []}, PAGE, said)
    assert "unsure" not in out["sections"][0]
    # "before section 5" is after = section 4
    out = U.flag({"sections": [], "steps": [step("move_section", "tokens", after=IDS[3], quote="put section 1 before section 5")]}, PAGE,
                 "put section 1 before section 5")
    assert "unsure" not in out["steps"][0]


# ---------- section references ----------


def test_every_way_of_writing_a_section_becomes_its_id():
    for raw in ("S3", "s3", "3", "section 3", "Section three", "third", "the third section", "3rd", "attention", "Attention mixes context", IDS[2]):
        assert U.resolve_ref(raw, PAGE)[0] == IDS[2], raw
    assert U.resolve_ref("end", PAGE, "after")[0] == IDS[-1] and U.resolve_ref("the start", PAGE, "after")[0] == "start"
    assert U.resolve_ref("S9", PAGE) == (None, [], "unknown") and U.resolve_ref("section 99", PAGE)[0] is None
    assert U.resolve_ref("the last playground", PAGE)[0] == "sampling"


def test_a_handle_wins_over_a_look_alike_id_only_when_haiku_wrote_it():
    page = {"stages": [{"id": "s3", "label": "Alpha", "blocks": []}, {"id": "b", "label": "Beta", "blocks": []}, {"id": "c", "label": "Gamma", "blocks": []}]}
    assert U.resolve_ref("s3", page, from_model=True)[0] == "c"  # Haiku reads handles: S3 is section 3
    assert U.resolve_ref("s3", page, from_model=False)[0] == "s3"  # the app sends ids back


def test_a_reference_that_fits_two_sections_is_asked_with_code_built_options():
    sid, cands, _ = U.resolve_ref("math lab", PAGE)
    assert sid is None and cands == ["uncertainty", "optimization"]
    sid, cands, _ = U.resolve_ref("the last one", PAGE)
    assert sid is None and cands == ["summary", "sampling"]  # the last section, the last playground
    out = plan_of(understood(sections=[intent("Math lab", "simpler", "the math lab"), intent("S1", "shorter", "shorten the first one")]),
                  "make the math lab simpler and shorten the first one")
    assert out["kind"] == "ask" and out["question"]["text"] == 'Which section did you mean for "the math lab"?'
    opts = out["question"]["options"]
    assert [o["label"] for o in opts] == ['Section 6 "Math lab: uncertainty"', 'Section 7 "Math lab: optimization"']
    again = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "x", "playground", UsageLog("x"), option=opts[1])
    assert again["kind"] == "change" and sorted(i["section"] for i in again["sections"]) == ["optimization", "tokens"]


def test_each_unplaced_part_is_asked_in_turn_and_a_hopeless_one_can_be_skipped():
    reply = understood(sections=[intent("Math lab", quote="the math lab"), intent("the last one", quote="fix the last one"), intent("S1", quote="x")])
    out = plan_of(reply, "the math lab, fix the last one, x")
    first = out["question"]["options"][0]
    again = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "x", "playground", UsageLog("x"), option=first)
    assert again["kind"] == "ask" and "fix the last one" in again["question"]["text"]
    out = plan_of(understood(sections=[intent("ghost", quote="the ghost part"), intent("S1", quote="x")]), "the ghost part and x")
    assert out["kind"] == "ask" and [o["label"] for o in out["question"]["options"]] == ["Skip that part, do the rest"]
    rest = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "x", "playground", UsageLog("x"), option=out["question"]["options"][0])
    assert rest["kind"] == "change" and [i["section"] for i in rest["sections"]] == ["tokens"]
    out = plan_of(understood(sections=[intent("ghost", quote="the ghost part")]), "the ghost part")
    assert out == {"error": 'I couldn\'t tell which section you meant by "the ghost part". Name it, for example "section 3".'}


def test_a_section_written_as_a_number_or_label_is_fixed_and_reported():
    reply = understood(sections=[intent("section 4", "make it work", "the demo shows nothing")],
                       steps=[step("move_section", "S2", after="end", quote="move the vectors part to the end")])
    out = plan_of(reply, "the demo shows nothing, and move the vectors part to the end")
    assert out["kind"] == "change" and out["sections"][0]["section"] == "sampling" and out["steps"][0]["after"] == IDS[-1]
    assert out["fixes"] == ['section "section 4" -> sampling (number)', 'after "end" -> summary (end)']


# ---------- knock-ons, confirm, budget ----------


def test_a_removed_section_gives_intents_to_the_sections_that_mention_it_or_need_its_ideas():
    idx = index_of(PAGE, attention={"refersTo": ["embeddings"]}, embeddings={"introduces": ["embedding vector"]}, summary={"uses": ["Embedding vector"]})
    out = U.knock_ons({"sections": [], "steps": [step("remove_section", "embeddings")]}, idx, PAGE)
    assert [(i["section"], i["derived"]) for i in out] == [("attention", True), ("summary", True)]
    assert "Tokens become vectors" in out[0]["intent"] and "Embedding vector" in out[1]["intent"]
    out = U.knock_ons({"sections": [intent("attention")], "steps": [step("remove_section", "embeddings")]}, idx, PAGE)
    assert [i["section"] for i in out] == ["summary"]  # the reader already named attention


def test_a_move_fixes_mentions_that_now_point_the_wrong_way():
    idx = index_of(PAGE, attention={"refersTo": ["embeddings"]}, tokens={"introduces": ["token"]}, loop={"uses": ["token"]})
    out = U.knock_ons({"sections": [], "steps": [step("move_section", "embeddings", after="attention")]}, idx, PAGE)
    assert [i["section"] for i in out] == ["attention"] and "now comes after" in out[0]["intent"]
    out = U.knock_ons({"sections": [], "steps": [step("move_section", "loop", after="start")]}, idx, PAGE)
    assert [i["section"] for i in out] == ["loop"] and "token" in out[0]["intent"]


def test_big_or_destructive_plans_are_confirmed():
    assert not U.needs_confirm({"sections": [intent("tokens")], "steps": []}, PAGE)
    assert U.needs_confirm({"sections": [], "steps": [step("remove_section", "tokens")]}, PAGE)
    assert U.needs_confirm({"sections": [], "steps": [step("set_level", level="beginner")]}, PAGE)
    assert U.needs_confirm({"sections": [intent(i) for i in IDS[:5]], "steps": []}, PAGE)


def test_a_plan_over_the_budget_asks_with_cheaper_options(monkeypatch):
    monkeypatch.setenv("FOLLOWUP_MAX_USD", "0.045")  # 4 sections + a playground = 0.046
    reply = understood(sections=[intent(i, quote="x") for i in ("tokens", "sampling", "loop", "summary")])
    out = plan_of(reply)
    assert out["kind"] == "ask" and "big change" in out["question"]["text"]
    labels = [o["label"] for o in out["question"]["options"]]
    assert labels[0] == "Change the text only, keep the playgrounds" and labels[-1] == "Only sections 1, 4, 5 for now"
    chosen = out["question"]["options"][-1]
    again = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "x", "playground", UsageLog("x"), option=chosen)
    assert again["kind"] == "change"  # a chosen option isn't asked about again


# ---------- the call ----------


def test_a_change_names_sections_with_intents_and_never_a_part():
    idx = index_of(PAGE, attention={"refersTo": ["embeddings"]})
    sent: list = []
    reply = understood(sections=[intent("sampling", "check that its playground and text are correct; fix anything wrong", "check the last playground")],
                       steps=[step("remove_section", "embeddings", quote="drop the vectors part")])
    out = U.plan(fake_client(reply, sent=sent), Store(idx), PAGE, "check the last playground and drop the vectors part", "playground", UsageLog("x"),
                 history=['"make it simpler": made version 2'])
    assert out["kind"] == "change" and out["confirm"]
    assert [(i["section"], bool(i.get("derived"))) for i in out["sections"]] == [("sampling", False), ("attention", True)]
    assert out["summary"][0].startswith('Remove Section 2 "Tokens become vectors"')
    assert 'Section 4 "Picking the next token": check that its playground' in out["summary"][1]
    assert "has a playground" in sent[0] and "earlier messages" in sent[0] and "made version 2" in sent[0]


def test_a_question_and_a_chosen_option():
    q = {"text": "Better how?", "options": [{"label": "Clearer wording", "sections": [], "steps": [step("set_tone", what="clearer")]},
                                           {"label": "Fix the playground", "sections": [intent("sampling", "make the playground clearer")], "steps": []},
                                           {"label": "Ghost", "sections": [intent("ghost")], "steps": []}]}
    out = plan_of(understood("ask", question=q))
    assert out["kind"] == "ask" and [o["label"] for o in out["question"]["options"]] == ["Clearer wording", "Fix the playground"]
    chosen = out["question"]["options"][1]
    out = U.plan(fake_client(RuntimeError("not called")), Store(index_of(PAGE)), PAGE, "make it better", "playground", UsageLog("x"), option=chosen)
    assert out["kind"] == "change" and out["sections"][0]["section"] == "sampling"
    sent: list = []
    U.plan(fake_client(understood(sections=[intent("sampling", quote="more sliders")]), sent=sent), Store(index_of(PAGE)), PAGE, "make it better",
           "playground", UsageLog("x"), option=chosen, extra="more sliders")
    assert 'chose: "Fix the playground"' in sent[0] and "more sliders" in sent[0]


def test_cannot_answer_new_topic_and_undo():
    alt = {"label": "A dropdown of examples", "sections": [intent("sampling", "add a dropdown of example sentences")], "steps": []}
    out = plan_of(understood("cannot", reason="Playgrounds can't take typed text.", alternative=alt))
    assert out["kind"] == "cannot" and out["alternative"]["label"] == alt["label"] and out["alternative"]["sections"][0]["section"] == "sampling"
    assert plan_of(understood("answer", reason="Because the rate decays.")) == {"kind": "answer", "answer": "Because the rate decays."}
    out = plan_of(understood("new_page", reason="That's about diffusion."))
    assert out["kind"] == "cannot" and out["alternative"] is None and out["newTopic"]
    assert plan_of(understood("revert")) == {"kind": "revert"}


def test_failures_and_unplaceable_requests_are_errors():
    assert "error" in plan_of(RuntimeError("down"))
    assert "error" in plan_of(understood(sections=[intent("ghost")]))


def test_the_flag_is_on_by_default(monkeypatch):
    monkeypatch.setenv("FOLLOWUP_PLAN", "0")
    assert U.handle(fake_client(RuntimeError("not called")), None, {"page": PAGE, "text": "x", "quality": "playground"})["kind"] == "off"
    monkeypatch.delenv("FOLLOWUP_PLAN", raising=False)
    out = U.handle(fake_client(understood(sections=[intent("tokens", quote="x")])), Store(index_of(PAGE)), {"page": PAGE, "text": "x", "quality": "playground"})
    assert out["kind"] == "change" and "usd" in out


def test_the_request_and_follow_up_plan_are_parsed():
    body = {"text": "make it simpler", "quality": "playground", "followUp": {**FOLLOW, "design": default_design(OLD)},
            "option": {"label": "A", "sections": [intent("tokens")], "steps": []}, "extra": "with numbers", "history": ["a", 3, "b"]}
    req = U.parse_plan_request(body)
    assert req["option"]["sections"] == [intent("tokens")] and req["extra"] == "with numbers" and req["history"] == ["a", "b"]
    assert parse_plan({"sections": [intent("tokens"), {"section": "x"}, {**intent("loop"), "derived": True, "because": "y"}],
                       "steps": [step("remove_section", "loop"), {"op": "edit_text"}]}) == {
        "sections": [intent("tokens"), {**intent("loop"), "derived": True, "because": "y"}], "steps": [step("remove_section", "loop")]}
    assert parse_plan({"sections": [], "steps": [step("revert")]}) is None  # undo never reaches the pipeline
    kept = parse_plan({"sections": [{**intent("tokens"), "unsure": "check"}], "steps": [{**step("remove_section", "loop"), "unsure": " "}]})
    assert kept["sections"][0]["unsure"] == "check" and "unsure" not in kept["steps"][0]
    f = parse_follow_up({**FOLLOW, "design": default_design(OLD), "plan": {"sections": [intent("tokens")], "steps": []}})
    assert f["plan"]["sections"][0]["section"] == "tokens"
    assert isinstance(parse_follow_up({**FOLLOW, "design": default_design(OLD), "plan": {"sections": [], "steps": []}}), str)


def test_the_step_list_matches_the_typescript_one():
    from explainer import data
    from explainer.followup import PLAN_OPS
    schema = data.formats()["followupUnderstand"]["properties"]
    assert schema["steps"]["items"]["properties"]["op"]["enum"] == list(U.OPS)
    assert set(PLAN_OPS) == set(U.OPS) - {"revert"}


def test_the_plan_route(app, client, monkeypatch):
    from conftest import signup
    headers = signup(client)
    body = {"text": "the demo shows nothing", "quality": "playground", "followUp": {**FOLLOW, "design": default_design(OLD)}}
    monkeypatch.setenv("FOLLOWUP_PLAN", "0")
    assert client.post("/api/followup/plan", json=body, headers=headers).get_json() == {"kind": "off", "usd": 0.0}
    monkeypatch.delenv("FOLLOWUP_PLAN", raising=False)
    index_reply = {"sections": [{"id": i, "introduces": [], "uses": [], "refersTo": [], "claims": [], "playgroundLevel": ""} for i in IDS]}
    app.extensions["explainer_ai"]["client"] = fake_client(index_reply, understood(sections=[intent("sampling", "make it work", "shows nothing")]))
    out = client.post("/api/followup/plan", json=body, headers=headers).get_json()
    assert out["kind"] == "change" and out["sections"][0]["section"] == "sampling" and out["usd"] > 0
    assert client.post("/api/followup/plan", json={**body, "quality": "playground-2"}, headers=headers).status_code == 400
