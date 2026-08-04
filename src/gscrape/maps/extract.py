"""Turning one raw Maps place payload into flat dicts.

Every function here is pure — payload in, dict out — so they are testable
against a saved fixture without touching the network, and reusable on payloads
obtained any other way.

Two extraction styles appear below, deliberately:

* **Indexed** (`place[203]`, `place[100]`) where the slot has been stable for
  years and the shape is unambiguous.
* **Generic walks** (photos, dishes, menu cards) where a record is recognised by
  its *shape* rather than its position. Google reshuffles top-level slots
  regularly; walking survives that, indexing does not.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .._core.parse import safe_get

# Photo URLs we never want: profile avatars, tiny map thumbs, review-strip crops.
_AVATAR_RE = re.compile(r"/a[/-]|/-[A-Za-z0-9_-]{10,}/AAAA")
_SIZE_SUFFIX_RE = re.compile(r"=[\w-]+$")

CLOSED_BADGES = {
    "GESCHLOSSEN",
    "CLOSED",
    "PERMANENTLY CLOSED",
    "DAUERHAFT GESCHLOSSEN",
    "TEMPORARILY CLOSED",
}


def strip_size(url: str) -> str:
    """`...=w203-h152-k-no` -> bare base url that any size can be appended to."""
    return _SIZE_SUFFIX_RE.sub("", url)


def photo_url(base_url: str, width: int = 1600, height: int | None = None) -> str:
    """Build a sized googleusercontent URL from a photo's `base_url`.

    Google serves any size on demand; `=w1600-h1200-k-no` is the shape the web
    app uses. There is no cost to asking for the biggest size, so callers that
    download photos should — the stored original is often 4000 px wide.
    """
    if "streetviewpixels" in base_url:
        return base_url
    suffix = f"=w{width}" + (f"-h{height}" if height else "") + "-k-no"
    return strip_size(base_url) + suffix


def photos(node: Any, *, streetview: bool = False) -> list[dict]:
    """Collect every photo record in a payload.

    A photo record is a list whose slot 6 looks like
    `[url, attribution, [origW, origH], [dispW, dispH]]` and whose slot 0 is the
    photo id. Recognising it by shape keeps this working across slot reshuffles.
    """
    found: dict[str, dict] = {}

    def visit(o: Any) -> None:
        if not isinstance(o, list):
            return
        img = o[6] if len(o) > 6 else None
        if (
            isinstance(img, list)
            and img
            and isinstance(img[0], str)
            and ("googleusercontent.com" in img[0] or "streetviewpixels" in img[0])
        ):
            url = img[0]
            is_sv = "streetviewpixels" in url
            skip = (is_sv and not streetview) or _AVATAR_RE.search(
                url.replace("https://lh3.googleusercontent.com", "")
            )
            if not skip:
                base = url if is_sv else strip_size(url)
                dims = img[2] if len(img) > 2 and isinstance(img[2], list) else None
                rec = found.setdefault(
                    base,
                    {
                        "id": o[0] if isinstance(o[0], str) else None,
                        "base_url": base,
                        "caption": None,
                        "width": dims[0] if dims else None,
                        "height": dims[1] if dims else None,
                        "streetview": is_sv,
                    },
                )
                if not rec["caption"] and isinstance(o[3], str) and o[3]:
                    rec["caption"] = o[3]
        for v in o:
            visit(v)

    visit(node)
    return list(found.values())


def popular_dishes(place: Any) -> list[dict]:
    """Dish name + photo pairs Google shows under "Beliebte Gerichte"."""
    out, seen = [], set()

    def visit(o: Any) -> None:
        if not isinstance(o, list):
            return
        if (
            len(o) > 6
            and isinstance(o[4], str)
            and o[4]
            and isinstance(o[6], list)
            and o[6]
            and isinstance(o[6][0], list)
        ):
            photo = safe_get(o, 6, 0, 6, 0)
            if (
                isinstance(photo, str)
                and "googleusercontent" in photo
                and o[4] not in seen
            ):
                seen.add(o[4])
                out.append({"name": o[4], "photo": strip_size(photo)})
        for v in o:
            visit(v)

    visit(safe_get(place, 120, default=[]))
    return out


def hours(place: Any) -> dict:
    """Opening hours plus the live "Geöffnet/Geschlossen" line.

    `place[203][0]` is one entry per weekday:
    `[label, weekday_no, [y, m, d], [["11:00-00:00", ...]], ...]`.
    """
    block = safe_get(place, 203)
    if not block:
        return {}
    days = []
    for row in safe_get(block, 0, default=[]) or []:
        ranges = [
            r[0]
            for r in (safe_get(row, 3, default=[]) or [])
            if isinstance(r, list) and r and isinstance(r[0], str)
        ]
        date = safe_get(row, 2)
        days.append(
            {
                "day": safe_get(row, 0),
                "weekday_no": safe_get(row, 1),
                "date": "-".join(f"{v:02d}" for v in date)
                if isinstance(date, list)
                else None,
                "ranges": ranges,
                "closed": not ranges,
            }
        )
    return {"days": days, "status": safe_get(block, 1, 4, 0)}


def attributes(place: Any) -> dict:
    """The "Serviceoptionen / Barrierefreiheit / Ambiente / ..." chips.

    `place[100]` holds groups keyed by a stable slug (`service_options`,
    `accessibility`, `payments`, ...). Each attribute carries a present flag, so
    absent-but-known features come back as False rather than vanishing — which
    matters: "has no wheelchair access" is a fact, "unknown" is not.
    """
    groups: dict[str, dict] = {}
    for outer in safe_get(place, 100, default=[]) or []:
        if not isinstance(outer, list):
            continue
        for grp in outer:
            if not (
                isinstance(grp, list)
                and len(grp) > 2
                and isinstance(grp[0], str)
                and not grp[0].startswith("/geo")
            ):
                continue
            bucket = groups.setdefault(grp[0], {"label": grp[1], "items": []})
            for attr in grp[2] if isinstance(grp[2], list) else []:
                if not (
                    isinstance(attr, list) and len(attr) > 2 and isinstance(attr[1], str)
                ):
                    continue
                # attr[2][1][0][0] == 1 -> the place HAS it, 0 -> explicitly not
                present = safe_get(attr, 2, 1, 0, 0)
                value = safe_get(attr, 2, 3, 2)  # e.g. "Parkplatz schwer zu finden"
                bucket["items"].append(
                    {
                        "key": attr[0].rsplit("/", 1)[-1],
                        "label": attr[1],
                        "present": bool(present) if present in (0, 1) else None,
                        "value": value if isinstance(value, str) else None,
                    }
                )
    return {k: v for k, v in groups.items() if v["items"]}


def menu_card(place: Any) -> list[dict]:
    """The full menu with prices, when the business published one.

    `place[125]` nests sections as
    `[[section_name, section_note], [[[[dish, description], [price]], ...]]]`.
    """
    sections: list[dict] = []

    def visit(node: Any) -> None:
        if not isinstance(node, list):
            return
        if (
            len(node) == 2
            and isinstance(node[0], list)
            and len(node[0]) == 2
            and isinstance(node[0][0], str)
            and isinstance(node[0][1], str)
            and isinstance(node[1], list)
        ):
            items = []
            for grp in node[1]:
                for dish in grp if isinstance(grp, list) else []:
                    name = safe_get(dish, 0, 0)
                    if not isinstance(name, str):
                        continue
                    items.append(
                        {
                            "name": name,
                            "description": safe_get(dish, 0, 1),
                            "price": safe_get(dish, 1, 0),
                        }
                    )
            if items:
                sections.append(
                    {
                        "section": node[0][0],
                        "note": node[0][1] or None,
                        "items": items,
                    }
                )
            return
        for v in node:
            visit(v)

    visit(safe_get(place, 125, default=[]))
    return sections


def popular_times(place: Any) -> list[dict]:
    """Per-weekday hourly business, as Google's "Stoßzeiten" bar chart."""
    out = []
    for day in safe_get(place, 84, 0, default=[]) or []:
        if not (isinstance(day, list) and len(day) > 1 and isinstance(day[1], list)):
            continue
        hrs = [
            {"hour": h[0], "busy_pct": h[1], "label": h[4]}
            for h in day[1]
            if isinstance(h, list) and len(h) > 4 and isinstance(h[0], int)
        ]
        if hrs:
            out.append({"weekday_no": day[0], "hours": hrs})
    return out


