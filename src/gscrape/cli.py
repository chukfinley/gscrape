"""`gscrape` — one command per Google surface.

    gscrape maps search "restaurant flensburg" --limit 5
    gscrape maps details ChIJd3UFGwNvs0cRCqhQbp-i6Jk --out place.json
    gscrape suggest "laufschuhe" --sweep --format csv --out keywords.csv
    gscrape news "laufschuhe" --when 7d --resolve
    gscrape trends interest laufschuhe sneaker --geo DE --csv
    gscrape trends now --geo DE
    gscrape yt search "fitness" --type shorts --sort views --limit 40
    gscrape patents "running shoe sole" --after 20220101

Global flags come before the subcommand's own: `--hl`, `--gl`, `--proxy`,
`--rate-limit`, `--format`, `--out`, `-v`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ._core.errors import GoogError
from ._core.export import emit


def _client_kwargs(a: argparse.Namespace) -> dict[str, Any]:
    return {
        "hl": a.hl,
        "gl": a.gl,
        "proxy": a.proxy,
        "rate_limit": a.rate_limit,
        "verbose": a.verbose,
    }


def _add_global(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    """Global flags. `suppress` builds the copy attached to subcommands.

    Without SUPPRESS the subcommand's defaults would overwrite a flag given
    before it (`gscrape --hl en maps ...`), so the same option has to be
    default-less on the sub-level and default-carrying on the top level.
    """
    d: Any = argparse.SUPPRESS if suppress else None
    p.add_argument("--hl", default=d or "de", help="interface language (default: de)")
    p.add_argument("--gl", default=d or "de", help="country (default: de)")
    p.add_argument("--proxy", default=d, help="http://user:pass@host:port")
    p.add_argument(
        "--rate-limit",
        type=float,
        dest="rate_limit",
        default=d,
        help="max requests per second for this run",
    )
    p.add_argument("--format", default=d or "json", choices=("json", "jsonl", "csv"))
    p.add_argument("--out", default=d, help="write here instead of stdout")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gscrape",
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Web Search needs a browser-minted cookie jar once per IP: "
        "`gscrape cookies 'NID=...; SOCS=...'` (copy the Cookie header "
        "from devtools). Everything else works out of the box.",
    )
    _add_global(ap)
    # Attached to every subcommand too, so flags work on either side of it.
    common = argparse.ArgumentParser(add_help=False)
    _add_global(common, suppress=True)
    sub = ap.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------ maps
    maps = sub.add_parser("maps", help="places, photos, hours, reviews", parents=[common])
    maps_sub = maps.add_subparsers(dest="action", required=True)

    m_search = maps_sub.add_parser(
        "search", help="free-text place lookup", parents=[common]
    )
    m_search.add_argument("query")
    m_search.add_argument("--limit", type=int, default=5)

    m_details = maps_sub.add_parser(
        "details", help="everything about one place", parents=[common]
    )
    m_details.add_argument("ref", help="placeId (ChIJ...) or feature id (0x..:0x..)")
    m_details.add_argument("--no-photos", action="store_true")

    m_batch = maps_sub.add_parser(
        "batch", help="details for a file of ids", parents=[common]
    )
    m_batch.add_argument("file", help="one placeId / feature id per line")
    m_batch.add_argument("--workers", type=int, default=6)

    m_rev = maps_sub.add_parser(
        "reviews", help="paginate reviews (needs a bgkey)", parents=[common]
    )
    m_rev.add_argument("fid")
    m_rev.add_argument("--limit", type=int, default=100)
    m_rev.add_argument("--bgkey", help="x-maps-bgkey, or set $GSCRAPE_MAPS_BGKEY")

    maps_sub.add_parser("bootstrap", help="refresh the consent cookies", parents=[common])

    # --------------------------------------------------------------- suggest
    sug = sub.add_parser(
        "suggest", help="autocomplete / keyword research", parents=[common]
    )
    sug.add_argument("query")
    sug.add_argument("--ds", help="vertical: yt, sh, bks, n, i, v, pl")
    sug.add_argument(
        "--sweep", action="store_true", help="alphabet + questions + modifiers"
    )
    sug.add_argument("--alphabet", action="store_true")
    sug.add_argument("--questions", action="store_true")
    sug.add_argument("--detailed", action="store_true", help="with relevance scores")

    # ------------------------------------------------------------------ news
    news = sub.add_parser("news", help="Google News (RSS, un-gated)", parents=[common])
    news.add_argument("query", nargs="?")
    news.add_argument("--when", help="1h, 7d, 1y ...")
    news.add_argument("--site", help="restrict to one publisher domain")
    news.add_argument("--topic", help="world, business, technology, ...")
    news.add_argument("--geo", help="local news for a place name")
    news.add_argument("--limit", type=int, default=25)
    news.add_argument(
        "--resolve",
        action="store_true",
        help="resolve google redirect links to publisher URLs",
    )

    # ---------------------------------------------------------------- trends
    tr = sub.add_parser(
        "trends", help="interest over time, regions, trending", parents=[common]
    )
    tr_sub = tr.add_subparsers(dest="action", required=True)

    t_int = tr_sub.add_parser("interest", help="interest over time", parents=[common])
    t_int.add_argument("keywords", nargs="+")
    t_int.add_argument("--geo", default="")
    t_int.add_argument("--timeframe", default="today 12-m")
    t_int.add_argument(
        "--property", default="", help="web, images, news, youtube, shopping"
    )
    t_int.add_argument("--csv", action="store_true", help="Google's own CSV export")

    t_geo = tr_sub.add_parser("region", help="interest by region", parents=[common])
    t_geo.add_argument("keywords", nargs="+")
    t_geo.add_argument("--geo", default="")
    t_geo.add_argument("--timeframe", default="today 12-m")
    t_geo.add_argument(
        "--resolution", default="REGION", choices=("COUNTRY", "REGION", "CITY", "DMA")
    )

    t_rel = tr_sub.add_parser(
        "related", help="related queries / topics", parents=[common]
    )
    t_rel.add_argument("keywords", nargs="+")
    t_rel.add_argument("--geo", default="")
    t_rel.add_argument("--timeframe", default="today 12-m")
    t_rel.add_argument("--topics", action="store_true", help="entities, not queries")

    t_now = tr_sub.add_parser("now", help="trending right now", parents=[common])
    t_now.add_argument("--geo", default="DE")
    t_now.add_argument("--hours", type=int, default=48)
    t_now.add_argument("--limit", type=int, default=25)
    t_now.add_argument("--rss", action="store_true", help="use the un-gated RSS feed")

    # --------------------------------------------------------------- youtube
    yt = sub.add_parser("yt", help="YouTube search, videos, channels", parents=[common])
    yt_sub = yt.add_subparsers(dest="action", required=True)

    y_search = yt_sub.add_parser("search", parents=[common])
    y_search.add_argument("query")
    y_search.add_argument(
        "--type", choices=("video", "shorts", "channel", "playlist", "movie")
    )
    y_search.add_argument(
        "--sort", default="relevance", choices=("relevance", "rating", "date", "views")
    )
    y_search.add_argument(
        "--upload-date",
        dest="upload_date",
        choices=("hour", "today", "week", "month", "year"),
    )
    y_search.add_argument("--duration", choices=("under3", "3to20", "over20"))
    y_search.add_argument(
        "--feature",
        action="append",
        dest="features",
        help="hd, 4k, subtitles, live, 360, hdr, ... (repeatable)",
    )
    y_search.add_argument("--limit", type=int, default=20)

    y_video = yt_sub.add_parser("video", parents=[common])
    y_video.add_argument("video_id")

    y_chan = yt_sub.add_parser("channel", parents=[common])
    y_chan.add_argument("channel", help="@handle, UC... id or channel URL")
    y_chan.add_argument("--videos", action="store_true", help="list uploads instead")
    y_chan.add_argument(
        "--tab", default="videos", choices=("videos", "shorts", "streams")
    )
    y_chan.add_argument("--limit", type=int, default=30)

    y_pl = yt_sub.add_parser("playlist", parents=[common])
    y_pl.add_argument("playlist_id")
    y_pl.add_argument("--limit", type=int, default=100)

    # --------------------------------------------------------- other surfaces
    pat = sub.add_parser(
        "patents", help="Google Patents (JSON, un-gated)", parents=[common]
    )
    pat.add_argument("query")
    pat.add_argument("--limit", type=int, default=20)
    pat.add_argument("--after", help="YYYYMMDD")
    pat.add_argument("--before", help="YYYYMMDD")
    pat.add_argument("--assignee")
    pat.add_argument("--inventor")
    pat.add_argument("--country", help="US,DE,EP")

    books = sub.add_parser("books", help="Google Books", parents=[common])
    books.add_argument("query")
    books.add_argument("--limit", type=int, default=20)
    books.add_argument("--author")
    books.add_argument("--isbn")

    sch = sub.add_parser(
        "scholar", help="Google Scholar (rate limits hard)", parents=[common]
    )
    sch.add_argument("query")
    sch.add_argument("--limit", type=int, default=10)
    sch.add_argument("--year-from", dest="year_from", type=int)
    sch.add_argument("--recent", action="store_true", help="sort by date")

    web = sub.add_parser(
        "search", help="Web search (needs a browser cookie jar)", parents=[common]
    )
    web.add_argument("query")
    web.add_argument("--limit", type=int, default=10)
    web.add_argument("--site", help="restrict to a domain")
    web.add_argument("--when", choices=("d", "w", "m", "y"), help="recency filter")
    web.add_argument("--ai", action="store_true", help="AI overview instead of results")
    web.add_argument(
        "--raw", action="store_true", help="full SERP instead of udm=14 web-only mode"
    )
    web.add_argument("--cx", help="use Programmable Search instead ($GSCRAPE_CSE_CX)")
    web.add_argument("--images", action="store_true", help="image results")

    cookies = sub.add_parser(
        "cookies",
        parents=[common],
        help="import a browser cookie jar (what Web Search needs)",
    )
    cookies.add_argument(
        "cookie_header", help="the Cookie header from devtools, e.g. 'NID=...; SOCS=...'"
    )

    return ap


def run(a: argparse.Namespace) -> Any:
    kw = _client_kwargs(a)

    if a.command == "maps":
        from .maps import Maps

        m = Maps(**kw, bgkey=getattr(a, "bgkey", None))
        if a.action == "search":
            return m.search(a.query, limit=a.limit)
        if a.action == "details":
            ref = a.ref
            return m.details(
                None if ref.startswith("0x") else ref,
                fid=ref if ref.startswith("0x") else None,
                with_photos=not a.no_photos,
            )
        if a.action == "batch":
            refs = [
                line.strip()
                for line in Path(a.file).read_text().splitlines()
                if line.strip()
            ]
            return m.details_many(refs, workers=a.workers)
        if a.action == "reviews":
            return m.reviews(a.fid, limit=a.limit)
        if a.action == "bootstrap":
            m.bootstrap(force=True)
            return {"cookies": str(m.client.cookie_file), "ok": True}

    if a.command == "suggest":
        from .suggest import Suggest

        s = Suggest(**kw)
        if a.sweep:
            return s.sweep(a.query)
        if a.alphabet:
            return s.alphabet(a.query, ds=a.ds)
        if a.questions:
            return s.questions(a.query, ds=a.ds)
        return s.suggest(a.query, ds=a.ds, detailed=a.detailed)

    if a.command == "news":
        from .news import News

        n = News(**kw)
        if a.topic:
            return n.topic(a.topic, limit=a.limit, resolve=a.resolve)
        if a.geo:
            return n.geo(a.geo, limit=a.limit, resolve=a.resolve)
        if not a.query:
            return n.top(limit=a.limit, resolve=a.resolve)
        return n.search(
            a.query, when=a.when, site=a.site, limit=a.limit, resolve=a.resolve
        )

    if a.command == "trends":
        from .trends import Trends

        t = Trends(**kw)
        if a.action == "interest":
            if a.csv:
                return t.csv(
                    a.keywords,
                    kind="timeseries",
                    geo=a.geo,
                    timeframe=a.timeframe,
                    property=a.property,
                )
            return t.interest_over_time(
                a.keywords, geo=a.geo, timeframe=a.timeframe, property=a.property
            )
        if a.action == "region":
            return t.interest_by_region(
                a.keywords, geo=a.geo, timeframe=a.timeframe, resolution=a.resolution
            )
        if a.action == "related":
            fn = t.related_topics if a.topics else t.related_queries
            return fn(a.keywords, geo=a.geo, timeframe=a.timeframe)
        if a.action == "now":
            if a.rss:
                return t.trending_rss(geo=a.geo, limit=a.limit)
            return t.trending_now(geo=a.geo, hours=a.hours, limit=a.limit)

    if a.command == "yt":
        from .youtube import YouTube

        y = YouTube(**kw)
        if a.action == "search":
            return y.search(
                a.query,
                type=a.type,
                sort=a.sort,
                upload_date=a.upload_date,
                duration=a.duration,
                features=a.features,
                limit=a.limit,
            )
        if a.action == "video":
            return y.video(a.video_id)
        if a.action == "channel":
            if a.videos:
                return y.channel_videos(a.channel, tab=a.tab, limit=a.limit)
            return y.channel(a.channel)
        if a.action == "playlist":
            return y.playlist(a.playlist_id, limit=a.limit)

    if a.command == "patents":
        from .patents import Patents

        return Patents(**kw).search(
            a.query,
            limit=a.limit,
            after=a.after,
            before=a.before,
            assignee=a.assignee,
            inventor=a.inventor,
            country=a.country,
        )

    if a.command == "books":
        from .books import Books

        return Books(**kw).search(a.query, limit=a.limit, author=a.author, isbn=a.isbn)

    if a.command == "scholar":
        from .scholar import Scholar

        return Scholar(**kw).search(
            a.query, limit=a.limit, year_from=a.year_from, sort_by_date=a.recent
        )

    if a.command == "cookies":
        from ._core.client import Client

        c = Client(**kw)
        c.import_cookies(a.cookie_header)
        return {
            "saved": str(c.cookie_file),
            "cookies": sorted(dict(c.session.cookies.items())),
        }

    if a.command == "search":
        from .search import Search

        s = Search(**kw, cx=a.cx)
        if a.ai:
            return s.ai_overview(a.query)
        if a.images:
            return s.images(a.query, limit=a.limit)
        if s.cx:
            return s.cse(a.query, limit=a.limit, site=a.site)
        return s.web(a.query, limit=a.limit, site=a.site, when=a.when, clean=not a.raw)

    raise SystemExit(f"unhandled command {a.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except GoogError as e:
        # Typed failures are the point of this package: print them plainly
        # rather than dumping a traceback at a shell user.
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    if isinstance(result, str):  # raw CSV export
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(result)
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(result)
    else:
        emit(result, args.out, args.format)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
