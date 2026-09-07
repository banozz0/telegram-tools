"""Watch on this tool: the event mapping, the runner's wiring, the two guarantees, and the P8 row.

Spec section 10 and the P8 row of section 21. Nothing here opens a socket: the
mapping functions are pure and take a fake Telethon message, the runner is
driven by a recorded event source, and the client is the archive tests' fake.
The one place a real file is touched is the detached-session test, which builds
an actual Telethon SQLite session, holds it open, and proves the runner's copy
reads it without taking it -- which is the whole reason `watch run` and a
one-shot command can be up at the same time.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools import watch as watch_ops
from telegram_tools._core import rules as _rules
from telegram_tools._core import runner as _runner
from telegram_tools._core.contract import CodedError, validate_envelope
from telegram_tools.adapters import events as watch_events
from telegram_tools.client import detached_session
from telegram_tools.config import SendDestination
from test_archive_sync import ACCOUNT, CHANNEL_ID, FORUM_ID, HARRY, IDENTITY, home  # noqa: F401 - fixtures
from test_archive_sync import FakeClient as ArchiveFakeClient

CHAT_RID = f"tg:chat:{FORUM_ID}"
TOPIC_RID = f"tg:topic:{FORUM_ID}:141"
ALERTS_RID = f"tg:chat:{CHANNEL_ID}"
BOT = SimpleNamespace(id=123456, first_name="Alerts", username="alertsbot", bot=True)


def run(coroutine):
    return asyncio.run(coroutine)


# -- fake messages and updates -----------------------------------------------


def fake_message(
    number: int,
    *,
    text: str = "deploy finished",
    topic: int | None = None,
    chat_id: int = FORUM_ID,
    links: tuple[str, ...] = (),
    document=None,
    sender=HARRY,
    edited: datetime | None = None,
):
    """A Telethon message as the update handlers see one, links spelled as entities."""
    body = text if not links else text + " " + " ".join(links)
    entities = []
    for url in links:
        offset = len(body[: body.index(url)].encode("utf-16-le")) // 2
        entities.append(MessageEntityUrl(offset=offset, length=len(url)))
    reply_to = (
        SimpleNamespace(reply_to_msg_id=topic, reply_to_top_id=None, forum_topic=True) if topic else None
    )
    media = None if document is None else SimpleNamespace(document=document, photo=None)
    return SimpleNamespace(
        id=number,
        chat_id=chat_id,
        date=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=number),
        raw_text=body,
        message=body,
        entities=entities,
        sender_id=sender.id,
        sender=sender,
        reply_to=reply_to,
        media=media,
        edit_date=edited,
    )


def document(size: int = 2048, mime: str = "application/pdf", name: str = "runbook.pdf"):
    return SimpleNamespace(id=99001, size=size, mime_type=mime, attributes=[SimpleNamespace(file_name=name)])


def reactions(*pairs):
    return SimpleNamespace(
        results=[SimpleNamespace(reaction=SimpleNamespace(emoticon=emoji), count=count) for emoji, count in pairs]
    )


# -- the mapping -------------------------------------------------------------


def test_a_plain_message_is_one_event_scoped_to_its_chat():
    (record,) = watch_events.message_events(fake_message(7))
    event = _rules.Event.from_dict(record)
    assert (event.platform, event.rid, event.subject_id, event.kind) == ("telegram", CHAT_RID, "7", "message")
    assert event.sender_rid == f"tg:user:{HARRY.id}" and not event.sender_is_bot
    assert record["cursor"] == "7"


def test_a_message_in_a_forum_topic_is_scoped_to_the_topic_not_the_chat():
    (record,) = watch_events.message_events(fake_message(9, topic=141))
    assert record["rid"] == TOPIC_RID
    assert record["metadata"]["topic_id"] == 141


def test_one_message_carrying_a_link_and_a_file_is_three_events_with_three_keys():
    records = watch_events.message_events(
        fake_message(11, links=("https://github.com/banozz0/x",), document=document())
    )
    kinds = [record["kind"] for record in records]
    assert kinds == ["message", "link", "media"]
    keys = {_rules.Event.from_dict(record).event_key for record in records}
    # Section 10.3: the key carries the kind, so a rule on `link` and a rule on
    # `message` both see this message and neither dedups the other away.
    assert len(keys) == 3
    link_event = _rules.Event.from_dict(records[1])
    assert link_event.domains == ("github.com",)
    media_event = _rules.Event.from_dict(records[2])
    assert media_event.media_types == ("application/pdf",) and media_event.sizes == (2048,)
    assert media_event.attachments[0]["display_name"] == "runbook.pdf"
    assert media_event.attachments[0]["locator"] == "document:99001"


def test_a_text_url_entity_is_read_as_the_link_it_points_at():
    message = fake_message(12, text="see the notes")
    message.entities = [MessageEntityTextUrl(offset=0, length=3, url="https://example.test/notes")]
    records = watch_events.message_events(message)
    assert [record["kind"] for record in records] == ["message", "link"]
    assert records[1]["links"] == ["https://example.test/notes"]


def test_an_edit_is_its_own_event_and_two_edits_of_one_message_are_two():
    first = watch_events.message_events(fake_message(4, edited=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)), kind="edit")
    second = watch_events.message_events(fake_message(4, edited=datetime(2026, 9, 1, 10, 5, tzinfo=UTC)), kind="edit")
    assert [record["kind"] for record in (*first, *second)] == ["edit", "edit"]
    assert _rules.Event.from_dict(first[0]).event_key != _rules.Event.from_dict(second[0]).event_key
    # The same edit delivered twice is the same key, which is what dedup reads.
    again = watch_events.message_events(fake_message(4, edited=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)), kind="edit")
    assert _rules.Event.from_dict(again[0]).event_key == _rules.Event.from_dict(first[0]).event_key


def test_an_edit_yields_only_itself_even_when_the_message_carries_a_link():
    records = watch_events.message_events(
        fake_message(5, links=("https://example.test/a",), edited=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)),
        kind="edit",
    )
    assert [record["kind"] for record in records] == ["edit"]


def test_a_reaction_state_already_seen_is_the_same_key_and_a_new_one_is_not():
    one = watch_events.reaction_version(reactions(("👍", 1)))
    same = watch_events.reaction_version(reactions(("👍", 1)))
    more = watch_events.reaction_version(reactions(("👍", 2)))
    other = watch_events.reaction_version(reactions(("👍", 1), ("❤", 1)))
    assert one == same
    assert len({one, more, other}) == 3


def test_a_reaction_update_maps_to_the_message_it_is_on():
    update = SimpleNamespace(
        peer=SimpleNamespace(channel_id=1000000001),
        msg_id=21,
        top_msg_id=141,
        reactions=reactions(("👍", 1)),
        actor=SimpleNamespace(user_id=777),
    )
    record = watch_events.reaction_event(update, chat_id=FORUM_ID)
    event = _rules.Event.from_dict(record)
    assert (event.kind, event.rid, event.subject_id) == ("reaction", TOPIC_RID, "21")
    assert event.sender_rid == "tg:user:777"
    # A reaction moves no history cursor: a replay walks messages, not reactions.
    assert record["cursor"] == ""


def test_joins_and_leaves_become_one_event_per_person():
    joined = watch_events.action_event(
        SimpleNamespace(user_joined=True, user_added=False, user_left=False, user_kicked=False, chat_id=FORUM_ID, user_ids=[777, 888], action_message=None)
    )
    left = watch_events.action_event(
        SimpleNamespace(user_joined=False, user_added=False, user_left=True, user_kicked=False, chat_id=FORUM_ID, user_ids=[555], action_message=None)
    )
    assert [record["kind"] for record in joined] == ["member_join", "member_join"]
    assert [record["subject_id"] for record in joined] == ["tg:user:777", "tg:user:888"]
    assert left[0]["kind"] == "member_leave" and left[0]["subject_id"] == "tg:user:555"


def test_a_peer_becomes_the_marked_id_every_rid_here_uses():
    assert watch_events._peer_id(SimpleNamespace(channel_id=1000000001)) == FORUM_ID
    assert watch_events._peer_id(SimpleNamespace(chat_id=400000000005)) == -400000000005
    assert watch_events._peer_id(SimpleNamespace(user_id=4242)) == 4242


# -- the runner, over a recorded stream ---------------------------------------


class RecordedSource:
    """An `EventSource` over a recorded list: what the runner sees, without a client."""

    def __init__(self, live=(), replays=None) -> None:
        self.live = list(live)
        self.replays = dict(replays or {})
        self.asked: list[tuple[str, str]] = []
        self.registered = False
        self.closed = False
        self.dropped = 0

    def register(self):
        self.registered = True

    def close(self):
        self.closed = True

    def events(self):
        for record in self.live:
            yield record

    def replay(self, rid, cursor):
        self.asked.append((rid, cursor))
        for record in self.replays.get(rid, []):
            if int(record["subject_id"]) > int(cursor):
                yield record


class RecordingSender:
    """A `MessageSender` that records instead of sending, and can refuse the way the real one does."""

    def __init__(self, *, refuse=(), flood=None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.refuse = set(refuse)
        self.flood = dict(flood or {})

    def send(self, rid, text, *, approval="yes_allowlist"):
        if rid in self.flood and self.flood[rid]:
            self.flood[rid] -= 1
            raise _runner.RateLimited(3.0, platform="telegram")
        if rid in self.refuse:
            raise CodedError("NOT_ALLOWLISTED", f"{rid} is not in the send allowlist")
        self.sent.append((rid, text))
        return {"status": "ok", "rid": rid, "message_id": 1000 + len(self.sent)}


def a_rule(name="deploys", **overrides):
    data = {
        "schema": _rules.SCHEMA,
        "name": name,
        "trigger": {"events": ["message"]},
        "filter": {"keywords": ["deploy"]},
        "actions": [{"kind": "alert", "destination": {"kind": "platform", "rid": ALERTS_RID}}],
    }
    data.update(overrides)
    return _rules.load_rule(data, which=lambda _name: "/usr/bin/true")


class LoadedRules:
    """A `RuleSet` over rules held in memory, so a runner test needs no directory."""

    def __init__(self, *rules) -> None:
        self.rules = tuple(rules)
        self.directory = Path("/nowhere")

    def reload(self):
        return self.rules

    def get(self, name):
        return next(rule for rule in self.rules if rule.name == name)

    def __iter__(self):
        return iter(self.rules)

    def __len__(self):
        return len(self.rules)


def a_runner(home, rules, sender, *, clock=None, wait=None):
    archive = archive_store.open_archive(home)
    return archive, _runner.Runner(
        archive,
        IDENTITY,
        rules,
        archive_store.paths_for(home),
        sender=sender,
        own_identities=(IDENTITY.id,),
        clock=clock,
        wait=wait or (lambda _seconds: None),
    )


def fires(archive) -> list[tuple[str, str]]:
    rows = archive.connection.execute("SELECT rule_name, event_key FROM rule_fires ORDER BY rowid").fetchall()
    return [(row[0], row[1]) for row in rows]


def test_a_restart_mid_stream_fires_each_recorded_event_exactly_once(home):
    """The P8 row: the runner goes down after two events and replays them on the way back up."""
    stream = [watch_events.message_events(fake_message(number))[0] for number in (1, 2, 3, 4)]
    sender = RecordingSender()

    first, runner = a_runner(home, LoadedRules(a_rule()), sender)
    with first:
        runner.replay(RecordedSource())
        for record in stream[:2]:
            runner.handle(record)
        before = fires(first)
    assert len(before) == 2

    # Back up: the cursor says 2, so the source replays 3 and 4, and re-serves 2.
    second, restarted = a_runner(home, LoadedRules(a_rule()), sender)
    with second:
        source = RecordedSource(replays={CHAT_RID: stream})
        restarted.replay(source)
        for record in stream[1:]:
            restarted.handle(record)
        after = fires(second)

    assert source.asked == [(CHAT_RID, "2")]
    assert len(after) == 4, "each recorded event fired once across the restart"
    assert len(set(after)) == 4
    assert len(sender.sent) == 4


def test_an_alert_carrying_the_origin_marker_from_a_bot_triggers_nothing(home):
    """Section 10.3: this is what stops two runners alerting each other forever."""
    alert = _rules.alert_text("deploys", _rules.Event.from_dict(watch_events.message_events(fake_message(1))[0]))
    echoed = watch_events.message_events(fake_message(50, text=alert, sender=BOT))[0]
    assert echoed["sender_is_bot"] and _rules.carries_marker(echoed["text"])

    sender = RecordingSender()
    archive, runner = a_runner(home, LoadedRules(a_rule(filter={"keywords": []})), sender)
    with archive:
        evaluation = runner.handle(echoed)
        assert evaluation.dropped == "origin_marker"
        assert fires(archive) == []
    assert sender.sent == []


def test_a_message_this_account_sent_is_dropped_before_any_rule_runs(home):
    own = watch_events.message_events(fake_message(51, sender=ACCOUNT))[0]
    sender = RecordingSender()
    archive, runner = a_runner(home, LoadedRules(a_rule()), sender)
    with archive:
        assert runner.handle(own).dropped == "own_identity"
    assert sender.sent == []


def test_an_alert_to_a_destination_outside_the_allowlist_is_refused_and_reported(home):
    sender = RecordingSender(refuse={ALERTS_RID})
    archive, runner = a_runner(home, LoadedRules(a_rule()), sender)
    with archive:
        runner.handle(watch_events.message_events(fake_message(1))[0])
        status = runner.status()
    (delivery,) = status["deliveries"]
    assert (delivery["status"], delivery["error"]) == ("refused", "NOT_ALLOWLISTED")
    assert sender.sent == []


def test_a_flood_wait_is_honoured_once_and_reported_in_status(home):
    waited: list[float] = []
    sender = RecordingSender(flood={ALERTS_RID: 1})
    archive, runner = a_runner(home, LoadedRules(a_rule()), sender, wait=waited.append)
    with archive:
        runner.handle(watch_events.message_events(fake_message(1))[0])
        status = runner.status()
    assert waited == [3.0]
    assert status["waits"][0]["seconds"] == 3.0
    assert len(sender.sent) == 1


def test_an_event_from_another_platform_is_refused_and_never_reaches_the_rules(home):
    sender = RecordingSender()
    archive, runner = a_runner(home, LoadedRules(a_rule()), sender)
    foreign = {**watch_events.message_events(fake_message(1))[0], "platform": "other", "rid": "dc:channel:1"}
    with archive:
        assert runner.handle(foreign) is None
        assert runner.counts["refused"] == 1
        assert fires(archive) == []


def test_a_clock_jump_in_either_direction_neither_loses_nor_duplicates_a_schedule(home):
    """The P8 row, on this tool's own wiring: one fire back, one fire forward, `late` once."""
    clock = _runner.SimulatedClock(wall=1_800_000_000.0, monotonic=1000.0)
    sender = RecordingSender()
    archive, runner = a_runner(home, LoadedRules(a_rule()), sender, clock=clock)
    with archive:
        runner.start()
        try:
            runner.schedules.add(ALERTS_RID, "standup", every="1h")
            assert runner.tick().fired == ()

            # The wall clock goes back two hours; the schedule must not fire twice
            # on the way through the same hour again.
            clock.advance(1)
            clock.jump(-7200)
            backward = runner.tick()
            assert backward.jump["direction"] == "backward"
            assert backward.fired == ()

            # And the wall clock set forward past several occurrences, while the
            # monotonic one says nothing elapsed: one fire, marked late.
            clock.jump(4 * 3600)
            forward = runner.tick()
            assert forward.jump["direction"] == "forward"
            assert [fire.late for fire in forward.fired] == [True]
            assert len(sender.sent) == 1
        finally:
            runner.stop()


