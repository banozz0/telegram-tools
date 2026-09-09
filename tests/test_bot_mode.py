"""`--as-bot`: acting as an owned bot, named beside the account it belongs to.

Spec section 5.2 and the P2 fixture row of section 21. Every run here is
against a fake bot client behind a patched `bot_client`; the token in the
fake config is a shape, not a secret, and the last assertion of the file is
that no run ever printed it.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import UserNotParticipantError

from telegram_tools import cli
from telegram_tools import profiles as profile_store
from telegram_tools._core import redaction
from telegram_tools._core.contract import validate_envelope
from telegram_tools.adapters.bot import BotPermissions, bot_label, is_username_reference, resolve_chat_as_bot
from telegram_tools.envelope import CommandError, Reporter, account_command
from telegram_tools.resolver import EntityResolutionError

ACCOUNT = SimpleNamespace(id=42, first_name="Sven", username="sven")
BOT = SimpleNamespace(id=98765, first_name="Alerts", username="alertsbot", bot=True)
# A token-shaped string with the bot's id in front, which is the only part the
# tool reads. Never a real one.
BOT_TOKEN_SAMPLE = "98765:AAExampleBotModeTokenValueXYZ"
CHAT_ID = -1001234567890


def channel(chat_id=CHAT_ID, title="Agency", username="agency"):
    # A channel's own id is the peer id without the -100 prefix Telethon adds.
    channel_id = abs(chat_id) - 1000000000000
    entity = SimpleNamespace(id=channel_id, title=title, username=username, megagroup=True, forum=False, broadcast=False)
    return SimpleNamespace(id=chat_id, title=title, entity=entity, input_entity=SimpleNamespace(channel_id=channel_id))


class FakeBotClient:
    """A client signed in with a bot token: no dialog list, sees only its chats."""

    def __init__(self, chats=None, *, member=True, rights=None, me=BOT):
        self.chats = list(chats if chats is not None else [channel()])
        self.member = member
        self.me = me
        self.rights = rights if rights is not None else SimpleNamespace(is_admin=True, send_messages=True)
        self.sent = []
        self.resolved = []

    async def get_me(self):
        return self.me

    async def iter_dialogs(self):
        raise AssertionError("a bot has no dialog list; bot mode must never ask for one")
        yield  # pragma: no cover - makes this an async generator

    async def get_entity(self, reference):
        self.resolved.append(reference)
        for item in self.chats:
            if item.id == reference or f"@{getattr(item.entity, 'username', None)}" == reference:
                return item.entity
        raise LookupError(reference)

    async def get_input_entity(self, entity):
        return SimpleNamespace(channel_id=getattr(entity, "id", 0))

    async def get_peer_id(self, entity):
        return -1000000000000 - getattr(entity, "id", 0)

    async def get_permissions(self, _entity, _user):
        if not self.member:
            raise UserNotParticipantError(None)
        return self.rights

    async def send_message(self, peer, text, reply_to=None):
        self.sent.append((peer, text, reply_to))
        return SimpleNamespace(id=7001)

    async def get_messages(self, _peer, ids=None):
        return SimpleNamespace(id=ids)


class FakeAccountClient:
    """The account session, for the runs that need to learn who the account is."""

    def __init__(self):
        self.asked = 0
        self.disconnected = False

    async def get_me(self):
        self.asked += 1
        return ACCOUNT

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", f"alerts:{BOT_TOKEN_SAMPLE}")
    monkeypatch.delenv("TELEGRAM_SEND_ALLOWLIST", raising=False)
    monkeypatch.setenv("TELEGRAM_TOOLS_SESSION", str(tmp_path / "session"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` with a fake bot behind `bot_client` and a fake account behind `create_client`."""

    def run(argv, *, bot=None, account=None, capsys, isatty=False, answer=""):
        fake_bot = bot or FakeBotClient()
        fake_account = account or FakeAccountClient()
        opened = []

        @asynccontextmanager
        async def fake_bot_client(_config, token):
            opened.append(token)
            yield fake_bot

        async def started(_client, *, authorize=True):
            return fake_account

        monkeypatch.setattr(cli, "bot_client", fake_bot_client)
        monkeypatch.setattr(cli, "create_client", lambda _config: fake_account)
        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{answer}\n"),
        )
        code = cli.main(argv)
        captured = capsys.readouterr()
        fake_bot.opened_with = opened
        return code, captured.out, captured.err, fake_bot, fake_account

    return run


def envelope_of(out: str) -> dict:
    envelope = json.loads(out)
    problems = validate_envelope(envelope)
    assert problems == [], problems
    assert not redaction.find(out), redaction.find(out)
    return envelope


def record_account(home, *, label="Sven (@sven)", user_id=42):
    """What `auth` leaves behind: the profile record naming the account."""
    profile_store.record_login(profile_store.load("default", home=home), label=label, user_id=user_id)


