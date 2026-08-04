# Surface reference

One section per scraper: the endpoints it speaks, the arguments that matter, the
shape of what comes back, and the limits that bite.

Common to all of them:

```python
Service(
    hl="de",
    gl="de",
    proxy=None,
    rate_limit=None,
    timeout=25,
    max_retries=4,
    cookie_file=None,
    verbose=False,
)
Service(client=shared_client)  # share one egress identity
```

`hl` is the interface language, `gl` the country — both change results, not just
labels. `rate_limit` is requests per second for that client.

---

## Maps

```python
from gscrape import Maps

m = Maps(hl="de", gl="de")
```

**Endpoints.** `/maps/preview/place` (place data), `/maps/search/<q>` +
`search?tbm=map&pb=…` (text search), `MapsWizUi/data/batchexecute` rpcid
`qv9Egd` (review pagination).

### `search(query, limit=20, *, with_photos=False, start=0)`

Full place records, 20 per page, paginated and deduplicated by feature id.
Costs one HTML fetch (to obtain the request proto) plus one request per page.

```python
m.search("bäckerei lübeck", limit=60)
```

### `details(place_id=None, *, fid=None, lat=0, lng=0, with_photos=True)`

Everything about one place in ~2 requests. Pass `fid` (the `0x…:0x…` feature id)
to skip the resolve step; `place_id` costs one extra HTML fetch.

Returned keys: `place_id fid cid knowledge_id name business_status closed
maps_url reviews_url categories primary_category category_ids descriptors
description address address_short street city postal_code country plus_code lat
lng timezone phone phone_international website menu_url reservation_urls rating
reviews rating_distribution price_range typical_duration hours attributes
menu_card popular_dishes popular_times top_reviews owner photos session_id`.

* `photos[].base_url` is size-free; build a URL with
  `gscrape.maps.extract.photo_url(base, 1600)` — Google serves any size, and the
  stored original is often 4000 px wide.
* `hours` is `{"days": [{day, weekday_no, date, ranges, closed}], "status": …}`.
* `attributes` groups the UI chips by slug (`service_options`, `accessibility`,
  `payments`, …); each item carries `present: True/False/None`, so "explicitly
  has no wheelchair access" is distinguishable from "unknown".
* `business_status` is read off Google's own red badge, not guessed. Trust
  `closed`; treat `CLOSED_TEMPORARILY` vs `CLOSED_PERMANENTLY` as a hint — in a
  120k-row run the "permanently" wording never appeared, because Google drops
  permanently closed places out of search entirely.

### `details_many(refs, *, workers=6)`

Parallel lookups over place ids and/or feature ids; bootstraps once so threads
share one ripe cookie jar. Failures come back as `{"ref": …, "error": …}` rows
rather than killing the run. 16+ workers on a single IP starts drawing 429s.

### `reviews(fid, *, session_id="", limit=200)`

Deep pagination. **Needs a BotGuard key** (`Maps(bgkey=…)` or
`$GSCRAPE_MAPS_BGKEY`) — copy `x-maps-bgkey` from any Maps reviews XHR in
devtools once; it works for every place afterwards. Without one, use
`details()["top_reviews"]` for the 8 embedded reviews.

**Limits.** ~5 place lookups/s per IP sustained; ~40/s across an 11-IP pool.

---

## YouTube

```python
from gscrape import YouTube

yt = YouTube(hl="de", gl="DE")
```

**Endpoint.** `youtubei/v1/{search,player,browse,navigation/resolve_url}` — the
InnerTube API youtube.com itself uses. The embedded "API key" is the public
web-client key, identical for every visitor; it is not a credential.

### `search(query, *, limit=20, type=None, sort="relevance", upload_date=None, duration=None, features=None)`

* `type`: `video`, `shorts`, `channel`, `playlist`, `movie`
* `sort`: `relevance`, `rating`, `date`, `views`
* `upload_date`: `hour`, `today`, `week`, `month`, `year`
* `duration`: `under3`, `3to20`, `over20`
* `features`: `hd`, `4k`, `subtitles`, `live`, `360`, `hdr`, `3d`, `vr180`,
  `creative_commons`, `location`, `purchased`

Filters are encoded as protobuf (`search_params()`), so they **combine** —
"Shorts, this week, sorted by views" is one call. Libraries that hardcode one
base64 string per filter cannot do that.

Note: YouTube itself returns nothing for some combinations (Shorts + a date
filter is one), which is a server-side quirk, not an encoding bug.

