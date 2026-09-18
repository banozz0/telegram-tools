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
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import helpers
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools import watch as watch_ops
from telegram_tools._core import rules as _rules
from telegram_tools._core import runner as _runner
from telegram_tools._core.contract import CodedError, validate_envelope
from telegram_tools.adapters import events as watch_events
from telegram_tools.adapters.archive import scope_rid_for
from telegram_tools.client import detached_session
from telegram_tools.config import SendDestination
from test_archive_sync import ACCOUNT, CHANNEL_ID, FORUM_ID, HARRY, IDENTITY, history, home  # noqa: F401 - fixtures
from test_archive_sync import FakeClient as ArchiveFakeClient
from test_adapters import holding, member

CHAT_RID = f"tg:chat:{FORUM_ID}"
# Where a message with no topic header in that forum really is: General, topic
# 1, the one every other surface here already calls it (card agent-bo-95422355).
GENERAL_RID = f"tg:topic:{FORUM_ID}:1"
TOPIC_RID = f"tg:topic:{FORUM_ID}:141"
ALERTS_RID = f"tg:chat:{CHANNEL_ID}"
BOT = SimpleNamespace(id=123456, first_name="Alerts", username="alertsbot", bot=True)


def run(coroutine):
    return asyncio.run(coroutine)


# -- fake messages and updates -----------------------------------------------


def fake_chat(chat_id: int | None):
    """The chat entity Telethon hangs on every message it builds.

    `_preprocess_updates` puts the update container's own chats on the update
    and `Message._finish_init` reads the message's own out of them, so a
    handler always has it; checked live on 2026-09-18 against the fixture
    forum, where every message's `chat` was a full `Channel` with `forum=True`
    and `min=False`. Without it here no fake could tell a forum from a plain
    group, which is exactly the difference between "no topic" and "General".
    """
    return SimpleNamespace(id=abs(chat_id or 0), forum=chat_id == FORUM_ID, megagroup=True)


def fake_reply_header(topic: int | None, replies_to: int | None):
    """The `MessageReplyHeader` Telegram really sends, for each of the four cases.

    All four read off `-1004458956767` on 2026-09-18 (`message reply` and
    `iter_messages`), because the one this tool got wrong is the one that is
    not there at all:

    * a message in General -- no header;
    * a reply inside General -- `forum_topic` off, the replied-to id, no top id;
    * a message in topic T -- `forum_topic` on, `reply_to_msg_id` T, no top id;
    * a reply inside topic T -- `forum_topic` on, top id T, the replied-to id.
    """
    if topic and replies_to:
        return SimpleNamespace(reply_to_msg_id=replies_to, reply_to_top_id=topic, forum_topic=True)
    if topic:
        return SimpleNamespace(reply_to_msg_id=topic, reply_to_top_id=None, forum_topic=True)
    if replies_to:
        return SimpleNamespace(reply_to_msg_id=replies_to, reply_to_top_id=None, forum_topic=False)
    return None


