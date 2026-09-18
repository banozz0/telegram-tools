import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl import types as tl

from telethon.errors.rpcbaseerrors import BadRequestError

from telegram_tools.envelope import CommandError
from telegram_tools.search import DERIVED_SCAN, format_message_records, search_messages
from test_typed_time_zone import malta  # noqa: F401 - fixture


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.iter_calls = []

    async def get_peer_id(self, user):
        assert user == "@alice"
        return 123

    def iter_messages(self, chat, **kwargs):
        self.iter_calls.append((chat, kwargs))

        async def iterator():
            for message in self.messages:
                yield message

        return iterator()


def test_topic_search_filters_locally_by_keyword_user_and_date():
    messages = [
        SimpleNamespace(id=1, date=datetime(2026, 7, 1, tzinfo=UTC), sender_id=123, raw_text="deploy ok"),
        SimpleNamespace(id=2, date=datetime(2026, 7, 2, tzinfo=UTC), sender_id=999, raw_text="deploy no"),
        SimpleNamespace(id=3, date=datetime(2026, 7, 3, tzinfo=UTC), sender_id=123, raw_text="other"),
        SimpleNamespace(id=4, date=datetime(2026, 7, 4, tzinfo=UTC), sender_id=123, raw_text="deploy late"),
    ]
    client = FakeClient(messages)

    records = asyncio.run(
        search_messages(
            client,
            "@group",
            chat_id=-1001,
            topic_id=10,
            keyword="deploy",
            from_user="@alice",
            since="2026-07-01",
            until="2026-07-03",
        )
    )

    assert [record["id"] for record in records] == [1]
    assert client.iter_calls[0][1]["reply_to"] == 10
    assert "search" not in client.iter_calls[0][1]


def test_non_topic_search_uses_telethon_server_side_filters_then_local_dates(malta):
    messages = [
        SimpleNamespace(id=1, date=datetime(2026, 7, 1, tzinfo=UTC), sender_id=123, raw_text="deploy old"),
        SimpleNamespace(id=2, date=datetime(2026, 7, 5, tzinfo=UTC), sender_id=123, raw_text="deploy ok"),
    ]
    client = FakeClient(messages)

    records = asyncio.run(
        search_messages(
            client,
            "@group",
            chat_id=-1001,
            keyword="deploy",
            from_user="@alice",
            since="2026-07-02",
            until="2026-07-06",
            limit=50,
        )
    )

    assert [record["id"] for record in records] == [2]
    assert client.iter_calls[0][1]["search"] == "deploy"
    assert client.iter_calls[0][1]["from_user"] == "@alice"
    assert client.iter_calls[0][1]["limit"] == 50
    # A bare `--until 2026-07-06` is that whole day on this machine's clock: Malta's, +02:00 in July.
    assert client.iter_calls[0][1]["offset_date"] == datetime(2026, 7, 6, 21, 59, 59, 999999, tzinfo=UTC)


def test_format_message_records_outputs_human_readable_table():
    text = format_message_records(
        [
            {
                "id": 12,
                "date": "2026-07-06T12:30:00+00:00",
                "topic_id": 141,
                "sender_username": "alice",
                "text": "deploy finished",
            }
        ]
    )

    assert "Messages" in text
    assert "12" in text
    assert "141" in text
    assert "alice" in text
    assert "deploy finished" in text


def test_format_message_records_marks_a_message_that_carries_media():
    text = format_message_records(
        [{"id": 6394, "date": "2026-08-25T10:10:40+00:00", "text": "attachment test", "has_media": True}]
    )

    assert "[media]" in text
    assert "attachment test" in text


def test_format_message_records_leaves_a_plain_message_unmarked():
    text = format_message_records(
        [{"id": 12, "date": "2026-07-06T12:30:00+00:00", "text": "deploy finished", "has_media": False}]
    )

    assert "[media]" not in text
    assert "deploy finished" in text


def test_a_media_only_message_is_not_a_blank_row():
    # The row that started this: a photo with no caption printed as nothing at all.
    text = format_message_records([{"id": 6394, "date": "2026-08-25T10:10:40+00:00", "text": "", "has_media": True}])

    assert "[media]" in text


def test_records_without_the_has_media_key_still_render():
    # Older exports and hand-built records predate the flag; they must not crash.
    text = format_message_records([{"id": 12, "date": "2026-07-06T12:30:00+00:00", "text": "hi"}])

    assert "hi" in text
    assert "[media]" not in text


def test_the_media_marker_does_not_eat_the_text_column():
    text = format_message_records(
        [{"id": 1, "date": "d", "text": "a" * 200, "has_media": True}]
    )

    row = [line for line in text.splitlines() if line.startswith("1\t")][0]
    # Truncation still applies to the text, and the marker sits outside it.
    assert row.count("[media]") == 1
    assert row.endswith("...")


