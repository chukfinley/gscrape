"""The HTTP layer: wall detection, cookie persistence, throttling, retries."""

from __future__ import annotations

import json
import time

import pytest

from gscrape._core.client import Client, _cookie_path
from gscrape._core.errors import Captcha, HttpError, JsRequired, RateLimited


class TestWallDetection:
    """Both of Google's soft walls answer with a valid HTTP response, so
    without these checks a captcha parses as "no results"."""

    def test_captcha_by_final_url(self, fake_response):
        r = fake_response(text="ok", url="https://www.google.com/sorry/index?continue=x")
        with pytest.raises(Captcha) as e:
            Client._check_wall(r, "https://www.google.com/search")
        assert e.value.sorry_url.startswith("https://www.google.com/sorry")

    def test_captcha_by_body(self, fake_response):
        r = fake_response(
            text='<a href="/sorry/index?q=1">', url="https://www.google.com/x"
        )
        with pytest.raises(Captcha):
            Client._check_wall(r, "x")

    def test_js_shell(self, fake_response):
        r = fake_response(
            text='<noscript><meta content="0;url=/httpservice/retry/enablejs?sei=x">'
        )
        with pytest.raises(JsRequired):
            Client._check_wall(r, "https://www.google.com/search")

    def test_clean_response_passes(self, fake_response):
        Client._check_wall(fake_response(text='{"ok":1}', url="https://x/y"), "x")

    def test_marker_deep_in_body_is_ignored(self, fake_response):
        # A page merely *mentioning* the path (e.g. a scraped article) is not a
        # wall; only the shell's own head carries it.
        r = fake_response(text="x" * 5000 + "/httpservice/retry/enablejs")
        Client._check_wall(r, "x")


class TestCookieJar:
    def test_roundtrip(self, tmp_path):
        c = Client(cookie_file=tmp_path / "j.json")
        c.session.cookies.set("NID", "abc", domain=".google.com")
        c.meta["maps_bgkey"] = "KEY"
        c.save_cookies()

        c2 = Client(cookie_file=tmp_path / "j.json")
        assert c2.load_cookies() is True
        assert c2.meta["maps_bgkey"] == "KEY"
        assert "NID" in dict(c2.session.cookies.items())

    def test_missing_file(self, tmp_path):
        assert Client(cookie_file=tmp_path / "nope.json").load_cookies() is False

    def test_corrupt_file_is_not_fatal(self, tmp_path):
        p = tmp_path / "j.json"
        p.write_text("{not json")
        assert Client(cookie_file=p).load_cookies() is False

    def test_legacy_flat_format(self, tmp_path):
        # The old gmaps_http jar was a flat {name: value} dict.
        p = tmp_path / "j.json"
        p.write_text(json.dumps({"NID": "x", "SOCS": "y"}))
        c = Client(cookie_file=p)
        assert c.load_cookies() is True
        assert dict(c.session.cookies.items())["SOCS"] == "y"

    def test_import_cookie_header(self, tmp_path):
        c = Client(cookie_file=tmp_path / "j.json")
        c.import_cookies("NID=abc; SOCS=xyz", save=False)
        jar = dict(c.session.cookies.items())
        assert jar["NID"] == "abc" and jar["SOCS"] == "xyz"

    def test_per_proxy_files(self, tmp_path):
        base = tmp_path / "j.json"
        a = _cookie_path("http://a:1", base)
        b = _cookie_path("http://b:2", base)
        # Jars are IP-bound: sharing one file across exits would poison both.
        assert a != b != base

    def test_permissions_are_tight(self, tmp_path):
        c = Client(cookie_file=tmp_path / "j.json")
        c.save_cookies()
        assert oct(c.cookie_file.stat().st_mode)[-3:] == "600"


class TestThrottle:
    def test_rate_limit_spaces_requests(self):
        c = Client(rate_limit=20)  # 50 ms apart
        t0 = time.monotonic()
        for _ in range(3):
            c._throttle()
        assert time.monotonic() - t0 >= 0.09

    def test_no_limit_is_free(self):
        c = Client()
        t0 = time.monotonic()
        for _ in range(50):
            c._throttle()
        assert time.monotonic() - t0 < 0.05


class TestRetries:
    def _client(self, statuses, fake_response):
        c = Client(max_retries=2)
        seq = list(statuses)

        class FakeSession:
            def request(self, method, url, **kw):
                return fake_response(text="body", status_code=seq.pop(0), url=url)

        c.session = FakeSession()  # ty: ignore[invalid-assignment] - test double
        c._cookies_loaded = True
        return c

    def test_retries_429_then_succeeds(self, fake_response, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        c = self._client([429, 200], fake_response)
        assert c.get("https://x/y") == "body"

    def test_raises_rate_limited_when_exhausted(self, fake_response, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        c = self._client([429, 429, 429], fake_response)
        with pytest.raises(RateLimited):
            c.get("https://x/y")

    def test_other_errors_do_not_retry(self, fake_response):
        c = self._client([404, 200], fake_response)
        with pytest.raises(HttpError) as e:
            c.get("https://x/y")
        assert e.value.status == 404
