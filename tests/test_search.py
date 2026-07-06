import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from telegram_tools.search import search_messages
from telegram_tools.search import format_message_records


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


def test_non_topic_search_uses_telethon_server_side_filters_then_local_dates():
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
    assert client.iter_calls[0][1]["offset_date"] == datetime(2026, 7, 6, 23, 59, 59, 999999, tzinfo=UTC)


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
