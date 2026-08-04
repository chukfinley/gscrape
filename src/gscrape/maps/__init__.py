"""Google Maps over plain HTTP. No browser.

Talks to the same internal endpoints the Maps web app uses
(`/maps/preview/place`, `search?tbm=map`, the `MapsUgcPostService` RPC bus) with
a real Chrome TLS fingerprint. One place = ~2 requests = ~300 ms, versus ~30 s
for a Playwright scraper.

## How this was reverse engineered

1. A browser recorded every `/maps/rpc/*` + `/maps/preview/*` request the Maps
   web app fires when a place page and its photo viewer are opened.
2. The photo grid turned out to need NO extra XHR: everything the viewer shows
   already sits in the single `/maps/preview/place` response.
3. Replaying that request with plain `requests` returned a 23 KB stub instead of
   the 127 KB full payload. Bisecting the browser's cookie jar showed the gate
   is `SOCS` + `NID` together — see `_core/consent.py`.
4. Reviews moved off `/maps/rpc/listugcposts` (that endpoint now answers 403)
   onto the batchexecute bus, which is BotGuard-gated — see `reviews()`.

    from gscrape import Maps
    m = Maps()
    place = m.details(place_id="ChIJd3UFGwNvs0cRCqhQbp-i6Jk")
    print(place["name"], place["rating"], len(place["photos"]))
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .._core.consent import bootstrap as _bootstrap
from .._core.errors import Blocked, GoogError, NotFound, ParseError
from .._core.parse import parse_json, safe_get
from .._core.service import Service
from . import extract, pb

BATCH_URL = "https://www.google.com/maps/_/MapsWizUi/data/batchexecute"
REVIEWS_RPCID = "qv9Egd"
REVIEWS_RPC = "/MapsUgcPostService.ListUgcPosts"
BGKEY_META = "maps_bgkey"

# A rich `preview/place` body is 60-400 KB; the cookie-gated stub is ~23 KB.
# Size alone is a bad test — a rural place with 3 photos legitimately lands
# under 45 KB — so `looks_gated` also checks content.
RICH_MIN_BYTES = 45_000

# Any real, photo-heavy place works as the bootstrap reference. This one is a
# German restaurant that has been stable for years.
PROBE_FID = "0x47b36f031b057577:0x99e8a29f6e50a80a"
PROBE_LAT, PROBE_LNG = 54.6486428, 9.4130149


#: Maps' own prefetch of its results request, sitting in the page head.
_SEARCH_LINK_RE = re.compile(r'<link href="(/search\?tbm=map[^"]+)"')
_OFFSET_RE = re.compile(r"!8i\d+")

#: Results per `tbm=map` page. `!7i20` in the pb asks for this many.
PAGE_SIZE = 20

#: Used only when Maps stops shipping the prefetch link. Captured 2026-08;
#: the toggles decide which fields each place record carries.
PB_FALLBACK = (
    "!1s{query}!7i20!10b1!12m58!1m5!18b1!30b1!31m1!1b1!34e1!2m4!5m1!6e2!20e3!39b1"
    "!6m31!32i1!49b1!63m0!66b1!85b1!114b1!149b1!206b1!209b1!212b1!215b1!216b1"
    "!222b1!223b1!232b1!234b1!235b1!246b1!253b1!260b1!262b1!266b1!270b1!271b1"
    "!273b1!280b1!281b1!286b1!291m0!302i300!303i100!10b1!12b1!13b1!14b1!16b1"
    "!17m1!3e1!20m3!5e2!6b1!14b1!46m1!1b0!96b1!99b1!19m4!2m3!1i360!2i120!4i8"
    "!20m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
    "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3"
    "!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
    "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
    "!24m107!1m25!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
    "!18m14!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!32b1!33m1!1b1!34b1!36e2"
    "!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1"
    "!36b1!37b1!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1"
    "!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
    "!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
    "!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
    "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
    "!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1"
    "!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b1"
    "!26m4!2m3!1i80!2i92!4i8!34m19!2b1!3b1!4b1!6b1!8m6!1b1!3b1!4b1!5b1!6b1!7b1"
    "!9b1!12b1!14b1!20b1!23b1!25b1!26b1!31b1!37m1!1e81!42b1"
    "!49m10!3b1!6m2!1b1!2b1!7m2!1e3!2b1!8b1!9b1!10e2!50m3!2e2!3m1!3b1!61b1"
    "!67m5!7b1!10b1!14b1!15m1!1b0!69i789!77b1"
)


def _with_offset(url: str, offset: int) -> str:
    """Page the results request.

    `!8i<n>` is the result offset, and it only works as a **top-level** field,
    i.e. appended at the very end of the pb. Inserting it next to the page-size
    field (`!7i20`) parses fine and is silently ignored — the same 20 results
    come back, which is exactly the kind of bug that looks like "Maps has no
    more results" instead of like a broken request.
    """
    if _OFFSET_RE.search(url):
        return _OFFSET_RE.sub(f"!8i{offset}", url)
    return f"{url}!8i{offset}"


def find_places(payload: Any) -> list[list]:
    """Collect the place records in a `tbm=map` response, by shape not by slot.

    Google moved these from `data[0][1][*][14]` to `data[64][*][1]` in 2026 and
    will move them again. A place record is recognisable on its own: a long
    list whose slot 11 is the name and whose slot 10 is a `0x...:0x...` feature
    id. Matching on that survives the reshuffles; indexing does not.
    """
    out: list[list] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, list):
            return
        fid = safe_get(node, 10)
        if (
            len(node) > 78
            and isinstance(safe_get(node, 11), str)
            and isinstance(fid, str)
            and fid.startswith("0x")
        ):
            if fid not in seen:
                seen.add(fid)
                out.append(node)
            return
        for v in node:
            visit(v)

    visit(payload)
    return out


def looks_gated(body: str) -> bool:
    """True when Google served the cookie-gated stub rather than the real thing.

    Deliberately biased towards false positives: a wrong "gated" verdict costs
    one extra bootstrap (`place()` retries exactly once, then accepts whatever
    comes back), while a missed stub would silently hand callers a place with no
    photos, no hours and no attributes.
    """
    if len(body) >= RICH_MIN_BYTES:
        return False
    return body.count("googleusercontent.com/gps-cs") <= 2


class Maps(Service):
    """Places, photos, hours, menus, popular times and reviews."""

    needs_consent = True

    def __init__(self, *args: Any, bgkey: str | None = None, **kw: Any):
        """
        Args:
            bgkey: a BotGuard `x-maps-bgkey` token, needed ONLY for deep review
                pagination. Copy it out of your browser's devtools (any Maps
                reviews XHR carries it) or set `$GSCRAPE_MAPS_BGKEY`. One key
                works for every place and survives sessions. Everything else in
                this class works without it.
        """
        super().__init__(*args, **kw)
        key = bgkey or os.environ.get("GSCRAPE_MAPS_BGKEY")
        if key:
            self.client.meta[BGKEY_META] = key

    # ------------------------------------------------------------- bootstrap

    @property
    def bgkey(self) -> str | None:
        return self.client.meta.get(BGKEY_META)

    def bootstrap(self, force: bool = False) -> None:
        """Run the consent flow until Maps serves rich payloads. Cached per IP."""
        _bootstrap(self.client, self._probe, force=force)

    def _probe(self) -> bool:
        """One cheap `preview/place` call; True when the full payload comes back."""
        try:
            body = self._place_raw(PROBE_FID, PROBE_LAT, PROBE_LNG)
        except GoogError as e:
            self.client.log("probe error", e)
            return False
        # The probe always hits the SAME known-rich reference place, so the
        # strict size test is valid here and must stay strict: accepting a stub
        # would end the warm-up early and cache a jar that never ripens.
        return len(body) >= RICH_MIN_BYTES

    # ------------------------------------------------------------ primitives

    def resolve(self, place_id: str) -> dict:
        """`placeId` -> `{fid, lat, lng}`. One ~0.25 s HTML fetch.

        The feature id (`0x...:0x...`) is what the internal endpoints speak;
        the `ChIJ...` place id only exists for the public API and permalinks.
        """
        self._ensure()
        url = "https://www.google.com/maps/place/?q=place_id:" + urllib.parse.quote(
            place_id
        )
        html = self.client.get(url, headers={"accept": "text/html"})
        import re

        m = re.search(r"(0x[0-9a-f]+:0x[0-9a-f]+)", html)
        if not m:
            raise NotFound(f"no feature id found for place_id {place_id!r}")
        lat = lng = 0.0
        c = re.search(r"@(-?[\d.]+),(-?[\d.]+)", html)
        if c:
            lat, lng = float(c.group(1)), float(c.group(2))
        return {"fid": m.group(1), "lat": lat, "lng": lng}

    def _place_raw(self, fid: str, lat: float, lng: float, session: str = "_") -> str:
        params = pb.place(fid, lat, lng, session)
        url = (
            f"https://www.google.com/maps/preview/place?authuser=0"
            f"&hl={self.hl}&gl={self.gl}"
            f"&pb={urllib.parse.quote(params, safe='')}"
        )
        return self.client.get(url, headers={"referer": "https://www.google.com/maps/"})

    def place(self, fid: str, lat: float = 0.0, lng: float = 0.0, retry: bool = True):
        """Fetch + parse `/maps/preview/place`. Re-bootstraps once if gated."""
        self._ensure()
        body = self._place_raw(fid, lat, lng)
        if retry and looks_gated(body):
            self.client.log("stub payload, re-bootstrapping")
            self.bootstrap(force=True)
            body = self._place_raw(fid, lat, lng)
        return parse_json(body, what="preview/place")

    # ---------------------------------------------------------------- public

    def search_url(self, query: str) -> str:
        """The `search?tbm=map` URL Maps itself would call for this query.

        The bare endpoint (`search?tbm=map&q=...`) answers a 6 KB stub: since
        2026 it only returns results when the `pb=` request proto is present,
        and that proto is long (2 KB of feature toggles) and changes with Maps
        releases.

        Rather than hardcode it, this reads it back off Maps' own page: the
        `/maps/search/<query>` HTML carries the exact request as a prefetch
        `<link>` in its head, session id and all. One cheap HTML fetch buys a
        pb that is never out of date. `PB_FALLBACK` covers the day that link
        disappears.
        """
        page = self.client.get(
            f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"
            f"?hl={self.hl}&gl={self.gl}",
            headers={"accept": "text/html"},
        )
        m = _SEARCH_LINK_RE.search(page)
        if m:
            return "https://www.google.com" + html.unescape(m.group(1))
        self.client.log("no prefetch link in maps HTML, using the pb fallback")
        return (
            f"https://www.google.com/search?tbm=map&authuser=0"
            f"&hl={self.hl}&gl={self.gl}&q={urllib.parse.quote(query)}"
            f"&pb={PB_FALLBACK.format(query=urllib.parse.quote(query))}"
        )

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        with_photos: bool = False,
        start: int = 0,
    ) -> list[dict]:
        """Free-text place lookup — 20 results per page, paginated to `limit`.

        Returns the same flat dicts as `details()`, built from the same place
        objects: `search?tbm=map` embeds full place records, not summaries. They
        are slightly thinner (no menu card, fewer photos), so use `details()`
        when completeness matters.
        """
        self._ensure()
        base = self.search_url(query)
        out: list[dict] = []
        seen: set[str] = set()
        offset = start
        while len(out) < limit:
            url = base if not offset else _with_offset(base, offset)
            # A non-JSON body means consent/captcha/HTML, i.e. the session went
            # bad. Swallowing that as "no results" is the worst failure mode for
            # a batch matcher: it silently turns every row into a no-match.
            # Re-bootstrap once, then surface the problem.
            for attempt in (0, 1):
                try:
                    data = parse_json(self.client.get(url), what="tbm=map")
                    break
                except ParseError:
                    if attempt == 0:
                        self.client.log("search returned non-JSON, re-bootstrapping")
                        self.bootstrap(force=True)
                        continue
                    raise

            page = find_places(data)
            if not page:
                break
            fresh = 0
            for p in page:
                fid = safe_get(p, 10)
                if fid in seen:
                    continue  # pages overlap by a few results
                seen.add(fid)
                fresh += 1
                out.append(
                    extract.shape(
                        p,
                        place_id=safe_get(p, 78),
                        fid=fid,
                        with_photos=with_photos,
                    )
                )
            offset += len(page)
            # Maps repeats results near the end of a result set rather than
            # returning an empty page, so "nothing new" is the stop signal.
            if len(page) < PAGE_SIZE or not fresh:
                break
        return out[:limit]

    def details(
        self,
        place_id: str | None = None,
        *,
        fid: str | None = None,
        lat: float = 0.0,
        lng: float = 0.0,
        with_photos: bool = True,
    ) -> dict:
        """Everything about one place, in ~2 HTTP requests.

        Pass either a `place_id` (costs one extra resolve request) or the `fid`
        directly. `session_id` in the result feeds `reviews()`.
        """
        if not fid:
            if not place_id:
                raise ValueError("need place_id or fid")
            loc = self.resolve(place_id)
            fid, lat, lng = loc["fid"], loc["lat"] or lat, loc["lng"] or lng

        data = self.place(fid, lat, lng)
        p = safe_get(data, 6, default=[])
        out = extract.shape(
            p,
            place_id=place_id or safe_get(p, 78),
            fid=fid,
            with_photos=with_photos,
            lat=lat,
            lng=lng,
        )
        out["session_id"] = safe_get(data, 12, default="")
        return out

    def details_many(
        self, refs: list[str], *, workers: int = 6, with_photos: bool = True
    ) -> list[dict]:
        """Parallel lookups over place ids and/or feature ids.

        Bootstraps once up front so the threads share one ripe cookie jar.
        16+ workers on a single IP starts drawing 429s; add proxies instead
        (`ClientPool`).
        """
        self.bootstrap()

        def one(ref: str) -> dict:
            try:
                if ref.startswith("0x"):
                    return self.details(fid=ref, with_photos=with_photos)
                return self.details(ref, with_photos=with_photos)
            except Exception as e:  # one bad row must not kill a 100k-row run
                return {"ref": ref, "error": str(e)[:200]}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, refs))

    def reviews(
        self,
        fid: str,
        *,
        session_id: str = "",
        limit: int = 200,
        page_size: int = 10,
    ) -> list[dict]:
        """Paginate reviews via the batchexecute `ListUgcPosts` RPC.

        **Needs a BotGuard key** (`bgkey=` / `$GSCRAPE_MAPS_BGKEY`). The bus is
        gated on the `x-maps-bgkey` header: without it Google answers 200 with
        an empty `[null,null,null,null,null,true]` body. Cookies, payload and
        URL make no difference — the header alone flips it. The token is minted
        by JS in the page, so it has to come from a browser session once; the
        same key then works for every place from plain HTTP.

        Without a key, use `details()["top_reviews"]` for the 8 embedded ones.
        """
        self._ensure()
        if not self.bgkey:
            raise Blocked(
                "review pagination needs a BotGuard key. Copy `x-maps-bgkey` "
                "from any Maps reviews XHR in your browser devtools and pass "
                "bgkey= or set $GSCRAPE_MAPS_BGKEY. `details()['top_reviews']` "
                "gives the 8 embedded reviews with no key at all."
            )

        out: list[dict] = []
        token = ""
        reqid = 100000
        while len(out) < limit:
            payload = [
                [[fid], None, None, None, None, [None, None, None, [[1], [3]]]],
                [page_size, token],
                None,
                None,
                [session_id or "", None, None, None, None, None, 81],
                None,
                None,
                [
                    None,
                    1,
                    1,
                    None,
                    1,
                    None,
                    1,
                    None,
                    None,
                    None,
                    None,
                    [1, 1, None, [[1]]],
                ],
                None,
                None,
                [3, 1, None, None, None, [2]],
                None,
                [1],
            ]
            res = self.client.batchexecute(
                BATCH_URL,
                REVIEWS_RPCID,
                REVIEWS_RPC,
                payload,
                reqid=reqid,
                source_path="/maps",
                extra_headers={
                    "x-maps-bgkey": self.bgkey,
                    "referer": "https://www.google.com/",
                },
            )
            reqid += 100000
            if res is None:
                break
            token = safe_get(res, 1, default="") or ""
            page = safe_get(res, 2, default=[]) or []
            if not page:
                break
            for wrapper in page:
                inner = safe_get(wrapper, 0)
                if inner:
                    out.append(extract._one_review(inner))
            if not token:
                break
        return out[:limit]


__all__ = ["Maps", "extract", "looks_gated", "pb"]
