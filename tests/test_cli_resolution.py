import argparse
import asyncio
from types import SimpleNamespace

import telegram_tools.cli as cli
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
        return ResolvedChat(id=-1003930298580, entity=SimpleNamespace(), input_entity=resolved_peer)

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
            argparse.Namespace(chat="-1003930298580", all_topics=False, topics=[123], execute=False, batch_size=100),
        )
    )

    assert status == 0
    assert calls["reference"] == "-1003930298580"
    assert calls["topics_peer"] is resolved_peer
    assert calls["delete_chat"] is resolved_peer
    assert calls["topics"][0].id == 123
    assert calls["execute"] is False


def test_search_command_uses_shared_chat_resolver(monkeypatch):
    resolved_peer = SimpleNamespace(kind="resolved-input")
    calls = {}

    async def fake_resolve_chat(client, reference):
        calls["reference"] = reference
        return ResolvedChat(id=-1003930298580, entity=SimpleNamespace(), input_entity=resolved_peer)

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
                chat="-1003930298580",
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
    assert calls["reference"] == "-1003930298580"
    assert calls["search_chat"] is resolved_peer
    assert calls["kwargs"]["chat_id"] == -1003930298580