def test_every_schedule_the_runner_holds_lists_its_guarantee(home):
    clock = _runner.SimulatedClock(wall=1_800_000_000.0, monotonic=1000.0)
    archive, runner = a_runner(home, LoadedRules(a_rule()), RecordingSender(), clock=clock)
    with archive:
        schedule = runner.schedules.add(ALERTS_RID, "standup", every="1h")
        row = _runner.listing(schedule)
    assert row["guarantee"] == watch_ops.RUNNER_HELD
    assert row["guarantee"].startswith("runner-held")


# -- the sender --------------------------------------------------------------


class SendingClient(ArchiveFakeClient):
    """The archive tests' fake plus a send path and Telegram's scheduled-message calls."""

    def __init__(self, *, flood_on=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sent: list[dict] = []
        self.scheduled: dict[int, list] = {}
        self.next_id = 5000
        self.flood_on = flood_on

    async def connect(self):
        self.connected = True

    async def send_message(self, peer, text, reply_to=None, schedule_date=None):
        if self.flood_on is not None:
            raise FloodWaitError(request=None, capture=self.flood_on)
        self.next_id += 1
        chat_id = getattr(peer, "chat_id", None)
        message = fake_message(self.next_id, text=text, chat_id=chat_id, topic=reply_to)
        self.sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to, "at": schedule_date})
        if schedule_date is not None:
            message.date = schedule_date
            self.scheduled.setdefault(chat_id, []).append(message)
        return message

    async def get_messages(self, peer, ids=None):
        return fake_message(int(ids), chat_id=getattr(peer, "chat_id", None))

    async def get_permissions(self, peer, user):
        from telegram_tools.adapters.account import RIGHT_NAMES

        return SimpleNamespace(**{name: True for name in RIGHT_NAMES})

    async def __call__(self, request):
        name = type(request).__name__
        chat_id = getattr(getattr(request, "peer", None), "chat_id", None)
        if name == "GetScheduledHistoryRequest":
            held = self.scheduled.get(chat_id, [])
            return SimpleNamespace(messages=list(held))
        if name == "DeleteScheduledMessagesRequest":
            self.scheduled[chat_id] = [m for m in self.scheduled.get(chat_id, []) if m.id not in request.id]
            return SimpleNamespace(updates=[])
        return await super().__call__(request)


