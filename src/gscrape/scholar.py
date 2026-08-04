"""Google Scholar.

Scholar never got the JavaScript gate — it still serves plain, parseable HTML.
What it does have is the most aggressive rate limiter of any Google surface:
a few dozen fast requests earn a captcha that lasts hours on that IP. Treat
`rate_limit=0.2` (one request every 5 s) as the sane default for anything
beyond a handful of queries, and expect `Captcha` otherwise.

    from gscrape import Scholar
    s = Scholar(rate_limit=0.2)
    for p in s.search("running shoe biomechanics", year_from=2020, limit=20):
        print(p["citations"], p["title"], p["pdf"])
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from selectolax.parser import HTMLParser

from ._core.service import Service

ENDPOINT = "https://scholar.google.com/scholar"
PAGE_SIZE = 10

_CITED_RE = re.compile(r"(\d+)")
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")


class Scholar(Service):
    """Academic search: papers, citation counts, direct PDF links."""

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
        sort_by_date: bool = False,
        include_citations: bool = True,
        author: str | None = None,
    ) -> list[dict]:
        """Paginate Scholar results.

        Args:
            include_citations: False drops citation-only stubs (entries with no
                document behind them).
            author: shorthand for the `author:` operator.
        """
        q = f'{query} author:"{author}"' if author else query
        out: list[dict] = []
        start = 0
        while len(out) < limit:
            params = {
                "q": q,
                "hl": self.hl,
                "as_sdt": "0,5" if include_citations else "1,5",
                "start": start,
                "num": PAGE_SIZE,
            }
            if year_from:
                params["as_ylo"] = year_from
            if year_to:
                params["as_yhi"] = year_to
            if sort_by_date:
                params["scisbd"] = 1
            html = self.client.get(
                f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                headers={"accept": "text/html"},
            )
            rows = self.parse(html)
            if not rows:
                break
            out.extend(rows)
            start += PAGE_SIZE
            if len(rows) < PAGE_SIZE:
                break
        return out[:limit]

    @staticmethod
    def parse(html: str) -> list[dict]:
        """Parse a Scholar result page. Pure, so it is testable on a fixture."""
        tree = HTMLParser(html)
        # `.gs_ri` is nested inside `.gs_r.gs_or.gs_scl`, so selecting both at
        # once returns every hit twice. Prefer the outer block (it also holds
        # the PDF link) and fall back to the inner one for stripped-down pages.
        nodes = tree.css("div.gs_r.gs_or.gs_scl") or tree.css("div.gs_ri")
        out = []
        for node in nodes:
            title_node = node.css_first("h3.gs_rt")
            if title_node is None:
                continue
            link = title_node.css_first("a")
            # `.gs_a` is "authors - journal, year - publisher", pipe-free and
            # unstructured; splitting on the dashes is the best available.
            meta = node.css_first(".gs_a")
            meta_text = meta.text(strip=True) if meta else ""
            parts = [p.strip() for p in meta_text.split(" - ")]
            year = _YEAR_RE.search(parts[1]) if len(parts) > 1 else None

            citations = None
            cluster = None
            for a in node.css(".gs_fl a"):
                href = a.attributes.get("href", "") or ""
                text = a.text(strip=True)
                if "cites=" in href:
                    m = _CITED_RE.search(text)
                    citations = int(m.group(1)) if m else None
                    cm = re.search(r"cites=(\d+)", href)
                    cluster = cm.group(1) if cm else None

            pdf_node = node.css_first(".gs_or_ggsm a")
            snippet = node.css_first(".gs_rs")
            out.append(
                {
                    "title": title_node.text(strip=True)
                    .removeprefix("[PDF]")
                    .removeprefix("[HTML]")
                    .strip(),
                    "url": link.attributes.get("href") if link else None,
                    "authors": parts[0] if parts else None,
                    "venue": parts[1] if len(parts) > 1 else None,
                    "publisher": parts[2] if len(parts) > 2 else None,
                    "year": int(year.group(1)) if year else None,
                    "snippet": snippet.text(strip=True) if snippet else None,
                    "citations": citations,
                    "cluster_id": cluster,
                    "pdf": pdf_node.attributes.get("href") if pdf_node else None,
                }
            )
        return out

    def citations(self, cluster_id: str, **kw: Any) -> list[dict]:
        """The papers citing a given result (`cluster_id` from `search()`)."""
        html = self.client.get(
            f"{ENDPOINT}?cites={urllib.parse.quote(cluster_id)}&hl={self.hl}",
            headers={"accept": "text/html"},
        )
        return self.parse(html)


__all__ = ["Scholar"]
