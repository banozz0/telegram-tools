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
from test_adapters import admin, member

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
        # An admin who may post in the channel as well as moderate the forum.
        self.rights = rights if rights is not None else admin("delete_messages", "edit_messages", "pin_messages", "post_messages")
        self.calls: list[tuple] = []
        self.next_id = 5000
        # Telegram keeps a draft per scope: one on the chat's dialog, one on
        # each forum topic. The fake keeps them apart for the same reason the
        # readback has to read the one it wrote to.
        self.dialog_state = {"unread_count": 3, "unread_mark": False, "draft": None}
        self.topic_drafts: dict[int, object] = {}
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
            rows = [SimpleNamespace(**{**vars(topic), "draft": self.topic_drafts.get(topic.id)}) for topic in found]
            return SimpleNamespace(topics=rows, count=len(rows))
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
            top = getattr(request.reply_to, "top_msg_id", None)
            self.calls.append(("draft", self._chat(request.peer), request.message, top))
            # An empty message removes the draft, which is what Telegram does.
            held = SimpleNamespace(message=request.message) if request.message else None
            if top is None:
                self.dialog_state["draft"] = held
            else:
                self.topic_drafts[int(top)] = held
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
    # With --i-know but a limit below the count, the hint names a limit that
    # would actually pass, never the 1000 cap that would refuse again.
    with pytest.raises(CommandError) as still:
        ops.bound_selection(1200, limit=200, i_know=True)
    assert "--limit 1200 --i-know" in still.value.hint and "--limit 1000" not in still.value.hint


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


def test_the_dry_run_tail_names_the_execute_flag_on_the_command_line(run_cli, capsys, home):
    """The command-line half of the menu's wording (card agent-bo-95422210).

    `--execute` is the control a shell has, and this is where it is named. The
    menu names its own row instead; `test_menu` holds that half.
    """
    code, out, _err, _fake = run_cli(["message", "delete", "--chat", FORUM, "--ids", "10,11"], capsys=capsys)

    assert code == 0
    assert "Dry-run: 2 message(s) would be deleted. Add --execute to do it; DELETE is asked for then." in out


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
    client = FakeClient(rights=member())
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
    # The target is where the verb writes, so on the two that post elsewhere it is `--to`.
    assert envelope["target"]["path"][0] == ("Alerts" if verb in ops.DESTINATION_VERBS else "Team Hermes")
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
    # Every right the verb needs is confirmed from what Telegram returned.
    assert envelope["warnings"] == [] and envelope["plan"]["preflight"]["missing"] == []
    assert fake.calls[0][: len(call)] == call
    readback = envelope["evidence"]["readback"]
    if verb == "typing":
        assert readback.startswith("unverified:")
    else:
        assert not readback.startswith("unverified:"), readback
    lines = audit_lines(home)
    assert len(lines) == 1 and lines[0]["command"] == f"message {verb}" and lines[0]["status"] == "ok"
    assert not redaction.find(json.dumps(lines))


@pytest.mark.parametrize("verb", ["reply", "poll"])
def test_a_member_posts_into_a_forum_without_a_warning(run_cli, capsys, home, verb, monkeypatch):
    flags, call = SINGLE[verb]
    code, out, _err, fake = run_cli(["--json", "message", verb, "--chat", FORUM, *flags], client=FakeClient(rights=member()), capsys=capsys, answers=("y",))

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["warnings"] == [] and "send_messages" in envelope["plan"]["preflight"]["held"]
    assert fake.calls[0][: len(call)] == call


@pytest.mark.parametrize("verb", ["forward", "copy"])
def test_a_subscriber_cannot_post_into_a_broadcast_channel(run_cli, capsys, home, verb):
    flags, _call = SINGLE[verb]
    code, out, _err, fake = run_cli(["--json", "message", verb, "--chat", FORUM, *flags], client=FakeClient(rights=member()), capsys=capsys, answers=("y",))

    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PERMISSION_DENIED" and "send_messages" in error["message"]
    assert fake.calls == []