@pytest.fixture
def loop_for():
    """A real `ClientLoop` over a fake client, torn down however the test ends.

    The bridge is not stubbed: these tests drive the same thread-and-loop the
    runner drives, because "the runner's loop is synchronous and the client's
    is not" is the thing most likely to break.
    """
    opened: list[watch_events.ClientLoop] = []

    def make(client):
        loop = watch_events.ClientLoop(client)
        loop.start()
        opened.append(loop)
        return loop

    yield make
    for loop in opened:
        loop.stop()


def allowlist(*entries):
    return tuple(SendDestination(chat=chat, topic=topic) for chat, topic in entries)


def test_the_sender_posts_through_the_allowlist_and_refuses_by_code_outside_it(loop_for):
    client = SendingClient()
    sender = watch_events.TelegramMessageSender(client, loop_for(client), allowlist((str(CHANNEL_ID), None)))
    record = sender.send(ALERTS_RID, "the alert")
    assert record["status"] == "ok" and client.sent[0]["text"] == "the alert"

    with pytest.raises(CodedError) as refused:
        sender.send(CHAT_RID, "somewhere else")
    assert refused.value.code == "NOT_ALLOWLISTED"


def test_the_sender_refuses_an_approval_that_is_not_the_unattended_one(loop_for):
    client = SendingClient()
    sender = watch_events.TelegramMessageSender(client, loop_for(client), allowlist((str(CHANNEL_ID), None)))
    with pytest.raises(CodedError) as refused:
        sender.send(ALERTS_RID, "x", approval="prompt_y")
    assert refused.value.code == "APPROVAL_REQUIRED"


