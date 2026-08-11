"""YouTube: protobuf filter encoding, count parsing, result and caption shaping."""

from __future__ import annotations

import json

import pytest

from gscrape._core.errors import NotFound, ParseError, RateLimited
from gscrape.youtube import (
    YouTube,
    _number,
    _text,
    _walk,
    parse_json3,
    search_params,
    to_srt,
    to_vtt,
)


class TestSearchParams:
    """The expected values are YouTube's own filter-chip params, read off a
    live search response. If YouTube renumbers its protobuf fields these break,
    which is the point — `YouTube._filter_chips()` re-derives them."""

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"type": "video"}, "EgIQAQ=="),
            ({"type": "shorts"}, "EgIQCQ=="),
            ({"type": "channel"}, "EgIQAg=="),
            ({"type": "playlist"}, "EgIQAw=="),
            ({"type": "movie"}, "EgIQBA=="),
            ({"duration": "under3"}, "EgIYBA=="),
            ({"duration": "3to20"}, "EgIYBQ=="),
            ({"duration": "over20"}, "EgIYAg=="),
            ({"upload_date": "today"}, "EgIIAg=="),
            ({"upload_date": "week"}, "EgIIAw=="),
            ({"upload_date": "month"}, "EgIIBA=="),
            ({"upload_date": "year"}, "EgIIBQ=="),
            ({"features": ["live"]}, "EgJAAQ=="),
            ({"features": ["4k"]}, "EgJwAQ=="),
            ({"features": ["hd"]}, "EgIgAQ=="),
            ({"features": ["subtitles"]}, "EgIoAQ=="),
            ({"features": ["creative_commons"]}, "EgIwAQ=="),
            ({"features": ["360"]}, "EgJ4AQ=="),
            ({"features": ["3d"]}, "EgI4AQ=="),
            ({"features": ["purchased"]}, "EgJIAQ=="),
            ({"features": ["vr180"]}, "EgPQAQE="),
            ({"features": ["hdr"]}, "EgPIAQE="),
            ({"features": ["location"]}, "EgO4AQE="),
            ({"sort": "views"}, "CAM="),
        ],
    )
    def test_matches_youtubes_own_chips(self, kwargs, expected):
        assert search_params(**kwargs) == expected

    def test_relevance_is_the_empty_message(self):
        assert search_params() == ""
        assert search_params(sort="relevance") == ""

    def test_filters_combine(self):
        # The whole reason for encoding instead of hardcoding chip strings.
        assert search_params(type="shorts", upload_date="week", sort="views") == (
            "CAMSBAgDEAk="
        )

    def test_unknown_filter_raises(self):
        with pytest.raises(KeyError):
            search_params(type="reels")


class TestNumber:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("3.934 Aufrufe", 3934),
            ("1.234.567 Aufrufe", 1234567),
            ("1,234,567 views", 1234567),
            ("1.2M views", 1200000),
            ("5.2K views", 5200),
            ("41 Mio. Aufrufe", 41_000_000),
            ("2,5 Mio. Aufrufe", 2_500_000),
            ("1 Mrd. Aufrufe", 1_000_000_000),
            ("999 views", 999),
            ("keine Aufrufe", None),
            ("", None),
            (None, None),
        ],
    )
    def test_locale_variants(self, text, expected):
        assert _number(text) == expected


class TestText:
    def test_simple_text(self):
        assert _text({"simpleText": "hi"}) == "hi"

    def test_runs_are_joined(self):
        assert _text({"runs": [{"text": "a"}, {"text": "b"}]}) == "ab"

    def test_non_dict(self):
        assert _text("x") is None
        assert _text(None) is None


class TestWalk:
    def test_finds_nested_renderers(self):
        data = {"a": [{"videoRenderer": {"videoId": "x"}}]}
        assert [r["videoId"] for r in _walk(data, "videoRenderer")] == ["x"]

    def test_ignores_non_dict_values(self):
        assert list(_walk({"videoRenderer": "string"}, "videoRenderer")) == []


class TestShaping:
    def test_search_results(self, youtube_search):
        rows = YouTube()._shape_results(youtube_search)
        assert len(rows) >= 10
        videos = [r for r in rows if r["kind"] == "video"]
        assert videos
        v = videos[0]
        assert v["video_id"] and len(v["video_id"]) == 11
        assert v["url"].endswith(v["video_id"])
        assert v["title"]
        assert v["views"] is None or v["views"] >= 0

    def test_continuation_token(self, youtube_search):
        assert YouTube._continuation(youtube_search)

    def test_lockup_video(self):
        lockup = {
            "contentId": "abc12345678",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "Titel"},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {
                                    "metadataParts": [
                                        {"text": {"content": "41 Mio. Aufrufe"}},
                                        {"text": {"content": "vor 10 Tagen"}},
                                    ]
                                }
                            ]
                        }
                    },
                }
            },
        }
        row = YouTube._shape_lockup(lockup)
        assert row["kind"] == "video"
        assert row["video_id"] == "abc12345678"
        assert row["views"] == 41_000_000
        assert row["published_text"] == "vor 10 Tagen"

    def test_lockup_playlist(self):
        row = YouTube._shape_lockup(
            {
                "contentId": "PL123",
                "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST",
                "metadata": {"lockupMetadataViewModel": {"title": {"content": "Liste"}}},
            }
        )
        assert row["kind"] == "playlist"
        assert row["playlist_id"] == "PL123"
        assert "playlist?list=PL123" in row["url"]


