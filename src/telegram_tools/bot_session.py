from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient, utils
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from telegram_tools.bots import DEFAULT_LANG_CODE, EditChange
from telegram_tools.config import Config


@asynccontextmanager
async def bot_client(config: Config, token: str) -> AsyncIterator[TelegramClient]:
    """Connect as the bot itself. MemorySession keeps the token off disk."""
    client = TelegramClient(MemorySession(), config.api_id, config.api_hash)
    client.flood_sleep_threshold = 24 * 60 * 60
    try:
        await client.start(bot_token=token)
        yield client
    finally:
        await client.disconnect()


async def _remove_photo(client) -> bool:
    result = await client(
        functions.photos.GetUserPhotosRequest(user_id=types.InputUserSelf(), offset=0, max_id=0, limit=1)
    )
    photos = list(getattr(result, "photos", []) or [])
    if not photos:
        return False
    await client(functions.photos.DeletePhotosRequest(id=[utils.get_input_photo(photos[0])]))
    return True


async def apply_bot_edits(client, changes: list[EditChange], applied: list[str] | None = None) -> list[str]:
    # Caller-owned for the same reason as apply_owner_edits: see that function's note.
    applied = [] if applied is None else applied
    for change in changes:
        if change.field == "commands":
            await client(
                functions.bots.SetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code=DEFAULT_LANG_CODE,
                    commands=[
                        types.BotCommand(command=command.command, description=command.description)
                        for command in change.value
                    ],
                )
            )
        elif change.field == "clear_commands":
            await client(
                functions.bots.ResetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code=DEFAULT_LANG_CODE,
                )
            )
        elif change.field == "group_rights":
            await client(functions.bots.SetBotGroupDefaultAdminRightsRequest(admin_rights=change.value))
        elif change.field == "channel_rights":
            await client(functions.bots.SetBotBroadcastDefaultAdminRightsRequest(admin_rights=change.value))
        elif change.field == "remove_photo":
            if not await _remove_photo(client):
                continue
        else:
            raise ValueError(f"Unknown bot-token edit: {change.field}")
        applied.append(change.field)
    return applied