def test_a_flood_wait_reaches_the_runner_as_rate_limited_rather_than_a_telethon_error(loop_for):
    client = SendingClient(flood_on=42)
    sender = watch_events.TelegramMessageSender(client, loop_for(client), allowlist((str(CHANNEL_ID), None)))
    with pytest.raises(_runner.RateLimited) as limited:
        sender.send(ALERTS_RID, "the alert")
    assert limited.value.retry_after_s == 42.0


def test_the_source_replays_a_scope_from_its_cursor_oldest_first(loop_for):
    client = SendingClient()
    source = watch_events.TelegramEventSource(client, loop_for(client))
    records = list(source.replay(f"tg:topic:{FORUM_ID}:141", "37"))
    assert records, "the fake serves history above the cursor"
    assert all(int(record["subject_id"]) > 37 for record in records)
    assert client.pages[-1]["min_id"] == 37
    assert all(record["rid"] == f"tg:topic:{FORUM_ID}:141" for record in records)


def test_a_bot_replays_nothing_because_a_bot_has_no_history(loop_for):
    client = SendingClient()
    source = watch_events.TelegramEventSource(client, loop_for(client), mode="bot")
    assert list(source.replay(CHAT_RID, "1")) == []
    assert client.pages == []


def test_a_cursor_this_source_did_not_write_replays_nothing(loop_for):
    client = SendingClient()
    source = watch_events.TelegramEventSource(client, loop_for(client))
    assert list(source.replay(CHAT_RID, "40:1:done")) == []


def test_the_queue_yields_none_when_nothing_arrives_so_the_runner_can_tick(loop_for):
    client = SendingClient()
    source = watch_events.TelegramEventSource(client, loop_for(client), idle_s=0.01)
    source.put(watch_events.message_events(fake_message(1))[0])
    stream = source.events()
    assert next(stream)["subject_id"] == "1"
    assert next(stream) is None
    source.close()


