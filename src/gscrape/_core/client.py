"""The shared HTTP layer every service is built on.

One `Client` == one egress identity: a curl_cffi session with a real Chrome
TLS/JA3 fingerprint, one Google cookie jar, one rate limiter, one proxy. Every
service (`Maps`, `Search`, `Trends`, ...) either brings its own or shares one
that the caller passes in, so a pool run can pin N clients to N proxies and
have every surface benefit at once.

Three things here are load-bearing and were paid for in debugging time:

1. **curl_cffi, not requests.** Google serves a cookie-gated stub payload (or a
   straight 403) to clients whose TLS fingerprint does not match the browser
   they claim to be in the User-Agent. `impersonate="chrome"` is what makes the
   plain-HTTP approach work at all.
2. **The consent cookies `SOCS` + `NID` together.** Either alone is not enough,
   forged values are not enough. See `consent.py`.
3. **The jar is bound to the IP it was issued on.** Replaying a jar through
   another proxy exit gets the stub payload back, so each proxy caches its own
   file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from curl_cffi import requests as cr

from .errors import Captcha, HttpError, JsRequired, RateLimited
from .parse import build_batchexecute_body, parse_batchexecute, parse_json

# Google's captcha wall. Detected on the final URL (it 302s there) and in the
# body (some surfaces render it inline with a 200).
SORRY_MARKERS = ("/sorry/index", "/sorry/image")

# The "JavaScript is required" shell Web Search serves to non-JS clients.
JS_SHELL_MARKER = "/httpservice/retry/enablejs"

#: The verbs curl_cffi accepts. Ours is narrowed to match, so a typo in a
#: service reaching for an unsupported verb is a type error, not a 405.
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

DEFAULT_IMPERSONATE = os.environ.get("GSCRAPE_IMPERSONATE", "chrome")
DEFAULT_CACHE_DIR = Path(
    os.environ.get("GSCRAPE_CACHE_DIR", Path.home() / ".cache" / "gscrape")
)


def _cookie_path(proxy: str | None, cookie_file: str | Path | None) -> Path:
    """Where this egress identity keeps its jar.

    SOCS+NID are issued against an IP, so a shared file would have pool runs
    clobbering the direct-IP jar with cookies that only work behind a proxy.
    """
    base = Path(cookie_file) if cookie_file else DEFAULT_CACHE_DIR / "cookies.json"
    if proxy:
        tag = hashlib.sha1(proxy.encode()).hexdigest()[:10]
        base = base.with_suffix(f".{tag}.json")
    return base


class Client:
    """A rate-limited, fingerprint-spoofing, cookie-persisting Google session."""

    def __init__(
        self,
        hl: str = "de",
        gl: str = "de",
        *,
        proxy: str | None = None,
        impersonate: str = DEFAULT_IMPERSONATE,
        timeout: int = 25,
        max_retries: int = 4,
        rate_limit: float | None = None,
        cookie_file: str | Path | None = None,
        verbose: bool = False,
    ):
        """
        Args:
            hl: interface language (`de`, `en`, ...). Changes result language.
            gl: country of the search (`de`, `us`, ...). Changes result ranking.
            proxy: `http://user:pass@host:port`, or None for the own IP.
            impersonate: curl_cffi browser target. `chrome` tracks the newest
                supported Chrome; pin (`chrome124`) only to reproduce a bug.
            rate_limit: max requests per second for this client. Google's
                limiter is per-IP; measured ceiling on one IP is ~5 req/s
                sustained for Maps place lookups before 429s appear.
            cookie_file: override the jar location (per-proxy suffixed).
        """
        self.hl = hl
        self.gl = gl
        self.proxy = proxy
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose
        self.cookie_file = _cookie_path(proxy, cookie_file)

        self._min_interval = 1.0 / rate_limit if rate_limit else 0.0
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._cookies_loaded = False
        self.meta: dict[str, Any] = {}  # bgkeys and other harvested secrets
        self.session = self._new_session()

    # ------------------------------------------------------------------ setup

    def _new_session(self) -> cr.Session:
        s = cr.Session(impersonate=self.impersonate)
        if self.proxy:
            s.proxies = {"http": self.proxy, "https": self.proxy}
        return s

    def reset_session(self) -> None:
        """Throw away cookies and start over (used by the consent bootstrap)."""
        self.session = self._new_session()
        self._cookies_loaded = False

    def log(self, *a: Any) -> None:
        if self.verbose:
            print("  [gscrape]", *a, file=sys.stderr)

    @property
    def accept_language(self) -> str:
        return f"{self.hl}-{self.gl.upper()},{self.hl};q=0.9"

    def headers(self, **extra: str) -> dict[str, str]:
        h = {
            "accept": "*/*",
            "accept-language": self.accept_language,
            "referer": "https://www.google.com/",
        }
        h.update(extra)
        return h

    # ----------------------------------------------------------------- cookies

    def load_cookies(self) -> bool:
        """Load the cached jar into the session. True when one was found."""
        if self._cookies_loaded:
            return True
        if not self.cookie_file.exists():
            return False
        try:
            blob = json.loads(self.cookie_file.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        cookies = blob.get("cookies", blob)  # tolerate the flat legacy format
        self.meta.update(blob.get("meta", {}))
        for name, value in cookies.items():
            if isinstance(value, str):
                self._set_cookie(name, value)
        self._cookies_loaded = True
        self.log("loaded cookies", sorted(cookies), "meta", sorted(self.meta))
        return True

    def _set_cookie(self, name: str, value: str) -> None:
        """`__Secure-`/`__Host-` prefixed cookies are only valid when secure."""
        self.session.cookies.set(
            name,
            value,
            domain=".google.com",
            secure=name.startswith(("__Secure-", "__Host-")),
        )

    def save_cookies(self) -> None:
        jar = dict(self.session.cookies.items())
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_text(
            json.dumps({"cookies": jar, "meta": self.meta}, indent=1)
        )
        with contextlib.suppress(OSError):  # e.g. a FAT-mounted cache dir
            self.cookie_file.chmod(0o600)
        self._cookies_loaded = True
        self.log("saved cookies ->", self.cookie_file)

    @property
    def has_cookies(self) -> bool:
        return self._cookies_loaded

    # -------------------------------------------------------------- requesting

    def _throttle(self) -> None:
        if not self._min_interval:
            return
        with self._lock:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_status: tuple[int, ...] = (200,),
        **kw: Any,
    ):
        """One HTTP call with exponential backoff on Google's rate limiter.

        Measured on Maps: sustained ~40 lookups/s across an 11-IP pool is fine,
        but bursting a single IP past ~5/s earns a 429. The limiter is per-IP,
        so a proxy pool multiplies the ceiling instead of raising it.
        """
        # Every surface benefits from a cached consent jar, not just the ones
        # that bootstrap one. Loading is a file read, so it costs nothing when
        # there is no jar yet.
        if not self._cookies_loaded:
            self.load_cookies()

        delay = 2.0
        last: Any = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            r = self.session.request(
                method,
                url,
                timeout=self.timeout,
                headers=self.headers(**(headers or {})),
                **kw,
            )
            last = r
            self._check_wall(r, url)
            if r.status_code in allow_status:
                return r
            if r.status_code in (429, 503) and attempt < self.max_retries:
                self.log(f"HTTP {r.status_code}, backing off {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (429, 503):
                raise RateLimited(r.status_code, url, r.text[:400])
            raise HttpError(r.status_code, url, r.text[:400])
        raise RateLimited(last.status_code if last else 429, url)

    @staticmethod
    def _check_wall(response: Any, url: str) -> None:
        """Turn Google's two soft walls into typed errors instead of garbage.

        Both answer with a perfectly valid HTTP response, so without this a
        captcha page parses as "no results" and poisons a whole batch run.
        """
        final = str(getattr(response, "url", "") or "")
        if any(m in final for m in SORRY_MARKERS):
            raise Captcha(final, url)
        body = response.text or ""
        if body[:4000].count(JS_SHELL_MARKER):
            raise JsRequired(
                f"Google served the JavaScript shell for {url[:90]} — this "
                "surface cannot be scraped over plain HTTP"
            )
        if any(m in body[:2000] for m in SORRY_MARKERS):
            raise Captcha(final or url, url)

    def import_cookies(self, cookies: str | dict[str, str], *, save: bool = True) -> None:
        """Adopt cookies from a browser session (after solving a captcha by hand).

        Accepts either a dict or a raw `Cookie:` header string copied out of
        devtools (`NID=abc; SOCS=xyz`). The captcha unblock is bound to the IP
        that solved it, so import into the `Client` that uses that same egress.
        """
        if isinstance(cookies, str):
            pairs = [c.strip().split("=", 1) for c in cookies.split(";") if "=" in c]
            cookies = {k.strip(): v.strip() for k, v in pairs}
        for name, value in cookies.items():
            self._set_cookie(name, value)
        self._cookies_loaded = True
        if save:
            self.save_cookies()

    def get(self, url: str, **kw: Any) -> str:
        return self.request("GET", url, **kw).text

    def post(self, url: str, **kw: Any) -> str:
        return self.request("POST", url, **kw).text

    def get_json(self, url: str, *, what: str = "payload", **kw: Any) -> Any:
        return parse_json(self.get(url, **kw), what=what)

    def batchexecute(
        self,
        endpoint: str,
        rpcid: str,
        rpc_name: str,
        payload: Any,
        *,
        reqid: int = 100000,
        source_path: str = "/",
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """POST one RPC onto a batchexecute bus and return its parsed result.

        `endpoint` is the full `.../data/batchexecute` URL of the app whose RPC
        is being called — every Google web app hosts its own bus and only
        accepts its own rpcids.

        Some buses (Maps reviews) are BotGuard-gated: without the app's token
        header they answer 200 with an empty envelope list, which surfaces here
        as None rather than an error.
        """
        url = (
            f"{endpoint}?rpcids={rpcid}&source-path={source_path}"
            f"&hl={self.hl}&_reqid={reqid}&rt=c"
        )
        headers = {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "origin": "https://www.google.com",
            "x-same-domain": "1",
        }
        headers.update(extra_headers or {})
        body = self.post(
            url,
            data={"f.req": build_batchexecute_body(rpc_name, payload)},
            headers=headers,
        )
        return parse_batchexecute(body, rpc_name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        via = self.proxy.split("@")[-1] if self.proxy else "direct"
        return f"<Client {self.hl}/{self.gl} via {via}>"