def fake_message(
    number: int,
    *,
    text: str = "deploy finished",
    topic: int | None = None,
    replies_to: int | None = None,
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
    reply_to = fake_reply_header(topic, replies_to)
    media = None if document is None else SimpleNamespace(document=document, photo=None)
    return SimpleNamespace(
        id=number,
        chat_id=chat_id,
        chat=fake_chat(chat_id),
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
    (record,) = watch_events.message_events(fake_message(7, chat_id=CHANNEL_ID))
    event = _rules.Event.from_dict(record)
    assert (event.platform, event.rid, event.subject_id, event.kind) == ("telegram", ALERTS_RID, "7", "message")
    assert event.sender_rid == f"tg:user:{HARRY.id}" and not event.sender_is_bot
    assert record["cursor"] == "7"


def test_a_message_in_a_forum_topic_is_scoped_to_the_topic_not_the_chat():
    (record,) = watch_events.message_events(fake_message(9, topic=141))
    assert record["rid"] == TOPIC_RID
    assert record["metadata"]["topic_id"] == 141


def test_a_general_message_is_scoped_to_the_general_topic_the_archive_already_uses():
    """Card agent-bo-95422355: General had two names, and the topic rid is the one.

    Live on 2026-09-18: message 54 went to General with `send --topic 1` and
    moved the runner's cursor for `tg:chat:-1004458956767`, while `archive
    sync --scope tg:topic:-1004458956767:1` held the same three messages. A
    forum has no chat scope at all -- `TelegramArchiveSource` lists one scope
    per topic -- so the chat rid was a scope nothing else wrote to.
    """
    (record,) = watch_events.message_events(fake_message(54))
    assert record["rid"] == GENERAL_RID
    # The archive's own spelling of the same place, from the other adapter.
    assert record["rid"] == scope_rid_for(str(FORUM_ID), 1)
    assert record["metadata"]["topic_id"] == 1


def test_a_rule_scoped_to_general_from_the_topic_list_matches_a_general_message():
    """The menu lists General with the other topics, so this is how a person scopes it."""
    rule = a_rule(filter={"keywords": ["deploy"], "scopes": [GENERAL_RID]})
    (record,) = watch_events.message_events(fake_message(54))
    assert rule.filter.matches(_rules.Event.from_dict(record), IDENTITY.id)


def test_a_reply_inside_general_is_general_and_not_the_message_it_replies_to():
    """Live: a reply in General carries `forum_topic` off and the replied-to id.

    Nothing may read that id as a topic, and the message is still General's.
    """
    (record,) = watch_events.message_events(fake_message(57, replies_to=56))
    assert record["rid"] == GENERAL_RID
    assert record["metadata"]["reply_to"] == 56


def test_a_reply_inside_a_topic_is_the_topic_and_not_the_message_it_replies_to():
    (record,) = watch_events.message_events(fake_message(58, topic=141, replies_to=55))
    assert record["rid"] == TOPIC_RID
    assert record["metadata"]["reply_to"] == 55


def test_a_message_in_a_plain_group_has_no_topic_at_all():
    """Only a forum turns "no topic header" into General; a group has no topics."""
    assert watch_events.topic_of(fake_message(7, chat_id=CHANNEL_ID)) is None
    assert watch_events.topic_of(fake_message(7)) == 1


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


def test_a_reaction_in_general_is_the_general_topic_too():
    """A reaction update names no topic for General, exactly as a message does not.

    The update carries no message to read the chat off, so the source that
    registered the handler says whether the chat is a forum; without that a
    rule scoped to General would see the message and miss the reaction on it.
    """
    update = SimpleNamespace(
        peer=SimpleNamespace(channel_id=1000000001),
        msg_id=54,
        top_msg_id=None,
        reactions=reactions(("👍", 1)),
        actor=SimpleNamespace(user_id=777),
    )
    record = watch_events.reaction_event(update, chat_id=FORUM_ID, forum=True)
    assert record["rid"] == GENERAL_RID
    assert record["metadata"]["topic_id"] == 1
    # The same update in a chat that has no topics stays the chat's.
    plain = watch_events.reaction_event(update, chat_id=CHANNEL_ID)
    assert plain["rid"] == ALERTS_RID


def test_joins_and_leaves_become_one_event_per_person():
    joined = watch_events.action_event(
        SimpleNamespace(user_joined=True, user_added=False, user_left=False, user_kicked=False, chat_id=CHANNEL_ID, user_ids=[777, 888], action_message=None)
    )
    left = watch_events.action_event(
        SimpleNamespace(user_joined=False, user_added=False, user_left=True, user_kicked=False, chat_id=CHANNEL_ID, user_ids=[555], action_message=None)
    )
    assert [record["kind"] for record in joined] == ["member_join", "member_join"]
    assert [record["subject_id"] for record in joined] == ["tg:user:777", "tg:user:888"]
    assert [record["rid"] for record in joined] == [ALERTS_RID, ALERTS_RID]
    assert left[0]["kind"] == "member_leave" and left[0]["subject_id"] == "tg:user:555"


def test_a_join_in_a_forum_is_scoped_where_telegram_files_its_service_message():
    """Telegram posts the join into General, and that is where the archive holds it.

    The action's own service message says which topic it landed in, so the
    join is scoped there rather than to a chat rid a forum never has.
    """
    joined = watch_events.action_event(
        SimpleNamespace(
            user_joined=True,
            user_added=False,
            user_left=False,
            user_kicked=False,
            chat_id=FORUM_ID,
            user_ids=[777],
            action_message=fake_message(44, text=""),
        )
    )
    assert [record["rid"] for record in joined] == [GENERAL_RID]


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
        source = RecordedSource(replays={GENERAL_RID: stream})
        restarted.replay(source)
        for record in stream[1:]:
            restarted.handle(record)
        after = fires(second)

    assert source.asked == [(GENERAL_RID, "2")]
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

    # `schedule`, spelled the way Telethon's own client methods spell it, and with
    # no `**kwargs` to absorb a different spelling: this fake agreeing with a
    # keyword Telethon does not name is how every `send --at` shipped broken.
    async def send_message(self, peer, text, reply_to=None, schedule=None):
        if self.flood_on is not None:
            raise FloodWaitError(request=None, capture=self.flood_on)
        self.next_id += 1
        chat_id = getattr(peer, "chat_id", None)
        message = fake_message(self.next_id, text=text, chat_id=chat_id, topic=reply_to)
        self.sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to, "at": schedule})
        if schedule is not None:
            message.date = schedule
            self.scheduled.setdefault(chat_id, []).append(message)
        return message

    async def get_messages(self, peer, ids=None):
        return fake_message(int(ids), chat_id=getattr(peer, "chat_id", None))

    async def get_permissions(self, peer, user):
        from telegram_tools.adapters.account import RIGHT_NAMES

        return holding(RIGHT_NAMES)

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


