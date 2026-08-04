"""Patents, Books, Scholar and the Web-Search door."""

from __future__ import annotations

import json

import pytest

from gscrape._core.errors import JsRequired, ParseError
from gscrape.books import Books
from gscrape.patents import Patents
from gscrape.scholar import Scholar
from gscrape.search import Search, parse_image_serp, parse_serp

PATENT_PAGE = {
    "results": {
        "total_num_results": 191072,
        "cluster": [
            {
                "result": [
                    {
                        "rank": 0,
                        "patent": {
                            "title": " An Improved Pneumatic Sole ",
                            "snippet": " Between the outsole and insole ",
                            "publication_number": "GB190013393A",
                            "inventor": "John Doherty",
                            "assignee": "John Doherty",
                            "priority_date": "1900-07-25",
                            "filing_date": "1900-07-25",
                            "grant_date": "1901-07-25",
                            "publication_date": "1901-07-25",
                            "language": "en",
                            "pdf": "path/to.pdf",
                            "thumbnail": "",
                        },
                    }
                ]
            }
        ],
    }
}

SCHOLAR_HTML = """
<div class="gs_r gs_or gs_scl"><div class="gs_ri">
  <h3 class="gs_rt"><a href="https://example.org/paper.pdf">Biomechanics of running shoes</a></h3>
  <div class="gs_a">A Autor, B Zweit - Journal of Sports, 2021 - elsevier.com</div>
  <div class="gs_rs">We measured impact forces in 40 runners &hellip;</div>
  <div class="gs_fl"><a href="/scholar?cites=12345&as_sdt=5">Zitiert von: 87</a></div>
</div>
<div class="gs_or_ggsm"><a href="https://example.org/paper.pdf">[PDF] example.org</a></div>
</div>
"""


class TestPatents:
    def test_shapes_a_row(self, monkeypatch):
        p = Patents()
        monkeypatch.setattr(p.client, "get", lambda *a, **k: json.dumps(PATENT_PAGE))
        rows = p.search("running shoe", limit=1)
        r = rows[0]
        assert r["publication_number"] == "GB190013393A"
        assert r["title"] == "An Improved Pneumatic Sole"  # whitespace stripped
        assert r["url"].endswith("/patent/GB190013393A/en")
        assert r["pdf"].startswith("https://patentimages.storage.googleapis.com/")

    def test_stops_on_short_page(self, monkeypatch):
        p = Patents()
        calls = []

        def fake_get(url, **k):
            calls.append(url)
            return json.dumps(PATENT_PAGE)

        monkeypatch.setattr(p.client, "get", fake_get)
        p.search("x", limit=50)
        assert len(calls) == 1  # one row came back, so there is no page 2

    def test_filters_land_in_the_query(self, monkeypatch):
        p = Patents()
        seen = {}

        def fake_get(url, **k):
            seen["url"] = url
            return json.dumps(PATENT_PAGE)

        monkeypatch.setattr(p.client, "get", fake_get)
        p.search("shoe", after="20220101", date_field="filing", country="US,DE")
        assert "filing%3A20220101" in seen["url"] or "filing:20220101" in seen["url"]
        assert "country" in seen["url"]

    def test_non_json_raises(self, monkeypatch):
        p = Patents()
        monkeypatch.setattr(p.client, "get", lambda *a, **k: "<html>captcha</html>")
        with pytest.raises(ParseError):
            p.search("x")

    def test_count(self, monkeypatch):
        p = Patents()
        monkeypatch.setattr(p.client, "get", lambda *a, **k: json.dumps(PATENT_PAGE))
        assert p.count("running shoe") == 191072


class TestBooks:
    PAGE = {
        "totalItems": 1,
        "items": [
            {
                "id": "abc",
                "volumeInfo": {
                    "title": "Laufen",
                    "authors": ["A"],
                    "publishedDate": "2020",
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9781234567897"}
                    ],
                    "imageLinks": {"thumbnail": "https://x/t.jpg"},
                },
                "saleInfo": {
                    "saleability": "FOR_SALE",
                    "listPrice": {"amount": 9.99, "currencyCode": "EUR"},
                },
            }
        ],
    }

    def test_shape(self, monkeypatch):
        b = Books()
        monkeypatch.setattr(b.client, "get", lambda *a, **k: json.dumps(self.PAGE))
        row = b.search("laufen", limit=1)[0]
        assert row["isbn_13"] == "9781234567897"
        assert row["price"] == 9.99 and row["for_sale"] is True

    def test_field_operators(self, monkeypatch):
        b = Books()
        seen = {}

        def fake_get(url, **k):
            seen["url"] = url
            return json.dumps(self.PAGE)

        monkeypatch.setattr(b.client, "get", fake_get)
        b.search(author="knopf", limit=1)
        assert "inauthor%3Aknopf" in seen["url"]

    def test_requires_some_query(self):
        with pytest.raises(ValueError):
            Books().search()


