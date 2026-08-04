"""Google News.

News is the one Google surface that still ships a full, un-gated RSS feed —
no consent cookies, no JavaScript, 100 items per query, and it accepts the same
search operators as the web UI (`site:`, `when:`, `intitle:`, `allinurl:`).
That makes it the most reliable scraper in this package after Maps.

    from gscrape import News
    n = News(hl="de", gl="DE")
    for a in n.search("laufschuhe", when="7d"):
        print(a["published"], a["source"], a["title"])

## The link problem

Feed links point at `news.google.com/rss/articles/CBMi...`, a protobuf blob, not
at the publisher. Following it lands on a consent page, and the blob cannot be
decoded offline — since 2024 it only carries an internal id.

`resolve()` does what the News web app does: fetch the article shell, read the
`data-n-a-id` / `data-n-a-sg` (signature) / `data-n-a-ts` (timestamp) triple out
of it, then POST them to the `Fbv4je` RPC, which answers with the publisher URL.
That is 2 requests per link, so resolution is opt-in and parallel.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ._core.consent import accept_consent
from ._core.errors import ParseError
from ._core.service import Service

RSS_BASE = "https://news.google.com/rss"
DECODE_ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
DECODE_RPCID = "Fbv4je"

_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
_ID_RE = re.compile(r'data-n-a-id="([^"]+)"')

#: The topic sections Google exposes by name. Country-specific ones (`geo`) go
#: through `geo()` instead.
TOPICS = {
    "world": "WORLD",
    "nation": "NATION",
    "business": "BUSINESS",
    "technology": "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT",
    "sports": "SPORTS",
    "science": "SCIENCE",
    "health": "HEALTH",
}


class News(Service):
    """Search, topic and geo feeds, with optional publisher-URL resolution."""

    @property
    def _ceid(self) -> str:
        """Google News wants country and language twice, in two spellings."""
        return f"{self.gl.upper()}:{self.hl}"

    def _params(self) -> str:
        return urllib.parse.urlencode(
            {"hl": self.hl, "gl": self.gl.upper(), "ceid": self._ceid}
        )

    def _feed(self, path: str, extra: dict[str, str] | None = None) -> list[dict]:
        url = f"{RSS_BASE}{path}?{self._params()}"
        if extra:
            url += "&" + urllib.parse.urlencode(extra)
        xml = self.client.get(url, headers={"accept": "application/xml"})
        return self._parse(xml)

    @staticmethod
    def _parse(xml: str) -> list[dict]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise ParseError(f"news feed was not XML ({len(xml)} bytes)") from e

        out = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title") or ""
            source_el = item.find("source")
            # Google appends " - Publisher" to every title; strip it when the
            # <source> tag confirms the publisher, keep it when it does not.
            source = (source_el.text or "").strip() if source_el is not None else None
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            out.append(
                {
                    "title": title,
                    "url": item.findtext("link"),
                    "published": item.findtext("pubDate"),
                    "source": source,
                    "source_url": (
                        source_el.get("url") if source_el is not None else None
                    ),
                    "guid": item.findtext("guid"),
                    "snippet_html": item.findtext("description"),
                    "resolved_url": None,
                }
            )
        return out

    # ------------------------------------------------------------------ feeds

    def search(
        self,
        query: str,
        *,
        when: str | None = None,
        after: str | None = None,
        before: str | None = None,
        site: str | None = None,
        limit: int | None = None,
        resolve: bool = False,
    ) -> list[dict]:
        """Up to 100 articles for a query.

        Args:
            when: relative window, e.g. `1h`, `7d`, `1y`. Cheaper and more
                reliable than after/before.
            after/before: `YYYY-MM-DD` absolute bounds.
            site: restrict to one publisher domain.
            resolve: also fetch the real publisher URLs (2 extra requests each).
        """
        q = query
        if site:
            q += f" site:{site}"
        if when:
            q += f" when:{when}"
        if after:
            q += f" after:{after}"
        if before:
            q += f" before:{before}"
        rows = self._feed("/search", {"q": q})
        if limit:
            rows = rows[:limit]
        return self.resolve_all(rows) if resolve else rows

    def top(self, *, limit: int | None = None, resolve: bool = False) -> list[dict]:
        """The country's front page."""
        rows = self._feed("")
        if limit:
            rows = rows[:limit]
        return self.resolve_all(rows) if resolve else rows

    def topic(self, topic: str, **kw: Any) -> list[dict]:
        """A section feed: `world`, `business`, `technology`, ... or a raw
        `CAAqJggK...` topic id copied from a News URL."""
        key = TOPICS.get(topic.lower(), topic)
        rows = self._feed(f"/headlines/section/topic/{key}")
        if kw.get("limit"):
            rows = rows[: kw["limit"]]
        return self.resolve_all(rows) if kw.get("resolve") else rows

    def geo(self, place: str, **kw: Any) -> list[dict]:
        """Local news for a place name (`Berlin`, `Hamburg-Altona`, ...)."""
        rows = self._feed(f"/headlines/section/geo/{urllib.parse.quote(place)}")
        if kw.get("limit"):
            rows = rows[: kw["limit"]]
        return self.resolve_all(rows) if kw.get("resolve") else rows

    # -------------------------------------------------------------- resolving

    def resolve(self, article_url: str) -> str | None:
        """`news.google.com/rss/articles/CBMi...` -> the publisher's URL.

        Two requests: the article shell carries a per-article signature and
        timestamp that the decode RPC refuses to work without.
        """
        if "news.google.com" not in article_url:
            return article_url  # already a publisher link

        html = self.client.get(article_url, headers={"accept": "text/html"})
        sg, ts, aid = _SG_RE.search(html), _TS_RE.search(html), _ID_RE.search(html)
        if not (sg and ts and aid) and "consent.google.com" in html[:4000]:
            # The article shell is consent-gated on EU IPs; one accept-all POST
            # fixes it for every later call on this client.
            accept_consent(self.client)
            self.client.save_cookies()
            html = self.client.get(article_url, headers={"accept": "text/html"})
            sg, ts, aid = _SG_RE.search(html), _TS_RE.search(html), _ID_RE.search(html)
        if not (sg and ts and aid):
            return None  # removed article, or the markup changed

        payload = [
            "garturlreq",
            [
                [
                    "X",
                    "X",
                    ["X", "X"],
                    None,
                    None,
                    1,
                    1,
                    self._ceid,
                    None,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    1,
                ],
                "X",
                "X",
                1,
                [1, 1, 1],
                1,
                1,
                None,
                0,
                0,
                None,
                0,
            ],
            aid.group(1),
            int(ts.group(1)),
            sg.group(1),
        ]
        res = self.client.batchexecute(
            DECODE_ENDPOINT,
            DECODE_RPCID,
            DECODE_RPCID,
            payload,
            extra_headers={
                "referer": "https://news.google.com/",
                "origin": "https://news.google.com",
            },
        )
        if isinstance(res, list) and len(res) > 1 and isinstance(res[1], str):
            return res[1]
        return None

    def resolve_all(self, rows: list[dict], *, workers: int = 8) -> list[dict]:
        """Fill in `resolved_url` for a whole feed, in parallel.

        Failures stay None instead of raising: a feed of 100 articles where 3
        links have rotted is still a good feed.
        """

        def one(row: dict) -> dict:
            try:
                row["resolved_url"] = self.resolve(row["url"])
            except Exception:
                row["resolved_url"] = None
            return row

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, rows))


__all__ = ["TOPICS", "News"]
