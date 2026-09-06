"""The `message` verbs on this tool, and the P5 fixture row of section 21.

Every run is against a fake account client. Per verb: a refusal first --
a declined gate, a missing right, an id that is not there, bot mode -- then
the success, with its readback and its audit line. The bulk bound is the
spec's own fixture: `--from-search` with 1001 hits exits 2 with BULK_LIMIT.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools import messages as ops
from telegram_tools import profiles as profile_store
from telegram_tools._core import redaction
from telegram_tools._core.contract import validate_envelope
from telegram_tools._core.identity import Identity
from telegram_tools.adapters.archive import TelegramArchiveSource, scope_rid_for
from telegram_tools.envelope import CommandError

ACCOUNT = SimpleNamespace(id=4242, first_name="Sven", username="sven", phone="+35699001122")
IDENTITY = Identity(platform="telegram", mode="account", label="Sven (@sven)", id="tg:user:4242", profile="default")
HARRY = SimpleNamespace(id=777, first_name="Harry", username="harry", bot=False)
FORUM_ID = -1001000000001
CHANNEL_ID = -1001000000003
BOT = SimpleNamespace(id=98765, first_name="Alerts", username="alertsbot", bot=True)
BOT_TOKEN_SAMPLE = "98765:AAExampleBotModeTokenValueXYZ"


def _message(number: int, *, own: bool = False, text: str | None = None, media=None, topic: int | None = None, pinned=False, chosen=()):
    when = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=number)
    reply_to = SimpleNamespace(reply_to_msg_id=topic, reply_to_top_id=None, forum_topic=True) if topic else None
    reactions = SimpleNamespace(
        results=[SimpleNamespace(reaction=SimpleNamespace(emoticon=emoji), count=1, chosen_order=0) for emoji in chosen]
    )
    return SimpleNamespace(
        id=number,
        date=when,
        raw_text=f"deploy {number}" if text is None else text,
        message=f"deploy {number}" if text is None else text,
        sender_id=ACCOUNT.id if own else HARRY.id,
        sender=ACCOUNT if own else HARRY,
        out=own,
        media=media,
        reply_to=reply_to,
        pinned=pinned,
        reactions=reactions,
        edit_date=None,
    )


def _dialog(chat_id, title, *, forum=False, channel=False, username=None):
    entity = SimpleNamespace(
        id=abs(chat_id) - 1000000000000, title=title, username=username, megagroup=not channel, broadcast=channel, forum=forum
    )
    return SimpleNamespace(id=chat_id, title=title, entity=entity, input_entity=SimpleNamespace(channel_id=entity.id, chat_id=chat_id))


class FakeClient:
    """A signed-in account with two chats, a few messages, and every call the verbs make."""

    def __init__(self, *, rights=None, rows=None, me=ACCOUNT):
        self.me = me
        self.dialogs = [
            _dialog(FORUM_ID, "Team Hermes", forum=True, username="teamhermes"),
            _dialog(CHANNEL_ID, "Alerts", channel=True, username="agencyalerts"),
        ]
        self.topics = {FORUM_ID: [SimpleNamespace(id=141, title="Deploys", top_message=900)]}
        self.rows = rows if rows is not None else {
            FORUM_ID: {
                10: _message(10, own=True, topic=141),
                11: _message(11, topic=141),
                12: _message(12, topic=141, media=object()),
                13: _message(13, own=True, topic=141, pinned=True, chosen=("👍",)),
            },
            CHANNEL_ID: {300: _message(300, own=True)},
        }
        self.rights = rights if rights is not None else SimpleNamespace(
            is_admin=True, send_messages=True, delete_messages=True, edit_messages=True, pin_messages=True
        )
        self.calls: list[tuple] = []
        self.next_id = 5000
        self.dialog_state = {"unread_count": 3, "unread_mark": False, "draft": None}
        self.saved: dict[int, object] = {}
        self.disconnected = False

    # -- identity and resolution ---------------------------------------
    async def get_me(self):
        return self.me

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnected = True

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_entity(self, reference):
        for dialog in self.dialogs:
            if dialog.id == reference or (isinstance(reference, str) and reference.lstrip("@") == dialog.entity.username):
                return dialog.entity
        raise ValueError(f"no entity {reference}")

    async def get_input_entity(self, entity):
        return SimpleNamespace(channel_id=entity.id, chat_id=-1000000000000 - entity.id)

    async def get_peer_id(self, entity):
        return -1000000000000 - entity.id

    async def get_permissions(self, _peer, _user):
        return self.rights

    def _chat(self, peer):
        if peer == "me":
            return "me"
        return getattr(peer, "chat_id", None)

    # -- reads ----------------------------------------------------------
    async def get_messages(self, peer, ids=None, **_):
        chat = self._chat(peer)
        store = self.saved if chat == "me" else self.rows.get(chat, {})
        if isinstance(ids, list):
            return [store.get(int(number)) for number in ids]
        return store.get(int(ids))

    async def iter_messages(self, peer, *, offset_id=None, min_id=0, reply_to=None, wait_time=None, **_):
        chat = self._chat(peer)
        rows = sorted(self.rows.get(chat, {}).values(), key=lambda row: -row.id)
        for row in rows:
            if reply_to is not None and getattr(getattr(row, "reply_to", None), "reply_to_msg_id", None) != reply_to:
                continue
            if offset_id is not None and row.id >= offset_id:
                continue
            if min_id and row.id <= min_id:
                continue
            yield row

    # -- writes ---------------------------------------------------------
    def _new(self, chat, text, **extra):
        self.next_id += 1
        message = _message(self.next_id, own=True, text=text, **extra)
        (self.saved if chat == "me" else self.rows.setdefault(chat, {}))[self.next_id] = message
        return message

    async def send_message(self, peer, message=None, *, reply_to=None, file=None, link_preview=True, **_):
        chat = self._chat(peer)
        self.calls.append(("send_message", chat, message, reply_to, file))
        return self._new(chat, message or "", media=file)

    async def edit_message(self, peer, message_id, text=None, **_):
        chat = self._chat(peer)
        self.calls.append(("edit_message", chat, message_id, text))
        row = self.rows[chat][message_id]
        row.raw_text = text
        row.message = text
        return row

    async def delete_messages(self, peer, ids, *, revoke=True):
        chat = self._chat(peer)
        self.calls.append(("delete_messages", chat, list(ids), revoke))
        for number in ids:
            self.rows[chat].pop(int(number), None)
        return [SimpleNamespace(pts_count=len(ids))]

    async def forward_messages(self, to_peer, ids, from_peer=None, **_):
        chat = self._chat(to_peer)
        self.calls.append(("forward_messages", chat, list(ids), self._chat(from_peer)))
        return [self._new(chat, f"fwd {number}") for number in ids]

    async def pin_message(self, peer, message_id, **_):
        chat = self._chat(peer)
        self.calls.append(("pin_message", chat, message_id))
        self.rows[chat][message_id].pinned = True

    async def unpin_message(self, peer, message_id=None, **_):
        chat = self._chat(peer)
        self.calls.append(("unpin_message", chat, message_id))
        self.rows[chat][message_id].pinned = False

    def action(self, peer, action, **_):
        client = self

        class Typing:
            async def __aenter__(self):
                client.calls.append(("typing", client._chat(peer), action))

            async def __aexit__(self, *_exc):
                return False

        return Typing()

    async def send_read_acknowledge(self, peer, message=None, *, max_id=None, **_):
        self.calls.append(("read", self._chat(peer)))
        self.dialog_state["unread_count"] = 0
        return True

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetForumTopicsByIDRequest":
            found = [topic for topic in self.topics.get(request.peer.chat_id, []) if topic.id in request.topics]
            return SimpleNamespace(topics=found, count=len(found))
        if name == "GetForumTopicsRequest":
            topics = self.topics.get(request.peer.chat_id, [])
            return SimpleNamespace(topics=topics, count=len(topics))
        if name == "SendReactionRequest":
            chat = self._chat(request.peer)
            emojis = [reaction.emoticon for reaction in (request.reaction or [])]
            self.calls.append(("react", chat, request.msg_id, emojis))
            row = self.rows[chat][request.msg_id]
            row.reactions = SimpleNamespace(
                results=[SimpleNamespace(reaction=SimpleNamespace(emoticon=emoji), count=1, chosen_order=0) for emoji in emojis]
            )
            return None
        if name == "MarkDialogUnreadRequest":
            self.calls.append(("unread", self._chat(request.peer.peer)))
            self.dialog_state["unread_mark"] = True
            return None
        if name == "SaveDraftRequest":
            self.calls.append(("draft", self._chat(request.peer), request.message, getattr(request.reply_to, "top_msg_id", None)))
            self.dialog_state["draft"] = SimpleNamespace(message=request.message)
            return None
        if name == "GetPeerDialogsRequest":
            return SimpleNamespace(dialogs=[SimpleNamespace(**self.dialog_state)])
        if name == "ForwardMessagesRequest":
            chat = self._chat(request.to_peer)
            self.calls.append(("forward_raw", chat, list(request.id), request.top_msg_id))
            made = [self._new(chat, f"fwd {number}") for number in request.id]
            return SimpleNamespace(updates=[SimpleNamespace(message=item) for item in made])
        raise AssertionError(f"unexpected request {name}")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", f"alerts:{BOT_TOKEN_SAMPLE}")
    monkeypatch.delenv("TELEGRAM_SEND_ALLOWLIST", raising=False)
    monkeypatch.setenv("TELEGRAM_TOOLS_SESSION", str(tmp_path / "session"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    profile_store.record_login(profile_store.load("default", home=tmp_path), label="Sven (@sven)", user_id=ACCOUNT.id)
    return tmp_path


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` against a fake account; `answers` are what the prompts read, in order."""

    def run(argv, *, client=None, capsys, isatty=True, answers=("",), bot=None):
        fake = client or FakeClient()
        queue = list(answers)

        def readline():
            return (queue.pop(0) if queue else "") + "\n"

        async def started(_client, *, authorize=True):
            return fake

        @asynccontextmanager
        async def fake_bot_client(_config, token):
            yield bot

        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr(cli, "bot_client", fake_bot_client)
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=readline))
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run


