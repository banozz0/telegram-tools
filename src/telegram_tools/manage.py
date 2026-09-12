"""The administration commands' local side: the verbs, their gates, the hierarchy rule, the screens.

Spec section 13. Five command groups -- `admin`, `member`, `join-requests`,
`invite`, `settings` -- and every one of their writes follows the shape every
other write here has: a plan, a preflight that names the missing right, the
gate section 7 assigns, a re-derivation after the gate, a readback and one
audit line. What is specific to these commands is settled in this module:

* **Which gate.** Removing someone's membership (`member ban`, `member kick`),
  someone's rights (`admin demote`) or every topic a chat has (`settings set
  --forum off`) is `typed_name`: dry-run by default, `--execute`, the exact label or
  title typed at a terminal, no `--yes`, and a terminal in either mode.
  Everything else is `prompt_y`, and `--yes` answers it (the preview still
  prints, the approval kind stays `prompt_y`); no allowlist applies, because
  none exists for an admin action.
* **A setting's scope decides its right.** `settings set` on a chat needs
  `change_info`; on a topic it needs `manage_topics`, and the flags of one
  scope are a usage error in the other. Every `set` reads the fields before
  and after and reports what actually moved (`settings_diff`), so a call
  Telegram accepted and did not apply reads as "no field changed".
* **The hierarchy rule** (`HIERARCHY_DENIED`). An admin can give only rights
  it holds, and can edit only an admin with no more than it holds; the creator
  can do anything. Telegram enforces the same, after the call; this refuses
  before, naming both rights sets, so the person reading the refusal knows
  which right is short.
* **Bounded restrictions.** `mute` and `restrict` need `--until`, at least a
  minute ahead and at most a year, because Telegram reads anything further as
  forever and a restriction with no end is a ban with a different name.
* **No audit reason on Telegram** (section 16). The platform stores no reason
  beside a ban or a kick, so `--reason` is recorded in the plan and in the
  local audit line, and the docs say that is the only record.
* **A kick is a ban then an unban.** Telegram has no kick of its own;
  Telethon's `kick_participant` bans and at once unbans, so the person is out,
  may rejoin, and leaves no ban row behind. The dry-run says so.
* **Invite links are shown only where they were asked for**: `invite list`
  and `invite create` carry them; every other screen, envelope and audit line
  goes through the shared redaction, which blanks them.

Nothing here talks to Telegram: the calls are `adapters/manage.py`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from telegram_tools._core import rid as _rid
from telegram_tools._core.plan import Approval
from telegram_tools._core.redaction import redact_text
from telegram_tools.adapters.blueprint import ADMIN_RIGHT_NAMES, BANNED_RIGHT_NAMES
from telegram_tools.envelope import PREFIX, CommandError

RULE = "--------------------------------------------"

# The rights `mute` takes away: sending anything. Telegram's own mute is the
# same single flag; the finer send_* rights exist for `restrict`.
MUTE_RIGHTS = ("send_messages",)
# What a ban is, in Telegram's vocabulary: not even reading.
BAN_RIGHTS = ("view_messages",)
# The bounds on `--until`, and why: below a minute the restriction would lapse
# before the readback, and Telegram treats more than a year as forever.
UNTIL_MIN = timedelta(minutes=1)
UNTIL_MAX = timedelta(days=366)
# How many rows a listing shows at most; the calls page at 200.
LIST_LIMIT = 200

_DURATION = re.compile(r"^(\d+)\s*([mhdw])$")
_UNIT = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


@dataclass(frozen=True)
class Op:
    """One verb: the group and verb it answers to, what it needs and how it is gated."""

    group: str
    verb: str
    approval: str | None  # None for a read
    required: tuple[str, ...]
    mutation: str | None = None
    # A typed_name verb dry-runs by default and takes `--execute`.
    typed: bool = False
    # Whether the verb names a person (`--user`).
    person: bool = False

    @property
    def command(self) -> str:
        return f"{self.group} {self.verb}"

    @property
    def writes(self) -> bool:
        return self.approval is not None


OPS: dict[tuple[str, str], Op] = {
    (op.group, op.verb): op
    for op in (
        Op("admin", "list", None, ()),
        Op("admin", "promote", "prompt_y", ("add_admins",), "admin.promote", person=True),
        Op("admin", "rights", "prompt_y", ("add_admins",), "admin.rights", person=True),
        Op("admin", "demote", "typed_name", ("add_admins",), "admin.demote", typed=True, person=True),
        Op("member", "list", None, ()),
        Op("member", "ban", "typed_name", ("ban_users",), "member.ban", typed=True, person=True),
        # A kick is what Telegram means by it: a ban followed at once by an
        # unban, so the person is out and may rejoin, and no ban row remains.
        Op("member", "kick", "typed_name", ("ban_users",), "member.kick", typed=True, person=True),
        Op("member", "unban", "prompt_y", ("ban_users",), "member.unban", person=True),
        Op("member", "mute", "prompt_y", ("ban_users",), "member.mute", person=True),
        Op("member", "unmute", "prompt_y", ("ban_users",), "member.unmute", person=True),
        Op("member", "restrict", "prompt_y", ("ban_users",), "member.restrict", person=True),
        Op("join-requests", "list", None, ()),
        Op("join-requests", "approve", "prompt_y", ("invite_users",), "join_request.approve", person=True),
        Op("join-requests", "decline", "prompt_y", ("invite_users",), "join_request.decline", person=True),
        Op("invite", "list", None, ("invite_users",)),
        Op("invite", "create", "prompt_y", ("invite_users",), "invite.create"),
        Op("invite", "revoke", "prompt_y", ("invite_users",), "invite.revoke"),
        Op("settings", "show", None, ()),
        # `settings set` is the one verb whose rights, gate and mutation are not
        # fixed by the table: a topic needs manage_topics where a chat needs
        # change_info, and switching topics off takes `delete`'s gate. What the
        # flags actually ask for is `settings_change` below.
        Op("settings", "set", "prompt_y", ("change_info",), "settings.set"),
    )
}
GROUPS = ("admin", "member", "join-requests", "invite", "settings")
# The argparse dest each group's verb lands in.
VERB_DESTS = {
    "admin": "admin_kind",
    "member": "member_kind",
    "join-requests": "join_kind",
    "invite": "invite_kind",
    "settings": "settings_kind",
}


def verbs_of(group: str) -> tuple[str, ...]:
    return tuple(verb for (name, verb) in OPS if name == group)


def op_for(args: Any) -> Op:
    """The Op an argv names, or a usage error naming the verbs."""
    group = str(getattr(args, "command", "") or "")
    verb = getattr(args, VERB_DESTS[group], None)
    if verb is None:
        raise ValueError(f"{group} needs one of: {', '.join(verbs_of(group))}.")
    return OPS[(group, verb)]


# -- people ------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One participant as the screens and the hierarchy rule see them."""

    id: int
    label: str
    username: str | None
    # creator, admin, member, banned, restricted, left, none
    status: str
    rights: tuple[str, ...] = ()
    rank: str | None = None
    until: str | None = None
    # Whether the acting admin may edit this admin (Telegram says so on the participant).
    can_edit: bool = True
    is_bot: bool = False

    @property
    def rid(self) -> str:
        return user_rid(self.id)

    @property
    def typed_label(self) -> str:
        """What the typed gate asks for: the `@username`, or the name when there is none."""
        return f"@{self.username}" if self.username else self.label

    @property
    def is_admin(self) -> bool:
        return self.status in ("creator", "admin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rid": self.rid,
            "label": self.label,
            "username": self.username,
            "status": self.status,
            "rights": list(self.rights),
            "rank": self.rank,
            "until": self.until,
            "is_bot": self.is_bot,
        }


