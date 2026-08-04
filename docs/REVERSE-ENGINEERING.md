# Reverse engineering Google, and re-doing it when it breaks

Everything in this package was found the same way: **watch what Google's own web
app does, then replay exactly that over plain HTTP.** Nothing here was guessed
from documentation, because there is none — and guessing produces endpoints that
return HTTP 200 with an empty body, which is worse than an error.

This document is the method plus the current state of every gate. When a scraper
starts returning nothing, come back here and run the loop again.

---

## The method

1. **Capture.** Open the surface in a real browser with devtools recording, and
   do the thing you want to scrape. Ignore `gen_204`, `log204` and `/vt/` —
   those are analytics and map tiles.
2. **Find the payload.** Sort responses by size. The one carrying your data is
   almost always the biggest non-asset response.
3. **Replay.** Copy that request into `curl_cffi` with `impersonate="chrome"`.
   Three outcomes:
   * identical payload → done, parameterise it;
   * smaller payload → a **gate**, go to step 4;
   * 302 to `/sorry/index` → the IP is burnt, switch proxy or solve the captcha.
4. **Bisect the gate.** The difference is always in exactly one of four places.
   Test them in this order, cheapest first:
   * **URL shape** — a missing `pb=`, a path segment, a query param.
   * **Headers** — `sec-fetch-*`, `referer`, `accept`.
   * **Cookies** — drop them one at a time from a working browser jar.
   * **TLS fingerprint** — loop over every `curl_cffi` impersonate target.
5. **Prove it with a metric, not a feeling.** Pick one number that separates
   "real" from "stub" (payload size, result count) and A/B every hypothesis
   against it. Most gate hunts fail because the tester eyeballs the response.
