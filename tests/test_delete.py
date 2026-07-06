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
