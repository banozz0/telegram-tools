from __future__ import annotations

from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from telegram_tools.bots import DEFAULT_LANG_CODE, EditChange
from telegram_tools.config import Config


@asynccontextmanager
async def bot_client(config: Config, token: str):
    """Connect as the bot itself. MemorySession keeps the token off disk."""
    client = TelegramClient(MemorySession(), config.api_id, config.api_hash)
    client.flood_sleep_threshold = 24 * 60 * 60
    await client.start(bot_token=token)
    try:
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
    photo = photos[0]
    input_photo = types.InputPhoto(
        id=getattr(photo, "id"),
        access_hash=getattr(photo, "access_hash"),
        file_reference=getattr(photo, "file_reference"),
    )
    await client(functions.photos.DeletePhotosRequest(id=[input_photo]))
    return True


async def apply_bot_edits(client, changes: list[EditChange]) -> list[str]:
    applied: list[str] = []
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
