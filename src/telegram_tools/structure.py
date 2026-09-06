"""The `structure` commands' local side: the allowlist, the banner, the screens, the plan.

Spec section 12. A blueprint is a secret-free JSON description of one chat's
structure; the engine that exports, diffs and applies one is the shared copy's
(`_core/blueprint.py`), and the calls that read a chat and make a step are
this tool's port (`adapters/blueprint.py`). This module is what sits between
them: the one registered field set that says what a Telegram blueprint may
carry, the banner every export prints, the lines a person reads before a
gate, and the plan the audit log records.

Nothing here talks to Telegram. `structure remap` reads the archive; `export`,
`diff` and `apply` open the account's client in `cli`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from telegram_tools import __version__
from telegram_tools._core.archive import Archive
from telegram_tools._core.blueprint import (
    Allowlist,
    ApplyReport,
    BlueprintError,
    Diff,
    Step,
    dumps,
    loads,
    register,
    remap_table,
    validate,
)
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Approval, Mutation, Plan, Preflight
from telegram_tools.envelope import TOOL, CommandError

SCHEMA = "cli-tools/blueprint/telegram/1"
# The gate an apply is behind (section 12): the target's exact title, typed.
GATE = "typed_name"
RULE = "--------------------------------------------"

# The one place that says what a Telegram blueprint carries. The container's
# `name` is its title; `kind` is supergroup, forum or channel and is checked,
# never changed, by an apply. The engine generates `never_transferred` from the
# complement: the six every platform refuses plus the three named here, so a
# field that is not on this list cannot reach a blueprint.
ALLOWLIST = register(
    Allowlist(
        schema=SCHEMA,
        container={"name", "kind", "about", "default_banned_rights", "slow_mode_seconds", "join_request"},
        objects={"topics": {"name", "icon_emoji_id"}},
        # Admins are people holding rights; a linked discussion group is another
        # chat; an invite link is a credential to a chat. None transfers.
        excluded=("admins", "invite_links", "linked_chat"),
    )
)

# What `structure export` prints first, every time, before the blueprint's own
# `never_transferred` list. Fixed text: a blueprint is a floor plan, not a copy.
NO_PERFECT_CLONE = (
    "This is a blueprint of the chat's structure, not a copy of the chat.",
    "It carries: kind, title, description, topics (title and icon), default rights, slow mode, join approval.",
    "It never carries: members, admins, messages, history, invite links, the linked discussion group.",
    "Applying it makes topics and settings on the target and never deletes anything there.",
)


def terminal_present() -> bool:
    """Whether a human can be asked: the tty check `Approval.interactive` carries."""
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def approval_for(answered: bool) -> Approval:
    """The Approval a typed title on a terminal produces. `interactive` is the tty check,
    not the answer, so a title piped into stdin is refused by the engine as no person."""
    return Approval(GATE, interactive=answered and terminal_present())


# -- the file ----------------------------------------------------------------


def read_blueprint(path: str | Path) -> dict[str, Any]:
    """The blueprint at `path`, checked against the allowlist before anything is resolved."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Cannot read the blueprint {path}: {exc.strerror or exc}.", code="CONFIG_INVALID") from exc
    try:
        blueprint = loads(text)
    except BlueprintError as exc:
        raise CommandError(f"{path} is not a blueprint: {exc}.", code="CONFIG_INVALID") from exc
    problems = validate(blueprint, ALLOWLIST)
    if problems:
        raise CommandError(
            f"{path} is not a Telegram blueprint this build can apply: " + "; ".join(problems) + ".",
            code="CONFIG_INVALID",
            hint="Export it again with `structure export`; a hand edit must stay inside the allowlist.",
        )
    return blueprint


