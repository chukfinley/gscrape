"""CLI argument wiring. No network: `run()` is monkeypatched per command."""

from __future__ import annotations

import json

import pytest

from gscrape import cli


def parse(argv):
    return cli.build_parser().parse_args(argv)


class TestGlobalFlags:
    def test_before_subcommand(self):
        a = parse(["--hl", "en", "--gl", "us", "suggest", "shoes"])
        assert (a.hl, a.gl) == ("en", "us")

    def test_after_subcommand(self):
        a = parse(["suggest", "shoes", "--hl", "en"])
        assert a.hl == "en"

    def test_defaults(self):
        a = parse(["suggest", "shoes"])
        assert (a.hl, a.gl, a.format, a.proxy) == ("de", "de", "json", None)

    def test_before_wins_when_not_repeated(self):
        # The subcommand copy uses SUPPRESS, so it must not reset the value.
        a = parse(["--hl", "en", "maps", "search", "x"])
        assert a.hl == "en"

    def test_deep_subcommand_flags(self):
        a = parse(["trends", "interest", "laufschuhe", "--geo", "DE", "--format", "csv"])
        assert a.geo == "DE" and a.format == "csv"


class TestCommands:
    def test_maps_details_ref(self):
        a = parse(["maps", "details", "ChIJabc"])
        assert a.command == "maps" and a.action == "details" and a.ref == "ChIJabc"

    def test_yt_search_filters(self):
        a = parse(
            [
                "yt",
                "search",
                "x",
                "--type",
                "shorts",
                "--sort",
                "views",
                "--upload-date",
                "week",
                "--feature",
                "hd",
                "--feature",
                "4k",
            ]
        )
        assert a.type == "shorts" and a.features == ["hd", "4k"]

    def test_invalid_choice_exits(self):
        with pytest.raises(SystemExit):
            parse(["yt", "search", "x", "--type", "reels"])

    def test_missing_subcommand_exits(self):
        with pytest.raises(SystemExit):
            parse(["maps"])

    def test_trends_keywords_are_variadic(self):
        a = parse(["trends", "interest", "a", "b", "c"])
        assert a.keywords == ["a", "b", "c"]


class TestRun:
    def test_dispatches_to_service(self, monkeypatch, capsys):
        import gscrape.suggest as suggest_mod

        class FakeSuggest:
            def __init__(self, **kw):
                self.kw = kw

            def sweep(self, q):
                return [f"{q} a", f"{q} b"]

        monkeypatch.setattr(suggest_mod, "Suggest", FakeSuggest)
        assert cli.main(["suggest", "laufschuhe", "--sweep"]) == 0
        assert json.loads(capsys.readouterr().out) == ["laufschuhe a", "laufschuhe b"]

    def test_client_kwargs_are_passed_through(self, monkeypatch):
        import gscrape.suggest as suggest_mod

        seen = {}

        class FakeSuggest:
            def __init__(self, **kw):
                seen.update(kw)

            def suggest(self, *a, **k):
                return []

        monkeypatch.setattr(suggest_mod, "Suggest", FakeSuggest)
        cli.main(["--proxy", "http://p:1", "--rate-limit", "2", "suggest", "x"])
        assert seen["proxy"] == "http://p:1" and seen["rate_limit"] == 2.0

    def test_typed_errors_print_without_traceback(self, monkeypatch, capsys):
        import gscrape.suggest as suggest_mod
        from gscrape._core.errors import Captcha

        class FakeSuggest:
            def __init__(self, **kw):
                pass

            def suggest(self, *a, **k):
                raise Captcha("https://www.google.com/sorry/index", "https://target")

        monkeypatch.setattr(suggest_mod, "Suggest", FakeSuggest)
        assert cli.main(["suggest", "x"]) == 1
        assert "Captcha" in capsys.readouterr().err

    def test_csv_output_to_file(self, monkeypatch, tmp_path):
        import gscrape.news as news_mod

        class FakeNews:
            def __init__(self, **kw):
                pass

            def search(self, *a, **k):
                return [{"title": "T", "url": "u"}]

        monkeypatch.setattr(news_mod, "News", FakeNews)
        out = tmp_path / "o.csv"
        cli.main(["news", "x", "--format", "csv", "--out", str(out)])
        assert out.read_text().startswith("title,url")

    def test_raw_csv_export_is_written_verbatim(self, monkeypatch, tmp_path):
        import gscrape.trends as trends_mod

        class FakeTrends:
            def __init__(self, **kw):
                pass

            def csv(self, *a, **k):
                return "Zeit,laufschuhe\n2026-01-01,50\n"

        monkeypatch.setattr(trends_mod, "Trends", FakeTrends)
        out = tmp_path / "t.csv"
        cli.main(["trends", "interest", "laufschuhe", "--csv", "--out", str(out)])
        assert out.read_text().startswith("Zeit,laufschuhe")
