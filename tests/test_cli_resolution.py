import argparse
import asyncio
import io
from types import SimpleNamespace

import pytest

import telegram_tools.cli as cli
from telegram_tools.config import parse_send_allowlist
from telegram_tools.send import SendNotAllowedError
from telegram_tools.models import DeleteResult, TopicInfo
from telegram_tools.resolver import ResolvedChat


class FakeClient:
    async def get_me(self):
        return SimpleNamespace(id=1)

    async def get_permissions(self, chat, user):
        return SimpleNamespace(delete_messages=True)

    async def get_input_entity(self, chat):
        raise AssertionError("CLI handlers should use resolve_chat(), not direct get_input_entity().")


def test_clear_messages_command_uses_shared_chat_resolver(monkeypatch):
    resolved_peer = SimpleNamespace(kind="resolved-input")
    calls = {}

    async def fake_resolve_chat(client, reference):
        calls["reference"] = reference
        return ResolvedChat(id=-1001234567890, entity=SimpleNamespace(), input_entity=resolved_peer)

    async def fake_get_topics(client, peer, topic_ids):
        calls["topics_peer"] = peer
        return [TopicInfo(id=123, title="Deploys", top_message=123)]

    async def fake_delete_topic_messages(client, chat, topics, **kwargs):
        calls["delete_chat"] = chat
        calls["topics"] = topics
        calls["execute"] = kwargs["execute"]
        return DeleteResult(matched=0, deleted=0, dry_run=True)

    monkeypatch.setattr(cli, "resolve_chat", fake_resolve_chat)
    monkeypatch.setattr(cli, "get_forum_topics_by_ids", fake_get_topics)
    monkeypatch.setattr(cli, "delete_topic_messages", fake_delete_topic_messages)

    status = asyncio.run(
        cli._run_clear_messages(
            FakeClient(),
            argparse.Namespace(chat="-1001234567890", all_topics=False, topics=[123], execute=False, batch_size=100),
        )
    )

    assert status == 0
    assert calls["reference"] == "-1001234567890"
    assert calls["topics_peer"] is resolved_peer
    assert calls["delete_chat"] is resolved_peer
    assert calls["topics"][0].id == 123
    assert calls["execute"] is False


def test_search_command_uses_shared_chat_resolver(monkeypatch):
    resolved_peer = SimpleNamespace(kind="resolved-input")
    calls = {}

    async def fake_resolve_chat(client, reference):
        calls["reference"] = reference
        return ResolvedChat(id=-1001234567890, entity=SimpleNamespace(), input_entity=resolved_peer)

    async def fake_search_messages(client, chat, **kwargs):
        calls["search_chat"] = chat
        calls["kwargs"] = kwargs
        return []

    monkeypatch.setattr(cli, "resolve_chat", fake_resolve_chat)
    monkeypatch.setattr(cli, "search_messages", fake_search_messages)

    status = asyncio.run(
        cli._run_search(
            FakeClient(),
            argparse.Namespace(
                chat="-1001234567890",
                topic=None,
                keyword=None,
                from_user=None,
                since=None,
                until=None,
                limit=None,
                output=None,
                format="json",
            ),
        )
    )

    assert status == 0
    assert calls["reference"] == "-1001234567890"
    assert calls["search_chat"] is resolved_peer
    assert calls["kwargs"]["chat_id"] == -1001234567890


class SendingClient(FakeClient):
    def __init__(self):
        self.sent = []

    async def send_message(self, entity, message, *, reply_to=None):
        self.sent.append({"entity": entity, "message": message, "reply_to": reply_to})
        return SimpleNamespace(id=9001)


def _patch_send_resolution(monkeypatch, *, username=None, title="Hermes"):
    resolved_peer = SimpleNamespace(kind="resolved-input")
    calls = {}

    async def fake_resolve_chat(client, reference):
        calls["reference"] = reference
        return ResolvedChat(
            id=-100111,
            entity=SimpleNamespace(title=title, username=username),
            input_entity=resolved_peer,
        )

    async def fake_get_topics(client, peer, topic_ids):
        calls["topics_peer"] = peer
        return [TopicInfo(id=int(list(topic_ids)[0]), title="Deploys", top_message=900)]

    monkeypatch.setattr(cli, "resolve_chat", fake_resolve_chat)
    monkeypatch.setattr(cli, "get_forum_topics_by_ids", fake_get_topics)
    return resolved_peer, calls


