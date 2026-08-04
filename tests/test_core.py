"""Export helpers, proxy handling, service construction, error types."""

from __future__ import annotations

import csv
import io
import json

import pytest

from gscrape._core.errors import Blocked, Captcha, GoogError, JsRequired, RateLimited
from gscrape._core.export import flatten, to_csv, to_json, to_jsonl
from gscrape._core.proxy import ClientPool, load_proxies, normalise
from gscrape._core.service import Service


class TestFlatten:
    def test_nested_dicts(self):
        assert flatten({"a": {"b": 1}}) == {"a.b": 1}

    def test_scalar_lists_are_joined(self):
        assert flatten({"cats": ["a", "b"]}) == {"cats": "a|b"}

    def test_dict_lists_are_indexed(self):
        assert flatten({"p": [{"u": "x"}, {"u": "y"}]}) == {"p.0.u": "x", "p.1.u": "y"}

    def test_none_in_list(self):
        assert flatten({"a": [None, 1]}) == {"a": "|1"}

    def test_empty_containers(self):
        assert flatten({"a": [], "b": {}}) == {"a": ""}


class TestExport:
    ROWS = [{"a": 1, "b": {"c": "x"}}, {"a": 2, "d": "only-in-second"}]

    def test_json_roundtrip(self, tmp_path):
        p = tmp_path / "o.json"
        to_json(self.ROWS, p)
        assert json.loads(p.read_text()) == self.ROWS

    def test_jsonl_one_line_per_row(self, tmp_path):
        p = tmp_path / "o.jsonl"
        to_jsonl(self.ROWS, p)
        assert len(p.read_text().strip().splitlines()) == 2

    def test_csv_unions_columns(self):
        rows = list(csv.DictReader(io.StringIO(to_csv(self.ROWS))))
        assert set(rows[0]) == {"a", "b.c", "d"}
        assert rows[1]["d"] == "only-in-second"

    def test_csv_column_subset(self):
        text = to_csv(self.ROWS, columns=["a"])
        assert text.splitlines()[0] == "a"

    def test_csv_of_empty_input(self):
        assert to_csv([]) == "\r\n"


class TestProxies:
    def test_vendor_format(self):
        assert normalise("1.2.3.4:8080:user:pass") == "http://user:pass@1.2.3.4:8080"

    def test_host_port(self):
        assert normalise("1.2.3.4:8080") == "http://1.2.3.4:8080"

    def test_url_untouched(self):
        assert normalise("socks5://x:1") == "socks5://x:1"

    def test_comments_and_blanks(self):
        assert normalise("# comment") == ""
        assert normalise("  ") == ""

    def test_direct_first(self):
        out = load_proxies(["1.2.3.4:80"])
        assert out[0] is None
        assert (out[1] or "").startswith("http://")

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("GSCRAPE_NO_PROXIES", "1")
        assert load_proxies(["1.2.3.4:80"]) == [None]

    def test_env_list(self, monkeypatch):
        monkeypatch.setenv("GSCRAPE_PROXIES", "http://a:1,http://b:2")
        assert load_proxies() == [None, "http://a:1", "http://b:2"]


class TestClientPool:
    def test_round_robins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GSCRAPE_CACHE_DIR", str(tmp_path))
        pool = ClientPool(["1.1.1.1:80", "2.2.2.2:80"], cookie_file=tmp_path / "c.json")
        assert len(pool) == 3  # direct + 2
        seen = [pool.next().proxy for _ in range(6)]
        assert seen[:3] == seen[3:]  # cycles

    def test_each_member_has_its_own_jar(self, tmp_path):
        pool = ClientPool(["1.1.1.1:80", "2.2.2.2:80"], cookie_file=tmp_path / "c.json")
        files = {c.cookie_file for c in pool}
        assert len(files) == 3  # jars are IP-bound, so they must not collide

    def test_empty_pool_falls_back_to_direct(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GSCRAPE_NO_PROXIES", "1")
        pool = ClientPool([], include_direct=False, cookie_file=tmp_path / "c.json")
        assert len(pool) == 1 and pool.next().proxy is None


class TestService:
    def test_owns_a_client_by_default(self):
        s = Service(hl="en", gl="us")
        assert s.hl == "en" and s.gl == "us"

    def test_shares_a_passed_client(self):
        from gscrape._core.client import Client

        c = Client(hl="fr")
        assert Service(client=c).client is c

    def test_sharing_beats_kwargs(self):
        from gscrape._core.client import Client

        c = Client(hl="fr")
        # A shared client keeps its own settings; passing both is a caller bug
        # that should not silently produce two identities.
        assert Service(hl="en", client=c).hl == "fr"


class TestErrors:
    def test_hierarchy(self):
        assert issubclass(RateLimited, GoogError)
        assert issubclass(Captcha, Blocked)
        assert issubclass(JsRequired, Blocked)

    def test_captcha_carries_the_solve_url(self):
        e = Captcha("https://www.google.com/sorry/index?x=1", "https://target")
        assert e.sorry_url.endswith("x=1")
        assert "solve it" in str(e)


class TestPublicApi:
    def test_lazy_imports(self):
        import gscrape

        assert gscrape.Maps.__name__ == "Maps"
        assert gscrape.YouTube.__name__ == "YouTube"

    def test_unknown_attribute(self):
        import gscrape

        with pytest.raises(AttributeError):
            gscrape.Nope

    def test_all_names_resolve(self):
        import gscrape

        for name in gscrape.__all__:
            assert getattr(gscrape, name) is not None