6. **Parse by shape, not by index** — see [Slot numbers rot](#slot-numbers-rot).

### The isolation trick that settles "is it JS?"

A browser's own HTTP client can fetch a URL *without executing JavaScript*:

```js
// Playwright, in the page's context but bypassing the JS app
const r = await page.request.get(url, { headers: { accept: 'text/html' } });
```

If that returns the stub while the rendered page has the data, the payload is
assembled by JavaScript — no amount of header tuning will help. If it returns
the full payload, the gate is in your HTTP client (fingerprint, cookies) and is
worth chasing. This one test saves hours.

---

## Gate catalogue

### 1. The consent gate — SOCS + NID (Maps, and everything on google.com)

A cookie-less request gets either a redirect to `consent.google.com/m` or a
**stub payload**: HTTP 200, correct shape, most fields missing. Maps
`preview/place` answers 23 KB instead of 127 KB.

The gate opens on `SOCS` **and** `NID` together. Either alone is not enough,
forged values are not enough, and the values do not have to be fresh — months-old
ones still work. Both are obtainable without a browser: any Google URL redirects
to the consent page, whose accept-all form POSTs to `consent.google.com/save`.

Two non-obvious properties:

* **A fresh `NID` needs warming.** Google keeps serving stubs for a few seconds
  after issuing one, so the bootstrap probes a known-rich reference place until
  the response comes back big, then caches the jar.
* **The jar is bound to the IP that got it.** Replaying it through another proxy
  exit gets the stub back, which is why every proxy keeps its own cookie file.

Implementation: `_core/consent.py`, `Maps.bootstrap()`.

### 2. The session gate — Web Search

`google.com/search` answers a plain-HTTP client with a ~90 KB shell (a redirect
to `/httpservice/retry/enablejs`) unless the cookie jar came from a browser.
Everything below was tested against a flagged IP and does **not** help on its
own:

| Attempt | Result |
|---|---|
| `gbv=1` (the old basic-HTML switch) | shell |
| Legacy/text-browser UA (w3m, Lynx) | "Browser aktualisieren" page |
| Mobile Chrome UA | shell |
| `udm=14`, `num=100`, `tbm=isch\|nws\|vid` | shell |
| Replaying the shell's own `emsg=SG_REL` reload URL | shell |
| 40+ `curl_cffi` TLS targets (chrome/firefox/safari/edge) | shell |
| Full navigation headers (`sec-fetch-*`, `upgrade-insecure-requests`) | shell |
| A jar this package minted itself through the consent POST | shell, forever |

What does work: a jar from a **browser** session. After a captcha was solved in
a browser on the same IP, the identical Python client got 730 KB of real
results. Minimising that jar one cookie at a time gave the exact gate:

* `SOCS` + `NID` together are **sufficient** — every other cookie
  (`AEC`, `DV`, `SNID`, `OTZ`, `_GRECAPTCHA`, `GOOGLE_ABUSE_EXEMPTION`) can be
  dropped;
* either alone is **not** sufficient;
* and the `NID` must be browser-minted. A `NID` from this package's own consent
  POST is refused indefinitely — unlike the Maps gate it never warms up, which
  8 spaced retries confirmed.

So Web Search costs one manual handoff per IP (`client.import_cookies(...)`),
after which Python scrapes on its own, paginating `start=0,10,…,90` in parallel:
98 unique results in ~2 s.

Image search (`udm=2`) unlocks with the same jar. Its results live in the page's
embedded JS state as `[thumb, w, h], [original, w, h]` pairs followed by the
source page — so `images()` returns **original** URLs with real dimensions,
which no thumbnail-only scraper can.

**Still closed even with a browser jar:** the AI answer. AI Overview and AI Mode
(`udm=50`) are delivered by an
`/async/callback:*` request whose `fc` token is computed by page JavaScript —
it appears nowhere in the HTML, not even when a real browser fetches the page
without executing JS. `Search.ai_overview()` extracts the block when Google
inlines it and raises `JsRequired` otherwise.

### 3. The BotGuard gate — Maps deep review pagination

The `MapsUgcPostService.ListUgcPosts` RPC on the batchexecute bus answers HTTP
200 with an empty `[null,null,null,null,null,true]` body unless the request
carries an `x-maps-bgkey` header. Cookies, payload and URL make no difference —
the header alone flips it.

The token is minted by JavaScript in the page, so it has to come from a browser
session once. It then works for every place and survives sessions, so
`Maps(bgkey=...)` / `$GSCRAPE_MAPS_BGKEY` is a one-time paste.

The 8 reviews embedded in `preview/place` need none of this.

### 4. The captcha wall — `/sorry/index`

Not a gate but a punishment: burst too hard from one IP and Google 302s
everything to `/sorry/index` with a reCAPTCHA. It is per-IP and per-surface
(Search can be blocked while Maps still answers).

`Client` raises `Captcha` with the solve URL. Recovery without changing IP:
open the URL in a browser on the same egress, solve it, then

```python
client.import_cookies("NID=...; SOCS=...")  # copied from devtools
```

Rates that were measured as safe: Maps ~5 places/s per IP, Trends ~0.3 req/s
(it is the touchiest), Scholar ~0.2 req/s, autocomplete and YouTube much higher.

---

## Case study: getting Maps text search back (2026-08)

`search?tbm=map&q=...` used to return the full result list. It started returning
a 6.5 KB stub with one empty candidate — and, importantly, **not** an error, so
callers saw "no results" rather than a breakage.

The capture showed why: the Maps app now sends a 2 KB `pb=` request proto with
the query. Without it, the endpoint answers a stub.

Hardcoding that proto would rot with the next Maps release. The fix instead
reads it back from Google: `/maps/search/<query>` ships the complete results
request as a prefetch `<link>` in its `<head>`, session id and all. One cheap
HTML fetch buys a `pb` that is never out of date, with a captured template as
fallback.

Two more findings from the same session:

* Results moved from `data[0][1][*][14]` to `data[64][*][1]`. `find_places()`
  therefore matches on shape (a long list whose slot 11 is a name and slot 10 a
  `0x…:0x…` feature id) and ignores position entirely.
* Paging is `!8i<offset>` **appended at the end of the pb**. Inserted next to
  the page-size field it parses fine and is silently ignored — you get page 1
  again, which reads exactly like "no more results".

Result: 60 places in 2 s, paginated.

---

## Slot numbers rot

Google's payloads are positional arrays with no field names, and Google
renumbers them without notice. Two rules follow, and both are load-bearing:

* **Index only where the slot has been stable for years and the shape is
  unambiguous** (`place[203]` for hours, `place[100]` for attributes).
* **Walk and match on shape everywhere else** — photos, dishes, menu cards,
  place records, YouTube renderers. A photo record is "a list whose slot 6 is
  `[url, attribution, [w,h], [w,h]]`", and that stays true across reshuffles.

Corollary: never let an index error escape. `safe_get()` returns a default for
any missing slot, wrong type or `None` along the path — a missing field is an
acceptable outcome in a 100k-row run, a `KeyError` is not.

## Read a 200 as an error when it is one

Every gate above answers HTTP 200. A scraper that only checks status codes
silently collects empty rows for hours. This package instead:

* detects the JS shell and the captcha wall in the HTTP layer (`Client._check_wall`),
* raises `JsRequired` when a SERP comes back without results *and* without the
  results-page marker, instead of reporting "no results",
* detects the Maps stub by content (`looks_gated`), not by size alone — a rural
  place with three photos legitimately lands under the size bar,
* raises `ParseError` when a JSON endpoint returns HTML, instead of returning `[]`.

`Maps.search()` and `Maps.place()` additionally re-bootstrap once and retry
before giving up, because the common cause is a jar that went stale.

---

## Keeping this honest

`tests/test_live.py` is the tripwire: contract tests that hit the real endpoints
and assert shapes, plus a `TestClosedDoors` class asserting that the gates are
still closed. A failure there is news either way.

```bash
uv run pytest -m live                      # is Google still behaving?
uv run python tests/refresh_fixtures.py    # re-capture payloads, then re-run
```
