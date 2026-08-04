"""Google Autocomplete — the cheapest keyword research there is.

`/complete/search` is a public, un-gated JSON endpoint: no consent cookies, no
JavaScript, no captcha under sane rates. It is also the only Google surface that
tells you what people actually type, which makes the alphabet/question
expansions below more useful than the raw call.

    from gscrape import Suggest
    s = Suggest(hl="de", gl="de")
    s.suggest("laufschuhe")                 # -> 10 completions
    s.alphabet("laufschuhe")                # -> "laufschuhe a", "... b", ...
    s.questions("laufschuhe")               # -> "warum ...", "welche ...", ...
    s.suggest("laufschuhe", ds="yt")        # -> YouTube autocomplete

Response dialects (`client=`):

* `chrome`  — `[query, [texts], [descriptions], [], {relevance, subtypes}]`
  The only one carrying relevance scores, so it is the default.
* `firefox` — `[query, [texts]]`, smallest payload.
* `youtube` — same as chrome but needs `ds=yt`.

`ds=` picks the vertical: `yt` YouTube, `sh` Shopping, `bks` Books, `n` News,
`i` Images, `v` Video, `pl` Play Store. Omit it for web.
"""

from __future__ import annotations

import string
import urllib.parse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from ._core.parse import parse_json, safe_get
from ._core.service import Service

ENDPOINT = "https://www.google.com/complete/search"

#: Question words that pull informational long-tails out of autocomplete.
QUESTION_WORDS = {
    "de": ["warum", "wie", "was", "wann", "wo", "welche", "wer", "ist", "kann", "sind"],
    "en": ["why", "how", "what", "when", "where", "which", "who", "is", "can", "are"],
}

#: Comparison/intent modifiers, the other half of a keyword sweep.
MODIFIERS = {
    "de": ["vs", "oder", "für", "ohne", "mit", "test", "kaufen", "günstig", "beste"],
    "en": ["vs", "or", "for", "without", "with", "review", "buy", "cheap", "best"],
}


class Suggest(Service):
    """Autocomplete for web and every vertical Google exposes one for."""

    def _url(self, q: str, *, client: str, ds: str | None) -> str:
        params = {
            "client": client,
            "q": q,
            "hl": self.hl,
            "gl": self.gl,
        }
        if ds:
            params["ds"] = ds
        return f"{ENDPOINT}?{urllib.parse.urlencode(params)}"

    def suggest(
        self,
        query: str,
        *,
        ds: str | None = None,
        client: str = "chrome",
        detailed: bool = False,
    ) -> list:
        """Completions for `query`.

        Returns a list of strings, or of `{text, description, relevance,
        subtypes}` dicts when `detailed=True` (chrome client only — the others
        carry no scores).
        """
        raw = self.client.get(self._url(query, client=client, ds=ds))
        data = parse_json(raw, what="complete/search")
        texts = safe_get(data, 1, default=[]) or []
        if not detailed:
            return [t for t in texts if isinstance(t, str)]

        descriptions = safe_get(data, 2, default=[]) or []
        meta = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
        relevance = meta.get("google:suggestrelevance", [])
        subtypes = meta.get("google:suggesttype", [])
        out = []
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                continue
            out.append(
                {
                    "text": text,
                    "description": descriptions[i] if i < len(descriptions) else None,
                    "relevance": relevance[i] if i < len(relevance) else None,
                    "type": subtypes[i] if i < len(subtypes) else None,
                }
            )
        return out

    # ------------------------------------------------------------ expansions

    def expand(
        self,
        query: str,
        suffixes: Iterable[str],
        *,
        prefix: bool = False,
        ds: str | None = None,
        workers: int = 8,
        dedupe: bool = True,
    ) -> list[str]:
        """Run one autocomplete call per suffix and merge the results.

        The single most productive keyword-research move: Google returns ~10
        completions per call, so 26 alphabet calls yield up to 260 real queries
        for the price of 26 tiny requests.

        Args:
            prefix: put the token in front (`warum laufschuhe`) instead of
                behind (`laufschuhe warum`). Question words belong in front.
            workers: parallel calls. Autocomplete tolerates far more than the
                other surfaces, but 8 is polite and already fast.
        """
        terms = [
            f"{s} {query}".strip() if prefix else f"{query} {s}".strip() for s in suffixes
        ]

        def one(term: str) -> list[str]:
            try:
                return self.suggest(term, ds=ds)
            except Exception:  # one dead call must not kill a 26-call sweep
                return []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(pool.map(one, terms))

        out: list[str] = []
        seen: set[str] = set()
        for batch in batches:
            for t in batch:
                if not dedupe or t not in seen:
                    seen.add(t)
                    out.append(t)
        return out

    def alphabet(self, query: str, **kw) -> list[str]:
        """`query a`, `query b`, ... — the classic a-z autocomplete sweep."""
        return self.expand(query, string.ascii_lowercase, **kw)

    def questions(self, query: str, *, lang: str | None = None, **kw) -> list[str]:
        """Question-word expansion, i.e. the informational long-tail."""
        words = QUESTION_WORDS.get(lang or self.hl, QUESTION_WORDS["en"])
        return self.expand(query, words, prefix=True, **kw)

    def modifiers(self, query: str, *, lang: str | None = None, **kw) -> list[str]:
        """Commercial-intent expansion (`test`, `kaufen`, `vs`, ...)."""
        words = MODIFIERS.get(lang or self.hl, MODIFIERS["en"])
        return self.expand(query, words, **kw)

    def sweep(self, query: str, **kw) -> list[str]:
        """alphabet + questions + modifiers in one merged, deduped list."""
        out: list[str] = []
        seen: set[str] = set()
        for batch in (
            self.suggest(query),
            self.alphabet(query, **kw),
            self.questions(query, **kw),
            self.modifiers(query, **kw),
        ):
            for t in batch:
                if t not in seen:
                    seen.add(t)
                    out.append(t)
        return out


__all__ = ["MODIFIERS", "QUESTION_WORDS", "Suggest"]
