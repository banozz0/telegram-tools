"""The `archive` commands' local side: where the store is, how it opens, what a screen prints.

Spec section 8. The store itself, its schema, search and retention are the
shared copy's (`_core/archive.py`); this module is what this tool adds around
it -- the file under `~/.telegram-tools/`, opened 0600 like every other file
there, the budgets from `config.json`, the lines a person reads, and the one
gate `retention` and `forget` share with `delete`: the target's exact name,
typed back.

Nothing here talks to Telegram. `archive sync` is the one archive command
that connects, and its source is `adapters/archive.py`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Sequence

from telegram_tools import __version__
from telegram_tools._core.archive import Archive, SearchHit, SyncReport
from telegram_tools._core.config import Budgets, human_bytes
from telegram_tools._core.config import load as load_budget_config
from telegram_tools._core.export import FORMATS as EXPORT_FORMATS
from telegram_tools._core.paths import ToolPaths, make_private_dir, open_private
from telegram_tools._core.plan import Plan
from telegram_tools import profiles as profile_store

CONFIG_FILE = "config.json"
# What `archive search` wraps a match in on a screen (section 8.4).
MARKERS = ("«", "»")


def core_version() -> str:
    """The workshop tag the vendored store came from, for the schema_version row."""
    version_file = Path(__file__).parent / "_core" / "VERSION"
    for line in version_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        if key == "tag":
            return value.strip()
    return ""


def paths_for(home: Path | None = None) -> ToolPaths:
    return profile_store.paths_for(home)


def config_path(home: Path | None = None) -> Path:
    return paths_for(home).root / CONFIG_FILE


def budgets_for(home: Path | None = None) -> Budgets:
    """The three budgets, from `config.json`; written with the defaults on first use."""
    make_private_dir(paths_for(home).root)
    return Budgets.from_file(config_path(home))


def open_archive(home: Path | None = None) -> Archive:
    """`~/.telegram-tools/archive.sqlite`, created 0600 and migrated forward.

    The file is created here, empty, before SQLite ever sees it: SQLite makes
    a new database at the process umask and gives the WAL and shm files the
    database's mode, so a 0600 file first is what keeps all three private.
    """
    paths = paths_for(home)
    make_private_dir(paths.root)
    if not paths.archive.exists():
        open_private(paths.archive, "w").close()
    return Archive.open(
        paths.archive,
        budgets=budgets_for(home),
        core_version=core_version(),
        tool_version=__version__,
    )


def archive_exists(home: Path | None = None) -> bool:
    path = paths_for(home).archive
    return path.exists() and path.stat().st_size > 0


def read_only(home: Path | None = None) -> sqlite3.Connection | None:
    """The archive opened read-only, or None when there is none yet. For `doctor` and pickers."""
    if not archive_exists(home):
        return None
    connection = sqlite3.connect(f"file:{paths_for(home).archive}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def list_scopes(home: Path | None = None) -> list[tuple[str, str]]:
    """Every scope the archive holds as `(rid, title)`, for the menu's picker. Opens nothing to write."""
    connection = read_only(home)
    if connection is None:
        return []
    try:
        rows = connection.execute("SELECT rid, title FROM scopes ORDER BY title, rid").fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [(row["rid"], row["title"] or "") for row in rows]


def budget_usage(home: Path | None = None) -> dict[str, Any] | None:
    """Rows, bytes and the budget row, read without migrating; None when there is no archive."""
    connection = read_only(home)
    if connection is None:
        return None
    try:
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        scopes = connection.execute("SELECT COUNT(*) FROM scopes").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        connection.close()
    used = int(page_count) * int(page_size)
    budgets = Budgets.from_config(load_budget_config(config_path(home)))
    return {
        "messages": messages,
        "scopes": scopes,
        "bytes": used,
        "budgets": budgets.report({"archive_max_bytes": used}),
    }


# -- what a person reads ---------------------------------------------------


