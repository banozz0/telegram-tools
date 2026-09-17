import asyncio
from types import SimpleNamespace

from telegram_tools.delete import confirm_clear_topic_messages, delete_topic_messages
from telegram_tools.models import TopicInfo


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.deleted_batches = []

    def iter_messages(self, chat, *, reply_to=None, wait_time=None):
        async def iterator():
            for message in self.messages:
                yield message

        return iterator()

    async def delete_messages(self, chat, ids):
        self.deleted_batches.append(list(ids))
        return [SimpleNamespace(pts_count=len(ids))]


def test_the_scanning_line_shows_the_topic_emoji_telegram_draws():
    client = FakeClient([SimpleNamespace(id=10), SimpleNamespace(id=11)])
    lines = []

    asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=80, title="Dobby", top_message=80, icon_emoji="\U0001f4bb")],
            execute=False,
            progress=lines.append,
            confirm=lambda: "DELETE",
        )
    )

    assert "Scanning topic 80 (\U0001f4bb Dobby): 2 to clear" in lines


class ThreadedClient(FakeClient):
    """A forum whose topics are threads of their own: `reply_to` picks the thread."""

    def __init__(self, threads):
        super().__init__([])
        self.threads = threads

    def iter_messages(self, chat, *, reply_to=None, wait_time=None):
        async def iterator():
            for message_id in self.threads.get(reply_to, []):
                yield SimpleNamespace(id=message_id)

        return iterator()


def test_each_scanned_topic_says_how_many_messages_it_would_clear():
    # The live dry-run over six topics printed one total, 19, and which topics
    # held them (13 in topic 2, 6 in topic 6) was known only from searches.
    client = ThreadedClient({2: [32, 31, 30, 2], 6: [41, 40, 6]})
    lines = []

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=2, title="1", top_message=32), TopicInfo(id=6, title="3", top_message=41)],
            execute=False,
            progress=lines.append,
        )
    )

    assert lines == [
        "Scanning topic 2 (1): 3 to clear",
        "Scanning topic 6 (3): 2 to clear",
        "Dry-run: 5 topic messages would be cleared",
    ]
    assert [row.to_dict() for row in result.topics] == [
        {"id": 2, "title": "1", "matched": 3},
        {"id": 6, "title": "3", "matched": 2},
    ]


def test_the_count_is_on_the_scan_line_before_the_typed_gate_too():
    client = ThreadedClient({2: [32, 31, 2]})
    lines = []

    asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=2, title="1", top_message=32)],
            execute=True,
            progress=lines.append,
            confirm=lambda: "NOPE",
        )
    )

    assert lines == ["Scanning topic 2 (1): 2 to clear", "Clear topic messages cancelled"]
    assert client.deleted_batches == []


def test_a_message_found_under_two_topics_counts_once_so_the_rows_add_up():
    client = ThreadedClient({2: [51, 50, 2], 6: [52, 51, 6]})

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=2, title="1"), TopicInfo(id=6, title="3")],
            execute=False,
        )
    )

    assert result.matched == 3
    assert [(row.id, row.matched) for row in result.topics] == [(2, 2), (6, 1)]
    assert sum(row.matched for row in result.topics) == result.matched


def test_dry_run_collects_topic_messages_without_deleting():
    client = FakeClient(
        [
            SimpleNamespace(id=10),
            SimpleNamespace(id=11),
            SimpleNamespace(id=12),
        ]
    )

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=10, title="Builds", top_message=10)],
            execute=False,
            confirm=lambda: "DELETE",
        )
    )

    assert result.matched == 2
    assert result.deleted == 0
    assert result.dry_run is True
    assert result.cancelled is False
    assert client.deleted_batches == []


def test_the_newest_message_is_cleared_too_and_only_the_opener_stays():
    # Telegram's ForumTopic.top_message is the topic's newest message, not the
    # one that opened it. Topic 4 of the live campaign held the opener 4 and
    # four real messages, the poll 21 newest; the dry-run counted 3.
    client = FakeClient(
        [
            SimpleNamespace(id=21),
            SimpleNamespace(id=13),
            SimpleNamespace(id=9),
            SimpleNamespace(id=5),
            SimpleNamespace(id=4),
        ]
    )

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=4, title="testing grounds", top_message=21)],
            execute=True,
            confirm=lambda: "DELETE",
        )
    )

    assert result.matched == 4
    assert result.deleted == 4
    assert client.deleted_batches == [[21, 13, 9, 5]]


def test_execute_requires_delete_confirmation():
    client = FakeClient([SimpleNamespace(id=11)])

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=10, title="Builds", top_message=10)],
            execute=True,
            confirm=lambda: "NOPE",
        )
    )

    assert result.cancelled is True
    assert result.deleted == 0
    assert client.deleted_batches == []


def test_execute_deletes_in_batches_and_skips_topic_starter():
    client = FakeClient(
        [
            SimpleNamespace(id=10),
            SimpleNamespace(id=11),
            SimpleNamespace(id=12),
            SimpleNamespace(id=13),
        ]
    )

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=10, title="Builds", top_message=10)],
            execute=True,
            confirm=lambda: "DELETE",
            batch_size=2,
        )
    )

    assert result.matched == 3
    assert result.deleted == 3
    assert result.dry_run is False
    assert result.cancelled is False
    assert client.deleted_batches == [[11, 12], [13]]


def test_clear_result_serializes_with_cleared_wording():
    client = FakeClient([SimpleNamespace(id=11)])

    result = asyncio.run(
        delete_topic_messages(
            client,
            "@group",
            [TopicInfo(id=10, title="Builds", top_message=10)],
            execute=False,
        )
    )

    assert result.to_dict() == {
        "matched": 1,
        "cleared": 0,
        "dry_run": True,
        "cancelled": False,
        "topics": [{"id": 10, "title": "Builds", "matched": 1}],
    }


def test_clear_topic_messages_confirmation_explains_topics_are_preserved():
    output = []

    result = confirm_clear_topic_messages(
        read=lambda _prompt: "DELETE",
        write=output.append,
    )

    text = "\n".join(output)
    assert result == "DELETE"
    assert "CLEAR TOPIC MESSAGES" in text
    assert "permanently delete ALL MESSAGES" in text
    assert "Forum topics will NOT be deleted" in text
    assert "Topic IDs will NOT change" in text
    assert "Only messages will be removed" in text
