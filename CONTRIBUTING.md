# Contributing

Most work on this project is not ordinary feature work — it is re-cracking a
surface Google just changed. That shapes the workflow below.

## Setup

```bash
git clone https://github.com/chukfinley/gscrape
cd gscrape
uv sync                 # everything, including dev tools
uv run pytest           # 233 offline tests, ~2 s
```

Everything runs through [uv](https://docs.astral.sh/uv/). No other toolchain is
required or supported.

## Before opening a pull request

```bash
uv run ruff check . && uv run ruff format .
uv run ty check
uv run pytest
uv run pytest -m live -k <surface>     # the surface you touched
```

CI runs the first three on Python 3.11, 3.12 and 3.13. The live suite is not in
CI: it needs network, spends rate-limit budget and fails for reasons that are
not the code's fault.

## Fixing a broken scraper

When Google changes a payload, the offline tests keep passing against the old
fixture while the live tests fail. That is the intended signal.

1. Reproduce with `verbose=True` and look at the raw response — size and first
   200 characters usually identify the failure mode (JS shell, captcha, stub,
   or a valid payload with a moved field).
2. Follow the method in [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md):
   capture what the web app does, replay it, then bisect the difference against
   **one measurable number** (payload size, result count) instead of eyeballing.
3. Fix the parser, then `uv run python tests/refresh_fixtures.py` and re-run the
   offline suite. Each failure names a field that moved.
4. Write down *why* in the code, not just *what*. The comments explaining that
   `!8i<offset>` only works at the end of a `pb`, or that a self-minted `NID` is
   refused forever, are the most valuable lines in this repository — they are
   the ones nobody can re-derive from reading the code.

## Style

- **Parse by shape, not by index.** Google renumbers slots. Match records by
  recognisable structure and walk generically; index only where a slot has been
  stable for years.
- **Never let a gate look like an empty result.** A captcha, a JS shell or a
  stub must raise a typed error. Returning `[]` turns a broken run into a silent
  one, which is the worst outcome in a 100k-row job.
- **Plain dicts out.** No result classes; everything must survive `to_csv()`.
- **Comment the traps, not the syntax.** Assume a competent reader.
- Line length 90, formatting by `ruff format`, English in code and docs.

## Tests

- Offline tests run against real captured payloads in `tests/fixtures/`. A
  parser change without a fixture-backed test will be asked for one.
- Live tests are contract tests: strict about shapes, tolerant about values
  (ratings change, trends pass).
- `TestClosedDoors` asserts that known-closed doors are still closed. A failure
  there is good news and should turn into a feature.

## Adding a surface

A new scraper belongs in its own module, subclasses `Service`, and:

- speaks through `self.client` so proxies, rate limiting, cookies and wall
  detection apply automatically;
- documents in its module docstring which endpoint it calls, how that was
  found, and what the gates and limits are;
- ships offline tests with a captured fixture, live contract tests, a section in
  `docs/SURFACES.md` and a CLI subcommand.

## Scope

Public, logged-out data only. Nothing that requires an account, defeats a
payment, or targets private individuals.
