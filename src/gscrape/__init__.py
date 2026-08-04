"""gscrape — fast pure-HTTP scrapers for Google's public surfaces.

    from gscrape import Maps, Search, Trends, Suggest

Every surface follows the same three rules:

* **No browser.** Everything is `curl_cffi` against the same internal endpoints
  Google's own web apps call, with a real Chrome TLS fingerprint.
* **Same constructor.** `hl`/`gl` for language and country, `proxy=`,
  `rate_limit=`, or `client=` to share one egress identity across surfaces.
* **Plain dicts out.** No custom result classes to unpack; `gscrape.to_csv` /
  `to_json` / `to_jsonl` export anything a scraper returns.

Services are imported lazily, so `from gscrape import Suggest` does not pay for
the Maps module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._core import (
    Blocked,
    Captcha,
    Client,
    ClientPool,
    GoogError,
    HttpError,
    JsRequired,
    NotFound,
    ParseError,
    RateLimited,
    Service,
    emit,
    safe_get,
    to_csv,
    to_json,
    to_jsonl,
)

__version__ = "0.1.0"

if TYPE_CHECKING:  # pragma: no cover
    from .books import Books
    from .maps import Maps
    from .news import News
    from .patents import Patents
    from .scholar import Scholar
    from .search import Search
    from .suggest import Suggest
    from .trends import Trends
    from .youtube import YouTube

_LAZY = {
    "Books": ".books",
    "Maps": ".maps",
    "News": ".news",
    "Patents": ".patents",
    "Scholar": ".scholar",
    "Search": ".search",
    "Suggest": ".suggest",
    "YouTube": ".youtube",
    "Trends": ".trends",
}


def __getattr__(name: str):
    """PEP 562 lazy service import."""
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'gscrape' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(mod, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "Blocked",
    "Books",
    "Captcha",
    "Client",
    "ClientPool",
    "GoogError",
    "HttpError",
    "JsRequired",
    "Maps",
    "News",
    "NotFound",
    "ParseError",
    "Patents",
    "RateLimited",
    "Scholar",
    "Search",
    "Service",
    "Suggest",
    "Trends",
    "YouTube",
    "__version__",
    "emit",
    "safe_get",
    "to_csv",
    "to_json",
    "to_jsonl",
]
