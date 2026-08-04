"""Shared fixtures.

Every offline test runs against a payload captured from the live endpoint (see
`tests/fixtures/`), so a parser change that breaks on real Google data fails
here instead of in production. Regenerate them with
`python tests/refresh_fixtures.py` when Google reshapes a payload.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    """Load a fixture; `.gz` and `.json` decode, everything else is text."""
    path = FIXTURES / name
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    if name.endswith(".json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def maps_payload():
    """A real `/maps/preview/place` response (restaurant, 24 photos)."""
    return load("maps_place.json.gz")


@pytest.fixture(scope="session")
def maps_place(maps_payload):
    """The place object inside it — what every extractor takes."""
    return maps_payload[6]


@pytest.fixture(scope="session")
def news_rss():
    return load("news_rss.xml")


@pytest.fixture(scope="session")
def trends_timeseries():
    return load("trends_timeseries.json")


@pytest.fixture(scope="session")
def trends_related():
    return load("trends_related.json")


@pytest.fixture(scope="session")
def youtube_search():
    return load("youtube_search.json.gz")


class FakeResponse:
    """Minimal stand-in for a curl_cffi response."""

    def __init__(self, text: str = "", status_code: int = 200, url: str = ""):
        self.text = text
        self.status_code = status_code
        self.url = url


@pytest.fixture
def fake_response():
    return FakeResponse