def test_a_replay_of_general_yields_events_in_general_so_its_cursor_can_move(loop_for):
    """A replay serves the scope it was asked for, General included.

    The runner saves a cursor under the *event's* rid, so General events
    carrying the chat rid meant `tg:topic:<chat>:1` never advanced: every
    restart walked the whole of General again, forever.
    """
    client = SendingClient(rows={(FORUM_ID, 1): history(4, start=50, forum=True)})
    client.topics[FORUM_ID] = [SimpleNamespace(id=1, title="General", top_message=902), *client.topics[FORUM_ID]]
    source = watch_events.TelegramEventSource(client, loop_for(client))
    records = list(source.replay(GENERAL_RID, "50"))
    assert sorted(record["subject_id"] for record in records) == ["51", "52", "53"]
    assert all(record["rid"] == GENERAL_RID for record in records)
    assert client.pages[-1]["scope"] == (FORUM_ID, 1)


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


# -- the exit path -------------------------------------------------------------


class TelethonShapedClient:
    """The two facts about a real client's teardown, and nothing else.

    `loop` is whatever loop runs on the *calling* thread -- Telethon's
    `TelegramBaseClient.loop` is `helpers.get_running_loop()` -- and
    `disconnect()` is not a coroutine while one is running: it puts the
    teardown on that loop and hands back a shield over it. Every other fake in
    this suite is an `async def disconnect`, which is exactly why none of them
    could ever show what the runner's exit path did to a person.
    """

    def __init__(self) -> None:
        self.background: list[asyncio.Task] = []
        self.connected_on: asyncio.AbstractEventLoop | None = None
        self.torn_down_on: asyncio.AbstractEventLoop | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return helpers.get_running_loop()

    async def connect(self) -> None:
        self.connected_on = asyncio.get_running_loop()
        # Telethon's update, keepalive, send and recv loops, which is what the
        # live run named in its "Task was destroyed but it is pending!" lines.
        self.background = [self.connected_on.create_task(self._idle()) for _ in range(4)]

    async def _idle(self) -> None:
        while True:
            await asyncio.sleep(3600)

    async def _disconnect_coro(self) -> None:
        self.torn_down_on = asyncio.get_running_loop()
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.background = []

    def disconnect(self):
        if self.loop.is_running():
            return asyncio.shield(self.loop.create_task(self._disconnect_coro()))
        return self.loop.run_until_complete(self._disconnect_coro())