def write_blueprint(path: str | Path, blueprint: Mapping[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dumps(blueprint), encoding="utf-8")
    return output


def kind_of(blueprint: Mapping[str, Any]) -> str:
    return str(blueprint["container"]["settings"].get("kind", "supergroup"))


def title_of(blueprint: Mapping[str, Any]) -> str:
    return str(blueprint["container"]["settings"].get("name", ""))


def require_same_kind(blueprint: Mapping[str, Any], target_kind: str, target: Target) -> None:
    """A blueprint of a forum applies to a forum: topics need one, and a channel has no
    default rights to set. Refused by name before any step is planned."""
    wanted = kind_of(blueprint)
    if wanted != target_kind:
        raise CommandError(
            f"{target.title} is a {target_kind}, and this blueprint describes a {wanted}.",
            code="TARGET_KIND_MISMATCH",
            hint=f"Pick a {wanted}, or `structure apply --create` to make one from the blueprint.",
        )


# -- what a person reads -----------------------------------------------------


def format_banner() -> str:
    return "\n".join(NO_PERFECT_CLONE)


def format_export(blueprint: Mapping[str, Any], *, hash: str, dropped: Sequence[str], path: str | None) -> str:
    settings = blueprint["container"]["settings"]
    lines = [format_banner(), RULE]
    lines.append(f"{settings.get('kind')}  {settings.get('name')}  ({blueprint['container']['source_rid']})")
    topics = [item for item in blueprint["objects"] if item["kind"] == "topic"]
    lines.append(f"{len(topics)} topic(s), blueprint {hash}")
    for item in topics:
        icon = f"  icon {item['fields']['icon_emoji_id']}" if item["fields"].get("icon_emoji_id") else ""
        lines.append(f"  {item['handle']}  {item['fields']['name']}{icon}")
    lines.append("never transferred: " + ", ".join(blueprint["never_transferred"]))
    if dropped:
        lines.append("read but not carried: " + ", ".join(dropped))
    lines.append(f"written to {path}" if path else "(no --output: printed below)")
    return "\n".join(lines)


def format_diff(diff: Diff, *, target: Target) -> str:
    counts = diff.to_dict()["counts"]
    head = f"{target.display}: {counts['add']} to add, {counts['change']} to change, {counts['remove']} extra on the target (left alone)"
    if diff.empty:
        return f"{target.display}: the chat already matches the blueprint."
    return "\n".join([head, RULE, *diff.lines])


def format_steps(steps: Sequence[Step], *, target: Target, extras: Sequence[str], execute: bool) -> str:
    """What an apply is about to do, before the gate: every step in order, the extras it
    leaves alone, and which mode this is."""
    lines = [format_banner(), RULE, f"structure apply: {target.display} ({target.rid})"]
    if not steps:
        lines.append("Nothing to do: the chat already matches the blueprint.")
    for index, step in enumerate(steps, 1):
        what = ", ".join(f"{key}={_show(value)}" for key, value in step.fields.items()) or "(position only)"
        lines.append(f"{index:>3}. {step.op} {step.handle}: {what}")
    for line in extras:
        lines.append(f"     left alone: {line}")
    lines.append(RULE)
    lines.append(
        "Executing: the next prompt asks for the chat's exact title."
        if execute
        else "Dry-run. Add --execute to do it; the exact title is asked for then."
    )
    return "\n".join(lines)


def _show(value: Any) -> str:
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return str(value)


def format_apply(report: ApplyReport) -> str:
    lines = [f"apply {report.apply_id}: {report.status}"]
    for step in report.made:
        lines.append(f"  made {step.op} {step.handle} -> {report.remap.by_handle.get(step.handle, '?')}")
    if report.failed is not None:
        lines.append(f"  failed at {report.failed.op} {report.failed.handle}: {report.error}")
        lines.append("  the remap so far is kept; run `structure diff` to see what remains")
    elif report.readback is not None:
        pending = report.readback.pending
        if pending:
            lines.append(f"  readback: {len(pending)} change(s) still pending")
            lines.extend(f"    {change.line}" for change in pending)
        else:
            lines.append("  readback: the chat matches the blueprint")
        for change in report.extras:
            lines.append(f"  extra on the target, left alone: {change.line}")
    elif report.error:
        lines.append(f"  {report.error}")
    lines.append(f"remap table: `structure remap --apply-id {report.apply_id}`")
    return "\n".join(lines)


def format_remap(rows: Sequence[Mapping[str, Any]], *, apply_id: str) -> str:
    if not rows:
        return f"No remap rows for apply {apply_id}."
    lines = [f"remap {apply_id}  (blueprint {rows[0]['blueprint_hash']}, {rows[0]['created']})", RULE]
    for row in rows:
        lines.append(f"{row['source_rid']}  ->  {row['target_rid']}")
    lines.append(f"{len(rows)} row(s).")
    return "\n".join(lines)


def remap_rows(archive: Archive, apply_id: str) -> list[dict[str, Any]]:
    return remap_table(archive, apply_id)


def latest_apply_ids(archive: Archive, limit: int = 20) -> list[tuple[str, str, str]]:
    """(apply_id, created, blueprint_hash) newest first, for the menu's picker."""
    rows = archive.connection.execute(
        "SELECT apply_id, MIN(created) AS created, blueprint_hash FROM remaps GROUP BY apply_id ORDER BY created DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(row["apply_id"], row["created"], row["blueprint_hash"]) for row in rows]


# -- the plan the audit line records -------------------------------------------


def plan_for(
    identity: Identity,
    target: Target,
    steps: Sequence[Step],
    *,
    required: Sequence[str],
    held: Sequence[str],
    blueprint_hash: str,
) -> Plan:
    """One mutation per step the apply will make, behind the typed title. The preflight
    is the rights the steps need: `manage_topics` when a topic is made or edited,
    `change_info` when the chat's own settings change."""
    mutations = [
        Mutation(
            op=f"blueprint.{step.op}",
            rid=step.target_rid or target.rid,
            params={"handle": step.handle, "kind": step.kind, "fields": dict(step.fields), "blueprint": blueprint_hash},
        )
        for step in steps
    ]
    return Plan(
        tool=TOOL,
        version=__version__,
        identity=identity,
        command="structure apply",
        targets=(target,),
        mutations=tuple(mutations),
        approval=GATE,
        preflight=Preflight(required=tuple(required), held=tuple(sorted(held))),
    )


def rights_for(steps: Sequence[Step]) -> tuple[str, ...]:
    """The rights a set of steps needs, in the probe's vocabulary."""
    required: list[str] = []
    if any(step.kind == "topic" for step in steps):
        required.append("manage_topics")
    if any(step.kind == "chat" for step in steps):
        required.append("change_info")
    return tuple(required)


__all__ = [
    "ALLOWLIST",
    "GATE",
    "NO_PERFECT_CLONE",
    "SCHEMA",
    "approval_for",
    "format_apply",
    "format_banner",
    "format_diff",
    "format_export",
    "format_remap",
    "format_steps",
    "kind_of",
    "latest_apply_ids",
    "plan_for",
    "read_blueprint",
    "remap_rows",
    "require_same_kind",
    "rights_for",
    "terminal_present",
    "title_of",
    "write_blueprint",
]
