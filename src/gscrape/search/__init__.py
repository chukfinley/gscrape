"""Google Web Search.

## The session gate

`google.com/search` answers a plain-HTTP client only when its cookie jar was
minted in a real browser. Without that, every request returns a ~90 KB
JavaScript shell whose only content is a redirect to
`/httpservice/retry/enablejs`.

The gate is `SOCS` + `NID` — the same pair Maps wants — but with one extra
condition that took a bisect to find: the `NID` has to come from a **browser**
session. A `NID` minted by this package's own consent POST is refused
indefinitely; unlike the Maps gate it does not warm up, no matter how many
requests it sees. Cookie sets were minimised one cookie at a time to confirm
that `SOCS`+`NID` alone are sufficient, and that everything else in a browser
jar (`AEC`, `DV`, `SNID`, `OTZ`, `GOOGLE_ABUSE_EXEMPTION`, …) is optional.

So the handoff is once per IP, and cheap:

    from gscrape import Search
    s = Search(hl="de", gl="de")
    s.client.import_cookies("NID=...; SOCS=...")   # copied from devtools
    s.web("beste laufschuhe", limit=30)

After that the jar is cached to disk and Python scrapes on its own. If the IP
is flagged you will get `Captcha` instead — solve it in the browser on the same
IP, then re-import.

## What is JavaScript-only

* **AI Overview** and **AI Mode** (`udm=50`). The answer is fetched by an
  `/async/callback:*` request whose `fc` token is computed by page JS; the
  token appears nowhere in the HTML, not even for a real browser fetching
  without JS. `ai_overview()` therefore extracts the block when Google happens
  to inline it and raises `JsRequired` otherwise, instead of pretending.
Image search (`udm=2`) needs the same browser jar and then works: `images()`
returns original image URLs with their real dimensions and source pages, not
thumbnails.

## The no-browser alternative

`cse()` — the Programmable Search Element API. Free, key-less, no browser, real
Google web results. One setup step: create an engine at
<https://programmablesearchengine.google.com>, switch on "Search the entire
web", pass its `cx`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from selectolax.parser import HTMLParser

from .._core.errors import Blocked, JsRequired, ParseError
from .._core.service import Service

CSE_JS = "https://cse.google.com/cse.js"
CSE_ELEMENT = "https://cse.google.com/cse/element/v1"
SERP_URL = "https://www.google.com/search"

_TOKEN_RE = re.compile(r'"cse_token"\s*:\s*"([^"]+)"')
_CALLBACK_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.S)
# "RunningXpert.com 09.04.2026 — real snippet starts here". The source label
# and the publication date both sit in front of the snippet text, and only the
# date is worth keeping as data.
_DATE_PREFIX_RE = re.compile(
    r"^(?:.{0,45}?\s)??(\d{1,2}\.\d{1,2}\.\d{4}|\w{3,9} \d{1,2}, \d{4})\s*[—–-]\s*"  # noqa: RUF001 - Google's own em/en dashes
)

SESSION_HELP = (
    "Web Search only answers a plain-HTTP client whose cookie jar was minted in "
    "a real browser: SOCS + NID together, and the NID has to come from a browser "
    "session, not from the consent POST this package can do on its own (a "
    "self-minted NID is refused indefinitely — it does not warm up). "
    "Open google.com in a browser on THIS egress IP, solve the captcha if one "
    "appears, copy the Cookie header from devtools, then "
    "`client.import_cookies('NID=...; SOCS=...')`. The jar then works from "
    "Python for as long as it stays valid. Alternatively use cse(cx=...), which "
    "needs no browser at all."
)


def parse_serp(html: str) -> list[dict]:
    """Extract organic results from a SERP page.

    Parsed structurally, not by class name: every organic result is an
    `<a href="http…">` containing an `<h3>`. Google's class names (`tF2Cxc`,
    `LC20lb`, `VwiC3b`, …) are regenerated regularly and a class-based parser
    dies with them; the anchor/h3 relationship has held for a decade.

    The snippet is recovered by text subtraction rather than by hunting for the
    right descendant: a result container's text is its link text followed by
    its snippet, so `block.text()` minus `anchor.text()` is the snippet. Two
    text extractions per result instead of a few hundred CSS queries — the
    whole page parses in ~2 ms.
    """
    tree = HTMLParser(html)
    root = tree.css_first("div#rso") or tree.css_first("div#search") or tree.body
    if root is None:
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for a in root.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href.startswith("http") or href in seen:
            continue
        if "google.com/" in href.split("?", 1)[0]:
            continue  # Google's own chrome: cached pages, translate, maps links
        h3 = a.css_first("h3")
        if h3 is None:
            continue
        seen.add(href)

        title = h3.text(strip=True)
        snippet, published = _snippet_of(a, title)
        out.append(
            {
                "position": len(out) + 1,
                "title": title,
                "url": href,
                "display_url": _nearest_cite(a),
                "snippet": snippet,
                "published": published,
            }
        )
    return out


#: Trailing UI labels Google appends inside the result container.
_TRAILING_JUNK = re.compile(
    r"(Webergebnisse|Web results|Im Cache|Cached|Ähnliche Seiten|Similar pages)\s*$"
)


def _snippet_of(anchor, title: str, max_levels: int = 7):
    """Walk up from the title link until the leftover text is a real snippet.

    At each ancestor the link's own text and any breadcrumb `<cite>` are
    subtracted (Google renders both twice — once visible, once for screen
    readers), and the walk stops at the first level where 60+ characters
    survive. Both halves matter: stopping on raw text growth lands on the
    accessibility duplicate, and a fixed nesting depth breaks whenever Google
    redesigns.

    Returns `(snippet, published)` — many snippets carry a leading publication
    date ("09.04.2026 — …"), which becomes its own field.
    """
    link = anchor.text(separator=" ", strip=True)
    node = anchor
    for _ in range(max_levels):
        node = node.parent
        if node is None:
            break
        full = node.text(separator=" ", strip=True)
        if len(full) < len(link) + 60:
            continue
        rest = full.replace(link, " ", 1)
        for cite in node.css("cite"):
            rest = rest.replace(cite.text(separator=" ", strip=True), " ")
        rest = _TRAILING_JUNK.sub("", " ".join(rest.split())).strip()
        if len(rest) < 60:
            continue
        m = _DATE_PREFIX_RE.match(rest)
        if m:
            return rest[m.end() :].strip(), m.group(1)
        return rest, None
    return None, None


# One image result, as Google embeds it in the page's JS state:
#   ["<id>", [thumb_url, w, h], [original_url, w, h], … "2003": [null, id,
#    page_url, page_title, …]]
# Matching the thumbnail/original pair and then reading the page metadata that
# follows is far cheaper than walking the (megabyte-sized) state array.
_IMAGE_PAIR_RE = re.compile(
    r'\["(https://encrypted-tbn[^"]+)",(\d+),(\d+)\],'
    r'\["(https?://[^"]+?)",(\d+),(\d+)\]'
)
_IMAGE_PAGE_RE = re.compile(r'"2003":\[null,"[^"]*","([^"]+)","((?:[^"\\]|\\.)*)"')
_IMAGE_DOMAIN_RE = re.compile(r'"2000":\[null,"([^"]+)","([^"]*)"')


def _unescape_json(text: str) -> str:
    """Decode a raw JSON string body (escapes included) without mojibake."""
    try:
        return json.loads(f'"{text}"')
    except json.JSONDecodeError:
        return text


def _unescape_js(text: str) -> str:
    """Google escapes `=` and `&` inside its embedded JSON strings."""
    return text.replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")


def parse_image_serp(html: str, limit: int = 100) -> list[dict]:
    """Extract image results (original URLs, not thumbnails) from a `udm=2` page."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _IMAGE_PAIR_RE.finditer(html):
        original = _unescape_js(m.group(4))
        if original in seen:
            continue
        seen.add(original)
        tail = html[m.end() : m.end() + 1200]
        page = _IMAGE_PAGE_RE.search(tail)
        domain = _IMAGE_DOMAIN_RE.search(tail)
        out.append(
            {
                "position": len(out) + 1,
                "url": original,
                "width": int(m.group(5)),
                "height": int(m.group(6)),
                "thumbnail": _unescape_js(m.group(1)),
                "source_url": _unescape_js(page.group(1)) if page else None,
                # json.loads decodes \uXXXX correctly; the unicode_escape
                # codec would mangle every umlaut into mojibake.
                "source_title": _unescape_json(page.group(2)) if page else None,
                "source_domain": domain.group(1) if domain else None,
                "file_size": domain.group(2) if domain else None,
            }
        )
        if len(out) >= limit:
            break
    return out