# What the child process below does: the bridge up over that client, and down
# again, inside a loop of its own the way `watch run` sits inside the CLI's.
EXIT_PATH = """
import asyncio

from telegram_tools.adapters.events import ClientLoop
from test_watch import TelethonShapedClient


async def watch_run():
    bridge = ClientLoop(TelethonShapedClient())
    bridge.start()
    bridge.stop()


asyncio.run(watch_run())
"""

# Every line shape the live run of 2026-09-17 printed after the Done screen.
TEARDOWN_NOISE = (
    "Traceback (most recent call last)",
    "Task was destroyed but it is pending!",
    "Event loop is closed",
    "Future exception was never retrieved",
    "Exception ignored in",
)


def test_the_client_is_torn_down_on_its_own_loop_and_not_the_callers():
    """`stop()` runs the disconnect where the client's tasks live.

    The bug this pins: `client.disconnect()` read from `stop()` resolves
    `client.loop` to the CLI's loop, so the teardown was scheduled there and
    cancelled this bridge's tasks from the wrong thread, after this bridge had
    closed. It also handed back a future rather than a coroutine, which `call`
    refused with a `TypeError` that `stop()` swallowed -- so the disconnect was
    never waited for at all.
    """
    client = TelethonShapedClient()

    async def cli_loop():
        bridge = watch_events.ClientLoop(client)
        bridge.start()
        bridge.stop()

    run(cli_loop())
    assert client.torn_down_on is not None, "stop() never ran the disconnect"
    assert client.torn_down_on is client.connected_on
    assert client.background == [], "the client's own loops outlived the bridge"


