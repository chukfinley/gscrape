"""Maps extractors, run against a real captured `preview/place` payload."""

from __future__ import annotations

from typing import Any

from gscrape.maps import RICH_MIN_BYTES, extract, looks_gated


def _slot(index: int, value: Any, size: int = 100) -> list[Any]:
    """A sparse Google payload row: `size` empty slots with one filled in."""
    row: list[Any] = [None] * size
    row[index] = value
    return row


def _place(index: int, value: Any) -> list[Any]:
    return _slot(index, value)


class TestShape:
    def test_core_identity(self, maps_place):
        p = extract.shape(maps_place, place_id="ChIJx", fid="0xa:0xb")
        assert p["name"]
        assert p["fid"] == "0xa:0xb"
        assert p["maps_url"].endswith("ChIJx")
        assert isinstance(p["categories"], list)

    def test_location(self, maps_place):
        p = extract.shape(maps_place)
        assert -90 <= p["lat"] <= 90
        assert -180 <= p["lng"] <= 180
        assert p["address"]
        assert p["postal_code"]

    def test_reputation(self, maps_place):
        p = extract.shape(maps_place)
        assert 0 < p["rating"] <= 5
        assert p["reviews"] > 0
        assert set(p["rating_distribution"]) <= {"1", "2", "3", "4", "5"}

    def test_photos_can_be_skipped(self, maps_place):
        assert "photos" not in extract.shape(maps_place, with_photos=False)


class TestPhotos:
    def test_finds_photos(self, maps_place):
        photos = extract.photos(maps_place)
        assert len(photos) > 5
        assert all(p["base_url"].startswith("https://") for p in photos)

    def test_urls_have_no_size_suffix(self, maps_place):
        # The stored base must be size-free so any resolution can be requested.
        assert all("=w" not in p["base_url"] for p in extract.photos(maps_place))

    def test_streetview_excluded_by_default(self, maps_place):
        assert not any(p["streetview"] for p in extract.photos(maps_place))

    def test_deduplicates(self, maps_place):
        photos = extract.photos(maps_place)
        assert len({p["base_url"] for p in photos}) == len(photos)

    def test_photo_url_builder(self):
        base = "https://lh3.googleusercontent.com/gps-cs/abc"
        assert extract.photo_url(base, 1600) == base + "=w1600-k-no"
        assert extract.photo_url(base, 800, 600) == base + "=w800-h600-k-no"

    def test_photo_url_leaves_streetview_alone(self):
        sv = "https://streetviewpixels-pa.googleapis.com/v1/thumbnail?x=1"
        assert extract.photo_url(sv) == sv

    def test_strip_size(self):
        assert extract.strip_size("https://x/y=w203-h152-k-no") == "https://x/y"


class TestHours:
    def test_seven_days(self, maps_place):
        h = extract.hours(maps_place)
        assert len(h["days"]) == 7
        assert all("ranges" in d for d in h["days"])

    def test_closed_flag_matches_ranges(self, maps_place):
        for day in extract.hours(maps_place)["days"]:
            assert day["closed"] == (not day["ranges"])

    def test_missing_block(self):
        assert extract.hours([]) == {}


class TestAttributes:
    def test_groups_are_slugs(self, maps_place):
        attrs = extract.attributes(maps_place)
        assert attrs
        assert all(not k.startswith("/geo") for k in attrs)

    def test_items_carry_present_flag(self, maps_place):
        items = [i for g in extract.attributes(maps_place).values() for i in g["items"]]
        assert items
        assert all(i["present"] in (True, False, None) for i in items)


class TestBusinessStatus:
    def test_open_place(self, maps_place):
        s = extract.business_status(maps_place)
        assert s == {"status": "OPERATIONAL", "closed": False, "badge": None}

    def test_closed_badge(self):
        place = _place(88, ["GESCHLOSSEN"])
        s = extract.business_status(place)
        assert s["closed"] is True
        assert s["status"] == "CLOSED"

    def test_temporarily_closed_from_edit_labels(self):
        place = _place(88, ["GESCHLOSSEN"])
        place[96] = _slot(5, [["Vorübergehend geschlossen melden"]])
        assert extract.business_status(place)["status"] == "CLOSED_TEMPORARILY"

    def test_permanently_closed_from_edit_labels(self):
        place = _place(88, ["CLOSED"])
        place[96] = _slot(5, [["Report permanently closed"]])
        assert extract.business_status(place)["status"] == "CLOSED_PERMANENTLY"

    def test_unknown_kind_stays_generic(self):
        # Absence of evidence is not evidence: an earlier version defaulted to
        # CLOSED_PERMANENTLY here and mislabelled 18 real places.
        place = _place(88, ["CLOSED"])
        assert extract.business_status(place)["status"] == "CLOSED"


class TestReviews:
    def test_embedded_reviews(self, maps_place):
        reviews = extract.reviews(maps_place)
        assert reviews
        assert all(r["stars"] is None or 1 <= r["stars"] <= 5 for r in reviews)
        assert all(r["author"] or r["text"] for r in reviews)

    def test_dates_are_iso(self, maps_place):
        for r in extract.reviews(maps_place):
            if r["date_approx"]:
                assert len(r["date_approx"]) == 10 and r["date_approx"][4] == "-"

    def test_microsecond_and_second_timestamps(self):
        assert extract._review_date(1_700_000_000) == extract._review_date(
            1_700_000_000_000_000
        )

    def test_garbage_timestamp(self):
        assert extract._review_date("nope") is None
        assert extract._review_date(10**20) is None


class TestPopularTimes:
    def test_shape(self, maps_place):
        for day in extract.popular_times(maps_place):
            assert 0 <= day["weekday_no"] <= 7
            assert all(0 <= h["busy_pct"] <= 100 for h in day["hours"])


class TestGating:
    def test_big_payload_is_never_gated(self):
        assert looks_gated("x" * RICH_MIN_BYTES) is False

    def test_small_payload_without_photos_is_gated(self):
        assert looks_gated("short body, no photos") is True

    def test_small_payload_with_photos_is_fine(self):
        # A three-photo village Imbiss legitimately lands under the size bar.
        body = "x" + "googleusercontent.com/gps-cs" * 3
        assert looks_gated(body) is False
