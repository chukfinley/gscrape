"""Proxy pools.

Google's rate limit is per-IP, so the pool is what turns a single-machine
scraper into a parallel one: 11 egress IPs (direct + 10 proxies) sustain
~40 Maps lookups/s together, where one IP alone tops out around 5/s.

Each proxy needs its own cookie jar (`SOCS`+`NID` are bound to the issuing IP);
`Client` handles that automatically when constructed with `proxy=`.

Proxies are read from, in order of precedence:

1. the `proxies=` argument,
2. `$GSCRAPE_PROXIES` (comma-separated URLs),
3. `$GSCRAPE_PROXY_FILE` / `~/.config/gscrape/proxies.txt`, one per line,
   accepting either `http://user:pass@host:port` or the bare
   `host:port:user:pass` format proxy vendors hand out.

`$GSCRAPE_NO_PROXIES=1` forces direct-only, which is the switch to flip when
debugging whether the pool itself is the problem.
"""

from __future__ import annotations

import itertools
import os
import threading
from pathlib import Path

from .client import Client

DEFAULT_PROXY_FILE = Path(
    os.environ.get(
        "GSCRAPE_PROXY_FILE", Path.home() / ".config" / "gscrape" / "proxies.txt"
    )
)


def normalise(raw: str) -> str:
    """`host:port:user:pass` or a full URL -> a full proxy URL."""
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return ""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
        return f"http://{user}:{pw}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{raw}"
    return raw


def load_proxies(
    proxies: list[str] | None = None, *, include_direct: bool = True
) -> list[str | None]:
    """`[None, 'http://...', ...]` — None means "use the machine's own IP"."""
    if os.environ.get("GSCRAPE_NO_PROXIES"):
        return [None] if include_direct else []

    raw: list[str] = list(proxies or [])
    if not raw and os.environ.get("GSCRAPE_PROXIES"):
        raw = os.environ["GSCRAPE_PROXIES"].split(",")
    if not raw and DEFAULT_PROXY_FILE.exists():
        raw = DEFAULT_PROXY_FILE.read_text().splitlines()

    out: list[str | None] = [None] if include_direct else []
    out.extend(p for p in (normalise(r) for r in raw) if p)
    return out


class ClientPool:
    """Round-robins `Client`s across egress IPs, thread-safely.

    Each client keeps its own cookie jar and rate limiter, so N proxies really
    do multiply throughput instead of sharing one budget.

        pool = ClientPool(hl="de", rate_limit=4)
        with pool.take() as client:
            Maps(client=client).details(place_id=...)
    """

    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        include_direct: bool = True,
        **client_kwargs,
    ):
        urls = load_proxies(proxies, include_direct=include_direct)
        if not urls:
            urls = [None]
        self.clients = [Client(proxy=u, **client_kwargs) for u in urls]
        self._cycle = itertools.cycle(self.clients)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.clients)

    def next(self) -> Client:
        with self._lock:
            return next(self._cycle)

    def __iter__(self):
        return iter(self.clients)
