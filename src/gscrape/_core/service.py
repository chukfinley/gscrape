"""Base class for every scraper surface.

Gives all services the same constructor shape, so these are equivalent:

    Maps(hl="en", gl="us", proxy="http://...")        # owns its client
    Maps(client=shared)                               # shares one

Sharing matters for pool runs: the cookie jar, the rate limiter and the TLS
session are per-`Client`, so one client used by `Maps` and `Search` together
stays within one IP's budget instead of two racing each other into a 429.
"""

from __future__ import annotations

from typing import Any

from .client import Client


class Service:
    #: Overridden by surfaces that need the consent jar before their first call.
    needs_consent: bool = False

    def __init__(
        self,
        hl: str = "de",
        gl: str = "de",
        *,
        client: Client | None = None,
        **client_kwargs: Any,
    ):
        self.client = client or Client(hl=hl, gl=gl, **client_kwargs)

    @property
    def hl(self) -> str:
        return self.client.hl

    @property
    def gl(self) -> str:
        return self.client.gl

    def bootstrap(self, force: bool = False) -> None:
        """Prepare cookies. No-op for surfaces that do not need them."""
        if self.needs_consent:
            raise NotImplementedError

    def _ensure(self) -> None:
        """Called before the first request of every public method."""
        if self.needs_consent and not self.client.has_cookies:
            self.bootstrap()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.client!r}>"