# -- the detached session ------------------------------------------------------


def _telethon_session(path: Path, auth_key: bytes | None) -> None:
    """A session file shaped exactly as Telethon writes one, version row included.

    Built by hand rather than through `SQLiteSession` so the test states the
    schema `detached_session` reads: if Telethon ever moves the authorization
    out of that one row, this file stops matching and the test says so.
    """
    from telethon.sessions.sqlite import CURRENT_VERSION

    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE version (version integer primary key)")
        connection.execute("INSERT INTO version VALUES (?)", (CURRENT_VERSION,))
        connection.execute(
            "CREATE TABLE sessions (dc_id integer primary key, server_address text, port integer, auth_key blob, takeout_id integer, tmp_auth_key blob)"
        )
        connection.execute(
            "CREATE TABLE entities (id integer primary key, hash integer not null, username text, phone integer, name text, date integer)"
        )
        connection.execute("CREATE TABLE sent_files (md5_digest blob, file_size integer, type integer, id integer, hash integer, primary key(md5_digest, file_size, type))")
        connection.execute("CREATE TABLE update_state (id integer primary key, pts integer, qts integer, date integer, seq integer)")
        connection.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (2, "149.154.167.51", 443, auth_key, None, None))


def test_the_runner_reads_the_session_without_holding_it_so_one_shot_commands_keep_working(tmp_path):
    """The P8 row: `watch run` and `telegram-tools send` are up at once, no SessionInUseError."""
    session_path = tmp_path / "default"
    key = bytes(range(256)) * 1
    _telethon_session(Path(f"{session_path}.session"), key)

    # Something else holds the file the way Telethon holds it: an open connection
    # inside a write transaction, which is what makes a second Telethon client fail.
    holder = sqlite3.connect(f"{session_path}.session")
    holder.execute("BEGIN IMMEDIATE")
    try:
        copied = detached_session(session_path, "default")
    finally:
        holder.rollback()
        holder.close()

    assert copied.auth_key.key == key
    assert (copied.dc_id, copied.server_address, copied.port) == (2, "149.154.167.51", 443)
    # The copy is in memory: nothing was written beside the session, and the
    # session itself is untouched.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["default.session"]

    # And the other half of the claim: the file is free afterwards, so the
    # ordinary one-shot path opens it the way Telethon always has. This is the
    # session object `create_client` builds, on the same file, at the same time.
    from telethon.sessions import SQLiteSession

    one_shot = SQLiteSession(str(session_path))
    try:
        assert one_shot.auth_key.key == key
        assert one_shot.dc_id == 2
    finally:
        one_shot.close()


def test_a_session_that_was_never_logged_in_refuses_by_name(tmp_path):
    from telegram_tools import login

    with pytest.raises(login.LoginRequired):
        detached_session(tmp_path / "missing", "default")

    empty = tmp_path / "blank.session"
    _telethon_session(empty, None)
    with pytest.raises(login.LoginRequired):
        detached_session(tmp_path / "blank", "default")


# -- the CLI -------------------------------------------------------------------


@pytest.fixture
def run_watch(home, monkeypatch):
    """`main(argv)` against the sending fake, with the terminal and the typed answer controlled."""

    def go(argv, *, client=None, capsys, isatty=False, answer="y", source=None):
        fake = client or SendingClient()

        async def started(_client, *, authorize=True):
            return fake

        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "create_detached_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", started)
        if source is not None:
            monkeypatch.setattr(cli.watch_events, "TelegramEventSource", lambda *_a, **_k: source)
        monkeypatch.setattr(
            cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{answer}\n")
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": f"{answer}")
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return go


def envelope_of(out: str) -> dict:
    payload = json.loads(out)
    validate_envelope(payload)
    return payload


def add_a_rule(run_watch, capsys, *extra, name="deploys"):
    return run_watch(
        ["--json", "watch", "rules", "add", "--name", name, "--on", "message", "--keyword", "deploy", *extra],
        capsys=capsys,
    )


# -- rules ---------------------------------------------------------------------


def test_a_rule_is_written_read_back_and_listed(run_watch, capsys, home):
    code, out, _err, _fake = add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID, "--tag", "deploy")
    assert code == 0
    payload = envelope_of(out)
    rule = payload["result"]["rule"]
    assert rule["name"] == "deploys" and rule["schema"] == _rules.SCHEMA
    assert rule["trigger"]["events"] == ["message"] and rule["filter"]["keywords"] == ["deploy"]
    assert [action["kind"] for action in rule["actions"]] == ["alert", "tag"]
    assert payload["evidence"]["readback"].startswith("deploys.json holds rule deploys")

    written = Path(payload["result"]["path"])
    assert written.stat().st_mode & 0o777 == 0o600
    assert json.loads(written.read_text())["name"] == "deploys"

    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "list"], capsys=capsys)
    assert code == 0
    assert envelope_of(out)["result"]["count"] == 1


def test_a_rule_with_no_action_and_a_rule_with_no_trigger_both_refuse(run_watch, capsys):
    code, _out, err, _fake = run_watch(["watch", "rules", "add", "--name", "empty", "--on", "message"], capsys=capsys)
    assert code == 2 and "at least one action" in err

    code, _out, err, _fake = run_watch(["watch", "rules", "add", "--name", "empty", "--tag", "x"], capsys=capsys)
    assert code == 2 and "at least one event kind" in err