class TestChannelResolution:
    def test_plain_id_is_passed_through(self):
        cid = "UCX6OQ3DkcsbYNE6H8uQQuVA"
        # No request should happen for a well-formed channel id.
        assert YouTube().resolve_channel(cid) == cid


#: One json3 payload as YouTube serves it: a window event without text, two
#: caption lines whose spans overlap (auto-generated tracks roll), and a blank
#: spacer event between them.
JSON3 = {
    "events": [
        {"tStartMs": 0, "dDurationMs": 12000, "id": 1, "wpWinPosId": 1},
        {
            "tStartMs": 0,
            "dDurationMs": 2400,
            "segs": [
                {"utf8": "Yo,"},
                {"utf8": " what's", "tOffsetMs": 320},
                {"utf8": " up", "tOffsetMs": 480},
            ],
        },
        {"tStartMs": 1360, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 1360, "dDurationMs": 2720, "segs": [{"utf8": "second\nline"}]},
    ]
}


class TestJson3:
    def test_segments_join_into_lines(self):
        cues = parse_json3(JSON3)
        assert [c["text"] for c in cues] == ["Yo, what's up", "second line"]

    def test_times_are_seconds(self):
        assert parse_json3(JSON3)[1] == {
            "start": 1.36,
            "dur": 2.72,
            "text": "second line",
        }

    def test_empty_and_style_events_are_dropped(self):
        assert parse_json3({"events": [{"tStartMs": 0, "wsWinStyleId": 1}]}) == []
        assert parse_json3({}) == []


class TestSubtitleFiles:
    def test_srt_cuts_overlapping_cues(self):
        # Cue 1 runs to 2.4s but cue 2 starts at 1.36s; players would show both.
        assert to_srt(parse_json3(JSON3)) == (
            "1\n00:00:00,000 --> 00:00:01,360\nYo, what's up\n\n"
            "2\n00:00:01,360 --> 00:00:04,080\nsecond line\n"
        )

    def test_vtt_has_the_header_and_dot_milliseconds(self):
        vtt = to_vtt(parse_json3(JSON3))
        assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.360\n")

    def test_empty_input(self):
        assert to_srt([]) == ""
        assert to_vtt([]) == "WEBVTT\n\n"


def _track(lang: str, kind: str = "auto", translatable: bool = True) -> dict:
    return {
        "language": lang,
        "name": lang,
        "kind": kind,
        "translatable": translatable,
        "base_url": f"https://timedtext/{lang}",
    }


class TestTrackChoice:
    def test_manual_beats_auto_for_the_same_language(self):
        tracks = [_track("de"), _track("de", "manual")]
        track, tlang = YouTube(hl="de")._pick_track(tracks, None)
        assert track["kind"] == "manual" and tlang is None

    def test_asked_for_language_wins(self):
        tracks = [_track("en"), _track("fr")]
        track, tlang = YouTube(hl="de")._pick_track(tracks, "fr")
        assert track["language"] == "fr" and tlang is None

    def test_regional_variants_match_the_base_language(self):
        track, _ = YouTube(hl="de")._pick_track([_track("en-GB")], "en")
        assert track["language"] == "en-GB"

    def test_missing_language_falls_back_and_translates(self):
        # No German track, so the English one gets YouTube's `tlang=de`.
        track, tlang = YouTube(hl="en")._pick_track([_track("en")], "de")
        assert track["language"] == "en" and tlang == "de"

    def test_interface_language_is_the_default_preference(self):
        track, tlang = YouTube(hl="fr")._pick_track([_track("en"), _track("fr")], None)
        assert track["language"] == "fr" and tlang is None

    def test_english_before_an_unrelated_track(self):
        track, _ = YouTube(hl="de")._pick_track([_track("ja"), _track("en")], None)
        assert track["language"] == "en"

    def test_no_track_at_all(self):
        with pytest.raises(NotFound):
            YouTube()._pick_track([], None)


