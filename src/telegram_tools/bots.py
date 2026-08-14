from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon.tl import functions, types

from telegram_tools.models import BotCommandInfo, BotInfo
from telegram_tools.resolver import EntityResolutionError, resolve_chat

DEFAULT_LANG_CODE = ""
MAX_COMMANDS = 100
COMMAND_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")

# Telegram sets this flag implicitly on any non-empty admin-rights set; it is never a
# right the user chose, so it is dropped from both the profile display and the edit-plan
# comparison. `right_names()`/`parse_rights` still treat it as a normal, valid name.
IMPLICIT_OTHER_RIGHT = "other"


def right_names() -> list[str]:
    parameters = inspect.signature(types.ChatAdminRights.__init__).parameters
    return [name for name in parameters if name != "self"]


def parse_rights(raw: str) -> types.ChatAdminRights:
    valid = right_names()
    value = raw.strip().lower()
    if value in {"", "none"}:
        return types.ChatAdminRights(**{name: False for name in valid})

    selected = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in selected if item not in valid]
    if unknown:
        raise ValueError(f"Unknown admin right(s): {', '.join(unknown)}. Valid names: {', '.join(valid)}, or none.")
    return types.ChatAdminRights(**{name: name in selected for name in valid})


def rights_to_names(rights: Any) -> list[str]:
    if rights is None:
        return []
    return [name for name in right_names() if name != IMPLICIT_OTHER_RIGHT and getattr(rights, name, False)]


def parse_commands_file(path: str | Path) -> list[BotCommandInfo]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError("Commands file must contain a JSON list of {command, description} objects.")
    if len(raw) > MAX_COMMANDS:
        raise ValueError(f"Commands file has {len(raw)} entries; Telegram allows at most {MAX_COMMANDS}.")

    commands: list[BotCommandInfo] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Command {index} must be an object with 'command' and 'description'.")
        command = str(entry.get("command", "")).strip().lstrip("/").lower()
        description = str(entry.get("description", "")).strip()
        if not COMMAND_PATTERN.match(command):
            raise ValueError(f"Command {index} ({command!r}) must be 1-32 characters of a-z, 0-9 or _.")
        if not 1 <= len(description) <= 256:
            raise ValueError(f"Command {index} (/{command}) needs a description of 1-256 characters.")
        if command in seen:
            raise ValueError(f"Command /{command} is listed more than once.")
        seen.add(command)
        commands.append(BotCommandInfo(command=command, description=description))
    return commands


@dataclass(frozen=True)
class ResolvedBot:
    user: Any
    input_user: Any
    is_owned: bool


def _users_from_result(result: Any) -> list[Any]:
    if isinstance(result, list):
        return list(result)
    return list(getattr(result, "users", []) or [])


def _bot_keys(user: Any) -> set[str]:
    keys = {str(int(getattr(user, "id")))}
    username = getattr(user, "username", None)
    if username:
        keys.add(str(username).lower())
    for extra in getattr(user, "usernames", None) or []:
        name = getattr(extra, "username", None)
        if name:
            keys.add(str(name).lower())
    return keys


def bot_info_from_user(user: Any, *, is_owned: bool, **fields: Any) -> BotInfo:
    return BotInfo(
        id=int(getattr(user, "id")),
        username=getattr(user, "username", None),
        name=str(getattr(user, "first_name", "") or ""),
        bio=fields.get("bio"),
        description=fields.get("description"),
        is_owned=is_owned,
        has_photo=bool(fields.get("has_photo", getattr(user, "photo", None) is not None)),
        commands=list(fields.get("commands") or []),
        group_rights=list(fields.get("group_rights") or []),
        channel_rights=list(fields.get("channel_rights") or []),
    )


async def list_admined_bots(client) -> list[Any]:
    return _users_from_result(await client(functions.bots.GetAdminedBotsRequest()))


async def list_bots(client) -> list[BotInfo]:
    return [bot_info_from_user(user, is_owned=True) for user in await list_admined_bots(client)]


