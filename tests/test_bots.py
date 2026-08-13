import asyncio
import json
from types import SimpleNamespace

import pytest

from telegram_tools.bots import parse_commands_file, parse_rights, right_names, rights_to_names
from telegram_tools.bots import (
    ResolvedBot,
    format_bot_profile,
    format_bot_table,
    format_edit_heading,
    get_bot_profile,
    list_bots,
    resolve_bot,
)
from telegram_tools.models import BotCommandInfo, BotInfo
from telegram_tools.resolver import EntityResolutionError


def test_right_names_come_from_telethon_and_include_the_common_flags():
    names = right_names()

    assert "delete_messages" in names
    assert "ban_users" in names
    assert "self" not in names


def test_parse_rights_enables_only_the_named_rights():
    rights = parse_rights("delete_messages, ban_users")

    assert rights.delete_messages is True
    assert rights.ban_users is True
    assert rights.add_admins is False


def test_parse_rights_none_clears_everything():
    rights = parse_rights("none")

    assert all(getattr(rights, name) is False for name in right_names())


def test_parse_rights_rejects_an_unknown_right():
    with pytest.raises(ValueError, match="not_a_right"):
        parse_rights("delete_messages,not_a_right")


def test_rights_to_names_lists_only_enabled_rights():
    assert rights_to_names(parse_rights("ban_users")) == ["ban_users"]
    assert rights_to_names(None) == []


def test_parse_commands_file_reads_a_valid_file(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "/Start", "description": "Start the bot"}]))

    assert parse_commands_file(path) == [BotCommandInfo(command="start", description="Start the bot")]


