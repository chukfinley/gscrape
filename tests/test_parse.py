"""Payload unwrapping: XSSI prefixes, batchexecute, safe indexing."""

from __future__ import annotations

import json

import pytest

from gscrape._core.errors import ParseError
from gscrape._core.parse import (
    build_batchexecute_body,
    extract_af_data,
    parse_batchexecute,
    parse_json,
    safe_get,
    strip_xssi,
    walk,
)


class TestStripXssi:
    def test_maps_prefix(self):
        assert strip_xssi(")]}'\n[1,2]") == "[1,2]"

    def test_trends_prefix_with_comma(self):
        # Trends sends `)]}',\n`, Maps sends `)]}'\n` — both must survive.
        assert strip_xssi(')]}\',\n{"a":1}') == '{"a":1}'

    def test_leading_whitespace(self):
        assert strip_xssi("  )]}'\n[]") == "[]"

    def test_untouched_when_absent(self):
        assert strip_xssi('{"a":1}') == '{"a":1}'

    def test_only_strips_once(self):
        assert strip_xssi(")]}'\n)]}'\n[]") == ")]}'\n[]"


class TestParseJson:
    def test_parses_prefixed(self):
        assert parse_json(")]}'\n[1, 2]") == [1, 2]

    def test_raises_parse_error_not_json_error(self):
        # A consent page or captcha arrives as HTML; callers must be able to
        # catch it as ParseError and re-bootstrap.
        with pytest.raises(ParseError) as e:
            parse_json("<!DOCTYPE html><html>nope</html>", what="tbm=map")
        assert "tbm=map" in str(e.value)

    def test_error_message_carries_a_snippet(self):
        with pytest.raises(ParseError) as e:
            parse_json("garbage" * 100)
        assert "garbage" in str(e.value)


class TestBatchexecute:
    ENVELOPE = (
        ")]}'\n\n42\n"
        '[["wrb.fr","Fbv4je","[\\"x\\",\\"https://example.com\\"]",null,null,null,"generic"]]\n'
        "12\n"
        '[["di",21],["af.httprm",21,"123",5]]\n'
    )

    def test_returns_named_rpc_payload(self):
        assert parse_batchexecute(self.ENVELOPE, "Fbv4je") == ["x", "https://example.com"]

    def test_returns_none_for_other_rpc(self):
        assert parse_batchexecute(self.ENVELOPE, "qv9Egd") is None

    def test_gated_empty_response_is_none(self):
        # A BotGuard-gated bus answers 200 with this exact shape; it must not
        # look like an error, and must not look like data either.
        assert (
            parse_batchexecute(")]}'\n\n[[null,null,null,null,null,true]]", "qv9Egd")
            is None
        )

    def test_body_roundtrip(self):
        body = build_batchexecute_body("Fbv4je", ["a", 1])
        assert json.loads(body) == [[["Fbv4je", '["a",1]', None, "generic"]]]


class TestSafeGet:
    PLACE = [None, {"a": [1, 2]}, [[["deep"]]]]

    def test_reads_nested(self):
        assert safe_get(self.PLACE, 2, 0, 0, 0) == "deep"

    def test_missing_index(self):
        assert safe_get(self.PLACE, 99) is None

    def test_missing_key(self):
        assert safe_get(self.PLACE, 1, "nope") is None

    def test_wrong_type_midway(self):
        assert safe_get(self.PLACE, 0, 1, 2) is None

    def test_default_is_returned_for_none(self):
        # Google frequently sends explicit nulls; callers want the default.
        assert safe_get([None], 0, default=[]) == []

    def test_negative_index(self):
        assert safe_get([[1, 2, 3]], 0, -1) == 3


class TestWalk:
    def test_visits_every_node(self):
        found = [n for n in walk({"a": [1, {"b": 2}]}) if isinstance(n, dict)]
        assert {"b": 2} in found

    def test_handles_scalars(self):
        assert list(walk(5)) == [5]


class TestExtractAfData:
    def test_pulls_data_array(self):
        html = 'x AF_initDataCallback({key: "ds:1", data:[1,[2,"three"]], sideChannel: {}}); y'
        assert extract_af_data(html) == [[1, [2, "three"]]]

    def test_ignores_broken_blob(self):
        assert extract_af_data("AF_initDataCallback({data:[1,") == []

    def test_string_aware_bracket_matching(self):
        # A "]" inside a string must not end the array early.
        html = 'AF_initDataCallback({data:["a]b", 2]});'
        assert extract_af_data(html) == [["a]b", 2]]