#: Reviews predating Google Maps are impossible; so are ones from the future.
_MIN_REVIEW_TS = 1_100_000_000  # 2004, the year Maps' predecessor launched


def _review_date(ts: Any) -> str | None:
    """Google stamps reviews in seconds or microseconds depending on endpoint.

    Scale is detected by magnitude, then sanity-checked: a corrupt slot would
    otherwise silently produce a year like 3170843 rather than a missing date.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    sec = ts / 1_000_000 if ts > 1e12 else ts
    if not _MIN_REVIEW_TS <= sec <= time.time() + 86_400:
        return None
    try:
        return time.strftime("%Y-%m-%d", time.localtime(sec))
    except (ValueError, OSError):
        return None


def _one_review(inner: Any) -> dict:
    return {
        "author": safe_get(inner, 1, 4, 5, 0),
        "stars": safe_get(inner, 2, 0, 0),
        "text": safe_get(inner, 2, 15, 0, 0, default="") or "",
        "date_approx": _review_date(safe_get(inner, 1, 2)),
    }


def reviews(place: Any) -> list[dict]:
    """The top reviews Google embeds in the place payload (usually 8).

    These cost no extra request and no BotGuard key — unlike deep pagination.
    """
    out = []
    for wrapper in safe_get(place, 175, 9, 0, 0, default=[]) or []:
        inner = wrapper[0] if isinstance(wrapper, list) and wrapper else wrapper
        rec = _one_review(inner)
        if rec["author"] or rec["text"]:
            out.append(rec)
    return out


def business_status(place: Any) -> dict:
    """OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY.

    `place[88][0]` is the localised red badge Google itself renders — not a
    heuristic, unlike scraping button strings off the SERP (that carried a ~5%
    false-positive rate).

    Do NOT confuse the badge with the report buttons at `place[96][5]`: EVERY
    place, open or not, offers an "Als geschlossen melden" action, so their
    presence means nothing on its own. Only closed places additionally get a
    "wieder öffnen" action, and its sibling label names the closure type.

    CAVEAT on the temporary/permanent split: across 500 sampled places and a
    120k-row production run the "dauerhaft" wording was never observed — every
    closure said "vorübergehend". Permanently closed businesses appear to be
    dropped from Maps search altogether, so `search()` returning nothing for a
    name that should exist is the stronger permanent-closure signal.
    **Trust `closed`; treat the specific kind as a hint.**
    """
    badge = safe_get(place, 88, 0)
    closed = isinstance(badge, str) and badge.strip().upper() in CLOSED_BADGES
    if not closed:
        return {"status": "OPERATIONAL", "closed": False, "badge": None}

    labels: list[str] = []
    for item in safe_get(place, 96, 5, default=[]) or []:
        if isinstance(item, list):
            labels += [x for x in item if isinstance(x, str)]
    blob = " ".join(labels).lower()
    if not blob:
        # No edit menu in this payload. Absence of evidence is not evidence: an
        # earlier version defaulted to CLOSED_PERMANENTLY here and mislabelled
        # 18 places the full payload shows as merely temporarily closed.
        kind = "CLOSED"
    elif "vorübergehend" in blob or "temporarily" in blob:
        kind = "CLOSED_TEMPORARILY"
    elif "dauerhaft" in blob or "permanently" in blob:
        kind = "CLOSED_PERMANENTLY"
    else:
        kind = "CLOSED"
    return {"status": kind, "closed": True, "badge": badge}


def shape(
    p: Any,
    *,
    place_id: str | None = None,
    fid: str | None = None,
    with_photos: bool = True,
    lat: float = 0.0,
    lng: float = 0.0,
) -> dict:
    """One raw place object -> the flat dict callers consume."""
    status = business_status(p)
    dist = safe_get(p, 175, 3, default=[]) or []
    addr = safe_get(p, 183, 1, default=[]) or []

    out = {
        # identity
        "place_id": place_id,
        "fid": fid,
        "cid": safe_get(p, 181, 5),
        "knowledge_id": safe_get(p, 89),
        "name": safe_get(p, 11),
        "business_status": status["status"],
        "closed": status["closed"],
        "maps_url": (
            f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            if place_id
            else None
        ),
        "reviews_url": safe_get(p, 4, 3, 0),
        # what it is
        "categories": safe_get(p, 13, default=[]),
        "primary_category": safe_get(p, 13, 0),
        "category_ids": [
            c[0]
            for c in (safe_get(p, 76, default=[]) or [])
            if isinstance(c, list) and c and isinstance(c[0], str)
        ],
        "descriptors": [
            d[1]
            for d in (safe_get(p, 32, default=[]) or [])
            if isinstance(d, list) and len(d) > 1 and isinstance(d[1], str)
        ],
        "description": safe_get(p, 154, 0, 0),
        # where it is
        "address": safe_get(p, 18),
        "address_short": safe_get(p, 39),
        "street": safe_get(addr, 1),
        "city": safe_get(addr, 3),
        "postal_code": safe_get(addr, 4),
        "country": safe_get(addr, 6),
        "plus_code": safe_get(p, 183, 2, 1, 0),
        "lat": safe_get(p, 9, 2, default=lat),
        "lng": safe_get(p, 9, 3, default=lng),
        "timezone": safe_get(p, 30),
        # how to reach it
        "phone": safe_get(p, 178, 0, 0),
        "phone_international": safe_get(p, 178, 0, 1, 1, 0),
        "website": safe_get(p, 7, 0),
        "menu_url": safe_get(p, 38, 0),
        "reservation_urls": [
            r[0]
            for r in (safe_get(p, 46, default=[]) or [])
            if isinstance(r, list) and isinstance(r[0], str)
        ],
        # reputation
        "rating": safe_get(p, 4, 7),
        "reviews": safe_get(p, 4, 8),
        "rating_distribution": (
            {str(i + 1): n for i, n in enumerate(dist)} if dist else {}
        ),
        "price_range": safe_get(p, 4, 2),
        "typical_duration": safe_get(p, 117, 0),
        # structured blocks
        "hours": hours(p),
        "attributes": attributes(p),
        "menu_card": menu_card(p),
        "popular_dishes": popular_dishes(p),
        "popular_times": popular_times(p),
        "top_reviews": reviews(p),
        "owner": safe_get(p, 57, 1),
    }
    if with_photos:
        out["photos"] = photos(p)
    return out
