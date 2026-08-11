"""YouTube, through InnerTube — the API the youtube.com frontend itself uses.

`youtubei/v1/*` takes JSON, answers JSON, needs no OAuth, no cookies and no
browser. The "API key" below is not a credential: it is the public web-client
key baked into every youtube.com page, identical for every visitor.

    from gscrape import YouTube
    yt = YouTube(hl="de", gl="DE")
    yt.search("laufschuhe test", limit=40)
    yt.search("fitness", type="shorts", upload_date="week", sort="views")
    yt.video("dQw4w9WgXcQ")             # metadata + transcript
    yt.transcript("dQw4w9WgXcQ", lang="de")
    yt.channel_videos("@MrBeast", limit=60)

## Captions come from the mobile clients, not the web one

The WEB client answers `playabilityStatus: UNPLAYABLE` for a growing share of
videos and then carries no `captions` block at all, and the caption URLs on a
watch page are signed for the browser session (they answer 200 with an empty
body for anyone else). The IOS client still returns a plain, unsigned
`timedtext` URL that any client can fetch, so `transcript()` asks IOS for the
track list and reads `fmt=json3` off it. Client versions matter: IOS 19.x and
ANDROID 19.x are rejected with HTTP 400 since 2025, so both are pinned to a 20.x
build here.

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

from ._core.errors import GoogError, NotFound, ParseError
from ._core.parse import safe_get
from ._core.service import Service

INNERTUBE = "https://www.youtube.com/youtubei/v1"
# The public web-client key from youtube.com's own bootstrap data.
WEB_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
WEB_VERSION = "2.20240101.00.00"

# `clientName` decides what YouTube hands back. WEB is the richest for metadata;
# ANDROID and IOS return caption tracks and stream URLs the web client hides, at
# the cost of thinner metadata (no microformat, so no publish date or category).
CLIENTS = {
    "web": {"clientName": "WEB", "clientVersion": WEB_VERSION},
    "android": {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "androidSdkVersion": 34,
        "osName": "Android",
        "osVersion": "14",
    },
    "ios": {
        "clientName": "IOS",
        "clientVersion": "20.03.02",
        "deviceMake": "Apple",
        "deviceModel": "iPhone16,2",
        "osName": "iPhone",
        "osVersion": "18.2.1.22C161",
    },
    "tv": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0"},
}

#: The client asked for caption tracks. WEB no longer carries them.
CAPTION_CLIENT = "ios"

TIMEDTEXT_FORMAT = "json3"

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


def parse_json3(data: dict) -> list[dict]:
    """YouTube's `fmt=json3` timedtext -> `[{start, dur, text}]` in seconds.

    A json3 event carries its text in word-level `segs`, and window/style events
    carry none at all. Joining the segments per event rebuilds the caption line
    the player shows; events that end up empty are dropped, because
    auto-generated tracks emit a blank spacer event between every line.
    """
    cues: list[dict] = []
    for event in data.get("events") or []:
        text = "".join(s.get("utf8", "") for s in event.get("segs") or [])
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        cues.append(
            {
                "start": round((event.get("tStartMs") or 0) / 1000, 3),
                "dur": round((event.get("dDurationMs") or 0) / 1000, 3),
                "text": text,
            }
        )
    return cues


def _stamp(seconds: float, *, comma: bool = False) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _timed(cues: list[dict]) -> Iterator[tuple[float, float, str]]:
    """Cues as non-overlapping `(start, end, text)` spans.

    Auto-generated tracks roll: every line stays on screen until two lines
    later, so raw json3 spans overlap by design. A subtitle file with
    overlapping cues renders as doubled lines in most players, so each span is
    cut at the start of the next one.
    """
    for i, c in enumerate(cues):
        end = c["start"] + c["dur"]
        if i + 1 < len(cues):
            end = min(end, cues[i + 1]["start"])
        yield c["start"], max(end, c["start"]), c["text"]


def to_srt(cues: list[dict]) -> str:
    """Cues as an `.srt` subtitle file."""
    return "\n".join(
        f"{i}\n{_stamp(start, comma=True)} --> {_stamp(end, comma=True)}\n{text}\n"
        for i, (start, end, text) in enumerate(_timed(cues), 1)
    )


def to_vtt(cues: list[dict]) -> str:
    """Cues as a `.vtt` subtitle file."""
    body = "\n".join(
        f"{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        for start, end, text in _timed(cues)
    )
    return f"WEBVTT\n\n{body}"


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

    def _context(self, client_name: str | None = None) -> dict:
        return {
            "client": {
                **CLIENTS[client_name or self.client_name],
                "hl": self.hl,
                "gl": self.gl.upper(),
            }
        }

    def call(self, endpoint: str, body: dict, *, client_name: str | None = None) -> dict:
        """POST one InnerTube endpoint (`search`, `player`, `browse`, `next`).

        `client_name` overrides this instance's client for a single call, which
        is what lets caption lookups borrow the IOS client without changing the
        client every other method uses.
        """
        url = f"{INNERTUBE}/{endpoint}?key={WEB_KEY}&prettyPrint=false"
        raw = self.client.post(
            url,
            json={"context": self._context(client_name), **body},
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

    def _player(self, video_id: str, *, client_name: str | None = None) -> dict:
        """One `player` call. `contentCheckOk` keeps age-gated videos answering."""
        return self.call(
            "player",
            {"videoId": video_id, "contentCheckOk": True, "racyCheckOk": True},
            client_name=client_name,
        )

    def video(
        self,
        video_id: str,
        *,
        transcript: bool = True,
        lang: str | None = None,
        translate: str | None = None,
    ) -> dict:
        """Full metadata for one video: exact date, description, tags, counts.

        The subtitles come along by default (`transcript=False` turns that off
        and saves two requests): `captions` lists the available track languages,
        `transcript` holds the plain text of the chosen one. A video without any
        caption track simply gets `transcript: None` — that is not an error.

        Args:
            lang: preferred transcript language, e.g. `de`. Defaults to `hl`,
                then English, then whatever the video has.
            translate: force YouTube's machine translation into this language.
        """
        data = self._player(video_id)
        details = data.get("videoDetails") or {}
        if not details:
            raise NotFound(f"no video {video_id!r} (private, removed or geo-blocked)")
        micro = safe_get(data, "microformat", "playerMicroformatRenderer", default={})
        row = {
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
        if not transcript:
            return row
        # A missing or unfetchable transcript must not cost the caller the
        # metadata it already paid for, so this failure stays soft.
        row.update(
            {
                "captions": [],
                "transcript_language": None,
                "transcript_kind": None,
                "transcript": None,
            }
        )
        try:
            tracks = self.caption_tracks(video_id)
            row["captions"] = [t["language"] for t in tracks]
            if tracks:
                sub = self.transcript(
                    video_id, lang=lang, translate=translate, tracks=tracks
                )
                row["transcript_language"] = sub["translated_to"] or sub["language"]
                row["transcript_kind"] = sub["kind"]
                row["transcript"] = sub["text"]
        except GoogError:
            pass
        return row

    # --------------------------------------------------------------- captions

    def caption_tracks(self, video_id: str) -> list[dict]:
        """Every subtitle track a video has, uploaded ones and auto-generated.

        One `player` request against the IOS client. `kind` is `manual` for
        author-uploaded subtitles and `auto` for YouTube's speech recognition;
        `translatable` says whether `translate=` can machine-translate it.
        """
        data = self._player(video_id, client_name=CAPTION_CLIENT)
        tracks = (
            safe_get(
                data,
                "captions",
                "playerCaptionsTracklistRenderer",
                "captionTracks",
                default=[],
            )
            or []
        )
        if not tracks and not (data.get("videoDetails") or {}):
            status = safe_get(data, "playabilityStatus", "reason") or safe_get(
                data, "playabilityStatus", "status"
            )
            raise NotFound(f"no video {video_id!r} ({status})")
        return [
            {
                "language": t.get("languageCode"),
                "name": _text(t.get("name")),
                "kind": "auto" if t.get("kind") == "asr" else "manual",
                "translatable": bool(t.get("isTranslatable")),
                "base_url": t.get("baseUrl"),
            }
            for t in tracks
            if t.get("baseUrl")
        ]

    def _pick_track(
        self, tracks: list[dict], lang: str | None
    ) -> tuple[dict, str | None]:
        """Choose a track and decide whether it needs translating.

        Preference order: the asked-for language, then `hl`, then English, then
        the first track — manual subtitles beating auto-generated ones at every
        step. When the wanted language exists nowhere but the fallback track is
        translatable, the caller gets that track plus a `tlang` to translate it
        into, which is exactly what the YouTube player does.
        """
        if not tracks:
            raise NotFound("no caption track on this video")

        def by_lang(code: str) -> list[dict]:
            code = code.lower().split("-")[0]
            hits = [
                t for t in tracks if (t["language"] or "").lower().split("-")[0] == code
            ]
            return sorted(hits, key=lambda t: t["kind"] != "manual")

        if lang:
            hits = by_lang(lang)
            if hits:
                return hits[0], None
        for code in (self.hl, "en"):
            hits = by_lang(code)
            if hits:
                return hits[0], lang if lang else None
        fallback = sorted(tracks, key=lambda t: t["kind"] != "manual")[0]
        return fallback, lang if lang else None

    def transcript(
        self,
        video_id: str,
        *,
        lang: str | None = None,
        translate: str | None = None,
        tracks: list[dict] | None = None,
    ) -> dict:
        """The subtitles of one video as timed cues plus one block of text.

        Args:
            lang: wanted language (`de`). Falls back to `hl`, then English, then
                the first track; if the language is missing entirely but the
                track can be translated, it is translated into `lang`.
            translate: force machine translation into this language, whatever
                the source track is.
            tracks: a `caption_tracks()` result to reuse instead of re-fetching.

        Returns:
            `{video_id, language, language_name, kind, translated_to, cues,
            text, duration_s}`, where each cue is `{start, dur, text}` in
            seconds.
        """
        tracks = tracks if tracks is not None else self.caption_tracks(video_id)
        track, auto_translate = self._pick_track(tracks, lang)
        tlang = translate or auto_translate
        if tlang and not track["translatable"]:
            tlang = None
        url = f"{track['base_url']}&fmt={TIMEDTEXT_FORMAT}"
        if tlang:
            url += f"&tlang={urllib.parse.quote(tlang)}"
        raw = self.client.get(url)
        if not raw.strip():
            raise ParseError(
                f"timedtext returned an empty body for {video_id!r} — the track "
                "URL is session-signed, retry without a proxy or with a fresh call"
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ParseError(f"timedtext returned non-JSON for {video_id!r}") from e
        cues = parse_json3(data)
        return {
            "video_id": video_id,
            "language": track["language"],
            "language_name": track["name"],
            "kind": track["kind"],
            "translated_to": tlang,
            "duration_s": round(cues[-1]["start"] + cues[-1]["dur"], 3) if cues else 0.0,
            "text": "\n".join(c["text"] for c in cues),
            "cues": cues,
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


__all__ = ["YouTube", "parse_json3", "search_params", "to_srt", "to_vtt"]
