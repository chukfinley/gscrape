"""Google News RSS parsing and query building."""

from __future__ import annotations

from gscrape.news import News


class TestParse:
    def test_reads_items(self, news_rss):
        rows = News._parse(news_rss)
        assert len(rows) > 5
        assert all(r["url"].startswith("https://news.google.com/") for r in rows)

    def test_publisher_suffix_stripped_from_title(self, news_rss):
        for r in News._parse(news_rss):
            if r["source"]:
                assert not r["title"].endswith(f" - {r['source']}")

    def test_fields_present(self, news_rss):
        r = News._parse(news_rss)[0]
        assert r["title"] and r["published"] and r["source"]
        assert r["resolved_url"] is None  # not resolved unless asked

    def test_empty_feed(self):
        empty = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        assert News._parse(empty) == []


class TestQueryBuilding:
    def _url_of(self, monkeypatch, **kw):
        seen = {}
        n = News(hl="de", gl="DE")

        def fake_get(url, **_):
            seen["url"] = url
            return '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'

        monkeypatch.setattr(n.client, "get", fake_get)
        n.search("laufschuhe", **kw)
        return seen["url"]

    def test_ceid_and_language(self, monkeypatch):
        url = self._url_of(monkeypatch)
        assert "hl=de" in url and "gl=DE" in url and "ceid=DE%3Ade" in url

    def test_when_operator(self, monkeypatch):
        assert "when%3A7d" in self._url_of(monkeypatch, when="7d")

    def test_site_operator(self, monkeypatch):
        assert "site%3Aspiegel.de" in self._url_of(monkeypatch, site="spiegel.de")

    def test_date_bounds(self, monkeypatch):
        url = self._url_of(monkeypatch, after="2026-01-01", before="2026-02-01")
        assert "after%3A2026-01-01" in url and "before%3A2026-02-01" in url


class TestResolve:
    def test_publisher_links_pass_through(self):
        n = News()
        assert n.resolve("https://spiegel.de/x") == "https://spiegel.de/x"

    def test_missing_signature_returns_none(self, monkeypatch):
        n = News()
        monkeypatch.setattr(n.client, "get", lambda *a, **k: "<html>nothing</html>")
        assert n.resolve("https://news.google.com/rss/articles/CBMi") is None

    def test_decodes_rpc_payload(self, monkeypatch):
        n = News()
        monkeypatch.setattr(
            n.client,
            "get",
            lambda *a, **k: 'data-n-a-id="CBMi" data-n-a-sg="SIG" data-n-a-ts="123"',
        )
        captured = {}

        def fake_batch(endpoint, rpcid, rpc_name, payload, **kw):
            captured["payload"] = payload
            return ["x", "https://publisher.example/article"]

        monkeypatch.setattr(n.client, "batchexecute", fake_batch)
        assert n.resolve("https://news.google.com/rss/articles/CBMi") == (
            "https://publisher.example/article"
        )
        # The signature triple is what the RPC refuses to work without.
        assert captured["payload"][2:] == ["CBMi", 123, "SIG"]

    def test_resolve_all_survives_failures(self, monkeypatch):
        n = News()
        monkeypatch.setattr(
            n, "resolve", lambda url: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        rows = n.resolve_all([{"url": "https://news.google.com/x", "resolved_url": None}])
        assert rows[0]["resolved_url"] is None
