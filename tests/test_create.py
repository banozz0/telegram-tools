import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import types
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import CreateForumTopicRequest

from telegram_tools.create import (
    confirm_create,
    create_channel,
    create_group,
    create_topic,
    format_create_preview,
)


class FakeClient:
    """Records the requests it is called with and replays canned Updates."""

    def __init__(self, result=None):
        self.requests = []
        self.result = result

    async def __call__(self, request):
        self.requests.append(request)
        return self.result


def channel_updates(channel_id=111, title="Hermes", **flags):
    channel = types.Channel(id=channel_id, title=title, photo=None, date=None, **flags)
    return SimpleNamespace(chats=[channel], updates=[])


def topic_updates(message_id=1477):
    update = SimpleNamespace(message=SimpleNamespace(id=message_id))
    return SimpleNamespace(chats=[], updates=[update])


def test_create_group_asks_for_a_supergroup():
    client = FakeClient(channel_updates(megagroup=True))

    created = asyncio.run(create_group(client, "Hermes", about="agency", forum=False, confirm=lambda: True))

    request = client.requests[0]
    assert isinstance(request, CreateChannelRequest)
    assert request.title == "Hermes"
    assert request.about == "agency"
    assert request.megagroup is True
    assert request.broadcast is None or request.broadcast is False
    assert request.forum is False
    assert created.id == -1000000000111
    assert created.kind == "group"
    assert created.forum is False


def test_create_group_with_forum_enables_topics_in_the_same_call():
    client = FakeClient(channel_updates(megagroup=True, forum=True))

    created = asyncio.run(create_group(client, "Hermes", about=None, forum=True, confirm=lambda: True))

    assert client.requests[0].forum is True
    assert len(client.requests) == 1
    assert created.forum is True


def test_create_channel_asks_for_a_broadcast():
    client = FakeClient(channel_updates(channel_id=222, title="Alerts", broadcast=True))

    created = asyncio.run(create_channel(client, "Alerts", about=None, confirm=lambda: True))

    request = client.requests[0]
    assert request.broadcast is True
    assert request.megagroup is None or request.megagroup is False
    assert created.id == -1000000000222
    assert created.kind == "channel"


def test_create_topic_returns_the_new_topic_id():
    client = FakeClient(topic_updates(message_id=1477))

    created = asyncio.run(create_topic(client, "PEER", chat_id=-100111, title="Deploys", confirm=lambda: True))

    request = client.requests[0]
    assert isinstance(request, CreateForumTopicRequest)
    assert request.title == "Deploys"
    assert created.kind == "topic"
    assert created.topic_id == 1477
    assert created.id == -100111


def test_declining_creates_nothing():
    client = FakeClient(channel_updates())

    created = asyncio.run(create_group(client, "Hermes", about=None, forum=False, confirm=lambda: False))

    assert client.requests == []
    assert created.cancelled is True
    assert created.id is None
    assert created.to_dict()["created"] is False


def test_no_confirm_callable_creates_without_asking():
    client = FakeClient(channel_updates())

    created = asyncio.run(create_group(client, "Hermes", about=None, forum=False))

    assert len(client.requests) == 1
    assert created.cancelled is False


def test_a_result_without_a_new_chat_is_an_error_not_a_silent_none():
    client = FakeClient(SimpleNamespace(chats=[], updates=[]))

    with pytest.raises(ValueError):
        asyncio.run(create_group(client, "Hermes", about=None, forum=False, confirm=lambda: True))


def test_a_topic_result_without_a_message_is_an_error():
    client = FakeClient(SimpleNamespace(chats=[], updates=[]))

    with pytest.raises(ValueError):
        asyncio.run(create_topic(client, "PEER", chat_id=-100111, title="Deploys", confirm=lambda: True))


def test_preview_names_what_is_about_to_exist():
    assert "forum group" in format_create_preview("group", "Hermes", about="agency", forum=True).lower()
    assert "Hermes" in format_create_preview("group", "Hermes", about="agency", forum=True)
    assert "agency" in format_create_preview("group", "Hermes", about="agency", forum=True)
    assert "channel" in format_create_preview("channel", "Alerts", about=None, forum=False).lower()


def test_topic_preview_names_the_chat_it_lands_in():
    preview = format_create_preview("topic", "Deploys", about=None, forum=False, chat_title="Hermes")

    assert "Deploys" in preview
    assert "Hermes" in preview


def test_confirm_accepts_only_y():
    assert confirm_create("preview", read=lambda _prompt: "y", write=lambda _line: None) is True
    assert confirm_create("preview", read=lambda _prompt: "", write=lambda _line: None) is False
