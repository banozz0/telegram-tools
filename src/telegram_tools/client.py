from __future__ import annotations

import sqlite3

from telethon import TelegramClient

from telegram_tools.config import Config

# Telethon's SQLite session raises this exact wording when a second client opens
# the same session file. It is the one lock message that means "someone else has
# it", rather than a corrupt or missing database.
_LOCKED = "database is locked"


class SessionInUseError(RuntimeError):
    """A second client tried to open a session file another one already holds."""

    envelope_code = "SESSION_IN_USE"
    envelope_hint = "Close the other telegram-tools - a menu open in another terminal counts."


def create_client(config: Config) -> TelegramClient:
    config.session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    client.flood_sleep_threshold = 24 * 60 * 60
    return client


async def start_client(client):
    """Start `client`, turning a held session file into an answer, not a traceback.

    One session file is one connection. Running the menu in one terminal and a
    command in another is the ordinary way to hit this, and the raw
    `sqlite3.OperationalError` it produces says nothing about how to fix it.
    """
    try:
        await client.start()
    except sqlite3.OperationalError as exc:
        # The lock is hit *after* the socket is up, so this client is connected and
        # its read/write tasks are running. Without this disconnect the clean message
        # below arrives buried in "Task was destroyed but it is pending!" on exit,
        # which is the noise it exists to replace.
        await _disconnect_quietly(client)
        if _LOCKED not in str(exc).lower():
            raise
        raise SessionInUseError(
            "Another telegram-tools is already using the login session. "
            "Close the other one - a menu open in another terminal counts - and try again."
        ) from exc
    return client


async def _disconnect_quietly(client) -> None:
    try:
        result = client.disconnect()
        if result is not None:
            await result
    except Exception:
        # Already failing; a teardown error here would replace the real cause.
        pass
