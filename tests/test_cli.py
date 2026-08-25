import asyncio
import pytest
from types import SimpleNamespace

from telegram_tools import cli
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


def test_run_uses_a_passed_client_and_leaves_it_connected(monkeypatch):
    seen = {}

    async def fake_discover(client, args):
        seen["client"] = client
        return 0

    def fail_create_client(_config):
        raise AssertionError("run must not create a client when it is given one")

    monkeypatch.setattr(cli, "_run_discover", fake_discover)
    monkeypatch.setattr(cli, "create_client", fail_create_client)

    client = SimpleNamespace(disconnect=lambda: (_ for _ in ()).throw(AssertionError("must not disconnect")))
    args = SimpleNamespace(command="discover", json_output=None, all_chats=False, admin_only=True)

    assert asyncio.run(cli.run(args, client=client, config=SimpleNamespace(bot_tokens={}))) == 0
    assert seen["client"] is client


def test_run_without_a_client_creates_and_disconnects_one(monkeypatch):
    events = []

    class FakeClient:
        async def start(self):
            events.append("start")

        async def disconnect(self):
            events.append("disconnect")

    async def fake_discover(_client, _args):
        events.append("discover")
        return 0

    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(bot_tokens={}))
    monkeypatch.setattr(cli, "create_client", lambda _config: FakeClient())
    monkeypatch.setattr(cli, "_run_discover", fake_discover)

    args = SimpleNamespace(command="discover", json_output=None, all_chats=False, admin_only=True)

    assert asyncio.run(cli.run(args)) == 0
    assert events == ["start", "discover", "disconnect"]


def test_run_doctor_never_loads_config_or_connects(monkeypatch):
    monkeypatch.setattr(cli, "run_doctor", lambda: 0)
    monkeypatch.setattr(cli, "load_config", lambda: (_ for _ in ()).throw(AssertionError("no config for doctor")))
    monkeypatch.setattr(cli, "create_client", lambda _config: (_ for _ in ()).throw(AssertionError("no client for doctor")))

    assert asyncio.run(cli.run(SimpleNamespace(command="doctor"))) == 0


def test_main_prints_help_instead_of_a_menu_without_a_tty(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    assert cli.main([]) == 0

    printed = capsys.readouterr().out
    assert "usage: telegram-tools" in printed
    assert "clear-messages" in printed


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_main_exits_130_on_a_keyboard_interrupt(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli.asyncio, "run", lambda _coro: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert cli.main([]) == 130


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_main_exits_130_when_input_ends(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli.asyncio, "run", lambda _coro: (_ for _ in ()).throw(EOFError()))

    assert cli.main([]) == 130


def test_send_parses_a_chat_topic_and_text():
    args = parse_args("send", "--chat", "@group", "--topic", "141", "--text", "ship it")

    assert args.command == "send"
    assert args.chat == "@group"
    assert args.topic == 141
    assert args.text == "ship it"
    assert args.yes is False


def test_send_requires_a_chat():
    with pytest.raises(SystemExit):
        parse_args("send", "--text", "hi")


def test_send_text_is_optional_at_parse_time_because_a_file_can_carry_it():
    # Whether there is anything to send is decided in _run_send, where --file is
    # visible too; argparse cannot express "one of these two".
    args = parse_args("send", "--chat", "@group")

    assert args.text is None
    assert args.files is None


def test_send_collects_repeated_file_flags():
    args = parse_args("send", "--chat", "@group", "--file", "a.png", "--file", "b.pdf")

    assert args.files == ["a.png", "b.pdf"]


def test_create_without_a_kind_parses_but_names_none():
    args = parse_args("create")

    assert args.command == "create"
    assert args.create_kind is None


def test_create_group_defaults_to_a_plain_supergroup():
    args = parse_args("create", "group", "--title", "Hermes")

    assert args.create_kind == "group"
    assert args.title == "Hermes"
    assert args.about is None
    assert args.forum is False
    assert args.yes is False


def test_create_group_accepts_forum():
    args = parse_args("create", "group", "--title", "Hermes", "--about", "agency", "--forum")

    assert args.forum is True
    assert args.about == "agency"


def test_create_channel_parses_a_title():
    args = parse_args("create", "channel", "--title", "Alerts")

    assert args.create_kind == "channel"
    assert args.title == "Alerts"


def test_create_topic_requires_a_chat_and_title():
    args = parse_args("create", "topic", "--chat", "@group", "--title", "Deploys")

    assert args.create_kind == "topic"
    assert args.chat == "@group"
    assert args.title == "Deploys"

    with pytest.raises(SystemExit):
        parse_args("create", "topic", "--title", "Deploys")
