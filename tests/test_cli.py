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


def test_bot_inventory_command_accepts_local_bots_json():
    args = parse_args("bot-inventory", "--bots-json", "bots.json")

    assert args.command == "bot-inventory"
    assert args.bots_json == "bots.json"


def test_bot_add_command_accepts_local_bots_json():
    args = parse_args("bot-add", "--bots-json", "bots.json")

    assert args.command == "bot-add"
    assert args.bots_json == "bots.json"


def test_doctor_command_accepts_local_paths():
    args = parse_args("doctor", "--bots-json", "bots.json")

    assert args.command == "doctor"
    assert args.bots_json == "bots.json"
