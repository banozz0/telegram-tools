"""The `review` commands' local side: the queue over this tool's archive, and what a screen prints.

Spec section 9. The queue, its seven states, the two human gates and the
download pipeline with its eleven checks are the shared copy's (`_core/review.py`,
`_core/download.py`, `_core/scanner.py`); this module is what this tool adds
around them -- the archive and directory layout under `~/.telegram-tools/`,
the limits from `config.json`, the fetchers the pipeline runs (this tool's
`MediaFetcher` for a file, the shared HTTP fetcher for a link), the lines a
person reads, and the plans the audit log records.

Nothing here contacts anything. `review list` and `review status` are queries
over the archive; the one command that reaches a host is `review approve`
(and `retry`), and it reaches the link's host or Telegram only after the
y/N, through the pipeline. The redirect chain a link resolves to is unknown
until then, by design: resolving it would hand the user's address to an
unknown host before anyone said yes.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Sequence

from telegram_tools import __version__
from telegram_tools._core.archive import Archive
from telegram_tools._core.config import human_bytes
from telegram_tools._core.config import load as load_budget_config
from telegram_tools._core.download import FetchReport, HttpFetcher, Limits, Pipeline
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Approval, Mutation, Plan, Preflight
from telegram_tools._core.review import KINDS, STATES, UNACCEPTABLE, QueueRow, ReviewError, ReviewQueue, directory_bytes
from telegram_tools._core.paths import make_private_dir
from telegram_tools._core.rid import parse as parse_rid
from telegram_tools._core.scanner import ClamAVAdapter
from telegram_tools.adapters.media import TelegramMediaFetcher
from telegram_tools.archive import config_path, paths_for
from telegram_tools.envelope import PLATFORM, TOOL

# The gate both human moves are behind (section 9.1): a y/N on a terminal.
GATE = "prompt_y"
# What a screen calls a candidate that has no verdict yet.
NO_VERDICT = "-"

# The three things the pipeline reaches outside this process, each replaceable
# by a test that has no network and no scanner binary: the link fetcher (over
# urllib), the DNS lookup the private-network check pins, and the scanner.
# None means the shared copy's own default.
link_fetcher: Callable[[], HttpFetcher] = HttpFetcher
resolver: Callable[[str, int], Sequence[str]] | None = None
scanner_factory: Callable[[], Any] = ClamAVAdapter


def queue_for(archive: Archive, home: Path | None = None) -> ReviewQueue:
    """The review queue over this tool's archive and directory layout."""
    return ReviewQueue(archive, paths_for(home))


def limits_for(home: Path | None = None) -> Limits:
    """`download_max_bytes` and the archive-expansion caps, from `config.json`."""
    return Limits.from_config(load_budget_config(config_path(home)))


def build_pipeline(queue: ReviewQueue, *, client=None, home: Path | None = None) -> Pipeline:
    """The pipeline for one run: a link through the shared HTTP fetcher, a file through
    Telegram when a client was opened for it. A media download with no client is a
    `failed` row naming the missing fetcher, never a link fetch in disguise."""
    # The layout's parents are made at the umask by `mkdir(parents=True)`, so
    # the two directories a fetch and an accept write under are tightened
    # first; a loose one would refuse the next write (section 8.1).
    make_private_dir(queue.paths.quarantine)
    make_private_dir(queue.paths.media)
    fetchers: dict[str, Any] = {"link": link_fetcher()}
    if client is not None:
        fetchers["media"] = TelegramMediaFetcher(client)
    return Pipeline(
        queue,
        queue.paths,
        fetchers=fetchers,
        limits=limits_for(home),
        resolver=resolver,
        scanner=scanner_factory(),
    )


def terminal_present() -> bool:
    """Whether a human can be asked: the tty check `Approval.interactive` carries."""
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def approval_for(answered: bool) -> Approval:
    """The Approval a y/N on a terminal produces. `interactive` is the tty check, not the
    answer: a script that pipes a `y` into a queue command still has no terminal, and
    the shared transition refuses it with APPROVAL_REQUIRED."""
    return Approval(GATE, interactive=answered and terminal_present())