# -- the keyword and the body this tool derived (card agent-bo-95422230) ----
#
# A live `--keyword` used to read `raw_text` and nothing else, so every string
# the tool works out for itself -- a poll's question, a forward's author, the
# event a service row is, a file's name -- was invisible to it while `archive
# search`, which stores the derived body, found all of them. The separator in a
# derived body is an em dash, so a test that greps for a hyphen proves nothing.

FORWARD = SimpleNamespace(
    sender_id=5046366253,
    sender=SimpleNamespace(username="harry", first_name="Harry"),
    chat_id=None,
    chat=None,
    from_name=None,
    date=datetime(2026, 9, 13, 9, 0, tzinfo=UTC),
)
FLANGE = tl.MessageMediaDocument(
    document=tl.Document(
        id=7,
        access_hash=7,
        file_reference=b"",
        date=None,
        mime_type="application/pdf",
        size=2048,
        dc_id=2,
        attributes=[tl.DocumentAttributeFilename(file_name="flange-spec.pdf")],
    )
)
ZARQUON = SimpleNamespace(
    poll=SimpleNamespace(
        question=SimpleNamespace(text="which build ships?", entities=[]),
        answers=[
            SimpleNamespace(text=SimpleNamespace(text="zarquon", entities=[]), option=b"a"),
            SimpleNamespace(text=SimpleNamespace(text="beta", entities=[]), option=b"b"),
        ],
    )
)


def derived_message(number, **fields):
    """A Telethon message with no text of its own: whatever it says, the tool derived."""
    base = {
        "id": number,
        "date": datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        "raw_text": "",
        "message": "",
        "sender_id": 123,
        "sender": SimpleNamespace(username="harry"),
        "reply_to": None,
        "media": None,
        "forward": None,
        "action": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


DERIVED = [
    derived_message(11, media=ZARQUON),
    derived_message(12, raw_text="morning report", message="morning report", forward=FORWARD),
    derived_message(13, action=tl.MessageActionTopicCreate(title="Deploys", icon_color=0)),
    derived_message(14, action=tl.MessageActionPinMessage()),
    derived_message(15, media=FLANGE),
    derived_message(16, raw_text="campaign is green", message="campaign is green"),
]


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("zarquon", [11]),          # a poll answer
        ("which build ships?", [11]),  # and its question
        ("fwd", [12]),              # the provenance mark the row carries
        ("harry", [12]),            # the author Telegram attributed it to
        ("topic created", [13]),    # a service row's event
        ("Deploys", [13]),          # and the one detail it keeps
        ("pinned", [14]),           # a service row with no title at all
        ("event", [13, 14]),        # the marker both service rows wear
        ("flange", [15]),           # a file's name
        ("campaign", [16]),         # the control: what somebody actually typed
    ],
)
def test_a_topic_search_matches_the_body_the_tool_derived(keyword, expected):
    client = FakeClient(DERIVED)

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, topic_id=10, keyword=keyword))

    assert [record["id"] for record in records] == expected
    assert client.iter_calls[0][1]["reply_to"] == 10, "the topic path filters here and asks Telegram nothing"


def test_a_derived_body_keeps_the_em_dash_a_hyphen_test_would_miss():
    client = FakeClient(DERIVED)

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, topic_id=10, keyword="zarquon"))

    assert records[0]["text"] == "[poll] which build ships? — zarquon / beta"
    assert " - " not in records[0]["text"]


class TwoPassClient:
    """A chat Telegram answers out of its own index: `search=` sees typed text only.

    Which is the defect in one object -- a derived body is a string this tool
    invented, so no `search=` can ever come back with it. Every call's kwargs
    and the number of messages it actually read are recorded.
    """

    def __init__(self, messages):
        self.messages = messages
        self.iter_calls = []
        self.reads = []

    def iter_messages(self, chat, **kwargs):
        self.iter_calls.append(kwargs)
        self.reads.append(0)
        index = len(self.reads) - 1
        search = kwargs.get("search")
        limit = kwargs.get("limit")

        async def iterator():
            served = 0
            for message in self.messages:
                if search is not None and search.lower() not in (message.raw_text or "").lower():
                    continue
                if limit is not None and served >= limit:
                    return
                served += 1
                self.reads[index] += 1
                yield message

        return iterator()


def test_a_whole_chat_search_asks_telegram_and_reads_the_chat_and_merges_both():
    client = TwoPassClient(list(reversed(DERIVED)))  # newest first, the order Telegram serves

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, keyword="campaign"))

    # Telegram's answer is still asked for first, with the word, unbounded in history.
    assert client.iter_calls[0]["search"] == "campaign"
    # The second pass reads the chat itself, with no `search=` for Telegram to fail at.
    assert "search" not in client.iter_calls[1]
    assert len(client.iter_calls) == 2
    # One row, not two: a message both passes saw is merged by id.
    assert [record["id"] for record in records] == [16]