# -- refusals, before anything connects ------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["discover"],
        ["search", "--chat", str(CHAT_ID)],
        ["bots"],
        ["clear-messages", "--chat", str(CHAT_ID), "--topic", "5"],
        ["delete", "topic", "--chat", str(CHAT_ID), "--topic", "5"],
        ["create", "group", "--title", "Nope"],
        ["create", "channel", "--title", "Nope"],
        # A folder is a shelf over an account's chat list, and a bot has none.
        ["folders", "list"],
        ["folders", "create", "--title", "Nope", "--include", str(CHAT_ID)],
        ["auth"],
    ],
)
def test_an_account_only_command_under_as_bot_refuses_before_any_connection(run_cli, monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load_config", lambda **_: (_ for _ in ()).throw(AssertionError("config was read")))
    code, out, _err, bot, account = run_cli(["--json", "--as-bot", "alerts", *argv], capsys=capsys)

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "IDENTITY_MODE_UNSUPPORTED"
    assert envelope["error"]["hint"] == " ".join(["telegram-tools", "--json", *argv])
    assert bot.opened_with == [] and account.asked == 0


def test_the_refusal_names_the_command_and_reads_the_same_in_human_mode(run_cli, capsys):
    # Human mode hands a refusal to argparse, as every ValueError has always been.
    with pytest.raises(SystemExit) as raised:
        run_cli(["--as-bot", "alerts", "discover"], capsys=capsys)

    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "`discover` needs the account" in err
    assert "Run it without --as-bot" in err


def test_account_command_strips_the_flag_in_both_spellings():
    assert account_command(["--json", "--as-bot", "alerts", "discover"]) == "telegram-tools --json discover"
    assert account_command(["--as-bot=alerts", "send", "--chat", "x"]) == "telegram-tools send --chat x"


def test_a_bare_as_bot_gets_no_menu(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    with pytest.raises(SystemExit) as raised:
        cli.main(["--as-bot", "alerts"])

    assert raised.value.code == 2
    assert "bot mode has no menu" in capsys.readouterr().err


def test_an_unknown_nickname_refuses_by_code_without_opening_a_bot(run_cli, capsys):
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "nobody", "send", "--chat", str(CHAT_ID), "--text", "hi", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "CONFIG_MISSING")
    assert "nobody" in envelope["error"]["message"]
    assert bot.opened_with == []


def test_a_token_whose_bot_is_not_the_one_telegram_signs_in_refuses(run_cli, home, capsys):
    record_account(home)
    stranger = FakeBotClient(me=SimpleNamespace(id=11111, first_name="Other", username="otherbot"))
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "hi", "--yes"],
        bot=stranger,
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "IDENTITY_MISMATCH")
    assert bot.sent == []


# -- the identity, the banner and the envelope -----------------------------


def test_a_bot_mode_send_names_the_bot_and_the_account_everywhere(run_cli, home, monkeypatch, capsys):
    record_account(home)
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    code, out, err, bot, account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["identity"] == {
        "platform": "telegram",
        "mode": "bot",
        "label": "@alertsbot",
        "id": "tg:bot:98765",
        "profile": "default",
        "via": "tg:user:42",
    }
    assert envelope["args"]["as_bot"] == "alerts"
    assert envelope["plan"]["approval"] == "yes_allowlist"
    assert envelope["plan"]["preflight"]["held"] == ["is_admin", "send_messages"]
    assert bot.sent == [(bot.chats[0].input_entity, "ship it", None)]
    # The banner, on stderr under --json, names both identities.
    assert f"Acting as: @alertsbot · bot (via Sven (@sven)) · Target: Agency ({CHAT_ID})" in err
    # The account's record answered for it; its session was never opened.
    assert account.asked == 0
    assert bot.opened_with == [BOT_TOKEN_SAMPLE]

    lines = (home / ".telegram-tools" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["identity"]["mode"] == "bot" and line["identity"]["via"] == "tg:user:42"
    assert BOT_TOKEN_SAMPLE not in lines[0] and BOT_TOKEN_SAMPLE not in out and BOT_TOKEN_SAMPLE not in err
    assert not redaction.find(lines[0])


def test_the_preview_names_the_bot_and_the_account(run_cli, home, capsys):
    record_account(home)
    code, out, err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it"],
        capsys=capsys,
        isatty=True,
        answer="y",
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert "Sending as @alertsbot (via Sven (@sven))" in err
    assert len(bot.sent) == 1


def test_a_profile_with_no_record_asks_the_account_once(run_cli, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    code, out, err, _bot, account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["identity"]["via"]) == (0, "tg:user:42")
    assert account.asked == 1 and account.disconnected is True
    assert "(via Sven (@sven))" in err


def test_a_bot_mode_create_topic_runs_as_the_bot(run_cli, home, capsys):
    record_account(home)
    forum = channel(title="Forum")
    forum.entity.forum = True

    class ForumBot(FakeBotClient):
        async def __call__(self, request):
            return SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=77, action=SimpleNamespace(title="Deploys")))])

    bot = ForumBot([forum])
    code, out, err, _bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "create", "topic", "--chat", str(CHAT_ID), "--title", "Deploys", "--yes"],
        bot=bot,
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert code == 0, envelope
    assert envelope["identity"]["mode"] == "bot"
    assert envelope["command"] == "create topic"
    assert "Acting as: @alertsbot · bot (via Sven (@sven))" in err