#: Where Google puts the AI answer when it ships it inside the page.
_AI_SELECTORS = (
    '[data-subtree="aimc"]',
    '[data-attrid*="AIOverview"]',
    'div[jsname="ZLcrmb"]',
)


def _ai_block(html: str) -> dict | None:
    """Pull an inlined AI Overview out of a SERP, with its source links."""
    tree = HTMLParser(html)
    node = next(
        (n for sel in _AI_SELECTORS if (n := tree.css_first(sel)) is not None), None
    )
    if node is None:
        return None
    text = node.text(separator="\n", strip=True)
    if len(text) < 40:
        return None
    sources = []
    for a in node.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith("http") and "google.com/" not in href.split("?", 1)[0]:
            sources.append({"title": a.text(strip=True) or None, "url": href})
    return {"text": text, "sources": sources}


def _nearest_cite(anchor) -> str | None:
    """The visible breadcrumb URL, which Google renders next to the link."""
    node = anchor
    for _ in range(4):
        node = node.parent
        if node is None:
            return None
        cite = node.css_first("cite")
        if cite is not None:
            return cite.text(strip=True)
    return None


class Search(Service):
    """Web results, via the one door Google left open."""

    def __init__(self, *args: Any, cx: str | None = None, **kw: Any):
        """
        Args:
            cx: Programmable Search Engine id. Also read from `$GSCRAPE_CSE_CX`.
                Without it only `web()`'s error message is available.
        """
        super().__init__(*args, **kw)
        self.cx = cx or os.environ.get("GSCRAPE_CSE_CX")
        self._token: str | None = None

    # -------------------------------------------------------------- the wall

    def web(
        self,
        query: str,
        *,
        limit: int = 10,
        start: int = 0,
        clean: bool = True,
        site: str | None = None,
        when: str | None = None,
        safe: str = "off",
    ) -> list[dict]:
        """Scrape google.com/search. **Needs a browser-minted cookie jar.**

        Args:
            limit: results wanted; Google pages 10 at a time.
            clean: `udm=14`, the web-only mode — no AI overview, no shopping
                carousel, no video shelf. Smaller payload, cleaner parse.
            site: restrict to a domain.
            when: `d`, `w`, `m`, `y` — recency filter (`tbs=qdr:`).

        Raises `JsRequired` when the jar is not trusted (see `SESSION_HELP`).
        Failing loudly matters here: returning [] would make callers believe
        their query had no results.
        """
        pages = -(-limit // 10)  # ceil
        urls = [
            self._serp_url(
                query, start + i * 10, clean=clean, site=site, when=when, safe=safe
            )
            for i in range(pages)
        ]
        # Pages are independent, so they are fetched at once: 5 pages take one
        # round trip instead of five. Google's per-IP limiter is the ceiling
        # here, which is what `rate_limit=` on the client is for.
        if len(urls) == 1:
            bodies = [self.client.get(urls[0], headers={"accept": "text/html"})]
        else:
            with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
                bodies = list(
                    pool.map(
                        lambda u: self.client.get(u, headers={"accept": "text/html"}),
                        urls,
                    )
                )

        out: list[dict] = []
        seen: set[str] = set()
        for html in bodies:
            rows = parse_serp(html)
            if not rows and "SearchResultsPage" not in html[:4000]:
                # A 200 with no results and no results-page marker is the shell
                # or an unknown layout — surface it instead of reporting "no
                # results", which is what callers would otherwise conclude.
                raise JsRequired(
                    f"google.com/search returned {len(html)} bytes without "
                    f"results. {SESSION_HELP}"
                )
            for row in rows:
                if row["url"] in seen:
                    continue
                seen.add(row["url"])
                row["position"] = len(out) + 1
                out.append(row)
        return out[:limit]

    def ai_overview(self, query: str) -> dict:
        """Google's AI answer for a query, when it can be had over plain HTTP.

        Explicitly separate from `web()`: an AI summary is a different thing
        from a result list, and mixing it into ordinary results would be
        nonsense.

        Reality check: the AI Overview block is normally fetched by an
        `/async/callback:*` request whose `fc` token page JavaScript computes —
        it is not in the HTML, and neither is it there when a real browser
        fetches the page without running JS. This method extracts the block on
        the queries and locales where Google does inline it, and raises
        `JsRequired` otherwise rather than returning an empty answer.
        """
        html = self.client.get(
            f"{SERP_URL}?{urllib.parse.urlencode({'q': query, 'hl': self.hl, 'gl': self.gl})}",
            headers={"accept": "text/html"},
        )
        block = _ai_block(html)
        if block is None:
            raise JsRequired(
                "no AI overview in the HTML for this query — Google loads it "
                "through an async call whose token is computed by page "
                "JavaScript. Nothing in a plain-HTTP client can produce that "
                "token."
            )
        return block

    def _serp_url(
        self,
        query: str,
        start: int,
        *,
        clean: bool,
        site: str | None,
        when: str | None,
        safe: str,
    ) -> str:
        q = f"{query} site:{site}" if site else query
        params = {
            "q": q,
            "hl": self.hl,
            "gl": self.gl,
            "num": 10,
            "safe": safe,
        }
        if clean:
            params["udm"] = 14  # web-only results
        if start:
            params["start"] = start
        if when:
            params["tbs"] = f"qdr:{when}"
        return f"{SERP_URL}?{urllib.parse.urlencode(params)}"

    # ---------------------------------------------------------------- the door

    def _cse_token(self, cx: str, *, refresh: bool = False) -> str:
        """Harvest the `cse_token` the element API demands, from `cse.js`.

        The token is per-engine and short-lived-ish; it is cached on the
        instance and re-harvested when the API rejects it.
        """
        if self._token and not refresh:
            return self._token
        js = self.client.get(
            f"{CSE_JS}?cx={urllib.parse.quote(cx)}",
            headers={"accept": "text/javascript", "referer": "https://cse.google.com/"},
        )
        m = _TOKEN_RE.search(js)
        if not m:
            raise Blocked(
                f"no cse_token in cse.js for cx={cx!r} — is the engine id right "
                "and the engine public?"
            )
        self._token = m.group(1)
        return self._token

    def cse(
        self,
        query: str,
        *,
        cx: str | None = None,
        limit: int = 10,
        start: int = 0,
        safe: str = "off",
        search_type: str | None = None,
        site: str | None = None,
        sort: str = "",
    ) -> list[dict]:
        """Real Google web results through the Programmable Search Element API.

        Free and key-less, but bound to a `cx` engine. An engine configured to
        "search the entire web" returns ordinary Google results, minus ads and
        SERP features.

        Args:
            limit: results wanted; the API pages 10 at a time.
            search_type: `"image"` for image results.
            site: restrict to a domain (appended as `site:` to the query).
            sort: `"date"` for recency instead of relevance.
        """
        cx = cx or self.cx
        if not cx:
            raise ValueError(
                "cse() needs a Programmable Search Engine id — pass cx= or set "
                "$GSCRAPE_CSE_CX (create one free at "
                "https://programmablesearchengine.google.com)"
            )
        q = f"{query} site:{site}" if site else query

        out: list[dict] = []
        offset = start
        while len(out) < limit:
            rows, total = self._cse_page(
                cx, q, offset, safe=safe, search_type=search_type, sort=sort
            )
            if not rows:
                break
            out.extend(rows)
            offset += len(rows)
            if offset >= total:
                break
        return out[:limit]

    def _cse_page(
        self,
        cx: str,
        query: str,
        start: int,
        *,
        safe: str,
        search_type: str | None,
        sort: str,
        _retried: bool = False,
    ) -> tuple[list[dict], int]:
        params = {
            "rsz": "filtered_cse",
            "num": 10,
            "hl": self.hl,
            "gl": self.gl,
            "source": "gcsc",
            "cx": cx,
            "q": query,
            "safe": safe,
            "cse_tok": self._cse_token(cx),
            "sort": sort,
            "exp": "cc_apiv3",
            "callback": "cb",
            "start": start,
        }
        if search_type:
            params["searchType"] = search_type
        url = f"{CSE_ELEMENT}?{urllib.parse.urlencode(params)}"
        body = self.client.get(
            url, headers={"referer": "https://cse.google.com/", "accept": "*/*"}
        )
        m = _CALLBACK_RE.match(body.strip())
        if not m:
            raise ParseError(f"cse element returned {len(body)} bytes of non-JSONP")
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise ParseError("cse element payload was not JSON") from e

        if data.get("error") and not _retried:
            # Stale token: harvest a fresh one and try this page once more.
            self._cse_token(cx, refresh=True)
            return self._cse_page(
                cx,
                query,
                start,
                safe=safe,
                search_type=search_type,
                sort=sort,
                _retried=True,
            )

        total = int((data.get("cursor") or {}).get("estimatedResultCount") or 0)
        return [self._shape(r) for r in data.get("results", [])], total

    @staticmethod
    def _shape(row: dict) -> dict:
        rich = row.get("richSnippet") or {}
        return {
            "title": row.get("titleNoFormatting") or row.get("title"),
            "url": row.get("unescapedUrl") or row.get("url"),
            "display_url": row.get("visibleUrl"),
            "snippet": row.get("contentNoFormatting") or row.get("content"),
            "breadcrumb": row.get("breadcrumbUrl"),
            "thumbnail": (rich.get("cseImage") or {}).get("src")
            or (rich.get("cseThumbnail") or {}).get("src"),
            "published": (rich.get("metatags") or {}).get("articlePublishedTime"),
            "site_name": (rich.get("metatags") or {}).get("ogSiteName"),
        }

    def images(self, query: str, *, limit: int = 50, safe: str = "off") -> list[dict]:
        """Image search (`udm=2`), returning **original** URLs, not thumbnails.

        Needs the same browser-minted jar as `web()`. Each row carries the
        image's real dimensions plus the page it sits on, which is what makes
        the results usable for anything beyond a preview grid.
        """
        params = {"q": query, "udm": 2, "hl": self.hl, "gl": self.gl, "safe": safe}
        html = self.client.get(
            f"{SERP_URL}?{urllib.parse.urlencode(params)}",
            headers={"accept": "text/html"},
        )
        rows = parse_image_serp(html, limit=limit)
        if not rows:
            raise JsRequired(
                f"image search returned {len(html)} bytes without image data. "
                f"{SESSION_HELP}"
            )
        return rows

    def cse_images(self, query: str, **kw: Any) -> list[dict]:
        """Image results through the Programmable Search API instead (no browser)."""
        return self.cse(query, search_type="image", **kw)


__all__ = ["Search"]