def envelope_of(out: str) -> dict:
    payload = json.loads(out)
    assert validate_envelope(payload) == []
    assert not redaction.find(out), redaction.find(out)
    return payload


def audit_lines(home: Path) -> list[dict]:
    path = home / ".telegram-tools" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


FORUM = str(FORUM_ID)
CHANNEL = str(CHANNEL_ID)


# -- the selection and its bound -------------------------------------------


def test_ids_parse_once_each_in_order_and_refuse_words():
    assert ops.parse_ids(["3,1", "2", "1"]) == [3, 1, 2]
    with pytest.raises(ValueError):
        ops.parse_ids(["1,two"])


def test_the_bound_refuses_above_limit_and_names_i_know_above_the_hard_limit():
    ops.bound_selection(200, limit=None)
    with pytest.raises(CommandError) as over:
        ops.bound_selection(201, limit=None)
    assert over.value.code == "BULK_LIMIT" and "--limit 201" in over.value.hint
    ops.bound_selection(900, limit=900)
    with pytest.raises(CommandError) as hard:
        ops.bound_selection(1001, limit=2000)
    assert hard.value.code == "BULK_LIMIT" and "--i-know" in hard.value.hint
    ops.bound_selection(1001, limit=2000, i_know=True)


def test_from_search_with_1001_hits_exits_2_with_bulk_limit(run_cli, capsys, home):
    """The P5 fixture row: the bound fires before anything is fetched, previewed or asked."""
    rows = {CHANNEL_ID: {number: _message(number, own=True) for number in range(1, 1002)}}
    client = FakeClient(rows=rows)
    with archive_store.open_archive() as archive:
        asyncio.run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=500))
    client.calls.clear()

    code, out, _err, fake = run_cli(["--json", "message", "delete", "--chat", CHANNEL, "--from-search", "deploy", "--execute"], client=client, capsys=capsys)

    assert code == 2
    envelope = envelope_of(out)
    assert envelope["error"]["code"] == "BULK_LIMIT"
    assert "1001" in envelope["error"]["message"]
    assert fake.calls == []
    assert audit_lines(home) == []


