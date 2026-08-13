from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon.tl import functions, types

from telegram_tools.models import BotCommandInfo, BotInfo
from telegram_tools.resolver import EntityResolutionError, resolve_chat

DEFAULT_LANG_CODE = ""
MAX_COMMANDS = 100
COMMAND_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


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
    return [name for name in right_names() if getattr(rights, name, False)]


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