Result rows carry `kind` (`video`/`short`/`channel`/`playlist`), ids, title,
channel, `views` (parsed int) and `views_text`, `published_text`, `thumbnail`.

### `video(video_id)`

Exact `published`/`uploaded` dates, `duration_s`, `views`, `keywords`,
`category`, `available_countries` — the things search only approximates.

### `channel(channel)` / `channel_videos(channel, *, tab="videos", limit=30)`

Accepts `@handle`, `UC…` id or channel URL (handles cost one extra resolve
request). `tab`: `videos`, `shorts`, `streams`.

### `playlist(playlist_id, *, limit=100)` / `hot(query, since="today")`

YouTube retired the Trending page in 2025 (`FEtrending`, `FEexplore` and
`FEmusic_trending` all answer HTTP 400), so `hot()` approximates it with
recent uploads sorted by views. For a real trend signal use
`Trends(...).interest_over_time(term, property="youtube")`.

**Limits.** Generous. Hundreds of requests without trouble.

---

## Suggest (autocomplete)

```python
from gscrape import Suggest

s = Suggest(hl="de", gl="de")
```

**Endpoint.** `/complete/search` — public, un-gated JSON. No cookies, no JS.

```python
s.suggest("laufschuhe")  # 10 completions
s.suggest("laufschuhe", detailed=True)  # + relevance scores, types
s.suggest("laufschuh", ds="yt")  # YouTube autocomplete
s.alphabet("laufschuhe")  # "laufschuhe a" … "z"  (26 calls)
s.questions("laufschuhe")  # "warum …", "welche …"
s.modifiers("laufschuhe")  # "… test", "… kaufen", "… vs"
s.sweep("laufschuhe")  # all of the above, deduped
```

`ds=`: `yt` YouTube, `sh` Shopping, `bks` Books, `n` News, `i` Images, `v`
Video, `pl` Play. Omit for web.

One sweep is ~60 requests and yields 150-300 real queries — the cheapest keyword
research Google offers, and the only surface that reports what people type.

---

## News

```python
from gscrape import News

n = News(hl="de", gl="DE")
```

**Endpoints.** `news.google.com/rss/*` (feeds, un-gated) and
`DotsSplashUi/data/batchexecute` rpcid `Fbv4je` (link resolution).

```python
n.search("laufschuhe", when="7d", site="spiegel.de", limit=50, resolve=True)
n.top(limit=20)
n.topic("technology")  # world, nation, business, technology, entertainment,
# sports, science, health, or a raw CAAqJggK… id
n.geo("Lübeck")
```

`search()` accepts the web UI's operators (`when:`, `after:`, `before:`,
`site:`, `intitle:`) and returns up to 100 items.

**The link problem.** Feed links point at `news.google.com/rss/articles/CBMi…`,
a protobuf blob that cannot be decoded offline. `resolve()` does what the News
app does: fetch the article shell, read its `data-n-a-id` / `-sg` / `-ts`
triple, POST them to the `Fbv4je` RPC, get the publisher URL back. Two requests
per link, so it is opt-in (`resolve=True`) and parallel.

---

## Trends

```python
from gscrape import Trends

t = Trends(hl="de", gl="de", rate_limit=0.3)
```

**Endpoints.** `/trends/api/explore` (mints a token per widget — nothing works
without one), `/trends/api/widgetdata/{multiline,comparedgeo,relatedsearches}`
(+ their `/csv` twins), `TrendsUi/data/batchexecute` rpcid `i0OFE` (trending
now), `/trending/rss` (un-gated trending feed).

```python
t.interest_over_time(["laufschuhe", "sneaker"], geo="DE", timeframe="today 12-m")
t.interest_by_region("laufschuhe", geo="DE", resolution="CITY")
t.related_queries("laufschuhe")["rising"]
t.trending_now(geo="DE", hours=48)
t.trending_rss(geo="DE")  # cheapest, includes news items
t.csv("peniaze", kind="timeseries", geo="SK", timeframe="now 1-d", path="x.csv")
t.by_country("laufschuhe", ["DE", "AT", "CH"])
```

* `timeframe`: `now 1-H`, `now 7-d`, `today 3-m`, `today 12-m`, `today 5-y`,
  `all`, or `"2024-01-01 2024-06-30"`.
* `geo`: `""` worldwide, `DE`, `DE-BY`, `US-NY-501`.
* `property`: `""` web, `images`, `news`, `youtube`, `shopping`.
* Interest values are **relative to the comparison**: 100 is this query set's
  peak, not an absolute volume. Two separately fetched curves cannot be
  compared — put both terms in one call.