def test_edit_of_another_persons_message_needs_the_edit_right(run_cli, capsys, home):
    client = FakeClient(rights=member())
    code, out, _err, fake = run_cli(["--json", "message", "edit", "--chat", FORUM, "--id", "11", "--text", "x"], client=client, capsys=capsys, answers=("y",))

    assert code == 2 and envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"
    assert fake.calls == []


def test_pin_without_the_right_is_refused_by_name(run_cli, capsys, home):
    client = FakeClient(rights=member())
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


@pytest.mark.parametrize("verb", ["forward", "copy"])
def test_the_banner_names_where_a_forward_lands_and_the_to_line_agrees(run_cli, capsys, home, verb):
    """`Target` is where the command writes, so on a two-ended verb it is `--to`.

    Every other verb's banner names the chat the effect lands in; forward and
    copy are the only ones whose effect lands somewhere else, and naming the
    source there made the screen disagree with itself -- the banner said no
    topic was in play while the `To` line below it said one was. The two lines
    are now the same string, because they name the same thing.
    """
    code, out, _err, _fake = run_cli(
        ["message", verb, "--chat", CHANNEL, "--ids", "300", "--to", FORUM, "--to-topic", "141"], capsys=capsys, answers=("y",)
    )

    assert code == 0
    where = f"Team Hermes › Deploys ({FORUM_ID}:141)"
    banner = next(line for line in out.splitlines() if line.startswith("Acting as:"))
    to_line = next(line for line in out.splitlines() if line.startswith("To      "))
    assert banner.endswith(f" · Target: {where}")
    assert to_line == f"To      {where}"
    # The source is not lost: the `Chat` line above still names where they came from.
    assert f"Chat    Alerts ({CHANNEL_ID})" in out


@pytest.mark.parametrize("verb", ["forward", "copy"])
def test_the_banner_and_the_to_line_agree_when_no_topic_was_named(run_cli, capsys, home, verb):
    """The same rule with the chat itself as the destination: one rid, one line."""
    code, out, _err, _fake = run_cli(
        ["message", verb, "--chat", FORUM, "--ids", "11", "--to", CHANNEL], capsys=capsys, answers=("y",)
    )

    assert code == 0
    where = f"Alerts ({CHANNEL_ID})"
    banner = next(line for line in out.splitlines() if line.startswith("Acting as:"))
    to_line = next(line for line in out.splitlines() if line.startswith("To      "))
    assert banner.endswith(f" · Target: {where}")
    assert to_line == f"To      {where}"


@pytest.mark.parametrize("verb", ["forward", "copy"])
def test_the_destination_line_carries_the_topics_rid_not_the_chats_id(run_cli, capsys, home, verb):
    """A change of mind about a documented tradeoff, not a bug found.

    The line used to print the chat's id in the parentheses and prefix the
    topic's id to the title to compensate -- topic 141 here is titled
    "Deploys", and a topic titled "2" whose id is 4 is what found this, so a
    bare title names nothing the target can be checked against. The compromise
    was right while the parentheses could only hold a chat id. They hold the
    destination's own rid id now, `<chat>:<topic>`, which carries the topic
    without borrowing the title's place, so the prefix has nothing left to do.
    The source `Topic` line keeps its id-first shape: it has no rid of its own
    beside it, and this line now follows the banner it has to match.
    """
    code, out, _err, _fake = run_cli(
        ["message", verb, "--chat", CHANNEL, "--ids", "300", "--to", FORUM, "--to-topic", "141"], capsys=capsys, answers=("y",)
    )

    assert code == 0
    to_line = next(line for line in out.splitlines() if line.startswith("To      "))
    assert to_line == f"To      Team Hermes › Deploys ({FORUM_ID}:141)"
    assert f"({FORUM_ID})" not in to_line, "the bare chat id is what made the line ambiguous"


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


# -- what the seven quiet verbs read back ----------------------------------
#
# A reaction, a pin, a mark read or unread and a draft used to answer with the
# argument they were given and nothing else, so a silent no-op and a real
# success read the same. Each now carries Telegram's own answer in `result`.


