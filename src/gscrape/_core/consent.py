"""The cookie gate in front of every Google surface, solved over plain HTTP.

## What the gate is

A cookie-less request to Google gets one of three things:

* a redirect to `consent.google.com/m` (EU IPs, always),
* a stub payload — HTTP 200, right shape, most fields missing (Maps
  `preview/place` answers 23 KB instead of 127 KB),
* HTML with a captcha (rare, and a sign the IP is burnt).

The gate opens on `SOCS` + `NID` **together**. Either alone is not enough,
forged values are not enough, and the values do not have to be fresh —
months-old ones still work. Both are obtainable without a browser:
`GET` any Google URL redirects to the consent page, whose "accept all" form
POSTs to `consent.google.com/save`, which sets both.

## The warm-up

A freshly minted `NID` is not immediately trusted: Google keeps serving stubs
for a few seconds. So the bootstrap probes with a cheap known-good request and
retries until the response comes back rich, then caches the jar. That dance
happens roughly once per IP, since the cached jar is reused forever after.

## The IP binding

The jar only works from the IP it was issued on — see `client._cookie_path`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from .client import Client
from .errors import Blocked

CONSENT_SAVE_URL = "https://consent.google.com/save"
WARMUP_URL = "https://www.google.com/maps/@54.6,9.4,12z"

# The accept-all form is the one carrying the `set_sc` field; the page also
# renders a "reject all" and a "more options" form with near-identical markup.
_FORM_RE = re.compile(r"<form(.*?)</form>", re.S)
_INPUT_RE = re.compile(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"')


def accept_consent(client: Client) -> bool:
    """Walk the consent form once. True when a consent page was actually seen."""
    r = client.session.get(
        WARMUP_URL,
        timeout=client.timeout,
        headers={"accept-language": client.accept_language},
    )
    if "consent.google.com" not in str(r.url):
        return False

    fields = None
    for m in _FORM_RE.finditer(r.text):
        block = m.group(1)
        if "set_sc" in block:
            fields = dict(_INPUT_RE.findall(block))
            break
    if not fields:
        raise Blocked("consent page had no accept-all form (markup changed?)")

    client.session.post(
        CONSENT_SAVE_URL,
        data=fields,
        timeout=client.timeout,
        headers={
            "origin": "https://consent.google.com",
            "referer": str(r.url),
            "accept-language": client.accept_language,
        },
    )
    client.log("consent accepted")
    return True


def bootstrap(
    client: Client,
    probe: Callable[[], bool],
    *,
    force: bool = False,
    max_probes: int = 8,
    probe_delay: float = 2.0,
) -> None:
    """Get `client` into a state where Google serves full payloads.

    Args:
        probe: cheap callable returning True once responses look rich. Each
            surface brings its own (Maps: a known place must come back over
            45 KB). It must be strict — accepting a stub here ends the warm-up
            early and caches a jar that never ripens.
        force: ignore the cached jar and redo the whole dance.
    """
    if not force and client.load_cookies() and probe():
        return

    keep_meta = dict(client.meta)  # a forced redo must not lose harvested keys
    client.reset_session()
    client.meta.update(keep_meta)

    accept_consent(client)

    for i in range(max_probes):
        if probe():
            client.save_cookies()
            client.log(f"cookies ripe after {i + 1} probe(s)")
            return
        time.sleep(probe_delay)

    raise Blocked(
        "bootstrap failed: Google keeps returning the gated stub payload. "
        "Try another proxy, or wait a minute — the IP may be rate limited."
    )
