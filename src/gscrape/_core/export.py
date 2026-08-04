"""Writing results out.

Every scraper returns plain dicts and lists — no custom classes to unpack — so
export is uniform: JSON for nested data, JSONL for streaming big runs, CSV for
the spreadsheet people. CSV flattens nested structures rather than dropping
them, because a silently missing column in a 100k-row export is worse than an
ugly one.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


def to_json(rows: Any, path: str | Path | None = None, *, indent: int = 1) -> str:
    text = json.dumps(rows, ensure_ascii=False, indent=indent, default=str)
    if path:
        Path(path).write_text(text, encoding="utf-8")
    return text


def to_jsonl(rows: Iterable[dict], path: str | Path | None = None) -> str:
    text = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    return text


def flatten(row: Any, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """Nested dict/list -> one flat dict of scalar columns.

    Lists of scalars become `"a|b|c"`; lists of dicts get indexed keys
    (`photos.0.base_url`). Deep, wide payloads (a Maps place) therefore produce
    a wide CSV — that is the honest representation, and `columns=` narrows it.
    """
    out: dict[str, Any] = {}
    if isinstance(row, dict):
        for k, v in row.items():
            out.update(flatten(v, f"{prefix}{sep}{k}" if prefix else str(k), sep))
    elif isinstance(row, list):
        if all(not isinstance(v, (dict, list)) for v in row):
            out[prefix] = "|".join("" if v is None else str(v) for v in row)
        else:
            for i, v in enumerate(row):
                out.update(flatten(v, f"{prefix}{sep}{i}", sep))
    else:
        out[prefix] = row
    return out


def to_csv(
    rows: Sequence[dict],
    path: str | Path | None = None,
    *,
    columns: Sequence[str] | None = None,
    delimiter: str = ",",
) -> str:
    """Flatten and write. Column order follows first appearance, not sort order."""
    flat = [flatten(r) for r in rows]
    if columns is None:
        seen: dict[str, None] = {}
        for r in flat:
            for k in r:
                seen.setdefault(k, None)
        columns = list(seen)

    import io

    buf = io.StringIO()
    w = csv.DictWriter(
        buf, fieldnames=list(columns), delimiter=delimiter, extrasaction="ignore"
    )
    w.writeheader()
    for r in flat:
        w.writerow(r)
    text = buf.getvalue()
    if path:
        Path(path).write_text(text, encoding="utf-8")
    return text


def emit(rows: Any, path: str | Path | None = None, fmt: str = "json") -> None:
    """CLI helper: write `rows` as `fmt` to `path`, or to stdout."""
    fmt = fmt.lower()
    if fmt == "json":
        text = to_json(rows, path)
    elif fmt == "jsonl":
        text = to_jsonl(rows if isinstance(rows, list) else [rows], path)
    elif fmt == "csv":
        text = to_csv(rows if isinstance(rows, list) else [rows], path)
    else:
        raise ValueError(f"unknown format {fmt!r} (json, jsonl, csv)")
    if path:
        print(f"wrote {path}", file=sys.stderr)
    else:
        print(text)
