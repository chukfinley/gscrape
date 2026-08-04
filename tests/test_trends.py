"""Trends: widget selection, payload shaping, CSV export wiring."""

from __future__ import annotations

import json

import pytest

from gscrape._core.errors import NotFound
from gscrape.trends import Trends


def _widgets(*ids):
    return {
        i: {
            "id": i,
            "token": f"tok-{i}",
            "request": {"comparisonItem": [{"keyword": "laufschuhe"}]},
        }
        for i in ids
    }


class TestWidgetPicking:
    def test_exact_id(self):
        assert Trends()._pick(_widgets("TIMESERIES"), "TIMESERIES")["token"] == (
            "tok-TIMESERIES"
        )

    def test_numbered_comparison_widget(self):
        # Comparisons number their widgets: GEO_MAP_0, GEO_MAP_1, ...
        assert Trends()._pick(_widgets("GEO_MAP_0"), "GEO_MAP")["id"] == "GEO_MAP_0"

    def test_missing_widget(self):
        with pytest.raises(NotFound):
            Trends()._pick(_widgets("TIMESERIES"), "GEO_MAP")


class TestInterestOverTime:
    def test_rows(self, monkeypatch, trends_timeseries):
        t = Trends()
        monkeypatch.setattr(t, "explore", lambda *a, **k: _widgets("TIMESERIES"))
        monkeypatch.setattr(t, "_widget_data", lambda w: trends_timeseries)
        rows = t.interest_over_time("laufschuhe")
        assert len(rows) > 10
        assert all(0 <= r["laufschuhe"] <= 100 for r in rows)
        assert all(isinstance(r["timestamp"], int) for r in rows)

    def test_partial_flag_present(self, monkeypatch, trends_timeseries):
        # The last bucket is usually incomplete; charts that ignore this show a
        # fake downtrend at the right edge.
        t = Trends()
        monkeypatch.setattr(t, "explore", lambda *a, **k: _widgets("TIMESERIES"))
        monkeypatch.setattr(t, "_widget_data", lambda w: trends_timeseries)
        rows = t.interest_over_time("laufschuhe")
        assert isinstance(rows[-1]["partial"], bool)


class TestRelated:
    def test_top_and_rising(self, monkeypatch, trends_related):
        t = Trends()
        monkeypatch.setattr(t, "explore", lambda *a, **k: _widgets("RELATED_QUERIES"))
        monkeypatch.setattr(t, "_widget_data", lambda w: trends_related)
        out = t.related_queries("laufschuhe")
        assert set(out) == {"top", "rising"}
        assert out["top"]
        assert all(r["query"] for r in out["top"])


class TestExploreValidation:
    def test_rejects_more_than_five_keywords(self):
        with pytest.raises(ValueError):
            Trends().explore(["a", "b", "c", "d", "e", "f"])

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            Trends().explore([])

    def test_request_shape(self, monkeypatch):
        t = Trends(hl="de", tz=-60)
        seen = {}

        def fake_get(url, what):
            seen["url"] = url
            return {"widgets": [{"id": "TIMESERIES", "token": "x", "request": {}}]}

        monkeypatch.setattr(t, "_get", fake_get)
        t.explore(["a", "b"], geo="DE", timeframe="today 3-m", property="youtube")
        assert "tz=-60" in seen["url"] and "hl=de" in seen["url"]
        req = json.loads(
            __import__("urllib.parse", fromlist=["unquote"]).unquote(
                seen["url"].split("req=")[1]
            )
        )
        assert req["property"] == "youtube"
        assert [c["keyword"] for c in req["comparisonItem"]] == ["a", "b"]
        assert all(c["geo"] == "DE" for c in req["comparisonItem"])


class TestCsvExport:
    def test_uses_the_csv_twin_endpoint(self, monkeypatch, tmp_path):
        t = Trends()
        monkeypatch.setattr(t, "explore", lambda *a, **k: _widgets("TIMESERIES"))
        seen = {}

        def fake_get(url, headers=None):
            seen["url"] = url
            return "Kategorie: Alle Kategorien\n\nZeit,laufschuhe\n2026-01-01,50\n"

        monkeypatch.setattr(t.client, "get", fake_get)
        out = tmp_path / "t.csv"
        text = t.csv("laufschuhe", kind="timeseries", path=str(out))
        assert "/widgetdata/multiline/csv" in seen["url"]
        assert out.read_text() == text

    def test_geo_resolution_is_injected(self, monkeypatch):
        t = Trends()
        monkeypatch.setattr(t, "explore", lambda *a, **k: _widgets("GEO_MAP"))
        seen = {}
        monkeypatch.setattr(
            t.client, "get", lambda url, headers=None: seen.setdefault("url", url) or "x"
        )
        t.csv("laufschuhe", kind="geo", resolution="CITY")
        assert "CITY" in seen["url"] and "/comparedgeo/csv" in seen["url"]

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            Trends().csv("x", kind="nope")


class TestTrendingNow:
    ROW = [
        "warnung vor extremer hitze",
        None,
        "DE",
        [1785741600],
        None,
        None,
        2000000,
        None,
        1000,
        ["warnung vor extremer hitze", "wetter hannover"],
        [20],
        [[4702851092, "de", "DE"], [4764696106, "de", "DE"]],
        "warnung vor extremer hitze",
    ]

    def test_shape(self, monkeypatch):
        t = Trends()
        monkeypatch.setattr(t.client, "batchexecute", lambda *a, **k: [None, [self.ROW]])
        rows = t.trending_now(geo="DE")
        assert rows[0]["term"] == "warnung vor extremer hitze"
        assert rows[0]["volume"] == 2000000
        assert rows[0]["growth_pct"] == 1000
        assert rows[0]["categories"] == [20]
        assert len(rows[0]["news_ids"]) == 2

    def test_limit(self, monkeypatch):
        t = Trends()
        monkeypatch.setattr(
            t.client, "batchexecute", lambda *a, **k: [None, [self.ROW] * 10]
        )
        assert len(t.trending_now(limit=3)) == 3

    def test_garbage_rows_skipped(self, monkeypatch):
        t = Trends()
        monkeypatch.setattr(
            t.client, "batchexecute", lambda *a, **k: [None, [None, [123], self.ROW]]
        )
        assert len(t.trending_now()) == 1


class TestByCountry:
    def test_failures_do_not_abort_the_sweep(self, monkeypatch):
        t = Trends()
        calls = []

        def fake(kw, geo=None, **k):
            calls.append(geo)
            if geo == "CZ":
                raise RuntimeError("429")
            return [{"geo": geo}]

        monkeypatch.setattr(t, "interest_over_time", fake)
        out = t.by_country("x", ["SK", "CZ", "AT"])
        assert calls == ["SK", "CZ", "AT"]
        assert out["SK"] == [{"geo": "SK"}]
        assert "error" in out["CZ"]
