import asyncio
from types import SimpleNamespace

from telethon.tl import types

from telegram_tools.bots import EditChange, parse_rights
from telegram_tools.bot_session import apply_bot_edits
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