def test_leaving_the_runner_prints_nothing_after_the_done_screen():
    """The exit path itself, in a process of its own, and its stderr.

    The noise is printed by the garbage collector as the interpreter goes down,
    so only a real exit can show it; in-process there is nothing to catch. A
    person who has just used the tool's headline feature sees the Done screen
    and then this, so "nothing" is the whole check.
    """
    done = subprocess.run(
        [sys.executable, "-c", EXIT_PATH],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    printed = [line for line in done.stderr.splitlines() if any(mark in line for mark in TEARDOWN_NOISE)]
    assert not printed, "the exit path printed:\n" + done.stderr


class FailingTeardownClient(TelethonShapedClient):
    """A client whose disconnect raises, so `stop()` has something to decide about."""

    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    async def _disconnect_coro(self) -> None:
        await super()._disconnect_coro()
        raise self.failure


def test_a_transport_failure_at_shutdown_is_tolerated_and_said_out_loud(capsys):
    """A disconnect that fails for a reason a network has is not the run's exit.

    It still has to be visible: a teardown that silently did nothing is how the
    exit path stayed broken for the life of the feature.
    """
    client = FailingTeardownClient(OSError("the socket went away"))

    async def cli_loop():
        bridge = watch_events.ClientLoop(client)
        bridge.start()
        bridge.stop()
        return bridge

    bridge = run(cli_loop())
    assert (bridge.loop, bridge.thread) == (None, None), "the bridge was not torn down"
    assert "the socket went away" in capsys.readouterr().err


def test_a_programming_error_at_shutdown_reaches_the_caller_rather_than_vanishing(capsys):
    """A `TypeError` from the bridge itself is a bug, and a bug must be seen.

    The bare `except Exception` this replaces hid exactly this shape for the
    life of the watch feature -- `call()` refusing a future it was handed --
    which is why the exit path was broken from the day it shipped. The loop and
    its thread still go down: the report is the point, not a leaked thread.
    """
    client = FailingTeardownClient(TypeError("a coroutine was expected"))

    async def cli_loop():
        bridge = watch_events.ClientLoop(client)
        bridge.start()
        with pytest.raises(TypeError, match="a coroutine was expected"):
            bridge.stop()
        return bridge

    bridge = run(cli_loop())
    assert (bridge.loop, bridge.thread) == (None, None), "the bridge outlived the failure"


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


def test_rules_remove_yes_skips_the_prompt_and_the_preview_still_prints(run_watch, capsys, home):
    """Card agent-bo-95422198: no terminal, answer n, the file is still gone."""
    code, out, _err, _fake = run_watch(["--json", "watch", "rules", "add", "--name", "deploys", "--on", "message", "--tag", "seen"], capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    path = archive_store.paths_for(home).rules / "deploys.json"
    assert path.exists()
    code, out, err, _fake = run_watch(["--json", "watch", "rules", "remove", "--name", "deploys", "--yes"], capsys=capsys, isatty=False, answer="n")
    assert code == 0, out
    assert not path.exists() and "[y/N]" not in err and "deploys" in err


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
    source = RecordedSource(live=stream, replays={GENERAL_RID: stream})
    code, out, _err, fake = run_watch(["--json", "watch", "run"], capsys=capsys, source=source)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["events"] == 2 and result["fired"] == 2
    assert result["rules"] == 1
    assert source.registered and source.closed
    assert [sent["text"].splitlines()[0] for sent in fake.sent] == ["[deploys] message in " + GENERAL_RID + " from tg:user:777"] * 2
    # Every alert ends with the origin marker, which is what a second runner drops on.
    assert all(_rules.carries_marker(sent["text"]) for sent in fake.sent)

    # Up again over the same stream: the cursor is at 2, the replay re-serves
    # nothing above it, and the two events it does see are duplicates.
    code, out, _err, again = run_watch(["--json", "watch", "run"], capsys=capsys, source=RecordedSource(live=stream, replays={GENERAL_RID: stream}))
    assert code == 0
    assert envelope_of(out)["result"]["fired"] == 0
    assert again.sent == []


def test_the_runner_says_what_it_did_when_it_stops(run_watch, capsys, home, monkeypatch):
    """The Done screen printed nothing after a four-minute run (Telegram 7, 2026-09-17).

    The counts were already in the envelope; a person driving the menu never
    saw one of them, so a runner that fired nothing and a runner that fired
    forty looked identical on the way out.
    """
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHANNEL_ID))
    add_a_rule(run_watch, capsys, "--alert-to", ALERTS_RID)
    stream = [watch_events.message_events(fake_message(number))[0] for number in (1, 2)]
    source = RecordedSource(live=stream)
    code, out, _err, _fake = run_watch(["watch", "run"], capsys=capsys, source=source)
    assert code == 0
    assert "Runner stopped" in out
    assert "Events        2 seen" in out
    assert "Fired         2" in out


def test_a_first_write_on_a_fresh_machine_leaves_the_tree_private(run_watch, capsys, home):
    """`mkdir(parents=True)` modes the leaf only, and every later write refuses over a loose root.

    Twice now a new writer has created `~/.telegram-tools` at the umask: the
    audit log, then the rules directory. Both paths are checked here, on a home
    where the directory does not exist yet.
    """
    root = home / ".telegram-tools"
    assert not root.exists()

    add_a_rule(run_watch, capsys, "--tag", "deploy")
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "rules").stat().st_mode & 0o777 == 0o700

    # And the second write, which is what used to refuse.
    code, _out, err, _fake = run_watch(["--json", "watch", "rules", "disable", "--name", "deploys"], capsys=capsys)
    assert code == 0, err


