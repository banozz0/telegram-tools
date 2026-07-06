import asyncio
from types import SimpleNamespace

import pytest

from telegram_tools.resolver import EntityResolutionError, resolve_chat


class FakeClient:
    def __init__(self, *, dialogs=None, entity_by_ref=None):
        self.dialogs = dialogs or []
        self.entity_by_ref = entity_by_ref or {}
        self.get_entity_calls = []
        self.get_input_entity_calls = []
        self.get_peer_id_calls = []

    def iter_dialogs(self):
        async def iterator():
            for dialog in self.dialogs:
                yield dialog

        return iterator()

    async def get_entity(self, reference):
        self.get_entity_calls.append(reference)
        if reference not in self.entity_by_ref:
            raise ValueError(f"Cannot find any entity corresponding to {reference!r}")
        return self.entity_by_ref[reference]

    async def get_input_entity(self, entity):
        self.get_input_entity_calls.append(entity)
        return SimpleNamespace(kind="input", entity=entity)

    async def get_peer_id(self, entity):
        self.get_peer_id_calls.append(entity)
        return entity.peer_id


def test_resolve_numeric_chat_id_from_dialogs_before_get_entity():
    entity = SimpleNamespace(title="Builds")
    input_entity = SimpleNamespace(kind="input-dialog")
    client = FakeClient(
        dialogs=[
            SimpleNamespace(id=-100111, entity=SimpleNamespace(title="Other"), input_entity=SimpleNamespace()),
            SimpleNamespace(id=-1001112223333, entity=entity, input_entity=input_entity),
        ]
    )

    resolved = asyncio.run(resolve_chat(client, "-1001112223333"))

    assert resolved.id == -1001112223333
    assert resolved.entity is entity
    assert resolved.input_entity is input_entity
    assert client.get_entity_calls == []


def test_resolve_username_uses_telethon_entity_lookup():
    entity = SimpleNamespace(peer_id=-100222)
    client = FakeClient(entity_by_ref={"@builds": entity})

    resolved = asyncio.run(resolve_chat(client, "@builds"))

    assert resolved.id == -100222
    assert resolved.entity is entity
    assert resolved.input_entity.entity is entity
    assert client.get_entity_calls == ["@builds"]


def test_resolve_tme_link_uses_telethon_entity_lookup():
    entity = SimpleNamespace(peer_id=-100333)
    client = FakeClient(entity_by_ref={"https://t.me/builds": entity})

    resolved = asyncio.run(resolve_chat(client, "https://t.me/builds"))

    assert resolved.id == -100333
    assert resolved.entity is entity
    assert client.get_entity_calls == ["https://t.me/builds"]


def test_resolve_invalid_numeric_chat_id_raises_clean_error():
    client = FakeClient(dialogs=[])

    with pytest.raises(EntityResolutionError, match="-100999"):
        asyncio.run(resolve_chat(client, "-100999"))

    assert client.get_entity_calls == [-100999]
