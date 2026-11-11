"""Follow-ups v3: the page map (page_index.py). References found by code, the AI part merged in, and the cache."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from explainer.modes.followup import page_index as P
from explainer.db import Database
from explainer.pricing import UsageLog

OLD = json.loads((Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"))["outline"]
IDS = [s["id"] for s in OLD["stages"]]


def page_with(texts: dict[str, str]) -> dict:
    return {**OLD, "stages": [{**s, "blocks": [{"type": "text", "md": texts[s["id"]]}, *s["blocks"][1:]]} if s["id"] in texts else s
                              for s in OLD["stages"]]}


def test_code_finds_mentions_of_other_sections():
    page = page_with({"attention": "As we saw above, vectors carry meaning. See section 1 and the second section.",
                      "loop": "Back in Picking the next token we chose one; the next section sums it up.", "tokens": "Plain text."})
    refs = P.code_refs(page)
    assert refs["attention"] == ["tokens", "embeddings"]  # section 1, the second section, "above" = the one before
    assert refs["loop"] == ["sampling", "uncertainty"]  # a label, and "the next section"
    assert refs["tokens"] == []


def test_the_ai_part_is_merged_and_checked():
    idx = P.code_index(OLD)
    raw = {"sections": [{"id": "sampling", "introduces": ["temperature", "x" * 300], "uses": ["logits"], "refersTo": ["sampling", "tokens", "ghost"],
                         "claims": ["shows the odds"], "playgroundLevel": "advanced"},
                        {"id": "ghost", "introduces": ["nope"], "uses": [], "refersTo": [], "claims": [], "playgroundLevel": ""}]}
    out = P.merge_ai(idx, raw)
    s = next(x for x in out["sections"] if x["id"] == "sampling")
    assert s["introduces"][0] == "temperature" and len(s["introduces"][1]) == 80 and s["uses"] == ["logits"]
    assert "tokens" in s["refersTo"] and "sampling" not in s["refersTo"] and "ghost" not in s["refersTo"]
    assert s["claims"] == [] and s["playgroundLevel"] == ""  # no playground on this page: no claims or level
    assert out["ai"] and len(out["sections"]) == len(IDS)


def fake_client(reply, calls: list):
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=500, output_tokens=50),
                               content=[SimpleNamespace(type="text", text=json.dumps(reply))])
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_a_page_is_mapped_once_and_cached_by_its_content():
    db = Database("sqlite://")
    db.create_all()
    store = P.PageIndexStore(db)
    calls: list = []
    client = fake_client({"sections": [{"id": "tokens", "introduces": ["token"], "uses": [], "refersTo": [], "claims": [], "playgroundLevel": ""}]}, calls)
    a = P.get_or_build(store, client, OLD, UsageLog("x"))
    b = P.get_or_build(store, client, OLD, UsageLog("x"))
    assert len(calls) == 1 and a == b and a["sections"][0]["introduces"] == ["token"]
    P.get_or_build(store, client, page_with({"tokens": "Changed."}), UsageLog("x"))
    assert len(calls) == 2  # another page: mapped again


def test_without_a_client_the_code_part_is_used_and_not_cached():
    db = Database("sqlite://")
    db.create_all()
    store = P.PageIndexStore(db)
    out = P.get_or_build(store, None, OLD, UsageLog("x"))
    assert not out.get("ai") and store.get(P.page_key(OLD)) is None


def test_the_map_text_lists_each_section():
    text = P.map_text(P.code_index(OLD), "lite-plus")
    assert text.startswith("Mode: lite-plus. Page level: beginner.") and 'S8 (section 8): "Putting it together"' in text and "no playground" in text
    assert '"summary"' not in text  # ids aren't shown: Haiku names sections by handle
