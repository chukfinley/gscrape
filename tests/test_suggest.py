"""Autocomplete parsing and the keyword expansions built on it."""

from __future__ import annotations

import json

from gscrape.suggest import MODIFIERS, QUESTION_WORDS, Suggest

CHROME_PAYLOAD = json.dumps(
    [
        "laufschuhe",
        ["laufschuhe damen", "laufschuhe herren"],
        ["", ""],
        [],
        {
            "google:suggestrelevance": [1000, 900],
            "google:suggesttype": ["QUERY", "QUERY"],
        },
    ]
)
FIREFOX_PAYLOAD = json.dumps(["laufschuhe", ["laufschuhe damen"]])


class TestSuggest:
    def _service(self, monkeypatch, payload=CHROME_PAYLOAD):
        s = Suggest(hl="de", gl="de")
        seen = {}
        monkeypatch.setattr(
            s.client, "get", lambda url, **k: (seen.setdefault("url", url), payload)[1]
        )
        return s, seen

    def test_plain_strings(self, monkeypatch):
        s, _ = self._service(monkeypatch)
        assert s.suggest("laufschuhe") == ["laufschuhe damen", "laufschuhe herren"]

    def test_detailed_carries_relevance(self, monkeypatch):
        s, _ = self._service(monkeypatch)
        rows = s.suggest("laufschuhe", detailed=True)
        assert rows[0] == {
            "text": "laufschuhe damen",
            "description": "",
            "relevance": 1000,
            "type": "QUERY",
        }

    def test_firefox_dialect_without_scores(self, monkeypatch):
        s, _ = self._service(monkeypatch, FIREFOX_PAYLOAD)
        rows = s.suggest("laufschuhe", client="firefox", detailed=True)
        assert rows[0]["relevance"] is None

    def test_vertical_parameter(self, monkeypatch):
        s, seen = self._service(monkeypatch)
        s.suggest("laufschuhe", ds="yt")
        assert "ds=yt" in seen["url"]

    def test_language_parameters(self, monkeypatch):
        s, seen = self._service(monkeypatch)
        s.suggest("x")
        assert "hl=de" in seen["url"] and "gl=de" in seen["url"]


class TestExpansions:
    def _service(self, monkeypatch):
        s = Suggest(hl="de", gl="de")
        calls = []

        def fake_suggest(term, ds=None, **kw):
            calls.append(term)
            return [f"{term} ergebnis", "geteiltes ergebnis"]

        monkeypatch.setattr(s, "suggest", fake_suggest)
        return s, calls

    def test_alphabet_covers_a_to_z(self, monkeypatch):
        s, calls = self._service(monkeypatch)
        s.alphabet("laufschuhe")
        assert len(calls) == 26
        assert "laufschuhe a" in calls and "laufschuhe z" in calls

    def test_questions_are_prefixed(self, monkeypatch):
        s, calls = self._service(monkeypatch)
        s.questions("laufschuhe")
        assert calls[0] == f"{QUESTION_WORDS['de'][0]} laufschuhe"

    def test_modifiers_are_suffixed(self, monkeypatch):
        s, calls = self._service(monkeypatch)
        s.modifiers("laufschuhe")
        assert calls[0] == f"laufschuhe {MODIFIERS['de'][0]}"

    def test_unknown_language_falls_back_to_english(self, monkeypatch):
        s = Suggest(hl="xx", gl="xx")
        calls = []
        monkeypatch.setattr(s, "suggest", lambda t, **k: calls.append(t) or [])
        s.questions("shoes")
        assert calls[0].startswith("why ")

    def test_results_are_deduped(self, monkeypatch):
        s, _ = self._service(monkeypatch)
        out = s.alphabet("laufschuhe")
        assert out.count("geteiltes ergebnis") == 1

    def test_one_dead_call_does_not_kill_the_sweep(self, monkeypatch):
        s = Suggest()

        def flaky(term, ds=None, **kw):
            if term.endswith(" c"):
                raise RuntimeError("429")
            return [term]

        monkeypatch.setattr(s, "suggest", flaky)
        assert len(s.alphabet("x")) == 25