def test_from_search_selects_the_archived_ids_and_shows_every_one_in_the_plan(run_cli, capsys, home):
    client = FakeClient()
    with archive_store.open_archive() as archive:
        asyncio.run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=50))
    client.calls.clear()

    code, out, err, _fake = run_cli(["--json", "message", "delete", "--chat", FORUM, "--from-search", "deploy"], client=client, capsys=capsys)

    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "dry_run"
    assert sorted(envelope["result"]["message_ids"]) == [10, 11, 12, 13]
    assert "4 messages" in err and "deploy 12 [media]" in err
    assert client.calls == []


# -- delete: typed_delete, bounded, no --yes ------------------------------------


def test_message_delete_has_no_yes_flag():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["message", "delete", "--chat", FORUM, "--ids", "10", "--yes"])


def test_delete_dry_runs_by_default_and_previews_the_target_and_the_messages(run_cli, capsys, home):
    code, out, err, fake = run_cli(["--json", "message", "delete", "--chat", FORUM, "--ids", "10,11"], capsys=capsys)

    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "dry_run"
    assert envelope["target"]["rid"] == f"tg:chat:{FORUM_ID}"
    assert envelope["plan"]["approval"] == "typed_delete"
    assert "Team Hermes" in err and "deploy 10" in err and "deploy 11" in err and "--execute" in err
    assert fake.calls == []


