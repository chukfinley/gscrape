"""Google Books.

The Books API is genuinely public: `googleapis.com/books/v1` answers without an
API key, without cookies and without a browser. The catch is the anonymous
quota, which is per-IP and small — a burst of a few dozen calls returns
`429 Quota exceeded`. A proxy pool resets it; an API key raises it (free, but
that is a key, so this module does not require one).

    from gscrape import Books
    b = Books()
    for v in b.search("laufschuhe", limit=20):
        print(v["published"], v["title"], v["authors"])
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from ._core.errors import ParseError
from ._core.parse import safe_get
from ._core.service import Service

ENDPOINT = "https://www.googleapis.com/books/v1/volumes"
MAX_PAGE = 40  # the API's hard per-request cap


class Books(Service):
    """Volume search across Google's book corpus."""

    def __init__(self, *args: Any, api_key: str | None = None, **kw: Any):
        """`api_key` is optional and only raises the quota; everything works
        without one."""
        super().__init__(*args, **kw)
        self.api_key = api_key

    def search(
        self,
        query: str = "",
        *,
        limit: int = 10,
        author: str | None = None,
        title: str | None = None,
        publisher: str | None = None,
        subject: str | None = None,
        isbn: str | None = None,
        print_type: str = "all",
        order_by: str = "relevance",
        lang: str | None = None,
    ) -> list[dict]:
        """Search volumes, paginating up to `limit`.

        The keyword arguments map onto Books' field operators, so
        `author="knopf"` becomes `inauthor:knopf` — combining them narrows.
        """
        terms = [query] if query else []
        for op, val in (
            ("inauthor", author),
            ("intitle", title),
            ("inpublisher", publisher),
            ("subject", subject),
            ("isbn", isbn),
        ):
            if val:
                terms.append(f"{op}:{val}")
        if not terms:
            raise ValueError("need a query or at least one field filter")

        out: list[dict] = []
        start = 0
        while len(out) < limit:
            params = {
                "q": " ".join(terms),
                "startIndex": start,
                "maxResults": min(MAX_PAGE, limit - len(out)),
                "printType": print_type,
                "orderBy": order_by,
                "country": self.gl.upper(),
            }
            if lang:
                params["langRestrict"] = lang
            if self.api_key:
                params["key"] = self.api_key
            body = self.client.get(
                f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                headers={"accept": "application/json"},
            )
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                raise ParseError("books returned non-JSON") from e
            items = data.get("items") or []
            if not items:
                break
            out.extend(self._shape(i) for i in items)
            start += len(items)
            if start >= int(data.get("totalItems") or 0):
                break
        return out[:limit]

    def get(self, volume_id: str) -> dict:
        """One volume by its Books id."""
        url = f"{ENDPOINT}/{urllib.parse.quote(volume_id)}?country={self.gl.upper()}"
        return self._shape(json.loads(self.client.get(url)))

    @staticmethod
    def _shape(item: dict) -> dict:
        v = item.get("volumeInfo", {})
        sale = item.get("saleInfo", {})
        ids = {
            i.get("type"): i.get("identifier") for i in v.get("industryIdentifiers", [])
        }
        return {
            "id": item.get("id"),
            "title": v.get("title"),
            "subtitle": v.get("subtitle"),
            "authors": v.get("authors", []),
            "publisher": v.get("publisher"),
            "published": v.get("publishedDate"),
            "description": v.get("description"),
            "pages": v.get("pageCount"),
            "categories": v.get("categories", []),
            "rating": v.get("averageRating"),
            "ratings_count": v.get("ratingsCount"),
            "language": v.get("language"),
            "isbn_13": ids.get("ISBN_13"),
            "isbn_10": ids.get("ISBN_10"),
            "preview_url": v.get("previewLink"),
            "info_url": v.get("infoLink"),
            "thumbnail": safe_get(v, "imageLinks", "thumbnail"),
            "price": safe_get(sale, "listPrice", "amount"),
            "currency": safe_get(sale, "listPrice", "currencyCode"),
            "for_sale": sale.get("saleability") == "FOR_SALE",
        }


__all__ = ["Books"]