class TestCaptionTracks:
    def test_shaping(self, monkeypatch):
        payload = {
            "videoDetails": {"videoId": "x"},
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [
                        {
                            "baseUrl": "https://timedtext/en",
                            "name": {"runs": [{"text": "English (auto-generated)"}]},
                            "languageCode": "en",
                            "kind": "asr",
                            "isTranslatable": True,
                        },
                        {  # no baseUrl: unusable, must be dropped
                            "languageCode": "de",
                            "name": {"simpleText": "Deutsch"},
                        },
                    ]
                }
            },
        }
        y = YouTube()
        monkeypatch.setattr(y, "call", lambda *a, **k: payload)
        assert y.caption_tracks("x") == [
            {
                "language": "en",
                "name": "English (auto-generated)",
                "kind": "auto",
                "translatable": True,
                "base_url": "https://timedtext/en",
            }
        ]

    def test_captions_are_asked_of_the_ios_client(self, monkeypatch):
        seen = {}

        def fake_call(endpoint, body, *, client_name=None):
            seen.update(endpoint=endpoint, body=body, client_name=client_name)
            return {"videoDetails": {"videoId": "x"}}

        y = YouTube()
        monkeypatch.setattr(y, "call", fake_call)
        assert y.caption_tracks("x") == []
        assert seen["endpoint"] == "player" and seen["client_name"] == "ios"
        assert seen["body"]["contentCheckOk"] is True

    def test_unplayable_video_raises(self, monkeypatch):
        y = YouTube()
        monkeypatch.setattr(
            y,
            "call",
            lambda *a, **k: {"playabilityStatus": {"status": "ERROR", "reason": "gone"}},
        )
        with pytest.raises(NotFound, match="gone"):
            y.caption_tracks("x")


class TestTranscript:
    def _yt(self, monkeypatch, url_sink: list) -> YouTube:
        y = YouTube(hl="de")
        monkeypatch.setattr(y, "caption_tracks", lambda vid: [_track("en")])

        def fake_get(url, **kw):
            url_sink.append(url)
            return json.dumps(JSON3)

        monkeypatch.setattr(y.client, "get", fake_get)
        return y

    def test_shape(self, monkeypatch):
        urls: list[str] = []
        sub = self._yt(monkeypatch, urls).transcript("x")
        assert sub["video_id"] == "x"
        assert sub["language"] == "en"
        assert sub["kind"] == "auto"
        assert sub["duration_s"] == 4.08
        assert sub["text"] == "Yo, what's up\nsecond line"
        assert len(sub["cues"]) == 2
        assert urls == ["https://timedtext/en&fmt=json3"]

    def test_translation_is_a_tlang_parameter(self, monkeypatch):
        urls: list[str] = []
        sub = self._yt(monkeypatch, urls).transcript("x", translate="de")
        assert sub["translated_to"] == "de"
        assert urls == ["https://timedtext/en&fmt=json3&tlang=de"]

    def test_untranslatable_tracks_are_served_as_is(self, monkeypatch):
        urls: list[str] = []
        y = self._yt(monkeypatch, urls)
        monkeypatch.setattr(
            y, "caption_tracks", lambda vid: [_track("en", translatable=False)]
        )
        assert y.transcript("x", translate="de")["translated_to"] is None
        assert urls == ["https://timedtext/en&fmt=json3"]

    def test_empty_body_is_a_parse_error(self, monkeypatch):
        y = self._yt(monkeypatch, [])
        monkeypatch.setattr(y.client, "get", lambda url, **kw: "")
        with pytest.raises(ParseError):
            y.transcript("x")


class TestVideoTranscript:
    PLAYER = {
        "videoDetails": {
            "videoId": "x",
            "title": "T",
            "lengthSeconds": "12",
            "viewCount": "5",
        },
        "microformat": {"playerMicroformatRenderer": {"publishDate": "2026-01-01"}},
    }

    def test_transcript_rides_along(self, monkeypatch):
        y = YouTube()
        monkeypatch.setattr(y, "call", lambda *a, **k: self.PLAYER)
        monkeypatch.setattr(y, "caption_tracks", lambda vid: [_track("en")])
        monkeypatch.setattr(
            y,
            "transcript",
            lambda vid, **kw: {
                "language": "en",
                "translated_to": None,
                "kind": "auto",
                "text": "hello",
            },
        )
        row = y.video("x")
        assert row["title"] == "T"
        assert row["captions"] == ["en"]
        assert row["transcript"] == "hello"
        assert row["transcript_language"] == "en"
        assert row["transcript_kind"] == "auto"

    def test_a_video_without_captions_is_not_an_error(self, monkeypatch):
        y = YouTube()
        monkeypatch.setattr(y, "call", lambda *a, **k: self.PLAYER)
        monkeypatch.setattr(y, "caption_tracks", lambda vid: [])
        row = y.video("x")
        assert row["captions"] == [] and row["transcript"] is None

    def test_a_failing_transcript_keeps_the_metadata(self, monkeypatch):
        y = YouTube()
        monkeypatch.setattr(y, "call", lambda *a, **k: self.PLAYER)

        def boom(vid):
            raise RateLimited(429, "https://timedtext/en")

        monkeypatch.setattr(y, "caption_tracks", boom)
        row = y.video("x")
        assert row["title"] == "T" and row["transcript"] is None

    def test_opting_out_skips_the_caption_requests(self, monkeypatch):
        y = YouTube()
        monkeypatch.setattr(y, "call", lambda *a, **k: self.PLAYER)
        monkeypatch.setattr(
            y, "caption_tracks", lambda vid: pytest.fail("should not be called")
        )
        row = y.video("x", transcript=False)
        assert "transcript" not in row
