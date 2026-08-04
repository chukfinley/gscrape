"""Google Patents.

`patents.google.com/xhr/query` is the cleanest endpoint Google operates: plain
JSON, no consent cookies, no JavaScript gate, no token dance, and it takes the
same query syntax as the web UI. It survives while the Search verticals get
walled off because it is an internal XHR of a JS app that never got the
anti-scraping treatment.

    from gscrape import Patents
    p = Patents()
    for pat in p.search("running shoe sole", after="20200101", limit=50):
        print(pat["publication_number"], pat["title"])

Query syntax the endpoint understands, all optional:
`q=`, `inventor=`, `assignee=`, `before=`/`after=` (`priority:20200101`,
`filing:...`, `publication:...`), `country=US,DE`, `language=ENGLISH`,
`status=GRANT`, `type=PATENT`, `litigation=YES`.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from ._core.errors import ParseError
from ._core.parse import safe_get
from ._core.service import Service

ENDPOINT = "https://patents.google.com/xhr/query"
PAGE_SIZE = 10  # the endpoint's own page size; `num` above 100 is ignored


class Patents(Service):
    """Full-text patent search with the filters the web UI exposes."""

    def _query_string(self, params: dict[str, Any]) -> str:
        """Patents nests a whole query string inside the `url=` parameter."""
        parts = [f"{k}={v}" for k, v in params.items() if v not in (None, "")]
        return urllib.parse.quote("&".join(parts), safe="")

    def search(
        self,
        query: str = "",
        *,
        limit: int = 10,
        inventor: str | None = None,
        assignee: str | None = None,
        after: str | None = None,
        before: str | None = None,
        date_field: str = "priority",
        country: str | None = None,
        language: str | None = None,
        status: str | None = None,
        type: str | None = None,
        sort: str | None = None,
    ) -> list[dict]:
        """Search patents, paginating until `limit` is reached.

        Args:
            after/before: `YYYYMMDD`. `date_field` picks which date they apply
                to: `priority`, `filing` or `publication`.
            country: comma-separated country codes (`US,DE,EP`).
            status: `GRANT` or `APPLICATION`.
            sort: `new`, `old`, or None for relevance.
        """
        params: dict[str, Any] = {
            "q": query,
            "inventor": inventor,
            "assignee": assignee,
            "country": country,
            "language": language,
            "status": status,
            "type": type,
            "sort": sort,
        }
        if after:
            params["after"] = f"{date_field}:{after}"
        if before:
            params["before"] = f"{date_field}:{before}"

        out: list[dict] = []
        page = 0
        while len(out) < limit:
            params["num"] = PAGE_SIZE
            params["page"] = page
            url = f"{ENDPOINT}?url={self._query_string(params)}&exp="
            body = self.client.get(url, headers={"accept": "application/json"})
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                raise ParseError(f"patents returned {len(body)} bytes of non-JSON") from e

            rows = []
            for cluster in safe_get(data, "results", "cluster", default=[]) or []:
                rows.extend(cluster.get("result", []))
            if not rows:
                break
            out.extend(self._shape(r) for r in rows)
            page += 1
            # `total_num_pages` caps at 100; stop early when the last page was
            # short, which is how the endpoint signals "no more results".
            if len(rows) < PAGE_SIZE:
                break
        return out[:limit]

    @staticmethod
    def _shape(row: dict) -> dict:
        p = row.get("patent", {})
        pub = p.get("publication_number", "")
        return {
            "publication_number": pub,
            "title": (p.get("title") or "").strip(),
            "snippet": (p.get("snippet") or "").strip(),
            "inventor": p.get("inventor"),
            "assignee": p.get("assignee"),
            "priority_date": p.get("priority_date"),
            "filing_date": p.get("filing_date"),
            "grant_date": p.get("grant_date") or None,
            "publication_date": p.get("publication_date"),
            "language": p.get("language"),
            "pdf": f"https://patentimages.storage.googleapis.com/{p['pdf']}"
            if p.get("pdf")
            else None,
            "thumbnail": p.get("thumbnail") or None,
            "url": f"https://patents.google.com/patent/{pub}/en" if pub else None,
            "rank": row.get("rank"),
        }

    def count(self, query: str, **kw: Any) -> int:
        """How many patents match, without pulling any of them."""
        params = {"q": query, "num": 10, "page": 0}
        url = f"{ENDPOINT}?url={self._query_string(params)}&exp="
        data = json.loads(self.client.get(url, headers={"accept": "application/json"}))
        return safe_get(data, "results", "total_num_results", default=0)


__all__ = ["Patents"]
