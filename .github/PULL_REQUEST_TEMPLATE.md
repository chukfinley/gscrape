## What this changes

<!-- One or two sentences. If it fixes an issue, link it. -->

## Why

<!-- For parser changes: what Google changed, and how you verified it. -->

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run ty check`
- [ ] `uv run pytest` passes
- [ ] `uv run pytest -m live -k <surface>` passes, or the failure is explained
- [ ] New behaviour has a test; parser changes have one against a real payload
- [ ] Fixtures refreshed (`uv run python tests/refresh_fixtures.py`) if a shape moved
- [ ] `CHANGELOG.md` updated for anything user-visible