def test_status_says_who_holds_the_lock_and_how_many_rules_load(run_watch, capsys, home):
    add_a_rule(run_watch, capsys, "--tag", "deploy")
    code, out, _err, _fake = run_watch(["--json", "watch", "status"], capsys=capsys)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["running"] is False and result["rules"] == 1
    assert result["guarantees"] == list(_runner.GUARANTEES)

    code, out, err, _fake = run_watch(["watch", "status"], capsys=capsys)
    assert "Not running (nothing holds the lock)" in out and "Rules loaded  1" in out


def test_status_does_not_claim_there_is_no_lock_file_when_one_is_lying_there(run_watch, capsys, home):
    """A released lock is an empty file, not an absent one (Telegram 7, 2026-09-17).

    `Lock.release()` truncates and unlocks; the file stays on disk, so a status
    that said "no lock file" told a person to go looking for something that was
    right there. What it knows is that nobody holds it.
    """
    paths = archive_store.paths_for(home)
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = _runner.Lock(paths.runner_lock)
    lock.acquire()
    lock.release()
    assert paths.runner_lock.exists() and paths.runner_lock.read_text() == ""

    code, out, _err, _fake = run_watch(["watch", "status"], capsys=capsys)
    assert code == 0
    assert "no lock file" not in out
    assert "Not running (nothing holds the lock)" in out


def test_status_says_why_no_rules_load_to_a_person_as_well(run_watch, capsys, home):
    """A read with no readback line: the warning prints where it is raised, not only in the envelope."""
    paths = archive_store.paths_for(home)
    paths.rules.mkdir(mode=0o700, parents=True, exist_ok=True)
    (paths.rules / "broken.json").write_text(json.dumps({"schema": _rules.SCHEMA, "name": "broken"}))

    code, out, _err, _fake = run_watch(["watch", "status"], capsys=capsys)
    assert code == 0
    warnings = [line for line in out.splitlines() if line.startswith("warning: ")]
    [broken] = [line for line in warnings if line.startswith("warning: the rules do not load: ")]
    assert out.index(broken) < out.index("Rules loaded  0")

    code, out, err, _fake = run_watch(["--json", "watch", "status"], capsys=capsys)
    assert [f"warning: {text}" for text in envelope_of(out)["warnings"]] == warnings
    assert broken in err.splitlines()


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


def test_a_human_send_at_says_which_message_telegram_holds_and_until_when(run_watch, capsys):
    """One sentence for a person: the id `schedule cancel` needs, the moment, the guarantee. No JSON."""
    when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
    code, out, _err, fake = run_watch(
        ["send", "--chat", "@agencyalerts", "--text", "standup", "--at", when.isoformat()], capsys=capsys, answer="y"
    )
    assert code == 0, out
    assert fake.sent[0]["at"] == when
    # The prompt ends without a newline, so the run's next words share its line.
    assert out.split("Send it? [y/N]: ", 1)[1].splitlines() == [
        f"Scheduled message 5001 in Alerts ({CHANNEL_ID}) for {when.isoformat()}; Telegram holds it ({watch_ops.SERVER_HELD}).",
        f"Read back: message 5001 is scheduled in Alerts for {when.isoformat()} ({watch_ops.SERVER_HELD})",
    ]
    assert "{" not in out


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


def test_asking_telegram_for_a_chat_says_so_even_when_it_holds_nothing(run_watch, capsys, home):
    """A chat Telegram holds nothing for gets a block saying that, not silence.

    Telegram 7, 2026-09-17: the list for a chat printed only the runner's own
    row, so the one question the `--chat` was asked -- what does the server
    hold? -- came back with no answer at all.
    """
    client = SendingClient()
    run_watch(["schedule", "post", "--chat", "@agencyalerts", "--text", "local", "--every", "1d"], capsys=capsys, client=client, answer="y")

    code, out, _err, _fake = run_watch(["schedule", "list", "--chat", "@agencyalerts"], capsys=capsys, client=client)
    assert code == 0
    assert "Held by Telegram (server-held): nothing" in out
    assert "Held by this runner (runner-held" in out

    # Without --chat nothing was asked, so nothing is claimed about Telegram.
    code, out, _err, _fake = run_watch(["schedule", "list"], capsys=capsys, client=client)
    assert "Held by Telegram" not in out


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


