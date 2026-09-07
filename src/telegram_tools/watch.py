"""The watch commands' local side: the rules a user writes, the runner's screens, the two guarantees.

Spec sections 10.2, 10.5 and 10.6. The engine, the lock, the cursors, the
clock-jump planner and the delivery all live in the shared core; what is
settled here is what only this tool can settle:

* **A rule is a file the user owns.** `watch rules add|edit` writes
  `~/.telegram-tools/rules/<name>.json` in the `cli-tools/rule/1` spelling,
  0600, and every one of those files is editable by hand -- the flags are a
  convenience, not the only way in. `edit` replaces the fields its flags name
  and leaves the rest; `none` empties a list, the same word `--rights none`
  takes on the administration commands. Naming any action flag replaces the
  action list outright, because half an action list is not a rule anybody
  meant to write.
* **What a rule may do is a closed list, and none of it fetches or mutates.**
  The core refuses anything outside `alert`, `tag`, `bookmark`,
  `capture_metadata`, `archive` and `queue_review` with `RULE_INVALID`; there
  is no download and no send beyond the alert's fixed template. A rule that
  wants a file downloaded queues it for review, where a person answers.
* **The two guarantees, spelled on every listing.** `send --at` hands the
  message to Telegram, which holds it and posts it with this machine off:
  `server-held`. `schedule post` stores a row this tool's runner fires:
  `runner-held: fires only while watch run is up on this machine`. Telegram
  has no repeat of its own, so `--every` is always runner-held. Neither label
  is inferred from context; both are fields on the row.
* **A local write is still a write.** A rule file and a schedule row are this
  machine's, not Telegram's, so their plan carries no rights to preflight --
  but the plan, the readback (the file or the row read again) and the audit
  line are the same four steps every other write here takes.

Nothing in this module talks to Telegram: the update handlers and the send are
`adapters/events.py`, and the native scheduled-message calls are in `cli.py`
beside the other raw requests.
"""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from telegram_tools._core import rid as _rid
from telegram_tools._core import rules as _rules
from telegram_tools._core import runner as _runner
from telegram_tools.envelope import PREFIX, CommandError

RULE = "--------------------------------------------"

# The verbs of each group, and which of them write something on this machine.
WATCH_VERBS = ("run", "status", "stop", "reload", "rules")
RULES_VERBS = ("list", "add", "edit", "remove", "enable", "disable", "test")
# `run` takes the lock and appends to the log; the four rule verbs write files.
WATCH_WRITES = ("run",)
RULES_WRITES = ("add", "edit", "remove", "enable", "disable")
SCHEDULE_VERBS = ("list", "post", "cancel")
SCHEDULE_WRITES = ("post", "cancel")

# The word a list flag takes to mean "empty this list", the same one
# `admin promote --rights none` takes.
NONE = "none"
# What `schedule list` and `send --at` print beside a schedule. The core owns
# the strings; naming them here keeps the screens from spelling a third one.
SERVER_HELD = _runner.SERVER_HELD
RUNNER_HELD = _runner.RUNNER_HELD


class WatchError(CommandError):
    """A watch argument this tool refuses before anything is written."""

    def __init__(self, message: str, *, code: str = "RULE_INVALID", hint: str | None = None) -> None:
        super().__init__(message, code=code, hint=hint)


# -- building a rule from flags -------------------------------------------


def _list_flag(values: Sequence[str] | None) -> tuple[str, ...] | None:
    """A repeatable flag as a list: absent is None (leave it), `none` is empty."""
    if not values:
        return None
    if len(values) == 1 and str(values[0]).strip().lower() == NONE:
        return ()
    out: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return tuple(out)


def _command_destination(text: str) -> dict[str, Any]:
    """`--alert-command 'my-hook --channel 12'` as a command destination."""
    argv = shlex.split(str(text))
    if not argv:
        raise WatchError(f"--alert-command {text!r} names no command")
    return {"kind": "command", "argv": argv}