def test_a_command_destination_that_is_not_on_path_is_command_missing_at_rule_load(run_watch, capsys, home):
    """The P8 row: the refusal happens when the rule is written, not when it would fire."""
    code, out, _err, _fake = run_watch(
        ["--json", "watch", "rules", "add", "--name", "hook", "--on", "message", "--alert-command", "definitely-not-on-path --x"],
        capsys=capsys,
    )
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "COMMAND_MISSING"
    assert not (archive_store.paths_for(home).rules / "hook.json").exists()


def test_an_action_kind_outside_the_closed_list_cannot_be_written_by_hand_either(run_watch, capsys, home):
    """There is no `download` and no `send`: a hand-written rule with one is RULE_INVALID."""
    paths = archive_store.paths_for(home)
    paths.rules.mkdir(mode=0o700, parents=True, exist_ok=True)
    (paths.rules / "sneaky.json").write_text(
        json.dumps(
            {
                "schema": _rules.SCHEMA,
                "name": "sneaky",
                "trigger": {"events": ["media"]},
                "actions": [{"kind": "download"}],
            }
        )
    )
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "list"], capsys=capsys)
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "RULE_INVALID"


def test_editing_replaces_the_fields_the_flags_name_and_leaves_the_rest(run_watch, capsys, home):
    add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID, "--cooldown", "300")
    code, out, _err, _fake = run_watch(
        ["--json", "watch", "rules", "edit", "--name", "deploys", "--domain", "github.com"], capsys=capsys
    )
    assert code == 0
    rule = envelope_of(out)["result"]["rule"]
    assert rule["filter"]["domains"] == ["github.com"]
    assert rule["filter"]["keywords"] == ["deploy"], "an untouched field stays"
    assert rule["cooldown_s"] == 300
    assert [action["kind"] for action in rule["actions"]] == ["alert"]

    # `none` is how a list is emptied, the same word the administration flags take.
    code, out, _err, _fake = run_watch(
        ["--json", "watch", "rules", "edit", "--name", "deploys", "--keyword", "none"], capsys=capsys
    )
    assert envelope_of(out)["result"]["rule"]["filter"]["keywords"] == []


def test_adding_a_rule_that_is_already_there_refuses_and_names_edit(run_watch, capsys):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    code, out, _err, _fake = add_a_rule(run_watch, capsys, "--tag", "deploy")
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "RULE_INVALID"
    assert error["hint"] == "telegram-tools watch rules edit --name deploys"


def test_disable_and_enable_flip_the_file_and_read_it_back(run_watch, capsys):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "disable", "--name", "deploys"], capsys=capsys)
    assert code == 0 and envelope_of(out)["result"]["rule"]["enabled"] is False
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "enable", "--name", "deploys"], capsys=capsys)
    assert code == 0 and envelope_of(out)["result"]["rule"]["enabled"] is True


def test_removing_a_rule_asks_first_and_a_no_keeps_the_file(run_watch, capsys, home):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    path = archive_store.paths_for(home).rules / "deploys.json"

    # No terminal: the y/N cannot be asked, so it refuses rather than assuming.
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "remove", "--name", "deploys"], capsys=capsys)
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert path.exists()

    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "remove", "--name", "deploys"], capsys=capsys, isatty=True, answer="n")
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    assert path.exists()

    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "remove", "--name", "deploys"], capsys=capsys, isatty=True, answer="y")
    assert code == 0 and not path.exists()
    assert envelope_of(out)["evidence"]["readback"] == "deploys.json is gone"


def test_test_says_what_would_fire_and_fires_nothing(run_watch, capsys, home, tmp_path):
    add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID)
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(watch_events.message_events(fake_message(1))[0]))

    code, out, _err, fake = run_watch(["--json", "watch", "rules", "test", "--event", str(event_file)], capsys=capsys)
    assert code == 0
    result = envelope_of(out)["result"]
    (entry,) = result["rules"]
    assert entry["would_fire"] == ["alert"]
    assert result["dedup"] == "not consulted"
    assert fake.sent == [], "a test sends nothing"

    # A message that does not match says which constraint stopped it.
    event_file.write_text(json.dumps(watch_events.message_events(fake_message(2, text="nothing to see"))[0]))
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "test", "--event", str(event_file)], capsys=capsys)
    (entry,) = envelope_of(out)["result"]["rules"]
    assert entry["would_fire"] == [] and entry["filter"]["keywords"] is False


def test_every_rule_write_leaves_one_audit_line(run_watch, capsys, home):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    run_watch(["--json", "watch", "rules", "disable", "--name", "deploys"], capsys=capsys)
    lines = [json.loads(line) for line in (home / ".telegram-tools" / "audit.jsonl").read_text().splitlines()]
    assert [line["command"] for line in lines] == ["watch rules add", "watch rules disable"]
    assert all(line["status"] == "ok" for line in lines)


# -- the runner's lifecycle through the CLI ------------------------------------


def test_a_second_runner_exits_two_with_runner_locked_naming_the_holder(run_watch, capsys, home):
    """The P8 row. The refusal happens before anything connects."""
    paths = archive_store.paths_for(home)
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = _runner.Lock(paths.runner_lock)
    lock.acquire()
    try:
        code, out, _err, fake = run_watch(["--json", "watch", "run"], capsys=capsys)
    finally:
        lock.release()
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "RUNNER_LOCKED"
    assert str(lock.pid) in error["message"]
    assert fake.pages == [], "it refused before it connected"