def test_delete_with_execute_needs_DELETE_typed_and_a_wrong_word_deletes_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "message", "delete", "--chat", FORUM, "--ids", "10", "--execute"], capsys=capsys, answers=("delete",)
    )

    assert code == 1
    assert envelope_of(out)["status"] == "cancelled"
    assert fake.calls == []
    assert audit_lines(home) == []


def test_delete_with_DELETE_typed_deletes_reads_back_and_audits(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "message", "delete", "--chat", FORUM, "--ids", "10,11", "--execute"], capsys=capsys, answers=("DELETE",)
    )

    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "ok"
    assert envelope["result"]["deleted"] == 2
    assert fake.calls == [("delete_messages", FORUM_ID, [10, 11], True)]
    assert envelope["evidence"]["readback"] == "2 message(s) gone from Team Hermes"
    lines = audit_lines(home)
    assert len(lines) == 1 and lines[0]["command"] == "message delete" and lines[0]["approval"] == "typed_delete"


def test_delete_of_someone_elses_message_needs_the_delete_right(run_cli, capsys, home):
    client = FakeClient(rights=SimpleNamespace(is_admin=False, send_messages=True, delete_messages=False))
    code, out, _err, fake = run_cli(
        ["--json", "message", "delete", "--chat", FORUM, "--ids", "11", "--execute"], client=client, capsys=capsys, answers=("DELETE",)
    )
    assert code == 2 and envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"
    assert fake.calls == []

    # Your own message needs no right at all.
    code, out, _err, fake = run_cli(
        ["--json", "message", "delete", "--chat", FORUM, "--ids", "10", "--execute"], client=FakeClient(rights=client.rights), capsys=capsys, answers=("DELETE",)
    )
    assert code == 0 and envelope_of(out)["status"] == "ok"


def test_above_the_hard_limit_the_count_is_typed_too(run_cli, capsys, home):
    rows = {CHANNEL_ID: {number: _message(number, own=True) for number in range(1, 1002)}}
    ids = ",".join(str(number) for number in range(1, 1002))
    argv = ["--json", "message", "delete", "--chat", CHANNEL, "--ids", ids, "--limit", "1001", "--i-know", "--execute"]

    code, out, _err, fake = run_cli(argv, client=FakeClient(rows=rows), capsys=capsys, answers=("DELETE", "1000"))
    assert code == 1 and envelope_of(out)["status"] == "cancelled" and fake.calls == []

    code, out, _err, fake = run_cli(argv, client=FakeClient(rows=rows), capsys=capsys, answers=("DELETE", "1001"))
    assert code == 0 and envelope_of(out)["result"]["deleted"] == 1001


