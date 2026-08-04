# Cookbook

Working recipes. Each one runs as-is.

## Lead list: every business of a type in a city, with contact data

```python
from gscrape import Maps, to_csv

m = Maps(hl="de", gl="de")
places = m.search("bäckerei lübeck", limit=100)

leads = [p for p in places if not p["closed"] and p["phone"]]
to_csv(
    leads,
    "leads.csv",
    columns=[
        "name",
        "phone",
        "website",
        "address",
        "postal_code",
        "city",
        "rating",
        "reviews",
        "primary_category",
        "maps_url",
    ],
)
```

Add the fields search leaves thin (menu card, all photos, popular times) only
for the rows worth it — `details()` costs one request each:

```python
best = sorted(leads, key=lambda p: p["reviews"] or 0, reverse=True)[:20]
full = m.details_many([p["fid"] for p in best], workers=6)
```

## Scale it across a proxy pool

Google's limiter is per-IP, so N proxies really do multiply throughput.

```python
from concurrent.futures import ThreadPoolExecutor
from gscrape import ClientPool, Maps

pool = ClientPool(rate_limit=4)  # reads ~/.config/gscrape/proxies.txt
cities = ["lübeck", "kiel", "flensburg", "hamburg", "rostock"]


def scrape(city):
    return Maps(client=pool.next()).search(f"bäckerei {city}", limit=100)


with ThreadPoolExecutor(max_workers=len(pool)) as ex:
    rows = [p for batch in ex.map(scrape, cities) for p in batch]
```

## Keyword research without a paid tool

```python
from gscrape import Suggest, Trends

s = Suggest(hl="de", gl="de")
keywords = s.sweep("laufschuhe")  # ~250 queries people actually type

# Rank them by real demand, 5 at a time (Trends compares within one call only).
t = Trends(hl="de", gl="de", rate_limit=0.3)
scored = []
for i in range(0, 25, 5):
    batch = keywords[i : i + 5]
    rows = t.interest_over_time(batch, geo="DE", timeframe="today 12-m")
    for kw in batch:
        values = [r[kw] for r in rows if r[kw] is not None and not r["partial"]]
        scored.append((kw, sum(values) / len(values) if values else 0))

for kw, score in sorted(scored, key=lambda x: -x[1])[:20]:
    print(f"{score:5.1f}  {kw}")
```

Note the constraint the API imposes: interest values are relative *within one
call*, so scores from different batches are not directly comparable. Keep a
fixed anchor keyword in every batch when you need cross-batch ranking.

## What is rising right now, per country

```python
from gscrape import Trends

t = Trends(hl="de", gl="de")
for row in t.trending_rss(geo="DE", limit=10):
    print(f"{row['traffic']:>8}  {row['term']}")
    for n in row["news"][:2]:
        print(f"          {n['source']}: {n['title'][:70]}")

# Same question for several markets, sequentially (Trends 429s on parallel calls)
by_market = t.by_country("laufschuhe", ["DE", "AT", "CH"], timeframe="now 7-d")
```

## Content research: what ranks, and what the competition publishes

```python
from gscrape import Search, News

s = Search(hl="de", gl="de")
s.client.import_cookies("NID=...; SOCS=...")  # once per IP

serp = s.web("beste laufschuhe 2026", limit=100)  # 10 pages, ~2 s
domains = {}
for r in serp:
    host = r["url"].split("/")[2]
    domains[host] = domains.get(host, 0) + 1
print(sorted(domains.items(), key=lambda x: -x[1])[:10])

# Everything one competitor published on the topic in the last month
print(News(hl="de", gl="DE").search("laufschuhe", site="runnersworld.de", when="1m"))
```

## YouTube: what performs in a niche

```python
from gscrape import YouTube

yt = YouTube(hl="de", gl="DE")

# Shorts that took off this week, most-viewed first
shorts = yt.shorts("fitness motivation", limit=50, sort="views")

# The outlier check: a channel's median views vs this video's
videos = yt.channel_videos("@some-channel", limit=50)
views = sorted(v["views"] or 0 for v in videos)
median = views[len(views) // 2]
outliers = [v for v in videos if (v["views"] or 0) > median * 3]
```

## Monitoring a place over time

```python
import json, time
from pathlib import Path
from gscrape import Maps

m = Maps(hl="de", gl="de")
fid = m.search("restaurant mandarin tarp", limit=1)[0]["fid"]

snapshot = m.details(fid=fid)
Path(f"watch/{fid}-{time.strftime('%Y-%m-%d')}.json").write_text(
    json.dumps(
        {
            k: snapshot[k]
            for k in ("name", "rating", "reviews", "closed", "hours", "top_reviews")
        },
        ensure_ascii=False,
    )
)
```

Run it on a schedule and diff the files: rating drift, review bursts, opening
hour changes and closures all show up without any API quota.

## Handling the blocks in a long run

```python
from gscrape import Captcha, RateLimited, ClientPool, Maps

pool = ClientPool(rate_limit=4)
clients = list(pool)


def robust_search(query, attempts=3):
    for _ in range(attempts):
        client = pool.next()
        try:
            return Maps(client=client).search(query, limit=100)
        except RateLimited:
            continue  # another exit IP will do
        except Captcha as e:
            print(f"solve and re-import for {client!r}: {e.sorry_url}")
            clients.remove(client) if client in clients else None
            continue
    return []
```