* The last bucket is usually incomplete (`partial: True`); charts that ignore
  the flag show a fake downtrend at the right edge.
* `csv()` calls Google's own CSV endpoints, so the output is byte-identical to
  the UI's download button.
* `trending_now()` volumes are bucketed (2000, 20000, 200000, …) — an order of
  magnitude, not a count. Its slot 11 holds article *ids*; use `trending_rss()`
  when you want headlines.

**Limits.** The touchiest surface here. A handful of `explore` calls in a burst
earns a 429 lasting minutes. `rate_limit=0.3` for sustained work, and
`by_country()` is deliberately sequential.

---

## Patents

```python
from gscrape import Patents

Patents().search("running shoe sole", after="20220101", country="US,DE", limit=50)
```

**Endpoint.** `patents.google.com/xhr/query` — plain JSON, no cookies, no JS, no
token. The cleanest surface Google operates.

Filters: `inventor`, `assignee`, `after`/`before` (+ `date_field`: `priority`,
`filing`, `publication`), `country`, `language`, `status`, `type`, `sort`.
`count(query)` returns the match count without pulling results.

---

## Books

```python
from gscrape import Books

Books().search("laufen", author="knopf", limit=40)
```

**Endpoint.** `googleapis.com/books/v1/volumes` — public, key-less. The
anonymous quota is per-IP and small; a burst of a few dozen calls returns
`429 Quota exceeded`. A proxy pool resets it, and `Books(api_key=…)` raises it.

---

## Scholar

```python
from gscrape import Scholar

Scholar(rate_limit=0.2).search("running shoe biomechanics", year_from=2020)
```

**Endpoint.** `scholar.google.com/scholar` — still plain HTML, no JS gate, but
the harshest rate limiter of any surface: a few dozen fast requests earn a
captcha lasting hours. Treat `rate_limit=0.2` as the default.

Rows carry `title url authors venue publisher year snippet citations cluster_id
pdf`. `citations(cluster_id)` lists the citing papers.

---

## Search (web)

```python
from gscrape import Search

s = Search(hl="de", gl="de")
s.client.import_cookies("NID=...; SOCS=...")  # once per IP, from devtools
s.web("beste laufschuhe", limit=100)  # 10 pages in parallel, ~2 s
s.web("laufschuhe", site="test.de", when="m")  # domain + recency filters
s.web("laufschuhe", clean=False)  # full SERP instead of udm=14
```

**Endpoint.** `google.com/search`, paginated with `start=0,10,…`. Pages are
independent, so `web()` fetches all of them at once: 10 pages cost one round
trip, not ten.

Needs a browser-minted cookie jar — see
[REVERSE-ENGINEERING.md](REVERSE-ENGINEERING.md#2-the-session-gate--web-search).
Without one, `web()` raises `JsRequired` with the fix in the message.

Rows: `position title url display_url snippet published`. The parser matches
structurally (`<a href="http…">` containing an `<h3>`) rather than on Google's
class names, which are regenerated regularly. `published` is the date Google
prefixes to many snippets, split off into its own field.

`clean=True` (default) uses `udm=14`, the web-only mode: no AI overview, no
shopping carousel, no video shelf — a smaller page and a cleaner parse.

### `images(query, *, limit=50)`

Image search over `udm=2`, returning **original** image URLs with their real
dimensions plus the page each sits on — not the gstatic thumbnails. Same
browser jar as `web()`; the parse is a regex sweep over the page's embedded JS
state, which is 20× cheaper than walking the megabyte-sized array.

```python
s.images("laufschuhe", limit=50)
# {'url': 'https://…/schuh.jpg', 'width': 800, 'height': 800,
#  'thumbnail': …, 'source_url': …, 'source_title': …, 'source_domain': …}
```

### `ai_overview(query)`

The AI answer, as an **explicit** call — it never leaks into `web()` results.
Google normally loads it through an async request whose token is computed by
page JavaScript, so this raises `JsRequired` unless the block happens to be
inlined. See the RE doc for the measurement.

### `cse(query, *, cx=None, limit=10, site=None, sort="")`

Real Google web results with **no browser step at all**: the Programmable
Search element API, free and key-less, bound to an engine id. Create one at
<https://programmablesearchengine.google.com>, switch on "Search the entire
web", pass `cx=` or set `$GSCRAPE_CSE_CX`. The `cse_token` it needs is
harvested from `cse.js` and refreshed automatically when it goes stale.
`images(query)` is the same call with `searchType=image`.
