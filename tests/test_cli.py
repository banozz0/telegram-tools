import pytest

from telegram_tools.cli import build_parser


def parse_args(*args: str):
    return build_parser().parse_args(list(args))


def test_discover_command_accepts_json_output():
    args = parse_args("discover", "--json", "exports/chats.json")

    assert args.command == "discover"
    assert args.json_output == "exports/chats.json"


def test_delete_defaults_to_dry_run_for_one_topic():
    args = parse_args("delete", "--chat", "@group", "--topic", "123")

    assert args.command == "delete"
    assert args.chat == "@group"
    assert args.topics == [123]
    assert args.all_topics is False
    assert args.execute is False
    assert args.batch_size == 100


def test_delete_requires_topic_or_all_topics():
    with pytest.raises(SystemExit):
        parse_args("delete", "--chat", "@group")


def test_delete_rejects_non_positive_batch_size():
    with pytest.raises(SystemExit):
        parse_args("delete", "--chat", "@group", "--topic", "123", "--batch-size", "0")


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
