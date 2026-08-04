"""Google Trends.

Trends has no official free API, but its web app talks to three JSON endpoints
that answer plain HTTP requests:

* `/trends/api/explore` — hands out a **token per widget**. Nothing else works
  without one: the data endpoints reject requests whose token was minted for a
  different query, geo or timeframe. So every data call costs two requests.
* `/trends/api/widgetdata/{multiline,comparedgeo,relatedsearches}` — the actual
  interest-over-time, by-region and related-queries payloads.
* `/_/TrendsUi/data/batchexecute` (rpcid `i0OFE`) — trending now, with search
  volume and the news stories behind each trend. This replaced the retired
  `/trends/api/dailytrends`, which now answers 404.

Plus `/trending/rss?geo=DE`, an un-gated RSS feed of the same trending list —
the cheapest way to poll trends on a schedule.

    from gscrape import Trends
    t = Trends(hl="de", gl="de")
    t.interest_over_time("laufschuhe", geo="DE", timeframe="today 12-m")
    t.related_queries("laufschuhe")["rising"][:5]
    t.trending_now(geo="DE")[:10]

**Rate limits bite here.** Trends is the touchiest surface in this package: a
handful of `explore` calls in a burst earns a 429 that lasts minutes. Keep
`rate_limit=0.5` or lower for sustained runs.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterable
from typing import Any
from xml.etree import ElementTree as ET

from .._core.errors import NotFound, ParseError
from .._core.parse import parse_json, safe_get
from .._core.service import Service

API = "https://trends.google.com/trends/api"
BATCH_URL = "https://trends.google.com/_/TrendsUi/data/batchexecute"
TRENDING_RPCID = "i0OFE"
TRENDING_RSS = "https://trends.google.com/trending/rss"

WIDGET_ENDPOINT = {
    "TIMESERIES": "multiline",
    "GEO_MAP": "comparedgeo",
    "RELATED_TOPICS": "relatedsearches",
    "RELATED_QUERIES": "relatedsearches",
}

#: What Google calls the properties internally. "" is web search.
PROPERTIES = {
    "web": "",
    "images": "images",
    "news": "news",
    "youtube": "youtube",
    "shopping": "froogle",
}


class Trends(Service):
    """Interest over time, by region, related queries, and trending now."""

    def __init__(self, *args: Any, tz: int = -60, **kw: Any):
        """
        Args:
            tz: minutes-west-of-UTC offset Google stamps timestamps with.
                -60 is CET. It only shifts bucket boundaries, never values.
        """
        super().__init__(*args, **kw)
        self.tz = tz

    def _get(self, url: str, what: str) -> Any:
        return parse_json(
            self.client.get(url, headers={"referer": "https://trends.google.com/"}),
            what=what,
        )

    # ------------------------------------------------------------- primitives

    def explore(
        self,
        keywords: str | Iterable[str],
        *,
        geo: str = "",
        timeframe: str = "today 12-m",
        category: int = 0,
        property: str = "",
    ) -> dict[str, dict]:
        """Mint the widget tokens for one comparison. Returns `{widget_id: widget}`.

        Args:
            keywords: one term, or up to 5 to compare against each other.
            geo: `""` worldwide, `"DE"`, `"DE-BY"`, `"US-NY-501"` (metro).
            timeframe: `now 1-H`, `now 7-d`, `today 3-m`, `today 12-m`,
                `today 5-y`, `all`, or an explicit `"2024-01-01 2024-06-30"`.
            category: Google's category id (0 = all, 71 = food & drink, ...).
            property: `""` web, or `images` / `news` / `youtube` / `shopping`.
        """
        terms = [keywords] if isinstance(keywords, str) else list(keywords)
        if not 1 <= len(terms) <= 5:
            raise ValueError("Trends compares between 1 and 5 keywords")
        req = {
            "comparisonItem": [
                {"keyword": k, "geo": geo, "time": timeframe} for k in terms
            ],
            "category": category,
            "property": PROPERTIES.get(property, property),
        }
        url = (
            f"{API}/explore?hl={self.hl}&tz={self.tz}"
            f"&req={urllib.parse.quote(json.dumps(req, separators=(',', ':')))}"
        )
        data = self._get(url, "trends/explore")
        widgets = {w.get("id", ""): w for w in data.get("widgets", [])}
        if not widgets:
            raise NotFound(f"Trends has no data for {terms}")
        return widgets

    def _pick(self, widgets: dict[str, dict], wid: str) -> dict:
        """Comparisons number their widgets (`GEO_MAP_0`), single terms do not."""
        if wid in widgets:
            return widgets[wid]
        for key, w in widgets.items():
            if key.startswith(wid):
                return w
        raise NotFound(f"no {wid} widget for this query")

    def _widget_data(self, widget: dict) -> Any:
        """Call the data endpoint this widget's token was minted for."""
        wid = widget.get("id", "")
        endpoint = WIDGET_ENDPOINT.get(wid) or WIDGET_ENDPOINT.get(wid.rsplit("_", 1)[0])
        if not endpoint:
            raise ParseError(f"unknown Trends widget {wid!r}")
        url = (
            f"{API}/widgetdata/{endpoint}?hl={self.hl}&tz={self.tz}"
            f"&req={urllib.parse.quote(json.dumps(widget['request'], separators=(',', ':')))}"
            f"&token={urllib.parse.quote(widget['token'])}"
        )
        return self._get(url, f"trends/{endpoint}")

    @staticmethod
    def _terms(widget: dict) -> list[str]:
        items = safe_get(widget, "request", "comparisonItem", default=[]) or []
        out = []
        for i, item in enumerate(items):
            # Single-keyword requests nest the term one level deeper than
            # comparisons do, and entity requests carry no keyword at all.
            kw = item.get("keyword") or safe_get(
                item, "complexKeywordsRestriction", "keyword", 0, "value"
            )
            out.append(kw if isinstance(kw, str) else f"term_{i}")
        return out

    # ----------------------------------------------------------------- public

    def interest_over_time(self, keywords: str | Iterable[str], **kw: Any) -> list[dict]:
        """The 0-100 interest curve: one row per bucket, one column per term.

        The numbers are *relative*: 100 is the peak of this comparison, not an
        absolute volume. Comparing two separately fetched curves is therefore
        meaningless — put both terms in one call instead.
        """
        widget = self._pick(self.explore(keywords, **kw), "TIMESERIES")
        data = self._widget_data(widget)
        terms = self._terms(widget)
        out = []
        for point in safe_get(data, "default", "timelineData", default=[]) or []:
            row = {
                "date": point.get("formattedTime"),
                "timestamp": int(point["time"]) if point.get("time") else None,
                # The last bucket is usually incomplete and reads low; charts
                # that ignore this flag show a fake downtrend at the right edge.
                "partial": bool(point.get("isPartial")),
            }
            for i, term in enumerate(terms):
                row[term] = safe_get(point, "value", i)
            out.append(row)
        return out

    def interest_by_region(
        self, keywords: str | Iterable[str], *, resolution: str = "REGION", **kw: Any
    ) -> list[dict]:
        """Interest per geography. `resolution`: COUNTRY, REGION, CITY or DMA."""
        widget = json.loads(
            json.dumps(self._pick(self.explore(keywords, **kw), "GEO_MAP"))
        )
        widget["request"]["resolution"] = resolution
        data = self._widget_data(widget)
        terms = self._terms(widget)
        out = []
        for row in safe_get(data, "default", "geoMapData", default=[]) or []:
            rec = {"geo": row.get("geoName"), "geo_code": row.get("geoCode")}
            for i, term in enumerate(terms):
                rec[term] = safe_get(row, "value", i)
            out.append(rec)
        return out

    def _related(self, wid: str, keywords: Any, kw: dict) -> dict[str, list[dict]]:
        widget = self._pick(self.explore(keywords, **kw), wid)
        data = self._widget_data(widget)
        out: dict[str, list[dict]] = {"top": [], "rising": []}
        ranked = safe_get(data, "default", "rankedList", default=[]) or []
        for i, bucket in enumerate(ranked):
            key = "top" if i == 0 else "rising"
            for item in bucket.get("rankedKeyword", []):
                out[key].append(
                    {
                        "query": item.get("query") or safe_get(item, "topic", "title"),
                        "type": safe_get(item, "topic", "type"),
                        "mid": safe_get(item, "topic", "mid"),
                        # 0-100 for `top`; for `rising` it is percent growth,
                        # where a breakout arrives as the sentinel 5000.
                        "value": item.get("value"),
                        "formatted": item.get("formattedValue"),
                        "link": item.get("link"),
                    }
                )
        return out

    def related_queries(self, keywords: Any, **kw: Any) -> dict[str, list[dict]]:
        """`{"top": [...], "rising": [...]}` — the searches around a term."""
        return self._related("RELATED_QUERIES", keywords, kw)

    def related_topics(self, keywords: Any, **kw: Any) -> dict[str, list[dict]]:
        """Same shape, but Knowledge-Graph entities instead of raw queries."""
        return self._related("RELATED_TOPICS", keywords, kw)

    # ---------------------------------------------------------- trending now

    def trending_now(
        self, *, geo: str = "DE", hours: int = 48, limit: int = 50
    ) -> list[dict]:
        """What is spiking right now, with volume and the driving news stories.

        Uses the `i0OFE` RPC that replaced the retired `dailytrends` endpoint.
        `hours` is the lookback window Google ranks within (4, 24, 48 or 168 in
        the UI).
        """
        payload = [None, None, geo.upper(), 0, self.hl, hours]
        res = self.client.batchexecute(
            BATCH_URL,
            TRENDING_RPCID,
            TRENDING_RPCID,
            payload,
            source_path="/trending",
            extra_headers={
                "referer": "https://trends.google.com/",
                "origin": "https://trends.google.com",
            },
        )
        out = []
        for row in (safe_get(res, 1, default=[]) or [])[:limit]:
            if not (isinstance(row, list) and isinstance(safe_get(row, 0), str)):
                continue
            out.append(
                {
                    "term": row[0],
                    "geo": safe_get(row, 2),
                    "started": safe_get(row, 3, 0),
                    "ended": safe_get(row, 4, 0),
                    # Google buckets volume (2000, 20000, 200000, ...) rather
                    # than reporting it exactly: an order of magnitude, not a
                    # count.
                    "volume": safe_get(row, 6),
                    # Percent growth over the window. 1000 means "10x".
                    "growth_pct": safe_get(row, 8),
                    "trend_keywords": [
                        k
                        for k in (safe_get(row, 9, default=[]) or [])
                        if isinstance(k, str)
                    ],
                    "categories": [
                        c
                        for c in (safe_get(row, 10, default=[]) or [])
                        if isinstance(c, int)
                    ],
                    # Slot 11 carries article IDs (`[id, lang, geo]`), not
                    # articles: the UI resolves them through a second RPC whose
                    # payload shape is undocumented. `trending_rss()` returns
                    # the same trends *with* their news items — use that when
                    # the headlines matter.
                    "news_ids": [
                        safe_get(n, 0)
                        for n in (safe_get(row, 11, default=[]) or [])
                        if isinstance(n, list)
                    ],
                }
            )
        return out

    def trending_rss(self, *, geo: str = "DE", limit: int = 50) -> list[dict]:
        """The same trending list as an un-gated RSS feed.

        No cookies, no RPC payload to keep in sync — the right choice for a cron
        job that just wants "what is hot in DE right now".
        """
        ns = {"ht": "https://trends.google.com/trending/rss"}
        xml = self.client.get(
            f"{TRENDING_RSS}?geo={geo.upper()}", headers={"accept": "application/xml"}
        )
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise ParseError("trending rss was not XML") from e
        out = []
        for item in root.findall("./channel/item")[:limit]:
            out.append(
                {
                    "term": item.findtext("title"),
                    "traffic": item.findtext("ht:approx_traffic", namespaces=ns),
                    "published": item.findtext("pubDate"),
                    "picture": item.findtext("ht:picture", namespaces=ns),
                    "news": [
                        {
                            "title": n.findtext("ht:news_item_title", namespaces=ns),
                            "url": n.findtext("ht:news_item_url", namespaces=ns),
                            "source": n.findtext("ht:news_item_source", namespaces=ns),
                        }
                        for n in item.findall("ht:news_item", ns)
                    ],
                }
            )
        return out

    # ----------------------------------------------------------------- export

    def csv(
        self,
        keywords: str | Iterable[str],
        *,
        kind: str = "timeseries",
        resolution: str | None = None,
        path: str | None = None,
        **kw: Any,
    ) -> str:
        """The exact CSV the Trends UI's download button produces.

        Google has a `/csv` twin of every widget data endpoint, so this is the
        real export — same header lines, same localised number and date
        formats — not a re-serialisation of the JSON.

        Args:
            kind: `timeseries`, `geo` or `related`.
            resolution: for `kind="geo"`: COUNTRY, REGION, CITY, DMA.
            path: write the CSV here as well as returning it.
        """
        wid = {
            "timeseries": "TIMESERIES",
            "geo": "GEO_MAP",
            "related": "RELATED_QUERIES",
            "related_topics": "RELATED_TOPICS",
        }.get(kind)
        if not wid:
            raise ValueError(
                f"unknown kind {kind!r} (timeseries, geo, related, related_topics)"
            )
        widget = self._pick(self.explore(keywords, **kw), wid)
        endpoint = WIDGET_ENDPOINT[wid]
        request = widget["request"]
        if resolution:
            request = json.loads(json.dumps(request))
            request["resolution"] = resolution
        url = (
            f"{API}/widgetdata/{endpoint}/csv?hl={self.hl}&tz={self.tz}"
            f"&req={urllib.parse.quote(json.dumps(request, separators=(',', ':')))}"
            f"&token={urllib.parse.quote(widget['token'])}"
        )
        text = self.client.get(url, headers={"referer": "https://trends.google.com/"})
        if path:
            from pathlib import Path

            Path(path).write_text(text, encoding="utf-8")
        return text

    def by_country(
        self,
        keywords: str | Iterable[str],
        countries: Iterable[str],
        *,
        method: str = "interest_over_time",
        **kw: Any,
    ) -> dict[str, Any]:
        """Run any Trends method once per country and key the results by geo.

            t.by_country("peniaze", ["SK", "CZ", "AT"], timeframe="now 1-d")

        Sequential on purpose: Trends 429s hard on parallel `explore` calls, and
        a partially-429'd sweep is worse than a slow one. Countries that fail
        land in the result as `{"error": "..."}` instead of killing the sweep.
        """
        fn = getattr(self, method)
        out: dict[str, Any] = {}
        for geo in countries:
            try:
                out[geo] = fn(keywords, geo=geo, **kw)
            except Exception as e:
                out[geo] = {"error": f"{type(e).__name__}: {e}"[:200]}
        return out

    def autocomplete(self, query: str) -> list[dict]:
        """Trends' own entity autocomplete — resolves a term to Knowledge topics.

        Useful before `related_topics`: a `mid` searches the *entity*
        ("Laufschuh, Produktkategorie") rather than the literal words.
        """
        url = f"{API}/autocomplete/{urllib.parse.quote(query)}?hl={self.hl}&tz={self.tz}"
        return (
            safe_get(
                self._get(url, "trends/autocomplete"), "default", "topics", default=[]
            )
            or []
        )


__all__ = ["PROPERTIES", "Trends"]