def test_a_missing_id_refuses_before_any_gate(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "message", "delete", "--chat", FORUM, "--ids", "10,99", "--execute"], capsys=capsys, answers=("DELETE",))

    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "TARGET_NOT_FOUND" and "99" in error["message"]
    assert fake.calls == []


def test_a_gate_with_no_terminal_is_exit_3_under_json(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "message", "delete", "--chat", FORUM, "--ids", "10", "--execute"], capsys=capsys, isatty=False)

    assert code == 3
    assert envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert fake.calls == []


# -- every other verb: a refusal, then the success ----------------------------

SINGLE = {
    "reply": (["--to", "11", "--text", "on it @harry"], ("send_message", FORUM_ID, "on it @harry", 11, None)),
    "edit": (["--id", "10", "--text", "deploy 10 fixed"], ("edit_message", FORUM_ID, 10, "deploy 10 fixed")),
    "react": (["--id", "11", "--emoji", "🔥"], ("react", FORUM_ID, 11, ["🔥"])),
    "unreact": (["--id", "13"], ("react", FORUM_ID, 13, [])),
    "pin": (["--id", "11"], ("pin_message", FORUM_ID, 11)),
    "unpin": (["--id", "13"], ("unpin_message", FORUM_ID, 13)),
    "poll": (["--question", "Ship?", "--option", "yes", "--option", "no"], ("send_message", FORUM_ID)),
    "typing": (["--seconds", "1"], ("typing", FORUM_ID, "typing")),
    "read": ([], ("read", FORUM_ID)),
    "unread": ([], ("unread", FORUM_ID)),
    "bookmark": (["--id", "11", "--label", "keep"], ("forward_messages", "me", [11], FORUM_ID)),
    "draft": (["--text", "later: deploy", "--topic", "141"], ("draft", FORUM_ID, "later: deploy", 141)),
    "forward": (["--ids", "11", "--to", CHANNEL], ("forward_messages", CHANNEL_ID, [11], FORUM_ID)),
    "copy": (["--ids", "12", "--to", CHANNEL], ("send_message", CHANNEL_ID)),
}


@pytest.mark.parametrize("verb", sorted(SINGLE))
def test_declining_the_prompt_does_nothing_and_leaves_no_audit_line(run_cli, capsys, home, verb, monkeypatch):
    flags, _call = SINGLE[verb]
    monkeypatch.setattr(cli.asyncio, "sleep", _no_sleep, raising=False)
    code, out, err, fake = run_cli(["--json", "message", verb, "--chat", FORUM, *flags], capsys=capsys, answers=("n",))

    assert code == 1
    envelope = envelope_of(out)
    assert envelope["status"] == "cancelled"
    assert envelope["target"]["path"][0] == "Team Hermes"
    # The preview named the chat, and the message where the verb has one.
    assert "Team Hermes" in err
    if "--id" in flags or "--to" in flags[:1] or "--ids" in flags:
        number = flags[flags.index("--id") + 1] if "--id" in flags else flags[1]
        assert f"deploy {number}" in err
    assert fake.calls == []
    assert audit_lines(home) == []


async def _no_sleep(_seconds):
    return None


@pytest.mark.parametrize("verb", sorted(SINGLE))
def test_confirming_runs_the_verb_reads_back_and_audits(run_cli, capsys, home, verb, monkeypatch):
    flags, call = SINGLE[verb]
    monkeypatch.setattr(ops.asyncio, "sleep", _no_sleep)
    code, out, _err, fake = run_cli(["--json", "message", verb, "--chat", FORUM, *flags], capsys=capsys, answers=("y",))

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["status"] == "ok"
    assert envelope["command"] == f"message {verb}"
    assert envelope["plan"]["approval"] == "prompt_y"
    assert fake.calls[0][: len(call)] == call
    readback = envelope["evidence"]["readback"]
    if verb == "typing":
        assert readback.startswith("unverified:")
    else:
        assert not readback.startswith("unverified:"), readback
    lines = audit_lines(home)
    assert len(lines) == 1 and lines[0]["command"] == f"message {verb}" and lines[0]["status"] == "ok"
    assert not redaction.find(json.dumps(lines))


