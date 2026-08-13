import json

import pytest

from telegram_tools.bots import parse_commands_file, parse_rights, right_names, rights_to_names
from telegram_tools.models import BotCommandInfo, BotInfo


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