# -- what a bot may reach ---------------------------------------------------


def test_a_send_to_a_chat_the_bot_is_not_in_refuses_before_the_preview(run_cli, home, capsys):
    record_account(home)
    outsider = FakeBotClient(member=False)
    code, out, err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it"],
        bot=outsider,
        capsys=capsys,
        isatty=True,
        answer="y",
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "PERMISSION_DENIED")
    assert "@alertsbot is not a member" in envelope["error"]["message"]
    assert "Sending as" not in err
    assert bot.sent == []


def test_a_bot_mode_yes_send_is_still_gated_by_the_allowlist(run_cli, home, capsys):
    record_account(home)
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "NOT_ALLOWLISTED")
    assert bot.sent == []


def test_a_missing_right_refuses_the_bot_by_name(run_cli, home, monkeypatch, capsys):
    record_account(home)
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    muted = FakeBotClient(rights=SimpleNamespace(is_admin=False, send_messages=False))
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"],
        bot=muted,
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "PERMISSION_DENIED")
    assert bot.sent == []


def test_a_username_resolves_without_a_dialog_list(run_cli, home, monkeypatch, capsys):
    record_account(home)
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", "@agency")
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", "@agency", "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    # Resolved for the preview and again for the drift check, by name both times.
    assert bot.resolved == ["@agency", "@agency"]


def test_a_link_is_refused_under_bot_mode(run_cli, home, capsys):
    record_account(home)
    code, out, _err, bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", "https://t.me/agency", "--text", "ship it", "--yes"],
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "TARGET_NOT_FOUND")
    assert "id or @username only" in envelope["error"]["message"]
    assert bot.resolved == []


def test_a_chat_the_bot_cannot_see_is_not_found(run_cli, home, capsys):
    record_account(home)
    code, out, _err, _bot, _account = run_cli(
        ["--json", "--as-bot", "alerts", "send", "--chat", "-1009999999999", "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "TARGET_NOT_FOUND")
    assert "only sees chats it has been added to" in envelope["error"]["message"]


# -- the adapter on its own -------------------------------------------------


def test_bot_label_is_the_username_or_the_id():
    assert bot_label(BOT) == "@alertsbot"
    assert bot_label(SimpleNamespace(id=5, username=None)) == "bot 5"


@pytest.mark.parametrize(
    ("reference", "expected"),
    [("@agency", True), ("agency", True), ("-1001234567890", False), ("t.me/agency", False), ("abc", False), ("", False)],
)
def test_username_references_are_names_not_numbers_or_links(reference, expected):
    assert is_username_reference(reference) is expected


def test_resolve_chat_as_bot_takes_ids_and_usernames_only():
    import asyncio

    bot = FakeBotClient()
    resolved = asyncio.run(resolve_chat_as_bot(bot, CHAT_ID))
    assert resolved.id == CHAT_ID
    resolved = asyncio.run(resolve_chat_as_bot(bot, "agency"))
    assert resolved.id == CHAT_ID
    with pytest.raises(CommandError) as refused:
        asyncio.run(resolve_chat_as_bot(bot, "https://t.me/+abc"))
    assert refused.value.code == "TARGET_NOT_FOUND"
    with pytest.raises(EntityResolutionError):
        asyncio.run(resolve_chat_as_bot(bot, "@unknownchat"))


def test_bot_permissions_turn_not_a_participant_into_a_named_refusal():
    import asyncio

    with pytest.raises(CommandError) as refused:
        asyncio.run(BotPermissions(FakeBotClient(member=False), BOT).probe(object()))
    assert refused.value.code == "PERMISSION_DENIED"
    assert "@alertsbot" in str(refused.value)

    rights = asyncio.run(BotPermissions(FakeBotClient(), BOT).probe(object()))
    assert rights.held == frozenset({"is_admin", "send_messages"})


def test_the_reporter_banner_carries_the_via_only_in_bot_mode():
    from telegram_tools.adapters.bot import BotIdentity

    report = Reporter()
    provider = BotIdentity(BOT, "default", via_id=42, via_label="Sven (@sven)")
    report.set_identity(provider.identity(), me=BOT, via_label="Sven (@sven)")
    assert report.banner() == "Acting as: @alertsbot · bot (via Sven (@sven))"

    from telegram_tools.adapters import AccountIdentity

    report.set_identity(AccountIdentity(ACCOUNT).identity(), me=ACCOUNT)
    assert report.banner() == "Acting as: Sven (@sven) · account"
