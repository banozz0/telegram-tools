import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import types

from telegram_tools.bots import EditChange, parse_rights
from telegram_tools.bot_session import apply_bot_edits, bot_client
from telegram_tools.models import BotCommandInfo


class FakeBotClient:
    def __init__(self, *, photos=()):
        self.requests = []
        self.photos = list(photos)

    async def __call__(self, request):
        self.requests.append(request)
        if type(request).__name__ == "GetUserPhotosRequest":
            return SimpleNamespace(photos=self.photos)
        return SimpleNamespace()


class FakeTelegramClient:
    """Stands in for TelegramClient so bot_client can be tested without a network."""

    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.flood_sleep_threshold = None
        self.started_with = None
        self.disconnected = False
        self.start_error = None

    async def start(self, bot_token=None):
        self.started_with = bot_token
        if self.start_error:
            raise self.start_error

    async def disconnect(self):
        self.disconnected = True


def patch_client(monkeypatch, *, start_error=None):
    created = []

    def factory(session, api_id, api_hash):
        client = FakeTelegramClient(session, api_id, api_hash)
        client.start_error = start_error
        created.append(client)
        return client

    monkeypatch.setattr("telegram_tools.bot_session.TelegramClient", factory)
    return created


def fake_config():
    from pathlib import Path

    from telegram_tools.config import Config

    return Config(api_id=1, api_hash="hash", session_path=Path("unused"))


def change(field, value, rail="bot"):
    return EditChange(field=field, rail=rail, old="", new="", value=value)


def test_apply_bot_edits_sets_commands():
    client = FakeBotClient()
    commands = [BotCommandInfo(command="start", description="Start the bot")]

    applied = asyncio.run(apply_bot_edits(client, [change("commands", commands)]))

    assert applied == ["commands"]
    request = client.requests[0]
    assert type(request).__name__ == "SetBotCommandsRequest"
    assert [command.command for command in request.commands] == ["start"]
    assert type(request.scope).__name__ == "BotCommandScopeDefault"


def test_apply_bot_edits_clears_commands():
    client = FakeBotClient()

    applied = asyncio.run(apply_bot_edits(client, [change("clear_commands", True)]))

    assert applied == ["clear_commands"]
    assert type(client.requests[0]).__name__ == "ResetBotCommandsRequest"


def test_apply_bot_edits_sets_group_and_channel_rights():
    client = FakeBotClient()
    changes = [change("group_rights", parse_rights("ban_users")), change("channel_rights", parse_rights("none"))]

    applied = asyncio.run(apply_bot_edits(client, changes))

    assert applied == ["group_rights", "channel_rights"]
    assert type(client.requests[0]).__name__ == "SetBotGroupDefaultAdminRightsRequest"
    assert type(client.requests[1]).__name__ == "SetBotBroadcastDefaultAdminRightsRequest"


def test_apply_bot_edits_removes_the_current_photo():
    photo = types.Photo(id=7, access_hash=8, file_reference=b"ref", date=None, sizes=[], dc_id=2)
    client = FakeBotClient(photos=[photo])

    applied = asyncio.run(apply_bot_edits(client, [change("remove_photo", True)]))

    assert applied == ["remove_photo"]
    assert type(client.requests[0]).__name__ == "GetUserPhotosRequest"
    delete_request = client.requests[1]
    assert type(delete_request).__name__ == "DeletePhotosRequest"
    assert delete_request.id[0].id == 7
    assert delete_request.id[0].access_hash == 8


def test_apply_bot_edits_is_a_no_op_when_there_is_no_photo_to_remove():
    client = FakeBotClient(photos=[])

    applied = asyncio.run(apply_bot_edits(client, [change("remove_photo", True)]))

    assert applied == []
    assert [type(request).__name__ for request in client.requests] == ["GetUserPhotosRequest"]


def test_bot_client_disconnects_after_a_normal_exit(monkeypatch):
    created = patch_client(monkeypatch)

    async def run():
        async with bot_client(fake_config(), "12345:AAOne") as client:
            assert client.started_with == "12345:AAOne"

    asyncio.run(run())

    assert created[0].disconnected is True


def test_bot_client_disconnects_when_start_fails(monkeypatch):
    created = patch_client(monkeypatch, start_error=RuntimeError("bad token"))

    async def run():
        async with bot_client(fake_config(), "12345:AAOne"):
            raise AssertionError("body must not run when start fails")

    with pytest.raises(RuntimeError, match="bad token"):
        asyncio.run(run())

    assert created[0].disconnected is True


def test_bot_client_keeps_the_token_out_of_the_session(monkeypatch):
    created = patch_client(monkeypatch)

    async def run():
        async with bot_client(fake_config(), "12345:AAOne"):
            pass

    asyncio.run(run())

    assert type(created[0].session).__name__ == "MemorySession"
