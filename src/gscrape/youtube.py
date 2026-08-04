"""YouTube, through InnerTube — the API the youtube.com frontend itself uses.

`youtubei/v1/*` takes JSON, answers JSON, needs no OAuth, no cookies and no
browser. The "API key" below is not a credential: it is the public web-client
key baked into every youtube.com page, identical for every visitor.

    from gscrape import YouTube
    yt = YouTube(hl="de", gl="DE")
    yt.search("laufschuhe test", limit=40)
    yt.search("fitness", type="shorts", upload_date="week", sort="views")
    yt.video("dQw4w9WgXcQ")
    yt.channel_videos("@MrBeast", limit=60)

## Search filters are protobuf, not magic strings

The `params` field of a search request is a base64url protobuf message:

    field 1 (varint)  sort:    0 relevance, 1 rating, 2 upload date, 3 views
    field 2 (message) filters:
        field 1  upload date: 1 hour, 2 today, 3 week, 4 month, 5 year
        field 2  type:        1 video, 2 channel, 3 playlist, 4 movie, 9 short
        field 3  duration:    1 <4min, 2 >20min, 4 <3min, 5 3-20min
        field 4  hd    field 5  subtitles   field 6  creative commons
        field 7  3d    field 8  live        field 9  purchased
        field 14 4k    field 15 360         field 23 location
        field 25 hdr   field 26 vr180

Hardcoding one string per filter (`EgIQCQ%3D%3D` = Shorts) is what most
libraries do, and it makes combinations impossible. Encoding the message means
"Shorts, this week, sorted by views" is one call. The field numbers above were
read off YouTube's own filter chips — `_filter_chips()` re-derives them live if
YouTube ever renumbers.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from collections.abc import Iterator
from typing import Any

from ._core.errors import NotFound, ParseError
from ._core.parse import safe_get
from ._core.service import Service

INNERTUBE = "https://www.youtube.com/youtubei/v1"
# The public web-client key from youtube.com's own bootstrap data.
WEB_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
WEB_VERSION = "2.20240101.00.00"

# `clientName` decides what YouTube hands back. WEB is the richest; ANDROID and
# IOS return stream URLs the web client hides, at the cost of thinner metadata.
CLIENTS = {
    "web": {"clientName": "WEB", "clientVersion": WEB_VERSION},
    "android": {
        "clientName": "ANDROID",
        "clientVersion": "19.09.37",
        "androidSdkVersion": 30,
    },
    "ios": {"clientName": "IOS", "clientVersion": "19.09.3"},
    "tv": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0"},
}

SORT = {"relevance": 0, "rating": 1, "date": 2, "views": 3}
UPLOAD_DATE = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
TYPE = {"video": 1, "channel": 2, "playlist": 3, "movie": 4, "short": 9, "shorts": 9}
DURATION = {"short": 1, "long": 2, "under4": 1, "under3": 4, "3to20": 5, "over20": 2}
FEATURES = {
    "hd": 4,
    "subtitles": 5,
    "creative_commons": 6,
    "3d": 7,
    "live": 8,
    "purchased": 9,
    "4k": 14,
    "360": 15,
    "location": 23,
    "hdr": 25,
    "vr180": 26,
}


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number: int, value: int | bytes) -> bytes:
    """One protobuf field: varint for ints, length-delimited for bytes."""
    if isinstance(value, bytes):
        return _varint((number << 3) | 2) + _varint(len(value)) + value
    return _varint((number << 3) | 0) + _varint(value)


def search_params(
    *,
    sort: str = "relevance",
    type: str | None = None,
    upload_date: str | None = None,
    duration: str | None = None,
    features: list[str] | None = None,
) -> str:
    """Build the base64url `params` value for a filtered search."""
    filters = b""
    if upload_date:
        filters += _field(1, UPLOAD_DATE[upload_date])
    if type:
        filters += _field(2, TYPE[type])
    if duration:
        filters += _field(3, DURATION[duration])
    for feat in features or []:
        filters += _field(FEATURES[feat], 1)

    msg = b""
    if SORT.get(sort):
        msg += _field(1, SORT[sort])
    if filters:
        msg += _field(2, filters)
    return base64.urlsafe_b64encode(msg).decode() if msg else ""


def _text(node: Any) -> str | None:
    """InnerTube spells text three ways depending on the renderer."""
    if not isinstance(node, dict):
        return None
    if "simpleText" in node:
        return node["simpleText"]
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(r.get("text", "") for r in runs)
    return None


#: Locale-dependent magnitude suffixes YouTube renders counts with.
_MULTIPLIER = {
    "k": 1_000,
    "tsd": 1_000,
    "mil": 1_000_000,
    "mio": 1_000_000,
    "m": 1_000_000,
    "mln": 1_000_000,
    "b": 1_000_000_000,
    "mrd": 1_000_000_000,
    "mld": 1_000_000_000,
}


def _number(text: str | None) -> int | None:
    """ "3.934 Aufrufe" / "1.2M views" / "41 Mio. Aufrufe" -> int.

    Both decimal conventions appear depending on `hl`, so the separator is
    decided by position, not by character: a `.` or `,` followed by exactly
    three digits at the end of the number is a thousands separator, anything
    else is a decimal point.
    """
    if not text:
        return None
    t = text.replace("\u00a0", " ").replace("\u202f", " ").strip()
    m = re.match(r"\s*([\d.,\s]+?)\s*([A-Za-z\u00c0-\u00ff]+)?\.?\s", t + " ")
    if not m:
        return None
    num = re.sub(r"\s", "", m.group(1)).rstrip(".,")
    if re.search(r"[.,]\d{3}$", num) or num.count(".") + num.count(",") > 1:
        num = re.sub(r"[.,]", "", num)  # thousands separators only
    else:
        num = num.replace(",", ".")  # a single separator is decimal
    try:
        value = float(num)
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower().rstrip(".")
    return int(value * _MULTIPLIER.get(suffix, 1))


def _walk(node: Any, key: str) -> Iterator[dict]:
    """Yield every `key` renderer anywhere in a response.

    InnerTube nests renderers 8-12 levels deep and moves them between shelves
    without notice; walking for the renderer name is the only stable read.
    """
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict):
            yield node[key]
        for v in node.values():
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


class YouTube(Service):
    """Search, video, channel and playlist data straight from InnerTube."""

    def __init__(self, *args: Any, client_name: str = "web", **kw: Any):
        super().__init__(*args, **kw)
        self.client_name = client_name

    # ------------------------------------------------------------- transport

    def _context(self) -> dict:
        return {
            "client": {
                **CLIENTS[self.client_name],
                "hl": self.hl,
                "gl": self.gl.upper(),
            }
        }

    def call(self, endpoint: str, body: dict) -> dict:
        """POST one InnerTube endpoint (`search`, `player`, `browse`, `next`)."""
        url = f"{INNERTUBE}/{endpoint}?key={WEB_KEY}&prettyPrint=false"
        raw = self.client.post(
            url,
            json={"context": self._context(), **body},
            headers={
                "content-type": "application/json",
                "origin": "https://www.youtube.com",
                "referer": "https://www.youtube.com/",
            },
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ParseError(f"innertube/{endpoint} returned non-JSON") from e

    # ---------------------------------------------------------------- search

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        type: str | None = None,
        sort: str = "relevance",
        upload_date: str | None = None,
        duration: str | None = None,
        features: list[str] | None = None,
    ) -> list[dict]:
        """Search videos, shorts, channels or playlists, with paging.

        Args:
            type: `video`, `shorts`, `channel`, `playlist`, `movie`.
            sort: `relevance`, `rating`, `date`, `views`.
            upload_date: `hour`, `today`, `week`, `month`, `year`.
            duration: `under3`, `3to20`, `over20`.
            features: any of `hd`, `4k`, `subtitles`, `live`, `360`, `hdr`,
                `creative_commons`, `3d`, `vr180`, `location`, `purchased`.
        """
        body: dict[str, Any] = {"query": query}
        params = search_params(
            sort=sort,
            type=type,
            upload_date=upload_date,
            duration=duration,
            features=features,
        )
        if params:
            body["params"] = params

        out: list[dict] = []
        continuation: str | None = None
        while len(out) < limit:
            data = self.call(
                "search", {**body, "continuation": continuation} if continuation else body
            )
            rows = self._shape_results(data)
            if not rows:
                break
            out.extend(rows)
            continuation = self._continuation(data)
            if not continuation:
                break
        return out[:limit]

    def shorts(self, query: str, **kw: Any) -> list[dict]:
        """Search Shorts only. Shorthand for `type="shorts"`."""
        return self.search(query, type="shorts", **kw)

    @staticmethod
    def _continuation(data: dict) -> str | None:
        for c in _walk(data, "continuationCommand"):
            if c.get("token"):
                return c["token"]
        return None

    def _shape_results(self, data: dict) -> list[dict]:
        out: list[dict] = []
        for v in _walk(data, "videoRenderer"):
            out.append(self._shape_video(v))
        # Shorts have their own renderer, and YouTube has migrated it twice:
        # `reelItemRenderer` (legacy, still served in some shelves) then
        # `shortsLockupViewModel` (current). Both are read, so a rollback or a
        # mixed response does not silently drop half the results.
        for v in _walk(data, "reelItemRenderer"):
            out.append(
                {
                    "kind": "short",
                    "video_id": v.get("videoId"),
                    "title": _text(v.get("headline")),
                    "url": f"https://www.youtube.com/shorts/{v.get('videoId')}",
                    "views": _number(_text(v.get("viewCountText"))),
                    "views_text": _text(v.get("viewCountText")),
                    "thumbnail": safe_get(v, "thumbnail", "thumbnails", -1, "url"),
                }
            )
        for v in _walk(data, "shortsLockupViewModel"):
            out.append(self._shape_short(v))
        for c in _walk(data, "channelRenderer"):
            out.append(
                {
                    "kind": "channel",
                    "channel_id": c.get("channelId"),
                    "title": _text(c.get("title")),
                    "handle": _text(c.get("subscriberCountText"))
                    if str(_text(c.get("subscriberCountText")) or "").startswith("@")
                    else None,
                    "subscribers_text": _text(c.get("videoCountText")),
                    "description": _text(c.get("descriptionSnippet")),
                    "url": f"https://www.youtube.com/channel/{c.get('channelId')}",
                    "thumbnail": safe_get(c, "thumbnail", "thumbnails", -1, "url"),
                }
            )
        for p in _walk(data, "playlistRenderer"):
            out.append(
                {
                    "kind": "playlist",
                    "playlist_id": p.get("playlistId"),
                    "title": _text(p.get("title")),
                    "video_count": _number(_text(p.get("videoCountText"))),
                    "channel": _text(p.get("shortBylineText")),
                    "url": f"https://www.youtube.com/playlist?list={p.get('playlistId')}",
                }
            )
        # Playlists (and increasingly videos) arrive as the generic lockup, so
        # this is what catches result types the older renderers no longer cover.
        for v in _walk(data, "lockupViewModel"):
            out.append(self._shape_lockup(v))
        return out

    @staticmethod
    def _shape_lockup(v: dict) -> dict:
        """The `lockupViewModel` channel pages migrated to in 2025.

        Same data as `videoRenderer`, restructured: the metadata rows are an
        ordered, delimiter-joined list ("41 Mio. Aufrufe • vor 10 Tagen"), so
        views and age are read positionally rather than by field name.
        """
        kinds = {
            "LOCKUP_CONTENT_TYPE_VIDEO": "video",
            "LOCKUP_CONTENT_TYPE_SHORTS": "short",
            "LOCKUP_CONTENT_TYPE_PLAYLIST": "playlist",
            "LOCKUP_CONTENT_TYPE_PODCAST": "playlist",
            "LOCKUP_CONTENT_TYPE_CHANNEL": "channel",
        }
        meta = safe_get(v, "metadata", "lockupMetadataViewModel", default={})
        parts = [
            safe_get(p, "text", "content")
            for row in safe_get(
                meta, "metadata", "contentMetadataViewModel", "metadataRows", default=[]
            )
            or []
            for p in row.get("metadataParts", [])
        ]
        parts = [p for p in parts if isinstance(p, str)]
        cid = v.get("contentId")
        kind = kinds.get(v.get("contentType") or "", "video")
        views_text = next((p for p in parts if any(c.isdigit() for c in p)), None)
        url = {
            "playlist": f"https://www.youtube.com/playlist?list={cid}",
            "channel": f"https://www.youtube.com/channel/{cid}",
            "short": f"https://www.youtube.com/shorts/{cid}",
        }.get(kind, f"https://www.youtube.com/watch?v={cid}")
        row = {
            "kind": kind,
            "title": safe_get(meta, "title", "content"),
            "url": url if cid else None,
            "channel": parts[0] if kind in ("playlist", "video") and parts else None,
            "views": _number(views_text) if kind != "playlist" else None,
            "views_text": views_text if kind != "playlist" else None,
            "published_text": parts[-1] if len(parts) > 1 else None,
            "thumbnail": safe_get(
                v, "contentImage", "thumbnailViewModel", "image", "sources", -1, "url"
            )
            or safe_get(
                v,
                "contentImage",
                "collectionThumbnailViewModel",
                "primaryThumbnail",
                "thumbnailViewModel",
                "image",
                "sources",
                -1,
                "url",
            ),
        }
        if kind == "playlist":
            row["playlist_id"] = cid
            row["video_count"] = _number(views_text)
        elif kind == "channel":
            row["channel_id"] = cid
        else:
            row["video_id"] = cid
        return row

    @staticmethod
    def _shape_short(v: dict) -> dict:
        """The current Shorts lockup: no channel, no date, only title + views."""
        vid = (
            safe_get(v, "onTap", "innertubeCommand", "reelWatchEndpoint", "videoId")
            or safe_get(v, "entityId", default="").removeprefix("shorts-shelf-item-")
            or None
        )
        views_text = safe_get(v, "overlayMetadata", "secondaryText", "content")
        return {
            "kind": "short",
            "video_id": vid,
            "title": safe_get(v, "overlayMetadata", "primaryText", "content"),
            "url": f"https://www.youtube.com/shorts/{vid}" if vid else None,
            "views": _number(views_text),
            "views_text": views_text,
            "thumbnail": safe_get(v, "thumbnail", "sources", 0, "url"),
        }

    @staticmethod
    def _shape_video(v: dict) -> dict:
        vid = v.get("videoId")
        badges = [
            _text(safe_get(b, "metadataBadgeRenderer", "label"))
            for b in v.get("badges", [])
        ]
        return {
            "kind": "video",
            "video_id": vid,
            "title": _text(v.get("title")),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": _text(v.get("longBylineText")) or _text(v.get("ownerText")),
            "channel_id": safe_get(
                v,
                "ownerText",
                "runs",
                0,
                "navigationEndpoint",
                "browseEndpoint",
                "browseId",
            ),
            "description": _text(
                v.get("detailedMetadataSnippets", [{}])[0].get("snippetText")
            )
            if v.get("detailedMetadataSnippets")
            else _text(v.get("descriptionSnippet")),
            "duration": _text(v.get("lengthText")),
            "views": _number(_text(v.get("viewCountText"))),
            "views_text": _text(v.get("viewCountText")),
            # Relative ("vor 4 Wochen") because that is all search returns;
            # `video()` gives the exact date.
            "published_text": _text(v.get("publishedTimeText")),
            "thumbnail": safe_get(v, "thumbnail", "thumbnails", -1, "url"),
            "live": any("LIVE" in (b or "").upper() for b in badges),
            "badges": [b for b in badges if b],
        }

    # ----------------------------------------------------------------- lookup

    def video(self, video_id: str) -> dict:
        """Full metadata for one video: exact date, description, tags, counts."""
        data = self.call("player", {"videoId": video_id})
        details = data.get("videoDetails") or {}
        if not details:
            raise NotFound(f"no video {video_id!r} (private, removed or geo-blocked)")
        micro = safe_get(data, "microformat", "playerMicroformatRenderer", default={})
        return {
            "video_id": details.get("videoId"),
            "title": details.get("title"),
            "url": f"https://www.youtube.com/watch?v={details.get('videoId')}",
            "channel": details.get("author"),
            "channel_id": details.get("channelId"),
            "description": details.get("shortDescription"),
            "duration_s": int(details.get("lengthSeconds") or 0),
            "views": int(details.get("viewCount") or 0),
            "keywords": details.get("keywords", []),
            "category": micro.get("category"),
            "published": micro.get("publishDate"),
            "uploaded": micro.get("uploadDate"),
            "live": bool(details.get("isLiveContent")),
            "family_safe": micro.get("isFamilySafe"),
            "thumbnail": safe_get(details, "thumbnail", "thumbnails", -1, "url"),
            "available_countries": micro.get("availableCountries", []),
        }

    def resolve_channel(self, channel: str) -> str:
        """`@handle`, `/c/custom` or a channel URL -> the `UC...` id.

        Handles are not ids: `browse` only speaks `UC...`, so anything else
        goes through `navigation/resolve_url` first (one extra request, cached
        by nothing — pass the id directly in loops).
        """
        if channel.startswith("UC") and len(channel) == 24:
            return channel
        url = (
            channel
            if channel.startswith("http")
            else (
                f"https://www.youtube.com/{channel if channel.startswith('@') else '@' + channel}"
            )
        )
        data = self.call("navigation/resolve_url", {"url": url})
        cid = safe_get(data, "endpoint", "browseEndpoint", "browseId")
        if not cid:
            raise NotFound(f"could not resolve channel {channel!r}")
        return cid

    def channel(self, channel: str) -> dict:
        """Channel metadata by id (`UC...`), handle (`@name`) or channel URL."""
        data = self.call("browse", {"browseId": self.resolve_channel(channel)})
        header = next(_walk(data, "pageHeaderRenderer"), {}) or next(
            _walk(data, "c4TabbedHeaderRenderer"), {}
        )
        meta = safe_get(data, "metadata", "channelMetadataRenderer", default={})
        return {
            "channel_id": meta.get("externalId"),
            "title": meta.get("title") or _text(header.get("title")),
            "handle": meta.get("vanityChannelUrl", "").rsplit("/", 1)[-1] or None,
            "description": meta.get("description"),
            "keywords": (meta.get("keywords") or "").split(),
            "url": meta.get("channelUrl"),
            "avatar": safe_get(meta, "avatar", "thumbnails", -1, "url"),
            "subscribers_text": _text(
                safe_get(header, "subscriberCountText", default={})
            ),
        }

    def channel_videos(
        self, channel: str, *, limit: int = 30, tab: str = "videos"
    ) -> list[dict]:
        """A channel's uploads, shorts or live streams.

        Args:
            tab: `videos`, `shorts` or `streams`.
        """
        cid = self.resolve_channel(channel)
        # Tab params are stable protobuf constants YouTube has used for years.
        params = {
            "videos": "EgZ2aWRlb3PyBgQKAjoA",
            "shorts": "EgZzaG9ydHPyBgUKA5oBAA%3D%3D",
            "streams": "EgdzdHJlYW1z8gYECgJ6AA%3D%3D",
        }[tab]
        out: list[dict] = []
        continuation = None
        while len(out) < limit:
            body = (
                {"continuation": continuation}
                if continuation
                else {"browseId": cid, "params": urllib.parse.unquote(params)}
            )
            data = self.call("browse", body)
            rows = [self._shape_video(v) for v in _walk(data, "videoRenderer")]
            rows += [self._shape_lockup(v) for v in _walk(data, "lockupViewModel")]
            rows += [self._shape_short(v) for v in _walk(data, "shortsLockupViewModel")]
            if not rows:
                break
            out.extend(rows)
            continuation = self._continuation(data)
            if not continuation:
                break
        return out[:limit]

    def playlist(self, playlist_id: str, *, limit: int = 100) -> list[dict]:
        """Every video in a playlist."""
        pid = playlist_id if playlist_id.startswith("VL") else f"VL{playlist_id}"
        out: list[dict] = []
        continuation = None
        while len(out) < limit:
            body = {"continuation": continuation} if continuation else {"browseId": pid}
            data = self.call("browse", body)
            rows = [
                {
                    "video_id": v.get("videoId"),
                    "title": _text(v.get("title")),
                    "channel": _text(v.get("shortBylineText")),
                    "duration": _text(v.get("lengthText")),
                    "index": _number(_text(v.get("index"))),
                    "url": f"https://www.youtube.com/watch?v={v.get('videoId')}",
                }
                for v in _walk(data, "playlistVideoRenderer")
            ]
            # Playlist pages migrated to the generic lockup like channel pages
            # did; keep both readers for the same reason.
            rows += [self._shape_lockup(v) for v in _walk(data, "lockupViewModel")]
            if not rows:
                break
            out.extend(rows)
            continuation = self._continuation(data)
            if not continuation:
                break
        return out[:limit]

    def hot(self, query: str, *, since: str = "today", limit: int = 20) -> list[dict]:
        """What is getting views right now for a query.

        YouTube retired the Trending page in 2025 — `browseId=FEtrending`,
        `FEexplore` and `FEmusic_trending` all answer HTTP 400 now — so there is
        no global trending feed left to scrape. This is the closest equivalent:
        recent uploads ranked by view count. For a true trend signal use
        `Trends(...).interest_over_time(term, property="youtube")`.
        """
        return self.search(
            query, limit=limit, sort="views", upload_date=since, type="video"
        )

    def _filter_chips(self, query: str = "test") -> dict[str, dict[str, str]]:
        """Re-derive the live filter params from YouTube's own chips.

        Only needed when YouTube renumbers its protobuf fields — compare the
        output against `search_params()` to spot the drift.
        """
        data = self.call("search", {"query": query})
        out: dict[str, dict[str, str]] = {}
        for grp in _walk(data, "searchFilterGroupRenderer"):
            title = _text(grp.get("title")) or "?"
            out[title] = {
                _text(f["searchFilterRenderer"].get("label")) or "?": safe_get(
                    f,
                    "searchFilterRenderer",
                    "navigationEndpoint",
                    "searchEndpoint",
                    "params",
                )
                for f in grp.get("filters", [])
            }
        return out


__all__ = ["YouTube", "search_params"]