def test_schedule_cancel_yes_skips_the_prompt_for_both_kinds(run_watch, capsys, home):
    """Card agent-bo-95422198: no terminal, answer n, both a runner-held and a Telegram-held one cancelled."""
    code, out, _err, _fake = run_watch(
        ["--json", "schedule", "post", "--chat", "@agencyalerts", "--text", "standup", "--every", "1d"], capsys=capsys, isatty=True, answer="y"
    )
    schedule_id = envelope_of(out)["result"]["schedule"]["id"]
    code, out, err, _fake = run_watch(["--json", "schedule", "cancel", "--id", schedule_id, "--yes"], capsys=capsys, isatty=False, answer="n")
    assert code == 0, out
    assert envelope_of(out)["result"]["cancelled_schedule"]["id"] == schedule_id and "[y/N]" not in err and schedule_id in err

    when = datetime.now(UTC) + timedelta(hours=2)
    client = SendingClient()
    code, out, _err, _fake = run_watch(
        ["--json", "send", "--chat", "@agencyalerts", "--text", "native", "--at", when.isoformat()], capsys=capsys, client=client, isatty=True, answer="y"
    )
    message_id = envelope_of(out)["result"]["message_id"]
    code, out, err, _fake = run_watch(
        ["--json", "schedule", "cancel", "--id", str(message_id), "--chat", "@agencyalerts", "--yes"], capsys=capsys, client=client, isatty=False, answer="n"
    )
    assert code == 0, out
    assert client.scheduled[CHANNEL_ID] == [] and "[y/N]" not in err


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
        return member("send_messages")

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


class MenuClient:
    """The one client a menu session holds: it answers `get_me` and nothing else."""

    def __init__(self, user=ACCOUNT):
        self.user = user
        self.get_me_calls = 0
        self.disconnected = 0

    async def get_me(self):
        self.get_me_calls += 1
        return self.user

    async def disconnect(self):
        self.disconnected += 1


def test_a_menu_that_holds_the_session_opens_no_second_one_for_the_identity(home, monkeypatch, capsys):
    """Card 319, run 2 and run 8 of the live transcript: the menu's own session is the session.

    A profile `auth` has never recorded has no label and no user id to read, so
    the identity lookup used to open a client of its own -- on the very session
    file the menu is holding -- and Telethon's lock answered. Every one of these
    three is offline: the queue, the runner's own schedules and the rule files
    are all on this machine.
    """
    from telegram_tools import menu
    from telegram_tools.client import SessionInUseError

    held = MenuClient()

    async def started(_client, *, authorize=True):
        return held

    def second_connection(_config):
        raise SessionInUseError(
            "Another telegram-tools is already using the login session. "
            "Close the other one - a menu open in another terminal counts - and try again."
        )

    monkeypatch.setattr(menu, "create_client", lambda _config: held)
    monkeypatch.setattr(menu, "start_client", started)
    # What a second client on one session file does, where it would be opened.
    monkeypatch.setattr(cli, "create_client", second_connection)

    session = menu.MenuSession()
    lines: list[str] = []

    async def drive():
        # The chat picker, or any other connecting row: the menu now holds the file.
        assert await session.client() is held
        rows = (
            (SimpleNamespace(command="review", review_kind="approve", ids=None), True),
            (SimpleNamespace(command="schedule", schedule_kind="list", chat=None), False),
            (SimpleNamespace(command="watch", watch_kind="rules", rules_verb="list"), False),
        )
        codes = []
        for args, connect in rows:
            codes.append(await menu._call(args, session=session, runner=cli.run, write=lines.append, connect=connect))
        return codes

    codes = run(drive())
    capsys.readouterr()
    assert [line for line in lines if line.startswith("error:")] == []
    assert codes == [0, 0, 0]
    # And the identity is the one the menu already resolved, asked for once.
    assert held.get_me_calls == 1


