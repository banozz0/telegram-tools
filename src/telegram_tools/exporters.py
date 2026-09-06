from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from telegram_tools._core import export as _export
from telegram_tools._core import rid as _rid

# What `search --format` takes. `json` and `csv` are written here exactly as
# they always were; the three that joined them on the archive card render
# through the shared writers, which is what keeps a live export and an archive
# export of the same rows readable by the same reader.
SEARCH_FORMATS = ("json", "csv", "jsonl", "markdown", "html")


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


def live_rows(records: Iterable[dict[str, Any]], *, prefix: str = "tg", chat_title: str = "") -> list[dict[str, Any]]:
    """Live search records in the shape the shared markdown and html writers take.

    The live record keeps its own keys (`id`, `chat_id`, `sender_username`, …)
    in `json`, `csv` and `jsonl`, because scripts read them; the two human
    formats want a rid, a message id, a sender and a media count, which is
    this mapping.
    """
    rows = []
    for record in records:
        chat_id = record.get("chat_id")
        topic_id = record.get("topic_id")
        if chat_id is None:
            rid = ""
        elif topic_id is not None:
            rid = str(_rid.make(prefix, "topic", chat_id, topic_id))
        else:
            rid = str(_rid.make(prefix, "chat", chat_id))
        rows.append(
            {
                "rid": rid,
                "message_id": str(record.get("id")),
                "scope_title": chat_title,
                "date": record.get("date"),
                "author": record.get("sender_username") or ("" if record.get("sender_id") is None else str(record["sender_id"])),
                "text": record.get("text") or "",
                "media": 1 if record.get("has_media") else 0,
            }
        )
    return rows


def write_records(
    records: Iterable[dict[str, Any]], output: str | Path, fmt: str, *, query: str = "", chat_title: str = ""
) -> None:
    rows = list(records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        # Explicit encoding, not the locale's: raw UTF-8 through the default
        # would raise on a machine whose locale is not UTF-8, where the old
        # ASCII-escaped output could not fail.
        path.write_text(json_text(rows) + "\n", encoding="utf-8")
        return

    if fmt == "jsonl":
        path.write_text("".join(json_line(row) + "\n" for row in rows), encoding="utf-8")
        return

    if fmt in ("markdown", "html"):
        rendered = _export.render(live_rows(rows, chat_title=chat_title), fmt, query=query, title="telegram-tools export")
        path.write_text(rendered, encoding="utf-8")
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
