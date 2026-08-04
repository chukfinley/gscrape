# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-05

First public release.

### Added

- **Search** — organic web results with parallel pagination across Google's
  result pages (~98 unique results in ~2 s), `udm=14` web-only mode, `site:`
  and recency filters. Image search returning original URLs, dimensions and
  source pages. `cse()` for a browser-free path through Programmable Search.
- **Maps** — place details (photos, hours, attributes, menu card, popular
  times, embedded reviews), paginated text search, parallel batch lookups, and
  review pagination when a BotGuard key is supplied.
- **YouTube** — search over the InnerTube API with protobuf-encoded filters
  that combine (type, sort, upload date, duration, features), Shorts, video
  metadata, channels and playlists.
- **Trends** — interest over time, interest by region, related queries and
  topics, trending now (RPC and RSS), per-country sweeps, and Google's own CSV
  exports byte-for-byte.
- **News** — search, topic and geo feeds over RSS, plus resolution of
  `news.google.com` redirect links to publisher URLs.
- **Suggest** — autocomplete for web and every vertical, with alphabet,
  question and modifier sweeps for keyword research.
- **Patents**, **Books**, **Scholar** — full-text search with the filters each
  surface exposes.
- Shared core: `curl_cffi` sessions with browser TLS fingerprints, consent
  bootstrap, per-IP cookie jars, proxy pools, rate limiting, retries, typed
  errors (`Captcha`, `JsRequired`, `RateLimited`, `Blocked`, `ParseError`,
  `NotFound`) and JSON/JSONL/CSV export.
- `gscrape` CLI covering every surface, plus `gscrape cookies` for the
  browser-jar handoff Web Search needs.
- 233 offline tests against payloads captured from live responses, 24 live
  contract tests, and `tests/refresh_fixtures.py` to re-capture them.
- Documentation: reverse-engineering method and gate catalogue, per-surface
  reference, and a cookbook.

[Unreleased]: https://github.com/chukfinley/gscrape/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/chukfinley/gscrape/releases/tag/v0.1.0