def format_coverage(report: SyncReport) -> str:
    """The table `archive sync` ends with: every scope, what happened to it."""
    lines = ["Coverage", "--------------------------------------------"]
    for scope in report.scopes:
        if scope.status == "skipped":
            what = f"skipped ({scope.skipped_reason})"
        elif scope.status == "failed":
            what = f"failed ({scope.error})"
        else:
            what = f"{scope.rows} rows" + (f", {scope.deleted} deleted" if scope.deleted else "")
        lines.append(f"{scope.rid}\t{scope.title}\t{what}")
    lines.append(
        f"{len(report.scopes)} scope(s), {report.rows} rows, "
        f"{len(report.skipped)} skipped, {len(report.failed)} failed"
    )
    return "\n".join(lines)


def format_status(status: dict[str, Any]) -> str:
    lines = [
        "Archive",
        "--------------------------------------------",
        f"scopes    {status['scopes']}",
        f"messages  {status['messages']} ({status['deleted']} marked deleted)",
        f"oldest    {status['oldest'] or '-'}",
        f"newest    {status['newest'] or '-'}",
        f"size      {human_bytes(status['bytes'])} of pages"
        f" ({human_bytes(status['disk']['files'])} on disk with the log)",
    ]
    coverage = status["coverage"]
    reasons = ", ".join(f"{reason} {count}" for reason, count in coverage["reasons"].items()) or "none"
    lines.append(f"coverage  {coverage['visible']} visible, {coverage['skipped']} skipped ({reasons})")
    for row in status["budgets"]:
        lines.append(f"budget    {row['budget']}: {row['used']} of {row['limit']} ({row['percent']}%)")
    lines.append(f"search    {'FTS5 available' if status['fts5'] else 'FTS5 missing'}")
    return "\n".join(lines)


def _context_line(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get("text") or "").split())
    return f"    {row.get('message_id')}\t{row.get('date') or ''}\t{text}"


def format_hits(hits: Sequence[SearchHit]) -> str:
    """One line per hit, the match marked, and the context rows indented under it."""
    if not hits:
        return "No messages match."
    lines = ["Messages", "--------------------------------------------"]
    for hit in hits:
        shown = " ".join((hit.highlight or hit.text).split())
        lines.append(f"{hit.rid}\t{hit.message_id}\t{hit.date or ''}\tsender={hit.sender}\t{shown}")
        for row in hit.context_before:
            lines.append(_context_line(row))
        for row in hit.context_after:
            lines.append(_context_line(row))
    return "\n".join(lines)


def format_plan(plan: Plan, *, execute: bool) -> str:
    """What `retention` and `forget` show before the gate: the target, the numbers, the mode."""
    mutation = plan.mutations[0]
    target = plan.targets[0]
    params = dict(mutation.params)
    lines = [f"{plan.command}: {target.display} ({target.rid})"]
    if mutation.op == "archive.retention":
        lines.append(f"keep {params.get('keep')}: {params.get('messages', 0)} message(s) before {params.get('cutoff') or 'nothing'} would go")
        if params.get("manifests"):
            lines.append(f"and {params['manifests']} media manifest(s) with them")
    else:
        lines.append(f"everything for this {'scope' if 'scope' in params else 'identity'} would go: {params.get('messages', 0)} message(s)")
    lines.append(
        "Executing: the next prompt asks for the exact title." if execute
        else "Dry-run. Add --execute to do it; the exact title is asked for then."
    )
    return "\n".join(lines)


def confirm_typed_name(
    preview: str, title: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> bool:
    """The gate `delete` uses, for the same reason: typing *this* name proves which one."""
    write(preview)
    typed = read(f"Type the exact title ({title}) to continue: ")
    return typed.strip().casefold() == title.casefold()


__all__ = [
    "EXPORT_FORMATS",
    "MARKERS",
    "archive_exists",
    "budget_usage",
    "budgets_for",
    "config_path",
    "confirm_typed_name",
    "core_version",
    "format_coverage",
    "format_hits",
    "format_plan",
    "format_status",
    "list_scopes",
    "open_archive",
    "paths_for",
    "read_only",
]
