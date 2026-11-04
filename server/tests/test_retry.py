"""Retry ("↻ Retry" in the app): the replaced page's titles and theme travel as `avoid`."""

from explainer.pipeline import avoid_hint, other_theme
from explainer.requests import parse_generate


def test_avoid_parsed_and_clamped():
    r = parse_generate({"mode": "concept", "text": "Attention", "quality": "playground",
                        "avoid": {"titles": ["  Why attention  ", "", 7, "x" * 300] + [f"t{i}" for i in range(20)], "theme": "ocean"}})
    a = r["avoid"]
    assert a["titles"][0] == "Why attention"
    assert len(a["titles"][1]) == 120  # cut, the empty and non-string titles dropped
    assert len(a["titles"]) == 12
    assert a["theme"] == "ocean"


def test_avoid_bad_input_dropped():
    assert "avoid" not in parse_generate({"mode": "concept", "text": "x", "avoid": "nope"})
    assert "avoid" not in parse_generate({"mode": "concept", "text": "x", "avoid": {"titles": ["", "  "]}})
    r = parse_generate({"mode": "concept", "text": "x", "avoid": {"titles": ["A"], "theme": "not-a-theme"}})
    assert r["avoid"] == {"titles": ["A"]}


def test_avoid_on_paper_and_lite():
    r = parse_generate({"mode": "paper", "title": "T", "note": "", "sections": [{"heading": "h", "text": "t"}],
                        "quality": "lite-plus", "avoid": {"titles": ["A"]}})
    assert r["avoid"] == {"titles": ["A"]}


def test_avoid_hint():
    assert avoid_hint(None) == ""
    h = avoid_hint({"titles": ["Why attention", "Scores"]})
    assert '"Why attention"; "Scores"' in h and "fresh" in h and "Keep every fact correct" in h


def test_other_theme():
    assert other_theme("forest", {"titles": ["A"], "theme": "ocean"}) == "forest"  # already different
    assert other_theme("ocean", {"titles": ["A"], "theme": "ocean"}) != "ocean"
    assert other_theme("ocean", None) == "ocean"
