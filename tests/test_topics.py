import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from telethon.errors import RPCError
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest

from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids, topic_from_telethon


def raw_topic(topic_id, title, *, icon_emoji_id=None):
    return SimpleNamespace(
        id=topic_id,
        title=title,
        top_message=topic_id,
        icon_emoji_id=icon_emoji_id,
        date=datetime(2026, 7, 6, tzinfo=UTC),
    )


def emoji_document(document_id, alt):
    return SimpleNamespace(id=document_id, attributes=[SimpleNamespace(alt=alt)])


class FakeClient:
    """Answers the topic request and the custom-emoji request, and records both."""

    def __init__(self, topics, documents=(), *, emoji_error=None):
        self.topics = list(topics)
        self.documents = list(documents)
        self.emoji_error = emoji_error
        self.emoji_requests = []

    async def __call__(self, request):
        if isinstance(request, GetCustomEmojiDocumentsRequest):
            self.emoji_requests.append(list(request.document_id))
            if self.emoji_error is not None:
                raise self.emoji_error
            return self.documents
        return SimpleNamespace(topics=self.topics, count=len(self.topics))


def test_topic_from_telethon_preserves_topic_id_and_top_message():
    topic = topic_from_telethon(raw_topic(42, "Deploys"))

    assert topic.id == 42
    assert topic.title == "Deploys"
    assert topic.top_message == 42


def test_a_topic_with_an_icon_carries_the_emoji_and_a_topic_without_one_stays_bare():
    client = FakeClient(
        [raw_topic(141, "Dobby", icon_emoji_id=5350554349074391003), raw_topic(1, ".")],
        [emoji_document(5350554349074391003, "💻")],
    )

    topics = asyncio.run(get_forum_topics(client, "@forum"))

    assert [(topic.icon_emoji, topic.display_title) for topic in topics] == [("💻", "💻 Dobby"), (None, ".")]
    # The title itself is untouched: --topic matching and the exports key on it.
    assert [topic.title for topic in topics] == ["Dobby", "."]


def test_a_page_of_topics_costs_one_custom_emoji_call_not_one_per_topic():
    client = FakeClient(
        [
            raw_topic(141, "Dobby", icon_emoji_id=5350554349074391003),
            raw_topic(217, "Researcher", icon_emoji_id=5309965701241379366),
            raw_topic(1, "."),
        ],
        [
            emoji_document(5350554349074391003, "💻"),
            emoji_document(5309965701241379366, "🔎"),
        ],
    )

    topics = asyncio.run(get_forum_topics(client, "@forum"))

    assert [topic.display_title for topic in topics] == ["💻 Dobby", "🔎 Researcher", "."]
    assert client.emoji_requests == [[5309965701241379366, 5350554349074391003]]


def test_topics_with_no_icons_at_all_skip_the_custom_emoji_call():
    client = FakeClient([raw_topic(141, "Deploys"), raw_topic(217, "Support")])

    topics = asyncio.run(get_forum_topics(client, "@forum"))

    assert [topic.icon_emoji for topic in topics] == [None, None]
    assert client.emoji_requests == []


def test_an_icon_id_telegram_will_not_resolve_leaves_the_topic_bare():
    client = FakeClient([raw_topic(141, "Dobby", icon_emoji_id=5350554349074391003)], [])

    topics = asyncio.run(get_forum_topics(client, "@forum"))

    assert topics[0].icon_emoji is None
    assert topics[0].display_title == "Dobby"


def test_a_failed_custom_emoji_call_leaves_every_topic_bare_instead_of_erroring():
    client = FakeClient(
        [raw_topic(141, "Dobby", icon_emoji_id=5350554349074391003)],
        emoji_error=RPCError(request=None, message="boom", code=400),
    )

    topics = asyncio.run(get_forum_topics(client, "@forum"))

    assert topics[0].display_title == "Dobby"


def test_get_forum_topics_by_ids_carries_the_icon_too():
    client = FakeClient(
        [raw_topic(141, "Dobby", icon_emoji_id=5350554349074391003)],
        [emoji_document(5350554349074391003, "💻")],
    )

    topics = asyncio.run(get_forum_topics_by_ids(client, "@forum", [141]))

    assert topics[0].display_title == "💻 Dobby"


def test_an_id_no_topic_came_back_for_is_still_a_bare_placeholder():
    client = FakeClient([])

    topics = asyncio.run(get_forum_topics_by_ids(client, "@forum", [999]))

    assert [(topic.id, topic.title, topic.icon_emoji) for topic in topics] == [(999, "999", None)]
