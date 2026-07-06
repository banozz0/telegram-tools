from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable


def write_records(records: Iterable[dict[str, Any]], output: str | Path, fmt: str) -> None:
    rows = list(records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        path.write_text(json.dumps(rows, indent=2, default=str) + "\n")
        return

    if fmt != "csv":
        raise ValueError(f"Unsupported export format: {fmt}")

    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