def test_an_alert_outside_the_send_allowlist_is_reported_not_allowlisted(run_watch, capsys, home):
    """The P8 row: automated alerts answer to the same list an unattended `send --yes` does."""
    add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID)
    source = RecordedSource(live=[watch_events.message_events(fake_message(1))[0]])
    code, out, _err, fake = run_watch(["--json", "watch", "run"], capsys=capsys, source=source)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["fired"] == 1, "the rule fired; the delivery is what was refused"
    (delivery,) = result["status"]["deliveries"]
    assert (delivery["status"], delivery["error"]) == ("refused", "NOT_ALLOWLISTED")
    assert fake.sent == []


def test_the_runner_replays_then_handles_the_live_stream_and_reports_what_it_did(run_watch, capsys, home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHANNEL_ID))
    add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID)
    stream = [watch_events.message_events(fake_message(number))[0] for number in (1, 2)]
    source = RecordedSource(live=stream, replays={CHAT_RID: stream})
    code, out, _err, fake = run_watch(["--json", "watch", "run"], capsys=capsys, source=source)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["events"] == 2 and result["fired"] == 2
    assert result["rules"] == 1
    assert source.registered and source.closed
    assert [sent["text"].splitlines()[0] for sent in fake.sent] == ["[deploys] message in " + CHAT_RID + " from tg:user:777"] * 2
    # Every alert ends with the origin marker, which is what a second runner drops on.
    assert all(_rules.carries_marker(sent["text"]) for sent in fake.sent)

    # Up again over the same stream: the cursor is at 2, the replay re-serves
    # nothing above it, and the two events it does see are duplicates.
    code, out, _err, again = run_watch(["--json", "watch", "run"], capsys=capsys, source=RecordedSource(live=stream, replays={CHAT_RID: stream}))
    assert code == 0
    assert envelope_of(out)["result"]["fired"] == 0
    assert again.sent == []


def test_status_says_who_holds_the_lock_and_how_many_rules_load(run_watch, capsys, home):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    code, out, _err, _fake = run_watch(["--json", "watch", "status"], capsys=capsys)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["running"] is False and result["rules"] == 1
    assert result["guarantees"] == list(_runner.GUARANTEES)

    code, out, err, _fake = run_watch(["watch", "status"], capsys=capsys)
    assert "Not running (no lock file)" in out and "Rules loaded  1" in out


def test_stop_and_reload_refuse_when_no_runner_holds_the_lock(run_watch, capsys):
    for verb in ("stop", "reload"):
        code, out, _err, _fake = run_watch(["--json", "watch", verb], capsys=capsys)
        assert code == 2, verb
        assert envelope_of(out)["error"]["code"] == "RUNNER_NOT_RUNNING"


# -- the two guarantees --------------------------------------------------------


def test_send_at_hands_the_message_to_telegram_and_reports_it_server_held(run_watch, capsys):
    """The P8 row: `send --at` lists as server-held."""
    when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
    code, out, _err, fake = run_watch(
        ["--json", "send", "--chat", "@agencyalerts", "--text", "standup", "--at", when.isoformat(), "--yes"],
        capsys=capsys,
        client=SendingClient(),
    )
    assert code == 2, "a --yes send still needs the allowlist"

    code, out, _err, fake = run_watch(
        ["--json", "send", "--chat", "@agencyalerts", "--text", "standup", "--at", when.isoformat()],
        capsys=capsys,
        client=SendingClient(),
        isatty=True,
        answer="y",
    )
    assert code == 0, out
    payload = envelope_of(out)
    assert payload["result"]["guarantee"] == watch_ops.SERVER_HELD
    assert payload["result"]["scheduled_at"] == when.isoformat()
    assert payload["result"]["sent"] is False
    assert fake.sent[0]["at"] == when
    # It is read back from what Telegram is holding, not from the chat's history.
    readback = payload["evidence"]["readback"]
    assert "is scheduled in" in readback and watch_ops.SERVER_HELD in readback


def test_a_send_at_in_the_past_refuses_before_anything_is_asked(run_watch, capsys):
    code, _out, err, fake = run_watch(
        ["send", "--chat", "@agencyalerts", "--text", "late", "--at", "2020-01-01T09:00"], capsys=capsys
    )
    assert code == 2 and "in the past" in err
    assert fake.sent == []


def test_schedule_post_is_runner_held_and_says_so_before_it_is_answered(run_watch, capsys, home):
    """The P8 row: `schedule post` lists as runner-held."""
    when = (datetime.now().astimezone() + timedelta(hours=2)).replace(microsecond=0)
    code, out, _err, fake = run_watch(
        ["schedule", "post", "--chat", "@agencyalerts", "--text", "standup", "--at", when.isoformat()],
        capsys=capsys,
        answer="y",
    )
    assert code == 0, out
    assert watch_ops.RUNNER_HELD in out
    assert fake.sent == [], "the runner posts it later; nothing is sent now"

    code, out, _err, _fake = run_watch(["--json", "schedule", "list"], capsys=capsys)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["native"] == []
    (row,) = result["local"]
    assert row["guarantee"] == watch_ops.RUNNER_HELD and row["rid"] == ALERTS_RID