def test_edit_of_another_persons_message_needs_the_edit_right(run_cli, capsys, home):
    client = FakeClient(rights=SimpleNamespace(is_admin=False, send_messages=True, edit_messages=False))
    code, out, _err, fake = run_cli(["--json", "message", "edit", "--chat", FORUM, "--id", "11", "--text", "x"], client=client, capsys=capsys, answers=("y",))

    assert code == 2 and envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"
    assert fake.calls == []


def test_pin_without_the_right_is_refused_by_name(run_cli, capsys, home):
    client = FakeClient(rights=SimpleNamespace(is_admin=False, send_messages=True, pin_messages=False))
    code, out, _err, fake = run_cli(["--json", "message", "pin", "--chat", FORUM, "--id", "11"], client=client, capsys=capsys, answers=("y",))

    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PERMISSION_DENIED" and "pin_messages" in error["message"]
    assert fake.calls == []


def test_copy_reposts_text_and_links_an_attachment_without_fetching_it(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "message", "copy", "--chat", FORUM, "--ids", "11,12", "--to", CHANNEL], capsys=capsys, answers=("y",))

    assert code == 0
    sent = [call for call in fake.calls if call[0] == "send_message"]
    assert sent[0][2] == "deploy 11"
    assert sent[1][2].startswith("deploy 12\n(attachment on the original: https://t.me/teamhermes/12)")
    assert all(call[4] is None for call in sent), "copy never sends bytes"
    assert envelope_of(out)["result"]["new_message_ids"] == [5001, 5002]


def test_forward_into_a_topic_uses_the_raw_request_with_the_topic(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "message", "forward", "--chat", CHANNEL, "--ids", "300", "--to", FORUM, "--to-topic", "141"], capsys=capsys, answers=("y",))

    assert code == 0
    assert fake.calls[0] == ("forward_raw", FORUM_ID, [300], 141)
    assert envelope_of(out)["evidence"]["readback"].endswith("is in Team Hermes › Deploys")


