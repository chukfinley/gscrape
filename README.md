<div align="center">

# gscrape

**Fast, pure-HTTP scrapers for Google's public surfaces.**
Search · Maps · YouTube · Trends · News · Images · Autocomplete · Patents · Books · Scholar

[![CI](https://github.com/chukfinley/gscrape/actions/workflows/ci.yml/badge.svg)](https://github.com/chukfinley/gscrape/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gscrape.svg)](https://pypi.org/project/gscrape/)
[![Python](https://img.shields.io/pypi/pyversions/gscrape.svg)](https://pypi.org/project/gscrape/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

</div>

No Playwright, no Selenium, no headless Chrome. Every request is
[`curl_cffi`](https://github.com/lexiforest/curl_cffi) with a real browser TLS
fingerprint, talking to the same internal endpoints Google's own web apps call.

One Maps place costs ~2 requests and ~300 ms, where a browser-driven scraper
needs ~30 s and half a gigabyte of RAM.

```python
from gscrape import Maps, Search, Suggest, Trends, YouTube

Search().web("beste laufschuhe", limit=100)  # 10 SERP pages, parallel, ~2 s
Maps().search("bäckerei lübeck", limit=60)  # 60 places with contact data, ~2 s
YouTube().search("fitness", type="shorts", sort="views", upload_date="week")
Trends().interest_over_time(["laufschuhe", "sneaker"], geo="DE")
Suggest().sweep("laufschuhe")  # ~250 queries people really type
```

Same from the shell:

```bash
gscrape search "beste laufschuhe" --limit 100 --format csv --out serp.csv
gscrape maps search "bäckerei lübeck" --limit 60 --format csv --out places.csv
gscrape yt search fitness --type shorts --sort views --limit 40
gscrape trends interest laufschuhe --geo DE --csv --out trend.csv
```

---

## Install

```bash
uv add gscrape        # or: pip install gscrape
```

Python 3.11+. Two runtime dependencies: `curl_cffi` and `selectolax`.

## Status of each surface

Measured, not assumed. [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md)
records how every verdict here was reached and how to re-check it.

| Surface | API | Status |
|---|---|---|
| Web search, paginated over Google's result pages | `Search.web` | ✅ needs a browser cookie jar, once per IP |
| Image search — original URLs, dimensions, source page | `Search.images` | ✅ same jar |
| Web results with no browser at all | `Search.cse` | ✅ needs a free Programmable Search `cx` |
| Maps places: photos, hours, attributes, menu, popular times | `Maps.details` | ✅ |
| Maps text search, paginated and deduplicated | `Maps.search` | ✅ |
| Maps deep review pagination | `Maps.reviews` | ⚠️ needs a BotGuard key you supply |
| YouTube search, Shorts, channels, playlists | `YouTube` | ✅ |
| Autocomplete + keyword sweeps, per vertical | `Suggest` | ✅ |
| News search, topics, geo, real publisher URLs | `News` | ✅ |
| Trends: interest, regions, related, trending, CSV export | `Trends` | ✅ |
| Patents | `Patents` | ✅ |
| Books | `Books` | ✅ small anonymous quota |
| Scholar | `Scholar` | ✅ harsh rate limit |
| AI Overview / AI Mode | `Search.ai_overview` | ❌ JavaScript-only |

### The one manual step

Web and image search answer a plain-HTTP client only when its cookie jar was
minted in a **browser**: `SOCS` + `NID` together, where the `NID` must come from
a real browser session — one this library mints itself through the consent flow
is refused indefinitely. That handoff happens once per egress IP:

```bash
gscrape cookies 'NID=...; SOCS=...'        # copy the Cookie header from devtools
gscrape search "beste laufschuhe" --limit 100
```

```python
s = Search(hl="de", gl="de")
s.client.import_cookies("NID=...; SOCS=...")
s.web("beste laufschuhe", limit=100)
```

Everything else works out of the box. The AI answer genuinely cannot be had over
plain HTTP: it arrives through an async call whose token page JavaScript
computes, absent from the HTML even when a real browser fetches without running
JS — so `ai_overview()` raises instead of returning something empty.

## Design

Every service shares one constructor, so what you learn once applies everywhere:

```python
from gscrape import Client, Maps, News

Maps(hl="en", gl="us")  # language and country
Maps(proxy="http://user:pass@host:6543")  # per-instance egress
Maps(rate_limit=4)  # 4 requests/s on this client

shared = Client(hl="de", rate_limit=4)  # one identity, many surfaces
Maps(client=shared), News(client=shared)  # shared cookies and rate budget
```

Results are plain dicts and lists — nothing to unpack, everything exportable:

```python
from gscrape import to_csv, to_json, to_jsonl

to_csv(Maps().search("bäckerei lübeck", limit=60), "places.csv")
```

### Proxies

Google's limits are per-IP, so proxies multiply throughput instead of raising a
ceiling: 11 egress IPs sustain ~40 Maps lookups/s where one manages ~5.

```python
from gscrape import ClientPool, Maps

pool = ClientPool(["1.2.3.4:8080:user:pass"], rate_limit=4)
places = Maps(client=pool.next()).search("bäckerei lübeck")
```

Proxies also come from `$GSCRAPE_PROXIES` or `~/.config/gscrape/proxies.txt`, in
either `http://user:pass@host:port` or `host:port:user:pass` form. Each proxy
keeps its own cookie jar automatically — Google's consent cookies are bound to
the IP that issued them.

### Errors are typed, so batch jobs can react

```python
from gscrape import Blocked, Captcha, JsRequired, NotFound, RateLimited

try:
    places = Maps().search("bäckerei lübeck")
except Captcha as e:
    print(e.sorry_url)  # solve in a browser on this IP, then import_cookies()
except RateLimited:
    ...  # back off, or switch proxy
```

A captcha, a JavaScript shell or a cookie-gated stub never comes back as an
empty list. That rule is the difference between a broken run and a *silently*
broken run.

## Documentation

| Document | What it covers |
|---|---|
| [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md) | The method, every known gate, and how to re-crack a surface when Google changes it |
| [docs/SURFACES.md](docs/SURFACES.md) | Per-surface reference: endpoints, parameters, payload shapes, rate limits |
| [docs/COOKBOOK.md](docs/COOKBOOK.md) | Recipes: lead lists, keyword research, competitor monitoring, trend tracking |

## Development

```bash
uv sync
uv run pytest                              # 233 offline tests, ~2 s
uv run pytest -m live                      # 24 contract tests against real Google
uv run ruff check . && uv run ty check     # lint and type-check
uv run python tests/refresh_fixtures.py    # re-capture payloads after a change
```

Offline tests run against payloads captured from live responses, so a parser
that breaks on real Google data fails in CI rather than in production.
Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Legal

Scraping these surfaces violates Google's Terms of Service. The data is public
and logged-out, but the risk is yours: rate limits, captchas and IP blocks are
normal operating conditions here, not bugs. No warranty of any kind.

MIT licensed — see [LICENSE](LICENSE).