def test_schedule_post_takes_a_repeat_and_refuses_a_repeat_it_cannot_read(run_watch, capsys):
    code, out, _err, _fake = run_watch(
        ["--json", "schedule", "post", "--chat", "@agencyalerts", "--text", "standup", "--every", "0 9 * * mon"],
        capsys=capsys,
        isatty=True,
        answer="y",
    )
    assert code == 0
    assert envelope_of(out)["result"]["schedule"]["every"] == "0 9 * * mon"

    code, _out, err, _fake = run_watch(
        ["schedule", "post", "--chat", "@agencyalerts", "--text", "x", "--every", "sometimes"], capsys=capsys
    )
    assert code == 2 and "cron" in err


def test_schedule_list_shows_both_kinds_each_with_its_own_guarantee(run_watch, capsys, home):
    when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
    client = SendingClient()
    run_watch(["send", "--chat", "@agencyalerts", "--text", "native", "--at", when.isoformat()], capsys=capsys, client=client, answer="y")
    run_watch(["schedule", "post", "--chat", "@agencyalerts", "--text", "local", "--every", "1d"], capsys=capsys, client=client, answer="y")

    code, out, _err, _fake = run_watch(["--json", "schedule", "list", "--chat", "@agencyalerts"], capsys=capsys, client=client)
    assert code == 0
    result = envelope_of(out)["result"]
    assert [row["guarantee"] for row in result["native"]] == [watch_ops.SERVER_HELD]
    assert [row["guarantee"] for row in result["local"]] == [watch_ops.RUNNER_HELD]

    code, out, _err, _fake = run_watch(["schedule", "list", "--chat", "@agencyalerts"], capsys=capsys, client=client)
    assert "Held by Telegram (server-held)" in out and "Held by this runner (runner-held" in out


def test_cancelling_one_telegram_holds_needs_its_chat_and_reads_back_that_it_is_gone(run_watch, capsys):
    when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
    client = SendingClient()
    code, out, _err, _fake = run_watch(
        ["--json", "send", "--chat", "@agencyalerts", "--text", "native", "--at", when.isoformat()],
        capsys=capsys,
        client=client,
        isatty=True,
        answer="y",
    )
    message_id = envelope_of(out)["result"]["message_id"]

    code, out, _err, _fake = run_watch(
        ["--json", "schedule", "cancel", "--id", str(message_id), "--chat", "@agencyalerts"],
        capsys=capsys,
        client=client,
        isatty=True,
        answer="y",
    )
    assert code == 0, out
    payload = envelope_of(out)
    assert payload["result"]["cancelled_schedule"]["guarantee"] == watch_ops.SERVER_HELD
    assert payload["evidence"]["readback"] == f"Telegram no longer holds {message_id}"
    assert client.scheduled[CHANNEL_ID] == []


def test_cancelling_one_this_runner_holds_needs_no_chat_and_no_connection(run_watch, capsys, home):
    code, out, _err, _fake = run_watch(
        ["--json", "schedule", "post", "--chat", "@agencyalerts", "--text", "standup", "--every", "1d"],
        capsys=capsys,
        isatty=True,
        answer="y",
    )
    schedule_id = envelope_of(out)["result"]["schedule"]["id"]

    code, out, _err, fake = run_watch(["--json", "schedule", "cancel", "--id", schedule_id], capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["cancelled_schedule"]["id"] == schedule_id
    assert fake.pages == [] and fake.sent == []

    code, out, _err, _fake = run_watch(["--json", "schedule", "cancel", "--id", schedule_id], capsys=capsys, isatty=True, answer="y")
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_NOT_FOUND"


def test_a_schedule_the_account_cannot_post_into_is_refused_at_the_preflight(run_watch, capsys, monkeypatch):
    client = SendingClient()

    async def no_rights(_peer, _user):
        return SimpleNamespace(send_messages=False)

    monkeypatch.setattr(client, "get_permissions", no_rights)
    code, out, _err, _fake = run_watch(
        ["--json", "schedule", "post", "--chat", "@agencyalerts", "--text", "x", "--every", "1d"],
        capsys=capsys,
        client=client,
    )
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"


# -- bot mode ------------------------------------------------------------------


def test_a_bot_may_watch_but_may_not_schedule_and_may_not_send_at(run_watch, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "alerts:123456:AAtest")
    when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
    for argv, named in (
        (["--json", "--as-bot", "alerts", "send", "--chat", "@agencyalerts", "--text", "x", "--at", when.isoformat()], "send --at"),
        (["--json", "--as-bot", "alerts", "schedule", "list"], "schedule list"),
        (["--json", "--as-bot", "alerts", "schedule", "post", "--chat", "c", "--text", "x", "--every", "1d"], "schedule post"),
    ):
        code, out, _err, fake = run_watch(argv, capsys=capsys)
        assert code == 2, argv
        error = envelope_of(out)["error"]
        assert error["code"] == "IDENTITY_MODE_UNSUPPORTED", argv
        assert named in error["message"], argv
        assert fake.sent == []

    # `watch` itself is not refused: a bot receives updates for the chats it is in.
    assert "watch" in cli.BOT_MODE_COMMANDS


# -- the menu ------------------------------------------------------------------


def test_the_menu_rule_form_reaches_every_flag_the_rules_parser_defines():
    from telegram_tools import menu

    parser = cli.build_parser()
    add = parser._subparsers._group_actions[0].choices["watch"]._subparsers._group_actions[0].choices["rules"]
    add = add._subparsers._group_actions[0].choices["add"]
    flags = {action.dest for action in add._actions if action.dest not in ("help",)}
    staged = {key for key, _label, _kind in menu.RULE_FIELDS}
    assert flags - staged == set(), f"the form reaches no row for {flags - staged}"