def parse_ids(values: Sequence[str] | None) -> list[str]:
    """`--ids` as typed: repeatable, comma-separated, each once, order kept."""
    found: list[str] = []
    for value in values or ():
        for piece in str(value).split(","):
            piece = piece.strip()
            if piece and piece not in found:
                found.append(piece)
    return found


# -- what a person reads ---------------------------------------------------


def _what(row: QueueRow) -> str:
    """The URL as written for a link; the filename or file key for media."""
    if row.kind == "link":
        return row.url or ""
    return str(row.extra.get("display_name") or row.extra.get("locator") or "")


def _claimed(row: QueueRow) -> str:
    parts = [row.claimed_type or "?"]
    if row.claimed_size is not None:
        parts.append(human_bytes(row.claimed_size))
    return " ".join(parts)


def _where(row: QueueRow) -> str:
    return f"{row.source_rid}#{row.source_message_id}"


def format_queue(rows: Sequence[QueueRow]) -> str:
    """One line per candidate: id, kind, state, source, sender, the thing itself, what was claimed."""
    if not rows:
        return "The review queue is empty."
    lines = ["Review queue", "--------------------------------------------"]
    for row in rows:
        lines.append(
            f"{row.manifest_id}\t{row.kind}\t{row.state}\t{_where(row)}\t{row.created_at}\t"
            f"sender={row.sender}\t{_what(row)}\t{_claimed(row)}"
            + (f"\tverdict={row.verdict}" if row.verdict else "")
        )
    lines.append(f"{len(rows)} candidate(s). Nothing is fetched until you run review approve.")
    return "\n".join(lines)


def format_candidates(rows: Sequence[QueueRow], *, heading: str) -> str:
    """The preview a gate shows: every candidate the answer covers, one block each."""
    lines = [heading, "--------------------------------------------"]
    for row in rows:
        lines.append(f"{row.manifest_id}  {row.kind}  {row.state}")
        lines.append(f"    {'URL as written' if row.kind == 'link' else 'file'}: {_what(row)}")
        lines.append(f"    claimed: {_claimed(row)}")
        lines.append(f"    from: {_where(row)}  sender={row.sender}  {row.created_at}")
        if row.verdict:
            lines.append(f"    verdict: {row.verdict}" + (f" ({row.last_error})" if row.last_error else ""))
        if row.sha256:
            lines.append(f"    sha256: {row.sha256}")
        if row.redirect_chain:
            lines.append(f"    redirects: {' -> '.join(row.redirect_chain)}")
    return "\n".join(lines)


def format_report(report: FetchReport) -> str:
    """What one fetch ended as, on one line a person can act on."""
    verdict = report.verdict or NO_VERDICT
    if report.state == "quarantined":
        head = f"{report.manifest_id}\tquarantined\tverdict={verdict}\t{human_bytes(report.bytes_fetched)}"
        if report.sha256:
            head += f"\tsha256={report.sha256}"
        if report.detail and verdict != "CLEAN":
            head += f"\t{report.detail}"
        return head
    return f"{report.manifest_id}\t{report.state}\t{human_bytes(report.bytes_fetched)}\t{report.error or ''}".rstrip()


def format_accepted(results: Sequence[dict[str, Any]]) -> str:
    lines = []
    for item in results:
        lines.append(f"{item['manifest_id']}\taccepted\tverdict={item['verdict']}\t{human_bytes(item['bytes'])}\t{item['storage_path']}")
    return "\n".join(lines)


def format_rejected(results: Sequence[dict[str, Any]]) -> str:
    return "\n".join(f"{item['manifest_id']}\trejected\tfreed {human_bytes(item['freed_bytes'])}" for item in results)


