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

            def sweep(self, q, **kw):
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

    def test_version_needs_no_network(self, capsys):
        from gscrape import __version__

        assert cli.main(["version"]) == 0
        assert json.loads(capsys.readouterr().out) == {"gscrape": __version__}

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


class _Recorder:
    """Fake service that records the method called and its arguments."""

    calls: list = []

    def __init__(self, **kw):
        pass

    def __getattr__(self, name):
        def call(*a, **k):
            type(self).calls.append((name, a, k))
            return {"called": name}

        return call


@pytest.fixture
def recorder(monkeypatch):
    def install(module: str, attr: str):
        import importlib

        mod = importlib.import_module(module)
        cls = type("Fake" + attr, (_Recorder,), {"calls": []})
        monkeypatch.setattr(mod, attr, cls)
        return cls

    return install


class TestSurfaceCoverage:
    """Every library entry point must be reachable from the shell."""

    def test_maps_resolve(self, recorder):
        calls = recorder("gscrape.maps", "Maps").calls
        cli.main(["maps", "resolve", "ChIJabc"])
        assert calls == [("resolve", ("ChIJabc",), {})]

    def test_maps_url(self, recorder, capsys):
        recorder("gscrape.maps", "Maps")
        cli.main(["maps", "url", "bäckerei"])
        assert "url" in json.loads(capsys.readouterr().out)

    def test_suggest_modifiers(self, recorder):
        calls = recorder("gscrape.suggest", "Suggest").calls
        cli.main(["suggest", "laufschuhe", "--modifiers", "--lang", "en"])
        assert calls[0][0] == "modifiers" and calls[0][2]["lang"] == "en"

    def test_suggest_expand_terms(self, recorder):
        calls = recorder("gscrape.suggest", "Suggest").calls
        cli.main(["suggest", "laufschuhe", "--expand", "test", "kaufen", "--prefix"])
        name, args, kw = calls[0]
        assert name == "expand" and args[1] == ["test", "kaufen"] and kw["prefix"] is True

    def test_trends_countries_splits_the_list(self, recorder):
        calls = recorder("gscrape.trends", "Trends").calls
        cli.main(["trends", "countries", "peniaze", "--countries", "SK, CZ ,AT"])
        name, args, kw = calls[0]
        assert name == "by_country" and args[1] == ["SK", "CZ", "AT"]
        assert kw["method"] == "interest_over_time"

    def test_trends_autocomplete(self, recorder):
        calls = recorder("gscrape.trends", "Trends").calls
        cli.main(["trends", "autocomplete", "laufschuh"])
        assert calls == [("autocomplete", ("laufschuh",), {})]

    def test_yt_video_pulls_the_transcript_by_default(self, recorder):
        calls = recorder("gscrape.youtube", "YouTube").calls
        cli.main(["yt", "video", "dQw4w9WgXcQ"])
        assert calls[0][0] == "video"
        assert calls[0][2] == {"transcript": True, "lang": None, "translate": None}

    def test_yt_video_no_transcript(self, recorder):
        calls = recorder("gscrape.youtube", "YouTube").calls
        cli.main(["yt", "video", "dQw4w9WgXcQ", "--no-transcript"])
        assert calls[0][2]["transcript"] is False

    def test_yt_transcript_passes_language_and_translation(self, recorder):
        calls = recorder("gscrape.youtube", "YouTube").calls
        cli.main(["yt", "transcript", "abc", "--lang", "de", "--translate", "en"])
        assert calls[0][0] == "transcript"
        assert calls[0][2] == {"lang": "de", "translate": "en"}

    def test_yt_transcript_list_only_lists(self, recorder):
        calls = recorder("gscrape.youtube", "YouTube").calls
        cli.main(["yt", "transcript", "abc", "--list"])
        assert calls[0][0] == "caption_tracks"

    @pytest.mark.parametrize(
        "to,head", [("srt", "1\n00:00:00,000"), ("vtt", "WEBVTT"), ("text", "hallo")]
    )
    def test_yt_transcript_subtitle_files_go_out_verbatim(
        self, recorder, capsys, to, head
    ):
        cls = recorder("gscrape.youtube", "YouTube")
        cls.transcript = lambda self, vid, **kw: {
            "text": "hallo",
            "cues": [{"start": 0.0, "dur": 1.0, "text": "hallo"}],
        }
        cli.main(["yt", "transcript", "abc", "--to", to])
        assert capsys.readouterr().out.startswith(head)

    def test_yt_hot(self, recorder):
        calls = recorder("gscrape.youtube", "YouTube").calls
        cli.main(["yt", "hot", "fitness", "--since", "week", "--limit", "5"])
        assert calls[0][0] == "hot" and calls[0][2] == {"since": "week", "limit": 5}

    def test_patents_count(self, recorder, capsys):
        calls = recorder("gscrape.patents", "Patents").calls
        cli.main(["patents", "shoe sole", "--count", "--country", "DE"])
        assert calls[0][0] == "count" and calls[0][2]["country"] == "DE"
        assert "total" in json.loads(capsys.readouterr().out)

    def test_books_by_id(self, recorder):
        calls = recorder("gscrape.books", "Books").calls
        cli.main(["books", "--id", "zyTCAlFPjgYC"])
        assert calls == [("get", ("zyTCAlFPjgYC",), {})]

    def test_books_without_query_or_id_fails(self, recorder):
        recorder("gscrape.books", "Books")
        with pytest.raises(SystemExit):
            cli.main(["books"])

    def test_scholar_citations(self, recorder):
        calls = recorder("gscrape.scholar", "Scholar").calls
        cli.main(["scholar", "--cites", "1234"])
        assert calls == [("citations", ("1234",), {})]

    def test_images_use_the_cse_when_a_cx_is_set(self, recorder):
        cls = recorder("gscrape.search", "Search")

        def init(self, **kw):
            self.cx = kw.get("cx")

        cls.__init__ = init
        cli.main(["search", "laufschuhe", "--images", "--cx", "abc:def"])
        assert cls.calls[0][0] == "cse_images"

    def test_images_pass_safesearch_without_a_cx(self, recorder):
        cls = recorder("gscrape.search", "Search")

        def init(self, **kw):
            self.cx = None

        cls.__init__ = init
        cli.main(["search", "laufschuhe", "--images", "--safe", "active"])
        assert cls.calls[0][0] == "images" and cls.calls[0][2]["safe"] == "active"
