from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

from telethon.tl import types

from telegram_tools.models import BotCommandInfo, BotInfo

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
