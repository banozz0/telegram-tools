from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable


def json_text(payload: Any) -> str:
    """One JSON spelling for the whole tool, emoji included.

    `json.dumps` escapes non-ASCII by default, so a topic named 💻 Dobby came
    out as "\\ud83d\\udcbb Dobby" while every other line of the same output --
    the pickers, the discover table, the CSV export -- drew the emoji. Same
    title, two spellings, one terminal. Both are valid JSON; only one is
    readable.
    """
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


def json_line(payload: Any) -> str:
    """One record as a single `--jsonl` line: the same spelling, no indent.

    A stream is read a line at a time, so the pretty-printing that makes a
    whole payload readable is exactly what a reader here cannot have.
    """
    return json.dumps(payload, default=str, ensure_ascii=False)


def write_records(records: Iterable[dict[str, Any]], output: str | Path, fmt: str) -> None:
    rows = list(records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        # Explicit encoding, not the locale's: raw UTF-8 through the default
        # would raise on a machine whose locale is not UTF-8, where the old
        # ASCII-escaped output could not fail.
        path.write_text(json_text(rows) + "\n", encoding="utf-8")
        return

    if fmt != "csv":
        raise ValueError(f"Unsupported export format: {fmt}")

    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