def _no_connection(monkeypatch):
    """Every way a command opens a client of its own, made loud."""

    def refuse(_config):
        raise AssertionError("this command opened a Telegram connection")

    monkeypatch.setattr(cli, "create_client", refuse)
    monkeypatch.setattr(cli, "create_detached_client", refuse)


def test_the_three_runner_signals_never_connect_on_a_profile_with_no_record(home, monkeypatch, capsys):
    """`watch status`, `stop` and `reload` read local files; the identity is decoration.

    A record-less profile used to buy that decoration with a whole second
    session. Status here has a live holder (this very process), and the two
    signals a stale one, so neither signals anything.
    """
    paths = archive_store.paths_for()
    paths.rules.mkdir(parents=True, exist_ok=True)
    _no_connection(monkeypatch)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False, read=lambda: "", readline=lambda: "\n"))

    import os

    paths.runner_lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "2026-09-17T17:22:56Z"}))
    code = cli.main(["--json", "watch", "status"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["result"]["running"] is True
    assert payload["identity"] is None
    assert any("has no record" in warning for warning in payload["warnings"])

    # A pid nothing holds: both signals refuse by name, and neither connected first.
    paths.runner_lock.write_text(json.dumps({"pid": 2, "started_at": "2026-09-17T17:22:56Z"}))
    monkeypatch.setattr(cli._runner, "_pid_alive", lambda _pid: False)
    for verb in ("stop", "reload"):
        code = cli.main(["--json", "watch", verb])
        payload = json.loads(capsys.readouterr().out)
        assert code == 2, verb
        assert payload["error"]["code"] == "RUNNER_NOT_RUNNING", verb


def test_a_profile_with_no_record_is_asked_once_and_never_through_a_second_client(home, monkeypatch):
    """The pin: what a record-less profile costs, with and without a client to hand."""
    config = SimpleNamespace(profile="default")
    held = MenuClient()

    # With a client, nothing is opened: the run asks the connection it has.
    _no_connection(monkeypatch)
    report = cli.Reporter()
    identity = run(cli._offline_identity(config, report, client=held))
    assert identity.id == "tg:user:4242" and identity.label == "Sven (@sven)" and identity.profile == "default"
    assert held.get_me_calls == 1

    # Without one, exactly one client is opened and disconnected again.
    opened = MenuClient()
    monkeypatch.setattr(cli, "create_client", lambda _config: opened)

    async def started(_client, *, authorize=True):
        return opened

    monkeypatch.setattr(cli, "start_client", started)
    identity = run(cli._offline_identity(config, cli.Reporter(), client=None))
    assert identity.id == "tg:user:4242"
    assert opened.get_me_calls == 1 and opened.disconnected == 1


def test_the_runner_never_opens_the_session_file_it_is_built_to_leave_alone(home, monkeypatch, capsys):
    """`watch run` connects detached so a one-shot command keeps working; the identity did not.

    A profile with no record sent the lookup through `create_client`, which is
    the SQLite session file itself -- so a runner started beside an open menu
    refused with the lock error, on the one command whose whole design is to
    hold that file open for nobody.
    """
    fake = SendingClient()

    def refuse(_config):
        raise AssertionError("the runner opened the profile's own session file")

    monkeypatch.setattr(cli, "create_client", refuse)
    monkeypatch.setattr(cli, "create_detached_client", lambda _config: fake)
    monkeypatch.setattr(cli.watch_events, "TelegramEventSource", lambda *_a, **_k: RecordedSource(live=[]))
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False, read=lambda: "", readline=lambda: "\n"))

    code = cli.main(["--json", "watch", "run"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload
    # It still acts as somebody: the detached client answered for the account.
    assert payload["identity"]["id"] == "tg:user:4242"
