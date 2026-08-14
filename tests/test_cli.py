import pytest

from telegram_tools.cli import build_parser


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_no_subcommand_opens_interactive_menu_mode():
    args = parse_args()

    assert args.command is None


def test_discover_command_accepts_json_output():
    args = parse_args("discover", "--json", "exports/chats.json")

    assert args.command == "discover"
    assert args.json_output == "exports/chats.json"
    assert args.all_chats is False


def test_discover_command_accepts_all_chats_filter():
    args = parse_args("discover", "--all")

    assert args.command == "discover"
    assert args.all_chats is True


def test_clear_messages_defaults_to_dry_run_for_one_topic():
    args = parse_args("clear-messages", "--chat", "@group", "--topic", "123")

    assert args.command == "clear-messages"
    assert args.chat == "@group"
    assert args.topics == [123]
    assert args.all_topics is False
    assert args.execute is False
    assert args.batch_size == 100


def test_clear_messages_accepts_all_topics_in_chat_alias():
    args = parse_args("clear-messages", "--chat", "@group", "--all-topics-in-chat")

    assert args.command == "clear-messages"
    assert args.all_topics is True


def test_clear_messages_requires_topic_or_all_topics():
    with pytest.raises(SystemExit):
        parse_args("clear-messages", "--chat", "@group")


def test_clear_messages_rejects_non_positive_batch_size():
    with pytest.raises(SystemExit):
        parse_args("clear-messages", "--chat", "@group", "--topic", "123", "--batch-size", "0")


def test_search_rejects_non_positive_limit():
    with pytest.raises(SystemExit):
        parse_args("search", "--chat", "@group", "--limit", "-1")


def test_search_command_accepts_export_filters():
    args = parse_args(
        "search",
        "--chat",
        "@group",
        "--topic",
        "123",
        "--keyword",
        "deploy",
        "--from-user",
        "@alice",
        "--since",
        "2026-07-01",
        "--until",
        "2026-07-06",
        "--format",
        "csv",
        "--output",
        "exports/messages.csv",
    )

    assert args.command == "search"
    assert args.chat == "@group"
    assert args.topic == 123
    assert args.keyword == "deploy"
    assert args.from_user == "@alice"
    assert args.since == "2026-07-01"
    assert args.until == "2026-07-06"
    assert args.format == "csv"
    assert args.output == "exports/messages.csv"


def test_search_command_accepts_contains_alias():
    args = parse_args("search", "--chat", "@group", "--contains", "deploy")

    assert args.keyword == "deploy"


def test_doctor_command_parses():
    args = parse_args("doctor")

    assert args.command == "doctor"


def test_bots_command_defaults_to_listing():
    args = parse_args("bots")

    assert args.command == "bots"
    assert args.bot is None
    assert args.json_output is None
    assert args.yes is False


def test_bots_command_accepts_every_edit_flag():
    args = parse_args(
        "bots",
        "--bot", "harry",
        "--name", "Harry",
        "--bio", "Assistant",
        "--description", "Does things",
        "--commands", "cmds.json",
        "--photo", "face.png",
        "--group-rights", "delete_messages",
        "--channel-rights", "none",
        "--yes",
    )

    assert args.bot == "harry"
    assert args.name == "Harry"
    assert args.bio == "Assistant"
    assert args.description == "Does things"
    assert args.commands == "cmds.json"
    assert args.photo == "face.png"
    assert args.group_rights == "delete_messages"
    assert args.channel_rights == "none"
    assert args.yes is True


def test_bots_rights_help_lists_the_valid_right_names(capsys):
    with pytest.raises(SystemExit):
        parse_args("bots", "--help")

    help_text = capsys.readouterr().out
    assert "ban_users" in help_text
    assert "delete_messages" in help_text


def test_bots_command_rejects_commands_with_clear_commands():
    with pytest.raises(SystemExit):
        parse_args("bots", "--bot", "harry", "--commands", "cmds.json", "--clear-commands")


def test_bots_command_rejects_photo_with_remove_photo():
    with pytest.raises(SystemExit):
        parse_args("bots", "--bot", "harry", "--photo", "face.png", "--remove-photo")