class TestScholar:
    def test_parses_a_result(self):
        rows = Scholar.parse(SCHOLAR_HTML)
        assert len(rows) == 1
        r = rows[0]
        assert r["title"] == "Biomechanics of running shoes"
        assert r["year"] == 2021
        assert r["citations"] == 87
        assert r["cluster_id"] == "12345"
        assert r["pdf"].endswith(".pdf")
        assert r["authors"].startswith("A Autor")

    def test_empty_page(self):
        assert Scholar.parse("<html></html>") == []

    def test_query_building(self, monkeypatch):
        s = Scholar()
        seen = {}

        def fake_get(url, **k):
            seen["url"] = url
            return ""

        monkeypatch.setattr(s.client, "get", fake_get)
        s.search("shoes", year_from=2020, sort_by_date=True, author="Autor")
        assert "as_ylo=2020" in seen["url"] and "scisbd=1" in seen["url"]
        assert "author" in seen["url"]


SERP_HTML = """
<div id="rso">
  <div class="MjjYud"><div class="tF2Cxc"><div class="yuRUbf">
    <a href="https://example.org/schuhe"><h3>Beste Laufschuhe 2026</h3>
      <cite>https://example.org › schuhe</cite></a>
  </div>
  <div class="VwiC3b">Example.org 09.04.2026 &mdash; Der Speedgoat 6 ist der perfekte
  Trailrunning-Schuh für alle, die Leistung und Komfort wollen.</div></div></div>
  <div class="MjjYud"><div class="tF2Cxc"><div class="yuRUbf">
    <a href="https://andere.de/test"><h3>Laufschuhe im Test</h3>
      <cite>https://andere.de › test</cite></a>
  </div>
  <div class="VwiC3b">Wir haben 40 aktuelle Modelle über 600 Kilometer getestet und
  bewertet, hier sind die Ergebnisse.</div></div></div>
</div>
"""


class TestSerpParser:
    def test_extracts_results(self):
        rows = parse_serp(SERP_HTML)
        assert [r["url"] for r in rows] == [
            "https://example.org/schuhe",
            "https://andere.de/test",
        ]
        assert rows[0]["title"] == "Beste Laufschuhe 2026"
        assert rows[0]["position"] == 1

    def test_splits_the_publication_date_off_the_snippet(self):
        row = parse_serp(SERP_HTML)[0]
        assert row["published"] == "09.04.2026"
        assert row["snippet"].startswith("Der Speedgoat 6")
        # The source label Google prefixes must not survive into the snippet.
        assert "Example.org" not in row["snippet"]

    def test_snippet_without_a_date(self):
        row = parse_serp(SERP_HTML)[1]
        assert row["published"] is None
        assert row["snippet"].startswith("Wir haben 40")

    def test_breadcrumb_is_not_the_snippet(self):
        for row in parse_serp(SERP_HTML):
            assert "›" not in (row["snippet"] or "")

    def test_display_url(self):
        assert parse_serp(SERP_HTML)[0]["display_url"].startswith("https://example.org")

    def test_ignores_google_own_links(self):
        html = '<div id="rso"><a href="https://www.google.com/x"><h3>T</h3></a></div>'
        assert parse_serp(html) == []

    def test_empty_page(self):
        assert parse_serp("<html><body></body></html>") == []