def test_a_yes_run_is_gated_by_the_allowlist_on_the_chat_it_lands_in(run_cli, capsys, home, monkeypatch):
    code, out, _err, fake = run_cli(["--json", "message", "react", "--chat", FORUM, "--id", "11", "--emoji", "🔥", "--yes"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "NOT_ALLOWLISTED" and fake.calls == []

    # A forward lands in --to, so that is the chat the allowlist has to name.
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", FORUM)
    code, out, _err, fake = run_cli(["--json", "message", "forward", "--chat", FORUM, "--ids", "11", "--to", CHANNEL, "--yes"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "NOT_ALLOWLISTED"

    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", f"{FORUM},{CHANNEL}")
    code, out, _err, fake = run_cli(["--json", "message", "forward", "--chat", FORUM, "--ids", "11", "--to", CHANNEL, "--yes"], capsys=capsys)
    assert code == 0 and envelope_of(out)["plan"]["approval"] == "yes_allowlist"
    assert fake.calls[0][:2] == ("forward_messages", CHANNEL_ID)


def test_bookmark_writes_the_archive_row_beside_the_saved_copy(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(["--json", "message", "bookmark", "--chat", FORUM, "--id", "11", "--label", "keep"], capsys=capsys, answers=("y",))

    assert code == 0
    assert "Saved Messages" in envelope_of(out)["evidence"]["readback"]
    connection = archive_store.read_only()
    row = connection.execute("SELECT rid, message_id, identity_id, label, source FROM bookmarks").fetchone()
    connection.close()
    assert tuple(row) == (scope_rid_for(FORUM, 141), "11", IDENTITY.id, "keep", "manual")


def test_a_reply_preview_names_the_mentions_and_the_message_replied_to(run_cli, capsys, home):
    _code, _out, err, _fake = run_cli(
        ["--json", "message", "reply", "--chat", FORUM, "--to", "11", "--text", "cc @harry and @all"], capsys=capsys, answers=("n",)
    )
    assert "Mentions @all (everyone in the chat), @harry" in err
    assert "11      2026-09-01 00:11 @harry: deploy 11" in err


def test_plan_drift_refuses_when_the_chat_is_renamed_after_the_gate(run_cli, capsys, home):
    client = FakeClient()
    original = client.get_permissions

    async def rename_then_answer(peer, user):
        client.dialogs[0].entity.title = "Team Hermes (archived)"
        return await original(peer, user)

    client.get_permissions = rename_then_answer
    code, out, _err, fake = run_cli(["--json", "message", "pin", "--chat", FORUM, "--id", "11"], client=client, capsys=capsys, answers=("y",))

    assert code == 2 and envelope_of(out)["error"]["code"] == "PLAN_DRIFT"
    assert fake.calls == []


# -- bot mode ---------------------------------------------------------------


@pytest.mark.parametrize("verb", ops.ACCOUNT_ONLY_VERBS)
def test_account_only_verbs_refuse_under_as_bot_before_anything_connects(run_cli, capsys, home, verb, monkeypatch):
    monkeypatch.setattr(cli, "create_client", lambda _config: (_ for _ in ()).throw(AssertionError("must not connect")))
    flags = {"read": [], "unread": [], "bookmark": ["--id", "11"], "draft": ["--text", "x"]}[verb]
    code = cli.main(["--json", "--as-bot", "alerts", "message", verb, "--chat", FORUM, *flags])
    out = capsys.readouterr().out

    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "IDENTITY_MODE_UNSUPPORTED"
    assert error["hint"] == f"telegram-tools --json message {verb} --chat {FORUM}" + ("".join(" " + flag for flag in flags))


def test_a_bot_reacts_where_it_is_a_member_and_the_screens_name_both(run_cli, capsys, home):
    bot = FakeClient(me=BOT)
    code, out, err, _fake = run_cli(["--json", "--as-bot", "alerts", "message", "react", "--chat", FORUM, "--id", "11", "--emoji", "🔥"], capsys=capsys, answers=("y",), bot=bot)

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["identity"]["mode"] == "bot" and envelope["identity"]["via"] == IDENTITY.id
    assert "Reacting as @alertsbot (via Sven (@sven))" in err
    assert bot.calls[0] == ("react", FORUM_ID, 11, ["🔥"])


# -- send --reply-to ----------------------------------------------------------


def test_send_reply_to_threads_the_message_and_says_so_in_the_preview(run_cli, capsys, home):
    code, out, err, fake = run_cli(["--json", "send", "--chat", FORUM, "--text", "ack @harry", "--reply-to", "11"], capsys=capsys, answers=("y",))

    assert code == 0
    assert "Reply to message 11" in err and "Mentions @harry" in err
    assert fake.calls[0] == ("send_message", FORUM_ID, "ack @harry", 11, None)
    assert envelope_of(out)["args"]["reply_to"] == 11


# -- the module's own pieces -----------------------------------------------------


def test_message_links_by_username_or_internal_id():
    assert ops.message_link(-1001000000001, 12, username="teamhermes") == "https://t.me/teamhermes/12"
    assert ops.message_link(-1001000000001, 12) == "https://t.me/c/1000000001/12"
    assert ops.message_link(-1001000000001, 12, topic_id=141) == "https://t.me/c/1000000001/141/12"


def test_brief_line_is_one_row_cut_to_width():
    brief = ops.brief_of(_message(7, text="x" * 200, media=object()))
    assert brief.line.startswith("7       2026-09-01 00:07 @harry: " + "x" * 59 + "…")
    assert brief.line.endswith("[media]")
