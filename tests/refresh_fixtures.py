#!/usr/bin/env python3
"""Re-capture the offline test fixtures from live Google responses.

Run this when a live test starts failing on shape (not on rate limits), so the
offline suite tests against what Google actually sends today:

    uv run python tests/refresh_fixtures.py

Then re-run `uv run pytest` and read the diff of the failures: each one names a
field Google moved.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gscrape import Maps, News, Trends, YouTube

FIXTURES = Path(__file__).parent / "fixtures"
PROBE_FID = "0x47b36f031b057577:0x99e8a29f6e50a80a"
PROBE_LAT, PROBE_LNG = 54.6486428, 9.4130149


def write_json_gz(name: str, payload) -> None:
    with gzip.open(FIXTURES / name, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"  {name}  {(FIXTURES / name).stat().st_size:,} bytes")


def main() -> int:
    FIXTURES.mkdir(exist_ok=True)

    print("maps...")
    write_json_gz(
        "maps_place.json.gz",
        Maps(hl="de", gl="de").place(PROBE_FID, PROBE_LAT, PROBE_LNG),
    )

    print("news...")
    xml = News(hl="de", gl="DE").client.get(
        "https://news.google.com/rss/search?q=laufschuhe&hl=de&gl=DE&ceid=DE:de"
    )
    # Keep it small but valid XML: whole <item> blocks only.
    head, _, rest = xml.partition("<item>")
    items = ("<item>" + rest).split("</item>")[:12]
    (FIXTURES / "news_rss.xml").write_text(
        head + "</item>".join(items) + "</item></channel></rss>", encoding="utf-8"
    )
    print(f"  news_rss.xml  {(FIXTURES / 'news_rss.xml').stat().st_size:,} bytes")

    print("youtube...")
    write_json_gz(
        "youtube_search.json.gz",
        YouTube(hl="de", gl="DE").call("search", {"query": "laufschuhe test"}),
    )

    print("trends (slow: rate limited)...")
    t = Trends(hl="de", gl="de", rate_limit=0.2)
    for attempt in range(6):
        try:
            widgets = t.explore("laufschuhe", geo="DE", timeframe="today 3-m")
            (FIXTURES / "trends_timeseries.json").write_text(
                json.dumps(
                    t._widget_data(t._pick(widgets, "TIMESERIES")), ensure_ascii=False
                )
            )
            time.sleep(10)
            (FIXTURES / "trends_related.json").write_text(
                json.dumps(
                    t._widget_data(t._pick(widgets, "RELATED_QUERIES")),
                    ensure_ascii=False,
                )
            )
            print("  trends_timeseries.json, trends_related.json")
            break
        except Exception as e:  # 429s are expected here, not fatal
            print(f"  retry {attempt + 1}: {type(e).__name__}")
            time.sleep(20)
    else:
        print("  trends fixtures NOT refreshed (rate limited)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
