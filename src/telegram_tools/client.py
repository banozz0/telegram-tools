from __future__ import annotations

from telethon import TelegramClient

from telegram_tools.config import Config


def create_client(config: Config) -> TelegramClient:
    config.session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    client.flood_sleep_threshold = 24 * 60 * 60
    return client