class CountingClient(FakeClient):
    """The same account, counting message fetches, and able to answer a
    reaction the way Telegram really does: an `Updates` carrying the new set."""

    def __init__(self, *, answer_with_updates=False, **kwargs):
        super().__init__(**kwargs)
        self.answer_with_updates = answer_with_updates
        self.fetches = 0

    async def get_messages(self, peer, ids=None, **kwargs):
        self.fetches += 1
        return await super().get_messages(peer, ids=ids, **kwargs)

    async def __call__(self, request):
        if self.answer_with_updates and type(request).__name__ == "SendReactionRequest":
            await super().__call__(request)
            row = self.rows[self._chat(request.peer)][request.msg_id]
            return SimpleNamespace(updates=[SimpleNamespace(msg_id=request.msg_id, reactions=row.reactions)])
        return await super().__call__(request)


class StubbornClient(FakeClient):
    """Telegram that takes the call and changes nothing: the reaction does not
    land, the pin does not hold, the chat stays unread, the draft reads its own."""

    async def __call__(self, request):
        name = type(request).__name__
        if name == "SendReactionRequest":
            chat = self._chat(request.peer)
            self.calls.append(("react", chat, request.msg_id, [item.emoticon for item in (request.reaction or [])]))
            return None
        if name == "SaveDraftRequest":
            self.calls.append(("draft", self._chat(request.peer), request.message, None))
            self.dialog_state["draft"] = SimpleNamespace(message="an older draft")
            return None
        return await super().__call__(request)

    async def pin_message(self, peer, message_id, **_):
        self.calls.append(("pin_message", self._chat(peer), message_id))

    async def send_read_acknowledge(self, peer, message=None, *, max_id=None, **_):
        self.calls.append(("read", self._chat(peer)))
        return True


READBACK = {
    "react": (["--id", "11", "--emoji", "🔥"], "reactions", [{"emoji": "🔥", "mine": True}]),
    "unreact": (["--id", "13"], "reactions", []),
    "pin": (["--id", "11"], "pinned", True),
    "unpin": (["--id", "13"], "pinned", False),
    "read": ([], "unread", {"count": 0, "marked": False}),
    "unread": ([], "unread", {"count": 3, "marked": True}),
    "draft": (["--text", "later: deploy", "--topic", "141"], "draft", "later: deploy"),
}

SHIPPED_KEYS = ("verb", "chat_id", "message_ids", "new_message_ids", "done", "dry_run", "cancelled")


@pytest.mark.parametrize("verb", sorted(READBACK))
def test_the_result_carries_the_server_state_beside_the_keys_that_already_shipped(run_cli, capsys, home, verb):
    flags, key, expected = READBACK[verb]
    code, out, _err, _fake = run_cli(["--json", "message", verb, "--chat", FORUM, *flags], capsys=capsys, answers=("y",))

    assert code == 0, out
    result = envelope_of(out)["result"]
    assert result[key] == expected
    assert all(shipped in result for shipped in SHIPPED_KEYS)


DISAGREES = {
    "react": (["--id", "13", "--emoji", "🔥"], "reactions", [{"emoji": "👍", "mine": True}]),
    "pin": (["--id", "11"], "pinned", False),
    "read": ([], "unread", {"count": 3, "marked": False}),
    "draft": (["--text", "later: deploy"], "draft", "an older draft"),
}