def test_parse_commands_file_rejects_a_non_list(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps({"command": "start"}))

    with pytest.raises(ValueError, match="JSON list"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_a_bad_command_name(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "not a command", "description": "nope"}]))

    with pytest.raises(ValueError, match="a-z"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_an_empty_description(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "start", "description": ""}]))

    with pytest.raises(ValueError, match="description"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_duplicates(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([
        {"command": "start", "description": "one"},
        {"command": "start", "description": "two"},
    ]))

    with pytest.raises(ValueError, match="more than once"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_more_than_100_entries(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": f"cmd{index}", "description": "x"} for index in range(101)]))

    with pytest.raises(ValueError, match="at most 100"):
        parse_commands_file(path)


def test_bot_info_to_dict_round_trips_nested_commands():
    info = BotInfo(
        id=12345,
        username="harrybot",
        name="Harry",
        bio="Assistant",
        description="Does things",
        is_owned=True,
        has_photo=True,
        commands=[BotCommandInfo(command="start", description="Start the bot")],
        group_rights=["delete_messages"],
        channel_rights=[],
    )

    assert info.to_dict() == {
        "id": 12345,
        "username": "harrybot",
        "name": "Harry",
        "bio": "Assistant",
        "description": "Does things",
        "is_owned": True,
        "has_photo": True,
        "commands": [{"command": "start", "description": "Start the bot"}],
        "group_rights": ["delete_messages"],
        "channel_rights": [],
    }


def fake_bot_user(bot_id=12345, username="harrybot", first_name="Harry"):
    return SimpleNamespace(id=bot_id, username=username, first_name=first_name, bot=True, photo=None, usernames=None)


class FakeClient:
    """Stands in for TelegramClient: records TL requests and replays canned answers."""

    def __init__(self, *, admined=None, full=None):
        self.admined = admined if admined is not None else []
        self.full = full
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        name = type(request).__name__
        if name == "GetAdminedBotsRequest":
            return self.admined
        if name == "GetFullUserRequest":
            return self.full
        raise AssertionError(f"unexpected request {name}")

    async def get_input_entity(self, entity):
        return SimpleNamespace(user_id=entity.id)


def fake_full_user(*, about="Assistant", description="Does things", commands=(), group_rights=None, channel_rights=None, photo=None):
    bot_info = SimpleNamespace(
        description=description,
        commands=[SimpleNamespace(command=command, description=text) for command, text in commands],
    )
    return SimpleNamespace(
        full_user=SimpleNamespace(
            about=about,
            bot_info=bot_info,
            bot_group_admin_rights=group_rights,
            bot_broadcast_admin_rights=channel_rights,
            profile_photo=photo,
        )
    )


def test_list_bots_maps_admined_bots_to_bot_info():
    client = FakeClient(admined=[fake_bot_user()])

    bots = asyncio.run(list_bots(client))

    assert [bot.id for bot in bots] == [12345]
    assert bots[0].username == "harrybot"
    assert bots[0].name == "Harry"
    assert bots[0].is_owned is True


def test_list_bots_accepts_a_boxed_users_result():
    client = FakeClient(admined=SimpleNamespace(users=[fake_bot_user()]))

    assert [bot.id for bot in asyncio.run(list_bots(client))] == [12345]


def test_resolve_bot_matches_an_owned_bot_by_username_case_insensitively():
    client = FakeClient(admined=[fake_bot_user()])

    resolved = asyncio.run(resolve_bot(client, "@HarryBot"))

    assert resolved.is_owned is True
    assert resolved.user.id == 12345


def test_resolve_bot_matches_an_owned_bot_by_numeric_id():
    client = FakeClient(admined=[fake_bot_user()])

    assert asyncio.run(resolve_bot(client, 12345)).is_owned is True


def test_resolve_bot_falls_back_to_entity_lookup_for_a_bot_you_do_not_own(monkeypatch):
    client = FakeClient(admined=[])
    other = SimpleNamespace(id=999, username="otherbot", first_name="Other", bot=True)

    async def fake_resolve_chat(_client, _reference):
        return SimpleNamespace(id=999, entity=other, input_entity=SimpleNamespace(user_id=999))

    monkeypatch.setattr("telegram_tools.bots.resolve_chat", fake_resolve_chat)
    resolved = asyncio.run(resolve_bot(client, "@otherbot"))

    assert resolved.is_owned is False
    assert resolved.user.id == 999


def test_resolve_bot_rejects_a_reference_that_is_not_a_bot(monkeypatch):
    client = FakeClient(admined=[])
    human = SimpleNamespace(id=42, username="sven", first_name="Sven", bot=False)

    async def fake_resolve_chat(_client, _reference):
        return SimpleNamespace(id=42, entity=human, input_entity=SimpleNamespace(user_id=42))

    monkeypatch.setattr("telegram_tools.bots.resolve_chat", fake_resolve_chat)

    with pytest.raises(EntityResolutionError, match="not a bot"):
        asyncio.run(resolve_bot(client, "@sven"))


def test_get_bot_profile_reads_bio_description_commands_and_rights():
    from telegram_tools.bots import parse_rights

    user = fake_bot_user()
    client = FakeClient(
        full=fake_full_user(
            commands=[("start", "Start the bot")],
            group_rights=parse_rights("delete_messages"),
            photo=SimpleNamespace(id=1),
        )
    )
    resolved = ResolvedBot(user=user, input_user=SimpleNamespace(user_id=12345), is_owned=True)

    profile = asyncio.run(get_bot_profile(client, resolved))

    assert profile.bio == "Assistant"
    assert profile.description == "Does things"
    assert profile.commands == [BotCommandInfo(command="start", description="Start the bot")]
    assert profile.group_rights == ["delete_messages"]
    assert profile.channel_rights == []
    assert profile.has_photo is True


def test_format_bot_table_lists_ids_and_usernames():
    bots = [BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

    output = format_bot_table(bots)

    assert "12345" in output
    assert "@harrybot" in output
    assert "Harry" in output


def test_format_bot_table_handles_no_bots():
    assert format_bot_table([]) == "No bots found."


def test_format_edit_heading_names_the_bot_and_falls_back_to_the_id():
    named = BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)
    unnamed = BotInfo(id=12345, username=None, name="Harry", bio=None, description=None, is_owned=True)

    assert format_edit_heading(named) == "Editing @harrybot (12345)"
    assert format_edit_heading(unnamed) == "Editing bot 12345"


def test_format_bot_profile_marks_a_bot_you_do_not_own():
    bot = BotInfo(id=999, username="otherbot", name="Other", bio=None, description=None, is_owned=False)

    output = format_bot_profile(bot)

    assert "not owned by you" in output
    assert "(not set)" in output