def user_rid(user_id: int | str) -> str:
    return str(_rid.make(PREFIX, "user", user_id))


def user_label(user: Any) -> str:
    """`Name (@username)`, `Name`, or `user ID`; redacted, because a name is text someone else chose."""
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in parts if part).strip()
    username = getattr(user, "username", None)
    if username and name:
        label = f"{name} (@{username})"
    elif username:
        label = f"@{username}"
    elif name:
        label = name
    else:
        label = f"user {getattr(user, 'id', '?')}"
    return redact_text(label)


def labels_match(typed: str, member: Member) -> bool:
    """The typed gate: the `@username` (with or without its `@`) or the name, case-insensitively."""
    wanted = typed.strip().casefold()
    if not wanted:
        return False
    candidates = {member.typed_label.casefold(), member.label.casefold()}
    if member.username:
        candidates.add(member.username.casefold())
    return wanted in candidates


# -- what the flags say ------------------------------------------------------


def parse_rights(text: str | None, *, universe: Sequence[str], what: str, noun: str = "right") -> tuple[str, ...]:
    """A comma-separated list of names from `universe`, or `none`; anything else is a usage error.

    `noun` is what the refusal calls them, because the same shape reads the
    admin and banned rights and the folder categories, and "unknown folder
    right" would name the wrong thing.
    """
    if text is None:
        return ()
    names = [name.strip() for name in str(text).replace(";", ",").split(",") if name.strip()]
    if names == ["none"]:
        return ()
    unknown = sorted(set(names) - set(universe))
    if unknown:
        raise ValueError(f"Unknown {what} {noun}(s): {', '.join(unknown)}. Valid names: {', '.join(universe)}.")
    if not names:
        raise ValueError(f"names at least one {what} {noun}, or `none`.")
    return tuple(sorted(set(names)))


