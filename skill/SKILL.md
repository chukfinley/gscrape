---
name: gscrape
description: Query Google's public surfaces from the shell without an API key, a browser, or a scraping vendor — Google Web Search, Google Images, YouTube (search, videos, shorts, channels, playlists, and subtitles/transcripts of any video), Google Maps places, Google Trends, Google News, autocomplete keyword research, Patents, Books and Scholar. Use whenever a task needs real SERP results, YouTube data (search, video stats, channel uploads, what a video says), local business data, keyword volumes/trends, or news monitoring — including "search Google for X", "what does YouTube return for X", "get the transcript/subtitles of this video", "summarize this YouTube video", "find channels about X", "keyword research", "is X trending", "find businesses in Y", "get this place's phone number", "competitor video analysis". Prefer this over WebSearch when the ask is about Google's own ranking, YouTube metadata or transcripts, Maps data, or bulk/CSV output. Command: `gscrape`.
---

# gscrape

One CLI over Google's own internal endpoints. Pure HTTP (`curl_cffi` with a real
Chrome TLS fingerprint), no browser, no API key, plain JSON on stdout.

`gscrape` is on PATH. Every command prints JSON unless `--format csv|jsonl` is
given, so pipe it into `jq` or write it with `--out file`.

## The two most used surfaces

```bash
# YouTube — search, filters, sorting; no API quota involved
gscrape yt search "fitness" --type shorts --sort views --upload-date week --limit 40
gscrape yt video dQw4w9WgXcQ                     # metadata AND the subtitles
gscrape yt video dQw4w9WgXcQ --no-transcript     # metadata only, two requests less
gscrape yt transcript dQw4w9WgXcQ --to text      # just the script
gscrape yt transcript dQw4w9WgXcQ --lang de --to srt --out sub.srt
gscrape yt transcript dQw4w9WgXcQ --list         # which caption tracks exist
gscrape yt channel @MrBeast                      # subs, id, description, links
gscrape yt channel @MrBeast --videos --tab shorts --limit 50
gscrape yt playlist PLxxxx --limit 100
gscrape yt hot "protein shake" --since week      # recent uploads ranked by views

# Google Web Search — needs a cookie jar once per IP (see below)
gscrape search "beste laufschuhe" --limit 100 --format csv --out serp.csv
gscrape search "laufschuhe" --site runnersworld.de --when m
gscrape search "warum ist der himmel blau" --ai   # AI overview text
gscrape search "laufschuhe" --images --limit 50   # original image URLs + dimensions
```

## Everything else

```bash
# keyword research — no gating at all, this always works
gscrape suggest "laufschuhe"                       # plain completions
gscrape suggest "laufschuhe" --sweep               # ~250 real queries (a-z + W-questions + intent)
gscrape suggest "laufschuhe" --questions --lang de
gscrape suggest "laufschuhe" --expand test kaufen vs
gscrape suggest "fitness" --ds yt                  # YouTube's own autocomplete

# Trends
gscrape trends interest laufschuhe sneaker --geo DE --timeframe "today 12-m"
gscrape trends interest laufschuhe --geo DE --csv --out trend.csv
gscrape trends region laufschuhe --geo DE --resolution CITY
gscrape trends related laufschuhe --topics
gscrape trends now --geo DE --hours 24
gscrape trends countries laufschuhe --countries DE,AT,CH

# Maps
gscrape maps search "bäckerei lübeck" --limit 60 --format csv --out places.csv
gscrape maps details ChIJd3UFGwNvs0cRCqhQbp-i6Jk
gscrape maps batch ids.txt --workers 6
gscrape maps resolve ChIJ...            # placeId -> feature id + lat/lng

# News (RSS, never gated)
gscrape news "laufschuhe" --when 7d --resolve
gscrape news --topic technology --limit 25
gscrape news --geo Hamburg

# long tail
gscrape patents "running shoe sole" --after 20220101 --count
gscrape books "clean code" --author "Robert Martin"
gscrape scholar "sports biomechanics" --recent
```

## Global flags (work before or after the subcommand)

`--hl de --gl de` language/country · `--proxy http://user:pass@host:port` ·
`--rate-limit 2` requests per second · `--format json|jsonl|csv` · `--out FILE` ·
`-v` for request logging.

## What is gated and what is not

Un-gated, always works: suggest, trends, news, youtube, patents, books, maps
(bootstraps its own consent cookies).

Web Search and Image Search need a browser-minted cookie jar, once per IP:

```bash
gscrape cookies 'NID=...; SOCS=...'   # Cookie header copied from devtools
```

Without it `Search.web` raises `JsRequired`. Alternative with no browser at all:
a Programmable Search engine — `export GSCRAPE_CSE_CX=...` (or `--cx`) makes
`gscrape search` use the CSE endpoint instead.

## Failure modes worth knowing

Typed errors print as one line on stderr with exit code 1, no traceback:
`Captcha` (rate-limited by /sorry — slow down, change IP), `JsRequired` (missing
cookie jar), `Blocked`, `RateLimited`, `NotFound`, `ParseError` (Google changed a
payload). Scholar rate-limits hardest; use `--rate-limit 0.2` there.

YouTube retired its Trending feed in 2025 — `yt hot` (recent uploads by views) or
`gscrape trends interest X --property youtube` are the replacements.

Transcripts: a video without captions returns `transcript: null` rather than
failing. `--lang de` translates through YouTube when no German track exists.
The subtitle host rate-limits harder than the rest, so bulk transcript runs want
`--rate-limit 0.5`.

## As a Python library

Same surfaces, plain dicts, lazy imports:

```python
from gscrape import Maps, Search, Suggest, Trends, YouTube

YouTube().search("fitness", type="shorts", sort="views", limit=40)
YouTube().transcript("dQw4w9WgXcQ", lang="de")["text"]
Suggest().sweep("laufschuhe")
Maps().details("ChIJ...", with_photos=True)
```

Repo: `/home/user/git/google` — `docs/COOKBOOK.md` for recipes,
`docs/SURFACES.md` for what each endpoint returns.
