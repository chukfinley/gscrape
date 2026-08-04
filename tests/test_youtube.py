"""YouTube: protobuf filter encoding, count parsing, result shaping."""

from __future__ import annotations

import pytest

from gscrape.youtube import YouTube, _number, _text, _walk, search_params


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