def send_args(**overrides):
    return argparse.Namespace(**{"chat": "-100111", "topic": None, "text": "ship it", "yes": False, **overrides})


def test_send_uses_the_shared_resolver_and_posts_into_the_topic(monkeypatch):
    resolved_peer, calls = _patch_send_resolution(monkeypatch)
    monkeypatch.setattr(cli, "confirm_send", lambda preview: True)
    client = SendingClient()

    status = asyncio.run(cli._run_send(client, send_args(topic=141), SimpleNamespace(send_allowlist=())))

    assert status == 0
    assert calls["reference"] == "-100111"
    assert calls["topics_peer"] is resolved_peer
    assert client.sent == [{"entity": resolved_peer, "message": "ship it", "reply_to": 141}]


def test_send_declined_at_the_preview_sends_nothing(monkeypatch):
    _patch_send_resolution(monkeypatch)
    monkeypatch.setattr(cli, "confirm_send", lambda preview: False)
    client = SendingClient()

    status = asyncio.run(cli._run_send(client, send_args(), SimpleNamespace(send_allowlist=())))

    assert status == 1
    assert client.sent == []


def test_send_with_yes_refuses_a_destination_that_is_not_allowlisted(monkeypatch):
    _patch_send_resolution(monkeypatch)
    client = SendingClient()

    with pytest.raises(SendNotAllowedError):
        asyncio.run(cli._run_send(client, send_args(yes=True), SimpleNamespace(send_allowlist=())))

    assert client.sent == []


def test_send_with_yes_posts_to_an_allowlisted_destination(monkeypatch):
    _patch_send_resolution(monkeypatch)
    client = SendingClient()
    allowlist = parse_send_allowlist("-100111:141")

    status = asyncio.run(cli._run_send(client, send_args(topic=141, yes=True), SimpleNamespace(send_allowlist=allowlist)))

    assert status == 0
    assert client.sent[0]["reply_to"] == 141


def test_send_with_yes_matches_an_allowlisted_username(monkeypatch):
    _patch_send_resolution(monkeypatch, username="hermes")
    client = SendingClient()
    allowlist = parse_send_allowlist("@hermes")

    status = asyncio.run(cli._run_send(client, send_args(yes=True), SimpleNamespace(send_allowlist=allowlist)))

    assert status == 0
    assert len(client.sent) == 1


def test_send_reads_the_body_from_stdin_for_a_dash(monkeypatch):
    _patch_send_resolution(monkeypatch)
    monkeypatch.setattr(cli, "confirm_send", lambda preview: True)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("line one\nline two\n"))
    client = SendingClient()

    asyncio.run(cli._run_send(client, send_args(text="-"), SimpleNamespace(send_allowlist=())))

    assert client.sent[0]["message"] == "line one\nline two"


def test_send_refuses_an_empty_body(monkeypatch):
    _patch_send_resolution(monkeypatch)
    client = SendingClient()

    with pytest.raises(ValueError):
        asyncio.run(cli._run_send(client, send_args(text="   "), SimpleNamespace(send_allowlist=())))

    assert client.sent == []


class CreatingClient(FakeClient):
    def __init__(self, result):
        self.requests = []
        self.result = result

    async def __call__(self, request):
        self.requests.append(request)
        return self.result


def test_create_topic_uses_the_shared_resolver(monkeypatch):
    resolved_peer, calls = _patch_send_resolution(monkeypatch)
    monkeypatch.setattr(cli, "confirm_create", lambda preview: True)
    client = CreatingClient(SimpleNamespace(chats=[], updates=[SimpleNamespace(message=SimpleNamespace(id=1477))]))

    status = asyncio.run(
        cli._run_create(client, argparse.Namespace(command="create", create_kind="topic", chat="-100111", title="Deploys", yes=False))
    )

    assert status == 0
    assert calls["reference"] == "-100111"
    assert client.requests[0].peer is resolved_peer
    assert client.requests[0].title == "Deploys"


def test_create_declined_creates_nothing(monkeypatch):
    monkeypatch.setattr(cli, "confirm_create", lambda preview: False)
    client = CreatingClient(None)

    status = asyncio.run(
        cli._run_create(client, argparse.Namespace(command="create", create_kind="group", title="Hermes", about=None, forum=False, yes=False))
    )

    assert status == 1
    assert client.requests == []


def test_create_without_a_kind_is_an_error():
    with pytest.raises(ValueError):
        asyncio.run(cli._run_create(FakeClient(), argparse.Namespace(command="create", create_kind=None, yes=False)))
