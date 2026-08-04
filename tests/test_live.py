"""Tests that hit the real Google. Deselected by default.

    uv run pytest -m live            # all of them
    uv run pytest -m live -k maps    # one surface

These are contract tests, not unit tests: they exist to catch the day Google
changes a payload shape or closes another door. They are deliberately tolerant
about *values* (a rating changes, a trend passes) and strict about *shapes*.

Expect occasional failures from rate limiting rather than from breakage —
Scholar and Trends 429 easily, and the whole suite shares one IP.
"""

from __future__ import annotations

import pytest

from gscrape import Maps, News, Patents, Suggest, Trends, YouTube
from gscrape._core.errors import JsRequired, RateLimited

pytestmark = pytest.mark.live

# A German restaurant that has been on Maps, with photos, for years.
PROBE_FID = "0x47b36f031b057577:0x99e8a29f6e50a80a"


class TestMaps:
    def test_details(self):
        p = Maps(hl="de", gl="de").details(fid=PROBE_FID)
        assert p["name"]
        assert p["rating"] and p["reviews"]
        assert len(p["photos"]) > 3
        assert p["hours"]["days"]
        assert p["top_reviews"]

    def test_search(self):
        hits = Maps(hl="de", gl="de").search("restaurant flensburg", limit=3)
        assert len(hits) == 3
        assert all(h["name"] for h in hits)

    def test_cookie_jar_is_reused(self, tmp_path):
        m = Maps(cookie_file=tmp_path / "jar.json")
        m.bootstrap()
        assert m.client.cookie_file.exists()


class TestSuggest:
    def test_web(self):
        assert Suggest(hl="de", gl="de").suggest("laufschuhe")

    def test_youtube_vertical(self):
        assert Suggest(hl="de", gl="de").suggest("laufschuh", ds="yt")

    def test_alphabet_sweep_is_wide(self):
        out = Suggest(hl="de", gl="de").alphabet("laufschuhe")
        assert len(out) > 50


class TestNews:
    def test_search(self):
        rows = News(hl="de", gl="DE").search("laufschuhe", when="7d", limit=5)
        assert rows and all(r["title"] and r["source"] for r in rows)

    def test_resolve_reaches_the_publisher(self):
        n = News(hl="de", gl="DE")
        rows = n.search("laufschuhe", when="7d", limit=1)
        url = n.resolve(rows[0]["url"])
        assert url and "news.google.com" not in url

    def test_topic_feed(self):
        assert News(hl="de", gl="DE").topic("technology", limit=3)


class TestTrends:
    def test_interest_over_time(self):
        rows = Trends(hl="de", gl="de", rate_limit=0.3).interest_over_time(
            "laufschuhe", geo="DE", timeframe="today 3-m"
        )
        assert len(rows) > 10
        assert all(0 <= r["laufschuhe"] <= 100 for r in rows)

    def test_trending_rss(self):
        rows = Trends(hl="de", gl="de").trending_rss(geo="DE", limit=5)
        assert rows and rows[0]["term"]

    def test_trending_now_has_volume(self):
        rows = Trends(hl="de", gl="de").trending_now(geo="DE", limit=5)
        assert rows and all(r["volume"] for r in rows)

    def test_csv_export_matches_the_ui(self):
        text = Trends(hl="de", gl="de", rate_limit=0.3).csv(
            "laufschuhe", kind="timeseries", geo="DE", timeframe="today 1-m"
        )
        assert text.splitlines()[0].startswith("Kategorie")


class TestYouTube:
    def test_search(self):
        rows = YouTube(hl="de", gl="DE").search("laufschuhe test", limit=12)
        assert len(rows) >= 12
        assert all(r["video_id"] for r in rows if r["kind"] == "video")

    def test_shorts_filter_returns_shorts(self):
        rows = YouTube(hl="de", gl="DE").shorts("fitness", limit=5)
        # YouTube occasionally folds a normal video shelf into shorts results,
        # so "mostly shorts" is the honest contract; "all shorts" flakes.
        assert rows
        assert sum(r["kind"] == "short" for r in rows) >= len(rows) - 1

    def test_video_details(self):
        yt = YouTube(hl="de", gl="DE")
        vid = yt.search("laufschuhe test", limit=1)[0]["video_id"]
        d = yt.video(vid)
        assert d["published"] and d["duration_s"] > 0

    def test_channel_and_uploads(self):
        yt = YouTube(hl="de", gl="DE")
        assert yt.channel("@MrBeast")["channel_id"].startswith("UC")
        assert yt.channel_videos("@MrBeast", limit=5)


class TestPatents:
    def test_search(self):
        rows = Patents().search("running shoe sole", limit=5)
        assert len(rows) == 5
        assert all(r["publication_number"] for r in rows)


class TestWebSearch:
    """Needs a browser-minted jar in the cache; skipped when there is none."""

    def _search(self):
        from gscrape import Search

        s = Search(hl="de", gl="de")
        if not s.client.load_cookies():
            pytest.skip("no cookie jar — run `gscrape cookies 'NID=...; SOCS=...'`")
        return s

    def test_first_page(self):
        rows = self._search().web("beste laufschuhe", limit=10)
        assert len(rows) >= 8
        assert all(r["url"].startswith("http") and r["title"] for r in rows)

    def test_paginates_across_result_pages(self):
        rows = self._search().web("laufschuhe test", limit=50)
        # 5 pages, deduplicated by URL, positions renumbered end to end.
        assert len(rows) > 35
        assert len({r["url"] for r in rows}) == len(rows)
        assert rows[-1]["position"] == len(rows)

    def test_site_filter(self):
        rows = self._search().web("laufschuhe", site="test.de", limit=10)
        assert rows and all("test.de" in r["url"] for r in rows)

    def test_image_search_returns_originals(self):
        rows = self._search().images("laufschuhe", limit=20)
        assert len(rows) >= 10
        assert all(r["width"] > 0 and r["height"] > 0 for r in rows)
        # Originals, not the gstatic thumbnails — that is the whole point.
        assert not any("encrypted-tbn" in r["url"] for r in rows)


class TestClosedDoors:
    """These document what is *not* possible, so a regression here is good news."""

    def test_ai_overview_is_javascript_only(self):
        from gscrape import Search

        s = Search(hl="de", gl="de")
        s.client.load_cookies()
        with pytest.raises((JsRequired, RateLimited)):
            s.ai_overview("warum sind carbon laufschuhe schneller")

    def test_ai_mode_is_javascript_only(self):
        from gscrape import Search
        from gscrape.search import _ai_block

        s = Search(hl="de", gl="de")
        s.client.load_cookies()
        html = s.client.get(
            "https://www.google.com/search?q=warum+laufschuhe+wechseln&udm=50&hl=de",
            headers={"accept": "text/html"},
        )
        # The page loads, it just carries no answer: that arrives through an
        # async call whose token page JavaScript computes.
        assert _ai_block(html) is None