@pytest.mark.parametrize("verb", sorted(DISAGREES))
def test_when_the_server_disagrees_with_the_ask_the_result_reports_the_server(run_cli, capsys, home, verb):
    flags, key, expected = DISAGREES[verb]
    code, out, _err, _fake = run_cli(
        ["--json", "message", verb, "--chat", FORUM, *flags], client=StubbornClient(), capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"][key] == expected
    # The argument is still there, and it is not what the readback says.
    if verb == "react":
        assert envelope["result"]["emoji"] == "🔥"
    if verb == "draft":
        assert envelope["result"]["text"] == "later: deploy"
    assert envelope["evidence"]["readback"].startswith("unverified:"), envelope["evidence"]
    assert audit_lines(home)[0]["evidence"]["readback"].startswith("unverified:")


# -- a draft: the scope it is read back from, and taking it back --------------
#
# Telegram keeps a forum's drafts on its topics, never one per chat. The write
# has always carried the topic in `reply_to`; the readback fetched the
# chat-level dialog, so every `--topic` run compared the write against a scope
# it had not written to and reported `unverified:`. The second half is that a
# draft written into a live chat had no way back: `--text` was required and an
# empty one refused.


def _drafting_client():
    """A forum whose chat and whose topic each hold a draft of their own."""
    fake = FakeClient()
    fake.dialog_state["draft"] = SimpleNamespace(message="the chat's own draft")
    fake.topic_drafts[141] = SimpleNamespace(message="the topic's own draft")
    return fake


def test_a_topic_draft_is_read_back_from_the_topic_and_not_from_the_chat(run_cli, capsys, home):
    fake = _drafting_client()
    code, out, _err, _f = run_cli(
        ["--json", "message", "draft", "--chat", FORUM, "--topic", "141", "--text", "later: deploy"],
        client=fake, capsys=capsys, answers=("y",),
    )

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["draft"] == "later: deploy"
    assert envelope["evidence"]["readback"] == "the draft in Team Hermes › Deploys reads the text"
    assert envelope["evidence"]["readback"] not in ("", None) and not envelope["evidence"]["readback"].startswith("unverified:")
    # The chat's own draft is a different string, was not written to, and is not
    # what the readback compared against.
    assert fake.dialog_state["draft"].message == "the chat's own draft"
    assert fake.topic_drafts[141].message == "later: deploy"
    assert audit_lines(home)[0]["evidence"]["readback"].startswith("the draft in")


def test_a_chat_draft_is_still_read_back_from_the_chat(run_cli, capsys, home):
    """The guard on the other side: a run with no --topic reads the dialog."""
    fake = _drafting_client()
    code, out, _err, _f = run_cli(
        ["--json", "message", "draft", "--chat", FORUM, "--text", "later: deploy"],
        client=fake, capsys=capsys, answers=("y",),
    )

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["draft"] == "later: deploy"
    assert not envelope["evidence"]["readback"].startswith("unverified:")
    # The topic's draft is untouched, and is not what was read.
    assert fake.topic_drafts[141].message == "the topic's own draft"


def test_clearing_a_chat_draft_takes_it_back_and_reads_back_as_none(run_cli, capsys, home):
    fake = _drafting_client()
    code, out, err, _f = run_cli(
        ["--json", "message", "draft", "--chat", FORUM, "--clear"],
        client=fake, capsys=capsys, answers=("y",),
    )

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["cleared"] is True and envelope["result"]["text"] is None
    assert envelope["result"]["draft"] is None
    assert envelope["evidence"]["readback"] == "Team Hermes holds no draft"
    # Telegram is told the empty message, which is how a draft is removed, and
    # the screen says in words what the empty text block cannot.
    assert fake.calls[-1] == ("draft", FORUM_ID, "", None)
    assert fake.dialog_state["draft"] is None
    assert "Draft   remove the draft this chat or topic holds" in err
    # The topic's draft is a different scope and is left alone.
    assert fake.topic_drafts[141].message == "the topic's own draft"


def test_clearing_a_topic_draft_leaves_the_chat_draft_alone(run_cli, capsys, home):
    fake = _drafting_client()
    code, out, _err, _f = run_cli(
        ["--json", "message", "draft", "--chat", FORUM, "--topic", "141", "--clear"],
        client=fake, capsys=capsys, answers=("y",),
    )

    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["draft"] is None and envelope["result"]["cleared"] is True
    assert envelope["evidence"]["readback"] == "Team Hermes › Deploys holds no draft"
    assert fake.calls[-1] == ("draft", FORUM_ID, "", 141)
    assert fake.topic_drafts[141] is None
    assert fake.dialog_state["draft"].message == "the chat's own draft"


def test_a_draft_is_written_or_taken_back_and_never_both_or_neither(run_cli, capsys, home):
    """Clearing is its own word, and the two words are one required choice."""
    for flags in ([], ["--text", "later: deploy", "--clear"]):
        code, _out, _err, fake = run_cli(
            ["--json", "message", "draft", "--chat", FORUM, *flags], capsys=capsys, answers=("y",)
        )
        assert code == 2, flags
        assert fake.calls == []


def test_a_reaction_takes_its_state_off_the_updates_instead_of_fetching_again(run_cli, capsys, home):
    argv = ["--json", "message", "react", "--chat", FORUM, "--id", "11", "--emoji", "🔥"]

    fetched = CountingClient()
    code, _out, _err, _fake = run_cli(argv, client=fetched, capsys=capsys, answers=("y",))
    assert code == 0

    carried = CountingClient(answer_with_updates=True)
    code, out, _err, _fake = run_cli(argv, client=carried, capsys=capsys, answers=("y",))

    assert code == 0, out
    assert envelope_of(out)["result"]["reactions"] == [{"emoji": "🔥", "mine": True}]
    # The Updates already held the new set, so the readback fetch is not made.
    assert carried.fetches == fetched.fetches - 1


# -- what a person reads once the write is done ------------------------------
#
# A confirmed send or message verb printed its result mapping as indented JSON
# under its Read back line -- `report.printed_result`, the call kept for the
# commands whose human output has always been JSON -- so the screen after the
# y was a dozen lines of `verb`, `chat_id`, `new_message_ids`, `done`. In human
# mode each now says what it did in one sentence, naming the message and the
# place the way the banner names it, and keeps its Read back line under it;
# the mapping is the envelope's alone.

HERMES = f"Team Hermes ({FORUM_ID})"
DEPLOYS = f"Team Hermes › Deploys ({FORUM_ID}:141)"
ALERTS = f"Alerts ({CHANNEL_ID})"


def after_the_gate(out: str) -> list[str]:
    """Every line the run printed once the y/N was answered.

    The prompt has no newline of its own and the fake terminal echoes nothing,
    so whatever the run prints next begins on the prompt's line.
    """
    marker = "[y/N]: "
    assert out.count(marker) == 1, out
    return out.split(marker, 1)[1].splitlines()


DONE_SENTENCES = {
    "reply": (
        ["--to", "11", "--text", "on it"],
        f"Sent message 5001 to {HERMES}, replying to message 11.",
        "Read back: message 5001 is in Team Hermes",
    ),
    "edit": (
        ["--id", "10", "--text", "deploy 10 fixed"],
        f"Edited message 10 in {HERMES}.",
        "Read back: message 10 in Team Hermes now reads the new text",
    ),
    "forward": (
        ["--ids", "11", "--to", CHANNEL],
        f"Forwarded message 11 to {ALERTS} as message 5001.",
        "Read back: message 5001 is in Alerts",
    ),
    "copy": (
        ["--ids", "12", "--to", CHANNEL],
        f"Copied message 12 to {ALERTS} as message 5001.",
        "Read back: message 5001 is in Alerts",
    ),
    "react": (
        ["--id", "11", "--emoji", "🔥"],
        f"Added 🔥 to message 11 in {HERMES}.",
        "Read back: message 11 in Team Hermes carries 🔥",
    ),
    "unreact": (
        ["--id", "13"],
        f"Removed your reactions from message 13 in {HERMES}.",
        "Read back: message 13 in Team Hermes carries no reaction of yours",
    ),
    "pin": (
        ["--id", "11"],
        f"Pinned message 11 in {HERMES}.",
        "Read back: message 11 in Team Hermes is pinned",
    ),
    "unpin": (
        ["--id", "13"],
        f"Unpinned message 13 in {HERMES}.",
        "Read back: message 13 in Team Hermes is not pinned",
    ),
    "poll": (
        ["--topic", "141", "--question", "Ship?", "--option", "yes", "--option", "no"],
        f"Posted poll message 5001 in {DEPLOYS}.",
        "Read back: message 5001 is in Team Hermes › Deploys",
    ),
    "typing": (
        ["--seconds", "1"],
        f"Showed typing in {HERMES} for 1 second(s).",
        "Read back: unverified: the typing could not be read back (LookupError)",
    ),
    "read": (
        [],
        f"Marked {HERMES} read.",
        "Read back: Team Hermes has no unread messages",
    ),
    "unread": (
        [],
        f"Marked {HERMES} unread.",
        "Read back: Team Hermes is marked unread",
    ),
    "bookmark": (
        ["--id", "11", "--label", "keep"],
        f"Bookmarked message 11 in {HERMES} to Saved Messages as message 5001.",
        "Read back: message 11 from Team Hermes is in Saved Messages as 5001",
    ),
    "draft": (
        ["--topic", "141", "--text", "later: deploy"],
        f"Saved a draft in {DEPLOYS}.",
        "Read back: the draft in Team Hermes › Deploys reads the text",
    ),
}


def test_every_prompted_verb_has_a_done_sentence_here():
    # `delete` is typed_delete and has its own tests below.
    assert set(DONE_SENTENCES) == set(ops.VERBS) - {"delete"}


@pytest.mark.parametrize("verb", sorted(DONE_SENTENCES))
def test_a_done_message_verb_prints_one_sentence_then_its_read_back_and_no_json(run_cli, capsys, home, verb, monkeypatch):
    flags, sentence, readback = DONE_SENTENCES[verb]
    monkeypatch.setattr(ops.asyncio, "sleep", _no_sleep)
    code, out, _err, _fake = run_cli(["message", verb, "--chat", FORUM, *flags], capsys=capsys, answers=("y",))

    assert code == 0, out
    assert after_the_gate(out) == [sentence, readback]
    assert "{" not in out and '"done"' not in out


def test_a_done_send_prints_one_sentence_then_its_read_back_and_no_json(run_cli, capsys, home):
    """The live case: no --yes, the preview answered y."""
    code, out, _err, _fake = run_cli(["send", "--chat", FORUM, "--text", "ship it"], capsys=capsys, answers=("y",))

    assert code == 0, out
    assert after_the_gate(out) == [f"Sent message 5001 to {HERMES}.", "Read back: message 5001 is in Team Hermes"]
    assert "{" not in out


def test_a_done_send_names_the_topic_and_the_message_it_replied_to(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(
        ["send", "--chat", FORUM, "--topic", "141", "--text", "ack", "--reply-to", "11"], capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert after_the_gate(out)[0] == f"Sent message 5001 to {DEPLOYS}, replying to message 11."


def test_a_done_send_with_files_says_how_many_went(run_cli, capsys, home, tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.txt"
    first.write_bytes(b"x")
    second.write_text("y")

    class FileClient(FakeClient):
        async def send_file(self, peer, files, *, caption=None, reply_to=None, **_):
            chat = self._chat(peer)
            self.calls.append(("send_file", chat, list(files), caption, reply_to))
            return [self._new(chat, caption or "", media=object()) for _path in files]

    code, out, _err, _fake = run_cli(
        ["send", "--chat", CHANNEL, "--file", str(first), "--file", str(second)],
        client=FileClient(),
        capsys=capsys,
        answers=("y",),
    )

    assert code == 0, out
    assert after_the_gate(out) == [f"Sent message 5001 to {ALERTS} with 2 file(s).", "Read back: message 5001 is in Alerts"]


def test_a_send_yes_says_the_same_sentence_with_no_gate_in_front_of_it(run_cli, capsys, home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", FORUM)
    code, out, _err, _fake = run_cli(["send", "--chat", FORUM, "--text", "ship it", "--yes"], capsys=capsys)

    assert code == 0, out
    banner, *rest = out.splitlines()
    assert banner.startswith("Acting as: ")
    assert rest == [f"Sent message 5001 to {HERMES}.", "Read back: message 5001 is in Team Hermes"]


def test_a_declined_send_prints_nothing_after_the_prompt(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["send", "--chat", FORUM, "--text", "ship it"], capsys=capsys, answers=("n",))

    assert code == 1
    assert fake.calls == []
    assert after_the_gate(out) == []


@pytest.mark.parametrize("verb", sorted(DONE_SENTENCES))
def test_a_declined_message_verb_prints_nothing_after_the_prompt(run_cli, capsys, home, verb):
    flags, _sentence, _readback = DONE_SENTENCES[verb]
    code, out, _err, fake = run_cli(["message", verb, "--chat", FORUM, *flags], capsys=capsys, answers=("n",))

    assert code == 1
    assert fake.calls == []
    assert after_the_gate(out) == []


def test_a_forward_of_several_names_the_count_and_the_last_one_posted(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(
        ["message", "forward", "--chat", FORUM, "--ids", "10,11", "--to", CHANNEL], capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert after_the_gate(out)[0] == f"Forwarded 2 messages to {ALERTS}, the last as message 5002."


def test_a_forward_into_a_topic_names_the_topic_it_landed_in(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(
        ["message", "forward", "--chat", CHANNEL, "--ids", "300", "--to", FORUM, "--to-topic", "141"], capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert after_the_gate(out)[0] == f"Forwarded message 300 to {DEPLOYS} as message 5001."


def _twice_reacted_client():
    """A message this account has reacted to twice, with a third reaction someone else left."""
    fake = FakeClient()
    row = _message(13, own=True, topic=141, pinned=True, chosen=("👍", "🎉"))
    row.reactions.results.append(SimpleNamespace(reaction=SimpleNamespace(emoticon="😂"), count=1, chosen_order=None))
    fake.rows[FORUM_ID][13] = row
    return fake


def test_an_unreact_naming_an_emoji_leaves_the_account_s_other_reactions_on(run_cli, capsys, home):
    # `SendReactionRequest` replaces the identity's whole set, so taking one
    # reaction off means sending the rest back: an empty list would take 🎉
    # off too, and someone else's 😂 must not be sent as this account's.
    fake = _twice_reacted_client()
    code, out, _err, _f = run_cli(
        ["message", "unreact", "--chat", FORUM, "--id", "13", "--emoji", "👍"], client=fake, capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert fake.calls == [("react", FORUM_ID, 13, ["🎉"])]
    assert after_the_gate(out) == [
        f"Removed 👍 from message 13 in {HERMES}.",
        "Read back: message 13 in Team Hermes carries no 👍 of yours",
    ]


def test_an_unreact_naming_the_account_s_only_reaction_sends_the_empty_set(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["message", "unreact", "--chat", FORUM, "--id", "13", "--emoji", "👍"], capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert fake.calls == [("react", FORUM_ID, 13, [])]
    assert after_the_gate(out) == [
        f"Removed 👍 from message 13 in {HERMES}.",
        "Read back: message 13 in Team Hermes carries no 👍 of yours",
    ]


def test_an_unreact_with_no_emoji_still_clears_every_reaction_of_yours(run_cli, capsys, home):
    fake = _twice_reacted_client()
    code, out, _err, _f = run_cli(["message", "unreact", "--chat", FORUM, "--id", "13"], client=fake, capsys=capsys, answers=("y",))

    assert code == 0, out
    assert fake.calls == [("react", FORUM_ID, 13, [])]
    assert after_the_gate(out) == [
        f"Removed your reactions from message 13 in {HERMES}.",
        "Read back: message 13 in Team Hermes carries no reaction of yours",
    ]


def test_a_cleared_draft_says_it_was_taken_back(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(
        ["message", "draft", "--chat", FORUM, "--clear"], client=_drafting_client(), capsys=capsys, answers=("y",)
    )

    assert code == 0, out
    assert after_the_gate(out) == [f"Removed the draft in {HERMES}.", "Read back: Team Hermes holds no draft"]


def test_an_executed_delete_says_how_many_went_and_from_where(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(
        ["message", "delete", "--chat", FORUM, "--ids", "10,11", "--execute"], capsys=capsys, answers=("DELETE",)
    )

    assert code == 0, out
    after = out.split("Type DELETE to continue: ", 1)[1].splitlines()
    assert after == [f"Deleted 2 message(s) from {HERMES}.", "Read back: 2 message(s) gone from Team Hermes"]
    assert "{" not in out


def test_a_delete_dry_run_ends_on_its_own_tail_and_prints_no_json(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["message", "delete", "--chat", FORUM, "--ids", "10,11"], capsys=capsys)

    assert code == 0
    assert fake.calls == []
    assert out.splitlines()[-1] == "Dry-run: 2 message(s) would be deleted. Add --execute to do it; DELETE is asked for then."
    assert "{" not in out


def test_a_delete_nobody_typed_DELETE_for_prints_nothing_more(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["message", "delete", "--chat", FORUM, "--ids", "10", "--execute"], capsys=capsys, answers=("nope",)
    )

    assert code == 1
    assert fake.calls == []
    assert out.splitlines()[-1] == "Type DELETE to continue: Cancelled - DELETE was not typed."


def test_under_json_the_sentence_stays_off_both_streams_and_the_result_keeps_its_keys(run_cli, capsys, home):
    code, out, err, _fake = run_cli(
        ["--json", "message", "reply", "--chat", FORUM, "--to", "11", "--text", "on it"], capsys=capsys, answers=("y",)
    )

    envelope = envelope_of(out)  # one object on stdout, nothing before or after it
    assert code == 0
    assert envelope["result"] == {
        "verb": "reply",
        "chat_id": FORUM_ID,
        "message_ids": [11],
        "new_message_ids": [5001],
        "done": True,
        "dry_run": False,
        "cancelled": False,
    }
    assert envelope["evidence"]["readback"] == "message 5001 is in Team Hermes"
    assert "Sent message" not in out + err and "Read back:" not in out + err

    code, out, err, _fake = run_cli(["--json", "send", "--chat", FORUM, "--text", "ship it"], capsys=capsys, answers=("y",))
    envelope = envelope_of(out)
    assert code == 0
    assert envelope["result"] == {
        "chat_id": FORUM_ID,
        "topic_id": None,
        "message_id": 5001,
        "files": 0,
        "sent": True,
        "cancelled": False,
    }
    assert "Sent message" not in out + err
    # The readback also stays in the local audit line.
    assert audit_lines(home)[-1]["evidence"]["readback"] == "message 5001 is in Team Hermes"


def test_under_json_a_declined_send_and_a_delete_dry_run_keep_their_results(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(["--json", "send", "--chat", FORUM, "--text", "ship it"], capsys=capsys, answers=("n",))
    assert code == 1
    assert envelope_of(out)["result"] == {
        "chat_id": FORUM_ID,
        "topic_id": None,
        "message_id": None,
        "files": 0,
        "sent": False,
        "cancelled": True,
    }

    code, out, _err, _fake = run_cli(["--json", "message", "delete", "--chat", FORUM, "--ids", "10,11"], capsys=capsys)
    assert code == 0
    assert envelope_of(out)["result"] == {
        "verb": "delete",
        "chat_id": FORUM_ID,
        "message_ids": [10, 11],
        "new_message_ids": [],
        "done": False,
        "dry_run": True,
        "cancelled": False,
        "deleted": 0,
    }


def test_a_confirmed_send_from_the_menu_lands_one_sentence_and_its_read_back_above_done(home, monkeypatch, capsys):
    """The real menu into the real command: the screen after the y, as a person saw it."""
    from telegram_tools import menu
    from telegram_tools.models import ChatChoice
    from test_menu import SEND, FakeSession

    fake = FakeClient()

    class Session(FakeSession):
        async def client(self):
            return fake

    session = Session(chats=[ChatChoice(id=FORUM_ID, title="Team Hermes", username=None, type="forum_group")], topics=[])
    session.config.send_allowlist = ()
    # The confirm reads the real `input`; a terminal echoes the typed y and ends the prompt's line.
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True, read=lambda: "", readline=lambda: print("y") or "y\n"))
    # 3 1 = write > send, 1 = forum groups, 1 = Team Hermes, 2 = Message, then
    # the body, 5 = Send it, Enter = main menu, 0 = exit.
    keys = iter([*SEND, "1", "1", "2", "ship it", ".", "5", "", "0"])
    code = asyncio.run(menu.run_menu(read=lambda _prompt: next(keys), write=print, session=session, runner=cli.run))

    assert code == 0
    assert [call[0] for call in fake.calls] == ["send_message"]
    lines = capsys.readouterr().out.splitlines()
    done = next(index for index, line in enumerate(lines) if line == "Main › Write › Send › Team Hermes › Done")
    assert lines[done - 3 : done] == [
        "Send it? [y/N]: y",
        f"Sent message 5001 to {HERMES}.",
        "Read back: message 5001 is in Team Hermes",
    ]
    assert not any(line.lstrip().startswith(("{", "}", '"message_id"')) for line in lines)
