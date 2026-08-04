"""Shared plumbing. Public API lives in `gscrape/__init__.py`."""

from .client import Client
from .consent import accept_consent, bootstrap
from .errors import (
    Blocked,
    Captcha,
    GoogError,
    HttpError,
    JsRequired,
    NotFound,
    ParseError,
    RateLimited,
)
from .export import emit, flatten, to_csv, to_json, to_jsonl
from .parse import (
    extract_af_data,
    parse_batchexecute,
    parse_json,
    safe_get,
    strip_xssi,
    walk,
)
from .proxy import ClientPool, load_proxies
from .service import Service

__all__ = [
    "Blocked",
    "Captcha",
    "Client",
    "ClientPool",
    "GoogError",
    "HttpError",
    "JsRequired",
    "NotFound",
    "ParseError",
    "RateLimited",
    "Service",
    "accept_consent",
    "bootstrap",
    "emit",
    "extract_af_data",
    "flatten",
    "load_proxies",
    "parse_batchexecute",
    "parse_json",
    "safe_get",
    "strip_xssi",
    "to_csv",
    "to_json",
    "to_jsonl",
    "walk",
]