async def resolve_bot(client, reference: str | int) -> ResolvedBot:
    key = str(reference).strip().lstrip("@").lower()
    for user in await list_admined_bots(client):
        if key in _bot_keys(user):
            return ResolvedBot(user=user, input_user=await client.get_input_entity(user), is_owned=True)

    resolved = await resolve_chat(client, reference)
    if not getattr(resolved.entity, "bot", False):
        raise EntityResolutionError(f"{reference!r} is not a bot.")
    return ResolvedBot(user=resolved.entity, input_user=resolved.input_entity, is_owned=False)


async def get_bot_profile(client, resolved: ResolvedBot) -> BotInfo:
    result = await client(functions.users.GetFullUserRequest(id=resolved.input_user))
    full_user = getattr(result, "full_user", None)
    bot_info = getattr(full_user, "bot_info", None)
    commands = [
        BotCommandInfo(
            command=str(getattr(command, "command", "")),
            description=str(getattr(command, "description", "")),
        )
        for command in (getattr(bot_info, "commands", None) or [])
    ]
    return bot_info_from_user(
        resolved.user,
        is_owned=resolved.is_owned,
        bio=getattr(full_user, "about", None),
        description=getattr(bot_info, "description", None),
        commands=commands,
        group_rights=rights_to_names(getattr(full_user, "bot_group_admin_rights", None)),
        channel_rights=rights_to_names(getattr(full_user, "bot_broadcast_admin_rights", None)),
        has_photo=getattr(full_user, "profile_photo", None) is not None or getattr(resolved.user, "photo", None) is not None,
    )


def _or_not_set(value: str | None) -> str:
    return value if value else "(not set)"


def format_bot_table(bots: list[BotInfo]) -> str:
    if not bots:
        return "No bots found."

    width = max(len(str(bot.id)) for bot in bots)
    lines = ["My Bots", "=" * len("My Bots")]
    for bot in bots:
        username = f"@{bot.username}" if bot.username else "(no username)"
        lines.append(f"{bot.id:<{width}}  {username}  {bot.name}")
    return "\n".join(lines)


def format_bot_profile(bot: BotInfo) -> str:
    lines = [
        bot.name or "(unnamed)",
        f"Bot ID: {bot.id}",
        f"Username: @{bot.username}" if bot.username else "Username: (none)",
        f"Bio: {_or_not_set(bot.bio)}",
        f"Description: {_or_not_set(bot.description)}",
        f"Profile photo: {'set' if bot.has_photo else 'not set'}",
        f"Default group rights: {', '.join(bot.group_rights) or '(none)'}",
        f"Default channel rights: {', '.join(bot.channel_rights) or '(none)'}",
    ]
    if not bot.is_owned:
        lines.append("Note: not owned by you - read-only.")

    lines.extend(["", "Commands", "--------------------------------------------"])
    if bot.commands:
        width = max(len(command.command) for command in bot.commands)
        lines.extend(f"/{command.command:<{width}}  {command.description}" for command in bot.commands)
    else:
        lines.append("(none)")
    return "\n".join(lines)


_DISPLAY_WIDTH = 60


@dataclass(frozen=True)
class EditChange:
    field: str
    rail: str
    old: str
    new: str
    value: Any


@dataclass(frozen=True)
class EditPlan:
    changes: list[EditChange]
    skipped: list[str]

    @property
    def owner_changes(self) -> list[EditChange]:
        return [change for change in self.changes if change.rail == "owner"]

    @property
    def bot_changes(self) -> list[EditChange]:
        return [change for change in self.changes if change.rail == "bot"]

    @property
    def is_empty(self) -> bool:
        return not self.changes


def _truncate(value: str) -> str:
    return value if len(value) <= _DISPLAY_WIDTH else f"{value[:_DISPLAY_WIDTH]}..."


def _commands_display(commands: list[BotCommandInfo]) -> str:
    return ", ".join(f"/{command.command}" for command in commands) or "(none)"