@pytest.mark.parametrize("keyword", ["zarquon", "fwd", "topic created", "pinned", "flange"])
def test_a_whole_chat_search_finds_what_telegram_cannot_match(keyword):
    client = TwoPassClient(list(reversed(DERIVED)))

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, keyword=keyword))

    assert records, "Telegram returns nothing for a string this tool invented; the read pass finds it"
    assert client.reads[0] == 0, "the server pass came back empty, which is the whole defect"


def test_a_whole_chat_search_returns_newest_first_across_both_passes():
    client = TwoPassClient(list(reversed(DERIVED)))

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, keyword="e"))

    ids = [record["id"] for record in records]
    assert ids == sorted(ids, reverse=True), "one merged list, in the order a single pass returned"


def test_the_read_pass_is_bounded_at_derived_scan_messages():
    chatter = [derived_message(number, raw_text="chatter", message="chatter") for number in range(3000, 0, -1)]
    client = TwoPassClient(chatter)

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, keyword="zarquon"))

    assert records == []
    assert client.reads[0] == 0, "Telegram matched nothing"
    assert client.reads[1] == DERIVED_SCAN == 1000, "and the read pass stops after a bounded window"


def test_the_read_pass_stops_as_soon_as_the_limit_is_filled():
    hits = [derived_message(number, media=FLANGE) for number in range(3000, 0, -1)]
    client = TwoPassClient(hits)

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, keyword="flange", limit=5))

    assert [record["id"] for record in records] == [3000, 2999, 2998, 2997, 2996]
    assert client.reads[1] == 5, "a match older than five already held cannot make the answer"


def test_a_whole_chat_search_without_a_keyword_still_makes_one_pass():
    client = TwoPassClient(list(reversed(DERIVED)))

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, limit=3))

    assert len(client.iter_calls) == 1, "nothing is derived to look for, so nothing is read for it"
    assert [record["id"] for record in records] == [16, 15, 14]


class ForumClient(FakeClient):
    """A forum whose topics are 1, 2, 4, 6, 30 and 31: any other `reply_to` is Telegram's 400."""

    TOPICS = (1, 2, 4, 6, 30, 31)

    def __init__(self, messages, *, forum=True):
        super().__init__(messages)
        self.forum = forum
        self.requests = []

    def iter_messages(self, chat, **kwargs):
        topic = kwargs.get("reply_to")
        if topic is not None and topic not in self.TOPICS:
            async def refused():
                raise BadRequestError(request=None, message="TOPIC_ID_INVALID")
                yield  # noqa: unreachable - makes this an async generator, as Telethon's is
            return refused()
        return super().iter_messages(chat, **kwargs)

    async def __call__(self, request):
        self.requests.append(type(request).__name__)
        if not self.forum:
            raise BadRequestError(request=None, message="CHANNEL_FORUM_MISSING")
        topics = [SimpleNamespace(id=topic_id, title=f"t{topic_id}", top_message=topic_id) for topic_id in self.TOPICS]
        return SimpleNamespace(topics=topics, count=len(topics))


def test_a_topic_the_chat_does_not_have_is_refused_by_name_with_the_real_topic_ids():
    """Live 2026-09-16: `search --topic 3` on a forum without a topic 3 printed a
    40-line Telethon traceback ending `RPCError 400: TOPIC_ID_INVALID`, exit 1,
    where every other bad target is refused by name."""
    client = ForumClient([SimpleNamespace(id=1, date=datetime(2026, 7, 1, tzinfo=UTC), sender_id=1, raw_text="x")])

    with pytest.raises(CommandError) as refused:
        asyncio.run(search_messages(client, "@group", chat_id=-1001, topic_id=3, chat_title="campaign 4.7"))

    assert refused.value.code == "TARGET_NOT_FOUND"
    assert str(refused.value) == "No topic 3 in campaign 4.7."
    assert refused.value.hint == "its topics are 1, 2, 4, 6, 30, 31 - telegram-tools discover names them"
    assert client.requests == ["GetForumTopicsRequest"], "the hint reads the chat's topic list once"


def test_the_refusal_still_names_the_topic_when_the_list_cannot_be_read():
    client = ForumClient([], forum=False)

    with pytest.raises(CommandError) as refused:
        asyncio.run(search_messages(client, "@group", chat_id=-1001, topic_id=3))

    assert refused.value.code == "TARGET_NOT_FOUND"
    assert str(refused.value) == "No topic 3 in this chat."
    assert refused.value.hint == "telegram-tools discover lists its topics"


def test_a_topic_the_chat_has_still_searches():
    client = ForumClient([SimpleNamespace(id=1, date=datetime(2026, 7, 1, tzinfo=UTC), sender_id=1, raw_text="x")])

    records = asyncio.run(search_messages(client, "@group", chat_id=-1001, topic_id=4))

    assert [record["id"] for record in records] == [1]
    assert client.requests == [], "nothing lists topics on the happy path"