def parse_until(text: str, *, now: datetime | None = None) -> datetime:
    """`--until` as a moment: a duration (`30m`, `2h`, `7d`, `1w`) or an ISO date or datetime.

    Bounded on both sides (`UNTIL_MIN`, `UNTIL_MAX`); outside them is a usage
    error, because a restriction that has already lapsed or never lapses is not
    what a bounded restriction means.
    """
    now = now or datetime.now(timezone.utc)
    raw = str(text).strip()
    match = _DURATION.match(raw.lower())
    if match:
        moment = now + timedelta(**{_UNIT[match.group(2)]: int(match.group(1))})
    else:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"--until {raw!r} is neither a duration (30m, 2h, 7d, 1w) nor an ISO date or datetime."
            ) from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    if moment < now + UNTIL_MIN:
        raise ValueError(f"--until {raw!r} is less than a minute ahead; a restriction needs an end that has not passed.")
    if moment > now + UNTIL_MAX:
        raise ValueError(f"--until {raw!r} is more than a year ahead, which Telegram treats as forever; use member ban for that.")
    return moment.astimezone(timezone.utc).replace(microsecond=0)


def until_text(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_slow_mode(value: int) -> int:
    """Telegram accepts exactly these: 0 (off), 10, 30, 60, 300, 900, 3600 seconds."""
    allowed = (0, 10, 30, 60, 300, 900, 3600)
    if int(value) not in allowed:
        raise ValueError(f"--slow-mode takes one of {', '.join(str(n) for n in allowed)} seconds (0 switches it off).")
    return int(value)


def on_off(text: str) -> bool:
    """A flag that has to say which way: `on` or `off`.

    A `store_true` switch cannot turn something off, and a bare `--closed` that
    meant "closed" would leave no spelling for "open". Absent still means
    "leave it alone", which is the third state neither of those two carries.
    """
    wanted = str(text).strip().casefold()
    if wanted in ("on", "true", "yes", "1"):
        return True
    if wanted in ("off", "false", "no", "0"):
        return False
    raise ValueError(f"{text!r} is neither on nor off.")


# -- what `settings set` changes ----------------------------------------------

# The fields of a chat and of a topic, each as the readback diff names it. The
# same `--title` lands in both, which is why the scope decides the list.
CHAT_FIELDS = ("title", "about", "forum", "slow_mode_seconds")
TOPIC_FIELDS = ("title", "icon_emoji_id", "closed", "hidden")
# Which flag spells each field, for the usage error that names the wrong one.
FLAG_OF = {
    "title": "--title",
    "about": "--about",
    "forum": "--forum",
    "slow_mode_seconds": "--slow-mode",
    "icon_emoji_id": "--icon-emoji-id",
    "closed": "--closed",
    "hidden": "--hidden",
}
# The General topic is the one a forum cannot be without: it has no service
# message to remove, `delete topic` refuses it for that reason, and Telegram
# accepts only its title and `hidden` here.
GENERAL_TOPIC_ID = 1
GENERAL_FIELDS = ("title", "hidden")


@dataclass(frozen=True)
class SettingsChange:
    """What one `settings set` will do: the scope, the fields, and the gate that follows from them."""

    scope: str  # chat or topic
    fields: Mapping[str, Any]
    required: tuple[str, ...]
    approval: str
    typed: bool
    details: tuple[str, ...]

    @property
    def mutation(self) -> str:
        return f"{self.scope}.settings"


def settings_change(args: Any, *, kind: str, topic_id: int | None) -> SettingsChange:
    """Read the `settings set` flags into the change they describe, or a usage error.

    Three rules live here rather than in the handler. A flag belongs to one
    scope, so `--about` with `--topic` is a usage error naming both rather than
    a call Telegram refuses. A field Telegram cannot change on this chat --
    slow mode on a broadcast channel, any topic on a chat with no topics -- is
    `PLATFORM_UNSUPPORTED` before anything connects further. And switching
    topics *off* takes `delete`'s gate: every topic stops existing and its
    messages land in one stream, which is section 7's "removes a container",
    not its "edits a setting".
    """
    given = {
        "title": getattr(args, "title", None),
        "about": getattr(args, "about", None),
        "forum": getattr(args, "forum", None),
        "slow_mode_seconds": getattr(args, "slow_mode", None),
        "icon_emoji_id": getattr(args, "icon_emoji_id", None),
        "closed": getattr(args, "closed", None),
        "hidden": getattr(args, "hidden", None),
    }
    fields = {name: value for name, value in given.items() if value is not None}
    if not fields:
        raise ValueError("settings set changes nothing: name at least one of " + ", ".join(sorted(set(FLAG_OF.values()))) + ".")

    scope = "topic" if topic_id is not None else "chat"
    allowed = TOPIC_FIELDS if scope == "topic" else CHAT_FIELDS
    stray = sorted(set(fields) - set(allowed))
    if stray:
        names = ", ".join(FLAG_OF[name] for name in stray)
        raise ValueError(
            f"{names} {'change' if len(stray) > 1 else 'changes'} a chat, not a topic; drop --topic to use {'them' if len(stray) > 1 else 'it'}."
            if scope == "topic"
            else f"{names} {'change' if len(stray) > 1 else 'changes'} a topic; add --topic ID to say which."
        )

    if scope == "topic":
        if kind != "forum":
            raise CommandError(
                "This chat has no topics, so there is no topic to change.",
                code="PLATFORM_UNSUPPORTED",
                hint="`settings set --chat … --forum on` switches topics on first.",
            )
        if int(topic_id) == GENERAL_TOPIC_ID:
            refused = sorted(set(fields) - set(GENERAL_FIELDS))
            if refused:
                raise CommandError(
                    f"Telegram changes only {', '.join(FLAG_OF[name] for name in GENERAL_FIELDS)} on the General topic; "
                    f"{', '.join(FLAG_OF[name] for name in refused)} it refuses.",
                    code="PLATFORM_UNSUPPORTED",
                )
        details = tuple(f"{FLAG_OF[name]:<16} {_setting_text(name, fields[name])}" for name in TOPIC_FIELDS if name in fields)
        return SettingsChange("topic", fields, ("manage_topics",), "prompt_y", False, details)

    if "slow_mode_seconds" in fields:
        parse_slow_mode(fields["slow_mode_seconds"])
        if kind == "channel":
            raise CommandError("A broadcast channel has no slow mode.", code="PLATFORM_UNSUPPORTED")
    if "forum" in fields and kind == "channel":
        raise CommandError("A broadcast channel has no topics.", code="PLATFORM_UNSUPPORTED")

    details = [f"{FLAG_OF[name]:<16} {_setting_text(name, fields[name])}" for name in CHAT_FIELDS if name in fields]
    off = fields.get("forum") is False
    if off:
        details.append("Switching topics off puts every topic's messages in one stream and its topics stop existing.")
    return SettingsChange(
        "chat",
        fields,
        ("change_info",),
        "typed_name" if off else "prompt_y",
        off,
        tuple(details),
    )


def _setting_text(name: str, value: Any) -> str:
    if name in ("forum", "closed", "hidden"):
        return "on" if value else "off"
    if name == "slow_mode_seconds":
        return "off" if not value else f"{value}s"
    if name == "icon_emoji_id":
        return "(none)" if not int(value) else str(value)
    return f"{value!r}"


def settings_diff(before: Mapping[str, Any], after: Mapping[str, Any], fields: Sequence[str]) -> str:
    """The readback: every named field that actually moved, `old -> new`.

    A write Telegram accepted and then did not apply reads as "no field
    changed" here rather than as a success, which is the whole point of
    reading it back instead of reporting the call's own return.
    """
    moved = [
        f"{FLAG_OF.get(name, name)} {_setting_text(name, before.get(name))} -> {_setting_text(name, after.get(name))}"
        for name in fields
        if before.get(name) != after.get(name)
    ]
    return "; ".join(moved) if moved else "no field changed"


# -- the hierarchy rule ------------------------------------------------------


def require_hierarchy(actor: Member, *, target: Member | None, granting: Sequence[str], chat_title: str) -> None:
    """Refuse before the call when the hierarchy makes a held right unusable.

    Three cases, each `HIERARCHY_DENIED` with both rights sets named: granting
    a right the actor does not hold; editing the creator; editing an admin who
    holds a right the actor lacks, or whom Telegram marks as not editable by
    the actor. The creator is never refused here.
    """
    if actor.status == "creator":
        return
    held = set(actor.rights)
    extra = sorted(set(granting) - held)
    if extra:
        raise CommandError(
            f"You cannot grant {', '.join(extra)} in {chat_title}: an admin gives only rights it holds. "
            f"You hold {_names(held)}; the set asked for is {_names(granting)}.",
            code="HIERARCHY_DENIED",
            hint=f"Drop {', '.join(extra)} from --rights, or ask the creator to run it.",
        )
    if target is None:
        return
    if target.status == "creator":
        raise CommandError(
            f"{target.label} created {chat_title}; nobody edits the creator's rights.",
            code="HIERARCHY_DENIED",
        )
    if target.status == "admin":
        more = sorted(set(target.rights) - held)
        if more or not target.can_edit:
            why = f"holds {', '.join(more)} which you do not" if more else "was not promoted by you and Telegram marks them as not yours to edit"
            raise CommandError(
                f"You cannot edit {target.label} in {chat_title}: they {why}. "
                f"You hold {_names(held)}; they hold {_names(target.rights)}.",
                code="HIERARCHY_DENIED",
                hint="Ask the creator, or an admin who holds every right they hold.",
            )


def _names(names: Any) -> str:
    return ", ".join(sorted(names)) or "no admin right"


# -- the gates ----------------------------------------------------------------


def terminal_present() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def approval_for(kind: str, answered: bool) -> Approval:
    return Approval(kind, interactive=answered and terminal_present())


def confirm_typed_label(
    preview: str, member: Member, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> bool:
    """`delete`'s gate on a person: their exact label typed back proves which one."""
    write(preview)
    typed = read(f"Type the exact label ({member.typed_label}) to continue: ")
    return labels_match(typed, member)


# -- the screens ---------------------------------------------------------------


def format_members(rows: Sequence[Member], *, chat_title: str, what: str) -> str:
    if not rows:
        return f"No {what} in {chat_title}."
    lines = [f"{len(rows)} {what} in {chat_title}", RULE]
    for member in rows:
        line = f"{member.id:>14}  {member.status:<10} {member.label}"
        if member.rank:
            line += f"  [{member.rank}]"
        if member.rights and member.status in ("admin", "restricted"):
            line += f"  {', '.join(member.rights)}"
        if member.until:
            line += f"  until {member.until}"
        lines.append(line)
    return "\n".join(lines)


def format_requests(rows: Sequence[Mapping[str, Any]], *, chat_title: str) -> str:
    if not rows:
        return f"No pending join request in {chat_title}."
    lines = [f"{len(rows)} pending join request(s) in {chat_title}", RULE]
    for row in rows:
        about = f"  {row['about']}" if row.get("about") else ""
        lines.append(f"{row['id']:>14}  {row['date'] or '':<20} {row['label']}{about}")
    return "\n".join(lines)


def format_invites(rows: Sequence[Mapping[str, Any]], *, chat_title: str) -> str:
    """The one screen that shows links: `invite list` and `invite create` asked for them."""
    if not rows:
        return f"No invite link in {chat_title}."
    lines = [f"{len(rows)} invite link(s) in {chat_title}", RULE]
    for row in rows:
        flags = []
        if row.get("revoked"):
            flags.append("revoked")
        if row.get("request_needed"):
            flags.append("approval needed")
        if row.get("expires"):
            flags.append(f"expires {row['expires']}")
        if row.get("usage_limit"):
            flags.append(f"{row.get('usage') or 0}/{row['usage_limit']} used")
        elif row.get("usage"):
            flags.append(f"{row['usage']} used")
        title = f"  {row['title']}" if row.get("title") else ""
        lines.append(f"{row['link']}{title}  {'; '.join(flags)}".rstrip())
    return "\n".join(lines)


def format_settings(settings: Mapping[str, Any], *, chat_title: str) -> str:
    lines = [f"{chat_title}", RULE, f"Kind          {settings.get('kind')}"]
    lines.append(f"Title         {settings.get('title')}")
    lines.append(f"About         {settings.get('about') or '(none)'}")
    if "forum" in settings:
        lines.append(f"Topics        {'on' if settings.get('forum') else 'off'}")
    if "slow_mode_seconds" in settings:
        seconds = settings["slow_mode_seconds"]
        lines.append(f"Slow mode     {'off' if not seconds else f'{seconds}s'}")
    lines.append(f"Join approval {'on' if settings.get('join_request') else 'off'}")
    if "default_banned_rights" in settings:
        banned = settings["default_banned_rights"]
        lines.append(f"Members may not {', '.join(banned) if banned else '(no default restriction)'}")
    if settings.get("participants_count") is not None:
        lines.append(f"Members       {settings['participants_count']}")
    if settings.get("admins_count") is not None:
        lines.append(f"Admins        {settings['admins_count']}")
    return "\n".join(lines)


def format_topic_settings(settings: Mapping[str, Any], *, chat_title: str) -> str:
    icon = settings.get("icon_emoji_id")
    drawn = f" ({settings['icon_emoji']})" if settings.get("icon_emoji") else ""
    return "\n".join(
        [
            f"{chat_title} > {settings.get('title')} (topic {settings.get('id')})",
            RULE,
            f"Icon          {icon if icon else '(none)'}{drawn}",
            f"Closed        {'yes' if settings.get('closed') else 'no'}",
            f"Hidden        {'yes' if settings.get('hidden') else 'no'}",
        ]
    )


def format_preview(
    op: Op,
    *,
    actor: str,
    chat_title: str,
    chat_id: int,
    member: Member | None,
    details: Sequence[str],
    execute: bool | None,
    typed_what: str = "the person's exact label",
) -> str:
    """What a person reads before the gate: who acts, on whom, in which chat, and what changes."""
    lines = [f"{op.command}: {chat_title} ({chat_id})", RULE, f"As      {actor}"]
    if member is not None:
        lines.append(f"Who     {member.label} ({member.id}), now {member.status}")
        if member.rights and member.status in ("admin", "restricted"):
            lines.append(f"Holds   {', '.join(member.rights)}")
    lines.extend(details)
    lines.append(RULE)
    if execute is None:
        lines.append("Nothing has happened yet.")
    elif execute:
        lines.append(f"Executing: the next prompt asks for {typed_what}.")
    else:
        lines.append(f"Dry-run. Add --execute to do it; {typed_what} is asked for then.")
    return "\n".join(lines)


@dataclass(frozen=True)
class Outcome:
    """What a write reports: the verb, the chat, the person where there is one, and what happened."""

    command: str
    chat_id: int
    member: Member | None = None
    dry_run: bool = False
    done: bool = False
    cancelled: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command": self.command,
            "chat_id": self.chat_id,
            "dry_run": self.dry_run,
            "executed": self.done,
            "cancelled": self.cancelled,
        }
        if self.member is not None:
            payload["member"] = self.member.to_dict()
        payload.update(dict(self.extra))
        return payload


__all__ = [
    "ADMIN_RIGHT_NAMES",
    "BANNED_RIGHT_NAMES",
    "BAN_RIGHTS",
    "CHAT_FIELDS",
    "FLAG_OF",
    "GENERAL_TOPIC_ID",
    "GROUPS",
    "LIST_LIMIT",
    "MUTE_RIGHTS",
    "OPS",
    "VERB_DESTS",
    "Member",
    "Op",
    "Outcome",
    "SettingsChange",
    "TOPIC_FIELDS",
    "approval_for",
    "confirm_typed_label",
    "format_invites",
    "format_members",
    "format_preview",
    "format_requests",
    "format_settings",
    "format_topic_settings",
    "labels_match",
    "on_off",
    "op_for",
    "parse_rights",
    "parse_slow_mode",
    "parse_until",
    "settings_change",
    "settings_diff",
    "require_hierarchy",
    "terminal_present",
    "until_text",
    "user_label",
    "user_rid",
    "verbs_of",
]