def build_edit_plan(current: BotInfo, requested: Mapping[str, Any]) -> EditPlan:
    changes: list[EditChange] = []
    skipped: list[str] = []

    def add(field: str, rail: str, old: str, new: str, value: Any, *, changed: bool) -> None:
        if changed:
            changes.append(EditChange(field=field, rail=rail, old=old, new=new, value=value))
        else:
            skipped.append(field)

    for field_name, existing in (("name", current.name), ("bio", current.bio), ("description", current.description)):
        if field_name in requested:
            new_value = str(requested[field_name])
            add(field_name, "owner", _or_not_set(existing), new_value, new_value, changed=new_value != (existing or ""))

    if "photo" in requested:
        add("photo", "owner", "set" if current.has_photo else "not set", str(requested["photo"]), requested["photo"], changed=True)

    if requested.get("remove_photo"):
        add("remove_photo", "bot", "set" if current.has_photo else "not set", "not set", True, changed=current.has_photo)

    if "commands" in requested:
        new_commands = list(requested["commands"])
        changed = [(command.command, command.description) for command in new_commands] != [
            (command.command, command.description) for command in current.commands
        ]
        add("commands", "bot", _commands_display(current.commands), _commands_display(new_commands), new_commands, changed=changed)

    if requested.get("clear_commands"):
        add("clear_commands", "bot", _commands_display(current.commands), "(none)", True, changed=bool(current.commands))

    for field_name, existing_names in (("group_rights", current.group_rights), ("channel_rights", current.channel_rights)):
        if field_name in requested:
            rights = requested[field_name]
            new_names = rights_to_names(rights)
            add(
                field_name,
                "bot",
                ", ".join(existing_names) or "(none)",
                ", ".join(new_names) or "(none)",
                rights,
                changed=sorted(new_names) != sorted(existing_names),
            )

    return EditPlan(changes=changes, skipped=skipped)


def format_edit_heading(bot: BotInfo) -> str:
    """Name the bot the diff belongs to, so a y/N is never answered blind."""
    return f"Editing @{bot.username} ({bot.id})" if bot.username else f"Editing bot {bot.id}"


def format_edit_plan(plan: EditPlan) -> str:
    lines = ["Changes", "--------------------------------------------"]
    lines.extend(f"{change.field}: {_truncate(change.old)} -> {_truncate(change.new)}" for change in plan.changes)
    if plan.skipped:
        lines.append(f"Unchanged (skipped): {', '.join(plan.skipped)}")
    return "\n".join(lines)


def confirm_bot_edits(plan: EditPlan, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    write(format_edit_plan(plan))
    answer = read("Apply these changes? [y/N]: ").strip().lower()
    if not answer:
        # A stray newline left in the terminal buffer reads as an empty answer, which
        # would otherwise print a bare cancel result indistinguishable from a typed `y`
        # being ignored.
        write("No answer read - cancelled.")
        return False
    return answer == "y"


async def apply_owner_edits(client, input_user, changes: list[EditChange], applied: list[str] | None = None) -> list[str]:
    # `applied` is caller-owned on purpose: a returned list is lost with the frame when a
    # later write raises, and the CLI prints this list as the record of what reached
    # Telegram before the failure.
    applied = [] if applied is None else applied
    info_fields = {change.field: change.value for change in changes if change.field in {"name", "bio", "description"}}
    if info_fields:
        await client(
            functions.bots.SetBotInfoRequest(
                bot=input_user,
                lang_code=DEFAULT_LANG_CODE,
                name=info_fields.get("name"),
                about=info_fields.get("bio"),
                description=info_fields.get("description"),
            )
        )
        applied.extend(sorted(info_fields))

    for change in changes:
        if change.field != "photo":
            continue
        uploaded = await client.upload_file(change.value)
        await client(functions.photos.UploadProfilePhotoRequest(bot=input_user, file=uploaded))
        applied.append("photo")

    return applied