def actions_from(args: Any) -> list[dict[str, Any]] | None:
    """The action list the flags name, or None when they name none.

    An alert to a rid on this platform is a `platform` destination; an alert to
    a command the user configured is how an alert leaves this platform, and
    this tool never learns where it lands (section 10.4).
    """
    actions: list[dict[str, Any]] = []
    for rid in _list_flag(getattr(args, "alert_to", None)) or ():
        try:
            _rid.parse(rid)
        except _rid.RidError as exc:
            raise WatchError(f"--alert-to {rid!r}: {exc}") from exc
        actions.append({"kind": "alert", "destination": {"kind": "platform", "rid": rid}})
    for command in getattr(args, "alert_command", None) or ():
        actions.append({"kind": "alert", "destination": _command_destination(command)})
    for label in _list_flag(getattr(args, "tag", None)) or ():
        actions.append({"kind": "tag", "label": label})
    for flag, kind in (("bookmark", "bookmark"), ("capture_metadata", "capture_metadata"), ("archive_scope", "archive"), ("queue_review", "queue_review")):
        if getattr(args, flag, False):
            actions.append({"kind": kind})
    return actions or None


def filter_from(args: Any, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The filter block, starting from `base` so `edit` keeps what its flags do not name."""
    out = dict(base or {})
    for dest, key in (
        ("identity", "identity"),
        ("scope", "scopes"),
        ("sender", "senders"),
        ("domain", "domains"),
        ("keyword", "keywords"),
        ("media_type", "media_types"),
    ):
        values = _list_flag(getattr(args, dest, None))
        if values is not None:
            out[key] = list(values)
    regex = getattr(args, "regex", None)
    if regex is not None:
        out["regex"] = None if regex.strip().lower() in ("", NONE) else regex
    for dest, key in (("min_bytes", "min_bytes"), ("max_bytes", "max_bytes")):
        value = getattr(args, dest, None)
        if value is not None:
            out[key] = None if value < 0 else value
    return out


def rule_from(args: Any, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The `cli-tools/rule/1` object the flags describe, over `base` for an edit.

    Only the shape is built here; the core validates it, which is what refuses
    an unknown action kind, a regex that does not compile or a command that is
    not on PATH.
    """
    name = str(getattr(args, "name", "") or "")
    data: dict[str, Any] = dict(base or {})
    data.update({"schema": _rules.SCHEMA, "name": name})
    events = _list_flag(getattr(args, "on", None))
    if events:
        data["trigger"] = {"events": list(events)}
    elif "trigger" not in data:
        raise WatchError(
            "a rule triggers on at least one event kind",
            hint="--on " + " --on ".join(_rules.EVENT_KINDS),
        )
    data["filter"] = filter_from(args, data.get("filter"))
    actions = actions_from(args)
    if actions is not None:
        data["actions"] = actions
    elif "actions" not in data:
        raise WatchError(
            "a rule does something: name at least one action",
            hint="--alert-to RID, --alert-command CMD, --tag LABEL, --bookmark, --capture-metadata, --archive-scope or --queue-review",
        )
    for dest, key in (("cooldown", "cooldown_s"), ("dedup_window", "dedup_window_s")):
        value = getattr(args, dest, None)
        if value is not None:
            data[key] = value
    enabled = getattr(args, "enabled", None)
    if enabled is not None:
        data["enabled"] = bool(enabled)
    data.setdefault("enabled", True)
    return data


def read_rule_file(path) -> dict[str, Any]:
    """The stored rule as JSON, for an edit to build on. A missing file is a refusal."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WatchError(f"no rule at {path.name}: {exc}", hint="`watch rules list` shows what is there") from exc
    except ValueError as exc:
        raise WatchError(f"{path.name} is not readable JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise WatchError(f"{path.name} does not hold a rule object")
    return dict(data)


def load_rules(directory, *, which: Callable[[str], str | None] = shutil.which) -> tuple[Any, ...]:
    """Every rule in the directory, or the first refusal, with the file named."""
    return _rules.load_directory(directory, which=which)


# -- the screens ------------------------------------------------------------


def _destination_label(destination: Mapping[str, Any]) -> str:
    if destination.get("kind") == "command":
        return "command " + " ".join(destination.get("argv") or ())
    return str(destination.get("rid") or "?")


def action_label(action: Mapping[str, Any]) -> str:
    kind = str(action.get("kind"))
    if kind == "alert":
        return f"alert → {_destination_label(action.get('destination') or {})}"
    if kind == "tag":
        return f"tag {action.get('label')}"
    return kind


def format_rules(rules: Sequence[Any], directory) -> str:
    """`watch rules list`: what is loaded, what each one watches and what it does."""
    lines = [f"Rules in {directory}", RULE]
    if not rules:
        lines.append("(none yet - `watch rules add` writes one, or drop a JSON file in that directory)")
        return "\n".join(lines)
    for rule in rules:
        data = rule.to_dict()
        state = "enabled" if data["enabled"] else "disabled"
        lines.append(f"{data['name']}  [{state}]")
        lines.append("  on      " + ", ".join(data["trigger"]["events"]))
        stated = [f"{key}={value}" for key, value in sorted(data["filter"].items()) if value not in (None, [], ())]
        lines.append("  filter  " + (", ".join(stated) if stated else "(everything the trigger sees)"))
        lines.append("  does    " + "; ".join(action_label(action) for action in data["actions"]))
        lines.append(f"  cooldown {data['cooldown_s']}s, dedup {data['dedup_window_s']}s")
    return "\n".join(lines)


def format_test(report: Mapping[str, Any]) -> str:
    """`watch rules test`: what would fire, and why each rule that would not, firing nothing."""
    lines = [f"Event {report['event_key'][:12]}  (nothing is fired; dedup is {report['dedup']})", RULE]
    if report.get("dropped"):
        lines.append(f"Dropped before any rule ran: {report['dropped']}")
        return "\n".join(lines)
    for entry in report["rules"]:
        if entry["would_fire"]:
            lines.append(f"{entry['name']}: would fire {', '.join(entry['would_fire'])}")
            for alert in entry.get("alerts") or ():
                lines.append(f"  → {_destination_label(alert['destination'])}")
                for line in str(alert["text"]).splitlines():
                    lines.append(f"    {line}")
            continue
        if not entry["enabled"]:
            why = "disabled"
        elif not entry["triggered"]:
            why = "this event kind is not in its trigger"
        else:
            failed = [key for key, passed in entry["filter"].items() if not passed]
            why = "filtered out by " + ", ".join(failed) if failed else "filtered out"
        lines.append(f"{entry['name']}: no ({why})")
    return "\n".join(lines)


def _when(value: Any) -> str:
    return str(value or "-")


def format_status(status: Mapping[str, Any], *, rules: int) -> str:
    """`watch status`: the lock and its holder, the cursors, the schedules, the last log lines."""
    lock = status.get("lock")
    lines = ["Runner", RULE]
    if lock is None:
        lines.append("Not running (no lock file). `watch run` starts one.")
    elif not lock.get("alive"):
        lines.append(f"Not running (the lock names pid {lock['pid']}, which is gone).")
    else:
        lines.append(f"Running: pid {lock['pid']} since {lock['started_at']}")
    lines.append(f"Rules loaded  {rules}")
    log = list(status.get("log") or ())
    last = next((line for line in reversed(log) if line.get("event") in ("delivered", "schedule_fired", "replayed", "started")), None)
    lines.append("Last tick     " + (_when(last.get("at")) + f" ({last.get('event')})" if last else "-"))
    cursors = status.get("cursors") or {}
    lines.append(f"Cursors       {len(cursors)} scope(s)")
    for rid, cursor in sorted(cursors.items()):
        lines.append(f"  {rid} after {cursor}")
    schedules = status.get("schedules") or []
    lines.append(f"Schedules     {len(schedules)} runner-held")
    for schedule in schedules:
        lines.append(f"  {schedule['id']}  {schedule['next']}  {schedule['rid']}")
    if log:
        lines.append(RULE)
        lines.append(f"Last {len(log)} log line(s):")
        for line in log:
            fields = " ".join(f"{key}={value}" for key, value in line.items() if key not in ("at", "event"))
            lines.append(f"  {line.get('at', '')} {line.get('event', '')} {fields}".rstrip())
    return "\n".join(lines)


@dataclass(frozen=True)
class ScheduledMessage:
    """One message Telegram is holding for a chat: what `schedule list` shows as server-held."""

    message_id: int
    rid: str
    at: str | None
    text: str

    @property
    def guarantee(self) -> str:
        return SERVER_HELD

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.message_id),
            "kind": "native",
            "rid": self.rid,
            "at": self.at,
            "text": self.text,
            "guarantee": self.guarantee,
        }


def format_schedules(native: Sequence[Mapping[str, Any]], local: Sequence[Mapping[str, Any]]) -> str:
    """`schedule list`: both kinds, each row carrying the guarantee it actually has."""
    lines = ["Scheduled", RULE]
    if not native and not local:
        lines.append("(nothing scheduled)")
        return "\n".join(lines)
    if native:
        lines.append(f"Held by Telegram ({SERVER_HELD}) - these post with this machine off:")
        for row in native:
            lines.append(f"  {row['id']:>12}  {_when(row['at'])}  {row['rid']}")
            lines.append(f"                {_one_line(row.get('text'))}")
    if local:
        lines.append(f"Held by this runner ({RUNNER_HELD}):")
        for row in local:
            repeat = f"every {row['every']}" if row.get("every") else _when(row.get("at"))
            lines.append(f"  {row['id']:>12}  next {row['next']}  {repeat}  {row['rid']}")
            lines.append(f"                {_one_line(row.get('text'))}")
    return "\n".join(lines)


def _one_line(text: Any, width: int = 72) -> str:
    body = " ".join(str(text or "").split())
    return body if len(body) <= width else body[: width - 1] + "…"


def format_schedule_created(row: Mapping[str, Any]) -> str:
    """What `schedule post` and `send --at` print: the moment, and which guarantee it has."""
    when = row.get("next") or row.get("at")
    repeat = f", repeating {row['every']}" if row.get("every") else ""
    return "\n".join(
        [
            f"Scheduled for {when}{repeat} in {row['rid']}",
            f"Guarantee: {row['guarantee']}",
        ]
    )


def confirm(preview: str, question: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    """The y/N every local watch write asks, printed the same way every other one is."""
    write(preview)
    answer = read(f"{question} [y/N]: ").strip().lower()
    if not answer:
        write("No answer read - cancelled.")
        return False
    return answer == "y"


# -- the schedule arguments -------------------------------------------------


def local_zone():
    """The zone a bare `--at` is read in: this machine's, which is what a person means."""
    return datetime.now().astimezone().tzinfo


def parse_when(text: str) -> datetime:
    """`--at` as an aware datetime; a time with no offset is this machine's local time."""
    try:
        return _runner.parse_at(text, local_zone())
    except _runner.RunnerError as exc:
        raise WatchError(str(exc), code="INVALID_ARGUMENT", hint="ISO 8601: 2026-09-09T09:00, or 2026-09-09T09:00+02:00") from exc


def require_future(when: datetime) -> datetime:
    if when.timestamp() <= datetime.now(timezone.utc).timestamp():
        raise WatchError(f"{when.isoformat()} is in the past", code="INVALID_ARGUMENT", hint="pick a moment ahead of now")
    return when


def check_every(text: str) -> str:
    """`--every` as core reads it: an interval like `15m`, or a five-field cron expression."""
    try:
        _runner.parse_every(text)
    except _runner.RunnerError as exc:
        raise WatchError(str(exc), code="INVALID_ARGUMENT", hint="15m, 2h, 1d, or a cron expression like `0 9 * * mon`") from exc
    return text


def schedule_rid(chat_id: int, topic_id: int | None) -> str:
    if topic_id is None:
        return str(_rid.make(PREFIX, "chat", chat_id))
    return str(_rid.make(PREFIX, "topic", chat_id, topic_id))


__all__ = [
    "RULES_VERBS",
    "RULES_WRITES",
    "RUNNER_HELD",
    "SCHEDULE_VERBS",
    "SCHEDULE_WRITES",
    "SERVER_HELD",
    "ScheduledMessage",
    "WATCH_VERBS",
    "WATCH_WRITES",
    "WatchError",
    "action_label",
    "actions_from",
    "check_every",
    "confirm",
    "filter_from",
    "format_rules",
    "format_schedule_created",
    "format_schedules",
    "format_status",
    "format_test",
    "load_rules",
    "local_zone",
    "parse_when",
    "read_rule_file",
    "require_future",
    "rule_from",
    "schedule_rid",
]