class TestSearchDoor:
    def test_web_raises_js_required_on_the_shell(self, monkeypatch):
        s = Search()
        monkeypatch.setattr(s.client, "get", lambda *a, **k: "shell" * 100)
        with pytest.raises(JsRequired) as e:
            s.web("laufschuhe")
        assert "NID" in str(e.value)  # the message names the actual fix

    def test_web_parses_a_real_page(self, monkeypatch):
        s = Search()
        page = '<html><body itemtype="SearchResultsPage">' + SERP_HTML + "</body></html>"
        monkeypatch.setattr(s.client, "get", lambda *a, **k: page)
        rows = s.web("laufschuhe", limit=2)
        assert len(rows) == 2

    def test_web_pages_in_parallel_and_dedupes(self, monkeypatch):
        s = Search()
        seen = []

        def fake_get(url, **k):
            seen.append(url)
            return (
                '<html><body itemtype="SearchResultsPage">' + SERP_HTML + "</body></html>"
            )

        monkeypatch.setattr(s.client, "get", fake_get)
        rows = s.web("laufschuhe", limit=30)
        assert len(seen) == 3  # 3 pages, fetched at once
        assert any("start=10" in u for u in seen)
        assert len(rows) == 2  # same results deduped by url

    def test_clean_mode_uses_udm14(self, monkeypatch):
        s = Search()
        seen = {}

        def fake_get(url, **k):
            seen["url"] = url
            return '<html><body itemtype="SearchResultsPage"></body></html>'

        monkeypatch.setattr(s.client, "get", fake_get)
        s.web("x", limit=1)
        assert "udm=14" in seen["url"]
        s.web("x", limit=1, clean=False, when="w", site="a.de")
        assert "udm=14" not in seen["url"]
        assert "qdr%3Aw" in seen["url"] and "site%3Aa.de" in seen["url"]

    def test_ai_overview_raises_when_not_inlined(self, monkeypatch):
        s = Search()
        monkeypatch.setattr(s.client, "get", lambda *a, **k: "<html></html>")
        with pytest.raises(JsRequired):
            s.ai_overview("warum sind carbon laufschuhe schneller")

    def test_ai_overview_extracts_an_inlined_block(self, monkeypatch):
        s = Search()
        html = (
            '<div data-subtree="aimc">Carbon-Laufschuhe sind schneller, weil die '
            "steife Platte den Energieverlust verringert und den Vorfuß als Hebel "
            'nutzt. <a href="https://quelle.de/a">Quelle</a></div>'
        )
        monkeypatch.setattr(s.client, "get", lambda *a, **k: html)
        out = s.ai_overview("x")
        assert out["text"].startswith("Carbon-Laufschuhe")
        assert out["sources"] == [{"title": "Quelle", "url": "https://quelle.de/a"}]

    def test_cse_needs_an_engine_id(self, monkeypatch):
        monkeypatch.delenv("GSCRAPE_CSE_CX", raising=False)
        with pytest.raises(ValueError):
            Search().cse("laufschuhe")

    def test_cse_parses_jsonp(self, monkeypatch):
        s = Search(cx="123:abc")
        s._token = "tok"
        payload = {
            "cursor": {"estimatedResultCount": "1"},
            "results": [
                {
                    "titleNoFormatting": "Titel",
                    "unescapedUrl": "https://example.org/a",
                    "visibleUrl": "example.org",
                    "contentNoFormatting": "Snippet",
                    "richSnippet": {"cseImage": {"src": "https://x/i.jpg"}},
                }
            ],
        }
        monkeypatch.setattr(
            s.client, "get", lambda *a, **k: f"cb({json.dumps(payload)});"
        )
        rows = s.cse("laufschuhe", limit=10)
        assert rows[0]["url"] == "https://example.org/a"
        assert rows[0]["thumbnail"].endswith("i.jpg")

    def test_cse_refreshes_a_stale_token_once(self, monkeypatch):
        s = Search(cx="123:abc")
        s._token = "stale"
        bodies = [
            'cb({"error": {"code": 401}});',
            'cb({"cursor": {"estimatedResultCount": "0"}, "results": []});',
        ]
        monkeypatch.setattr(s.client, "get", lambda *a, **k: bodies.pop(0))
        monkeypatch.setattr(s, "_cse_token", lambda cx, refresh=False: "fresh")
        assert s.cse("x") == []
        assert not bodies  # both requests were made

    def test_cse_rejects_non_jsonp(self, monkeypatch):
        s = Search(cx="1:2")
        s._token = "t"
        monkeypatch.setattr(s.client, "get", lambda *a, **k: "<html>nope</html>")
        with pytest.raises(ParseError):
            s.cse("x")


IMAGE_STATE = (
    'x["I98OXU0r6cWIbM",'
    '["https://encrypted-tbn0.gstatic.com/images?q\\u003dtbn:ANd9GcT\\u0026s\\u003d10",447,447],'
    '["https://images.example.fr/schuh.jpg",800,800],null,2,"rgb(1,2,3)",null,0,'
    '{"2000":[null,"www.example.de","35KB"],'
    '"2003":[null,"BYUnzVv4BFN84M","https://www.example.de/artikel/68194",'
    '"Laufschuhe f\\u00fcr Herren | Example",null,0]}]'
)


class TestImageSerpParser:
    def test_returns_the_original_not_the_thumbnail(self):
        row = parse_image_serp(IMAGE_STATE)[0]
        assert row["url"] == "https://images.example.fr/schuh.jpg"
        assert (row["width"], row["height"]) == (800, 800)
        assert row["thumbnail"].startswith("https://encrypted-tbn0")

    def test_unescapes_googles_json_escapes(self):
        row = parse_image_serp(IMAGE_STATE)[0]
        # \u003d / \u0026 in URLs, and \uXXXX in the title — the latter turns
        # into mojibake if decoded with the unicode_escape codec.
        assert "=" in row["thumbnail"] and "&" in row["thumbnail"]
        assert row["source_title"] == "Laufschuhe für Herren | Example"

    def test_source_page(self):
        row = parse_image_serp(IMAGE_STATE)[0]
        assert row["source_url"] == "https://www.example.de/artikel/68194"
        assert row["source_domain"] == "www.example.de"
        assert row["file_size"] == "35KB"

    def test_limit_and_dedupe(self):
        assert len(parse_image_serp(IMAGE_STATE * 3)) == 1  # same url three times
        assert parse_image_serp("", limit=5) == []

    def test_images_raises_without_data(self, monkeypatch):
        s = Search()
        monkeypatch.setattr(s.client, "get", lambda *a, **k: "<html>shell</html>")
        with pytest.raises(JsRequired):
            s.images("laufschuhe")
