"""Payload unwrapping shared by every Google surface.

Google's internal JSON endpoints all speak one of three dialects:

* **XSSI-prefixed JSON** — the body starts with `)]}'` (Maps `preview/place`,
  `search?tbm=map`) or `)]}',\\n` (Trends). Strip the prefix, parse the rest.
* **batchexecute** — a length-prefixed chunk stream of RPC envelopes. Used by
  Maps reviews, Trends trending-now and most modern Google web apps.
* **AF_initDataCallback** — JSON smuggled inside `<script>` tags of an HTML
  page (Images, some SERP verticals).

`safe_get` is the workhorse for reading these payloads: every response is a
deeply nested list where Google reshuffles slots without notice, so indexing
must never raise.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import ParseError

_XSSI_RE = re.compile(r"^\)\]\}'(?:,)?\s*")


def strip_xssi(text: str) -> str:
    """Remove Google's `)]}'` anti-JSON-hijacking prefix, in all its variants."""
    return _XSSI_RE.sub("", text.lstrip(), count=1)


def parse_json(text: str, *, what: str = "payload") -> Any:
    """Strip the XSSI prefix and parse. Raises `ParseError`, never JSONDecodeError.

    A non-JSON body almost always means the session went bad (consent wall,
    captcha, HTML error page). Swallowing that as "no results" is the worst
    possible failure mode for a batch job — it silently turns every row into a
    miss — so this raises loudly and lets the caller decide to re-bootstrap.
    """
    body = strip_xssi(text)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        snippet = body[:200].replace("\n", " ")
        raise ParseError(
            f"{what}: expected JSON, got {len(text)} bytes starting {snippet!r}"
        ) from e


def parse_batchexecute(text: str, rpc_name: str) -> Any:
    """Unwrap a batchexecute response and return the payload of one RPC.

    The body is `)]}'` followed by length-prefixed JSON chunks; each chunk is a
    list of envelopes like `["wrb.fr", "<rpc name>", "<json string>", ...]`.
    Returns None when the RPC is absent — which is also what an unauthorised
    (BotGuard-gated) call looks like: HTTP 200 with an empty envelope list.
    """
    body = strip_xssi(text)
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue  # length prefixes and blank separators
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        for env in chunk:
            if (
                isinstance(env, list)
                and len(env) > 2
                and env[0] == "wrb.fr"
                and env[1] == rpc_name
                and isinstance(env[2], str)
            ):
                return json.loads(env[2])
    return None


def build_batchexecute_body(rpc_name: str, payload: Any) -> str:
    """Serialise one RPC into the `f.req` form field batchexecute expects."""
    inner = json.dumps(payload, separators=(",", ":"))
    return json.dumps([[[rpc_name, inner, None, "generic"]]], separators=(",", ":"))


_AF_RE = re.compile(r"AF_initDataCallback\(\s*(\{.*?\})\s*\);", re.S)


def extract_af_data(html: str) -> list[Any]:
    """Pull every `AF_initDataCallback({...})` blob out of an HTML page.

    Google ships SERP verticals (Images above all) as JS callbacks rather than
    markup. The object literal is not valid JSON (unquoted keys, `function`
    values), so only the `data:` array is extracted, by balanced-bracket scan.
    """
    out = []
    for m in re.finditer(r"AF_initDataCallback\(", html):
        start = html.find("data:", m.end())
        if start == -1:
            continue
        arr = _balanced(html, html.find("[", start))
        if arr is None:
            continue
        try:
            out.append(json.loads(arr))
        except json.JSONDecodeError:
            continue
    return out


def _balanced(text: str, start: int) -> str | None:
    """Return the balanced `[...]` slice beginning at `start`, string-aware."""
    if start < 0 or start >= len(text) or text[start] != "[":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def safe_get(obj: Any, *keys: Any, default: Any = None) -> Any:
    """Index a deeply nested payload without ever raising.

    `safe_get(place, 4, 7)` reads `place[4][7]`, returning `default` on any
    missing slot, wrong type or None along the way. Google reshuffles these
    slots regularly; a KeyError in the middle of a 100k-row run is not an
    acceptable failure mode, a missing field is.
    """
    for k in keys:
        if obj is None:
            return default
        try:
            obj = obj[k]
        except (IndexError, KeyError, TypeError):
            return default
    return default if obj is None else obj


def walk(node: Any):
    """Depth-first iterator over every list/dict node in a payload.

    Extractors walk generically instead of hardcoding top-level indices, which
    is what keeps them working when Google renumbers slots (it does, often).
    """
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