def status_of(queue: ReviewQueue, home: Path | None = None) -> dict[str, Any]:
    """Counts per state, the two directories against their budgets, the scanner, and every
    candidate past `queued` with what its fetch learned. A query and two directory walks."""
    rows = queue.list()
    counts = Counter(row.state for row in rows)
    budgets = queue.archive.budgets
    quarantine = directory_bytes(queue.paths.quarantine)
    media = directory_bytes(queue.paths.media)
    held = [entry for entry in (queue.paths.quarantine.iterdir() if queue.paths.quarantine.exists() else ()) if entry.is_dir()]
    return {
        "states": {state: counts.get(state, 0) for state in STATES},
        "candidates": len(rows),
        "quarantine": {"downloads": len(held), "bytes": quarantine, "limit": budgets.limit("quarantine_max_bytes")},
        "media": {"bytes": media, "limit": budgets.limit("media_max_bytes")},
        "scanner": scanner_factory().report(),
        "downloads": [row.to_dict() for row in rows if row.state != "queued"],
    }


def format_status(status: dict[str, Any]) -> str:
    lines = ["Review", "--------------------------------------------"]
    states = status["states"]
    lines.append("candidates  " + ", ".join(f"{state} {count}" for state, count in states.items() if count) + (f"  ({status['candidates']} in all)" if status["candidates"] else "none"))
    quarantine = status["quarantine"]
    lines.append(f"quarantine  {quarantine['downloads']} download(s) held, {human_bytes(quarantine['bytes'])} of {human_bytes(quarantine['limit'])}")
    media = status["media"]
    lines.append(f"media       {human_bytes(media['bytes'])} of {human_bytes(media['limit'])} accepted")
    scanner = status["scanner"]
    if scanner.get("binary"):
        lines.append(f"scanner     {scanner['command']} found; a verdict is CLEAN or INFECTED")
    else:
        lines.append(f"scanner     none on PATH (looked for {', '.join(scanner.get('looked_for', ()))}); every verdict is UNSCANNED")
    for row in status["downloads"]:
        what = row["url"] or row.get("display_name") or row.get("locator") or ""
        line = f"{row['manifest_id']}\t{row['kind']}\t{row['state']}\tverdict={row['verdict'] or NO_VERDICT}\t{what}"
        if row["redirect_chain"]:
            line += f"\tredirects={' -> '.join(row['redirect_chain'])}"
        if row["last_error"] and row["verdict"] != "CLEAN":
            line += f"\t{row['last_error']}"
        lines.append(line)
    return "\n".join(lines)


# -- the plan the audit line records ---------------------------------------


def _target_for(archive: Archive, rid: str) -> Target:
    known = archive.scope_target(rid)
    if known is not None:
        return known
    parsed = parse_rid(rid)
    return Target(rid=rid, kind=parsed.kind, title=parsed.ids[-1], path=(parsed.ids[-1],), platform=PLATFORM)


def plan_for(command: str, op: str, identity: Identity, archive: Archive, rows: Sequence[QueueRow]) -> Plan:
    """The plan a queue move is: one target per source scope, one mutation per candidate,
    behind the y/N. A retry is behind the y/N its approve already answered."""
    targets: dict[str, Target] = {}
    mutations = []
    for row in rows:
        targets.setdefault(row.source_rid, _target_for(archive, row.source_rid))
        params: dict[str, Any] = {"manifest_id": row.manifest_id, "kind": row.kind, "message_id": row.source_message_id}
        if row.kind == "link":
            params["url"] = row.url
        else:
            params["locator"] = row.extra.get("locator")
        if row.sha256:
            params["sha256"] = row.sha256
        if row.verdict:
            params["verdict"] = row.verdict
        mutations.append(Mutation(op=op, rid=row.source_rid, params=params))
    return Plan(
        tool=TOOL,
        version=__version__,
        identity=identity,
        command=command,
        targets=tuple(targets.values()),
        mutations=tuple(mutations),
        approval=GATE,
        # A queue move needs no right on Telegram: the archive is this
        # machine's, and a media fetch reads what the account already read.
        preflight=Preflight(required=(), held=()),
    )


def kinds() -> tuple[str, ...]:
    return KINDS


def states() -> tuple[str, ...]:
    return STATES


__all__ = [
    "GATE",
    "UNACCEPTABLE",
    "ReviewError",
    "approval_for",
    "build_pipeline",
    "format_accepted",
    "format_candidates",
    "format_queue",
    "format_rejected",
    "format_report",
    "format_status",
    "limits_for",
    "parse_ids",
    "plan_for",
    "queue_for",
    "status_of",
    "terminal_present",
]
