"""Exception hierarchy shared by every service.

Callers that only care "did it work" catch `GoogError`. Callers that retry on
their own care about the split: `RateLimited` and `Blocked` are worth retrying
from another IP, `ParseError` means Google changed a payload shape and retrying
will not help.
"""

from __future__ import annotations


class GoogError(RuntimeError):
    """Base class for everything this package raises."""


class HttpError(GoogError):
    """Non-200 response that is not a rate limit."""

    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} for {url[:120]}")


class RateLimited(HttpError):
    """429/503: the per-IP limiter kicked in. Back off or switch proxy."""


class Blocked(GoogError):
    """Google served a consent wall, captcha or the cookie-gated stub payload."""


class Captcha(Blocked):
    """Google redirected to `/sorry/index` — this IP has to prove it is human.

    Recoverable without a proxy: open `sorry_url` in a normal browser on the
    same egress IP, solve it, then hand the resulting cookies back with
    `Client.import_cookies(...)`. The unblock is per-IP, so one solve covers the
    whole pool member.
    """

    def __init__(self, sorry_url: str, target: str = ""):
        self.sorry_url = sorry_url
        self.target = target
        super().__init__(
            f"captcha wall for {target[:80] or 'request'} — solve it at "
            f"{sorry_url[:160]} and re-import the cookies"
        )


class JsRequired(Blocked):
    """Google answered with the JavaScript shell instead of content.

    Web Search has required JavaScript since 2025: a cookie-less HTTP client
    gets a ~90 KB shell whose only content is a redirect to
    `/httpservice/retry/enablejs`. No header, cookie or TLS fingerprint changes
    that — the surface is simply not scrapeable over plain HTTP.
    """


class ParseError(GoogError):
    """The response arrived but did not look like what the parser expects."""


class NotFound(GoogError):
    """Query resolved to nothing (unknown place id, empty SERP, ...)."""
