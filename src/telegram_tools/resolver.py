from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class EntityResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedChat:
    id: int
    entity: Any
    input_entity: Any


def _parse_numeric_reference(reference: Any) -> int | None:
    if isinstance(reference, int):
        return reference
    if isinstance(reference, str):
        value = reference.strip()
        if value and value.lstrip("+-").isdigit():
            return int(value)
    return None


async def _resolve_from_dialogs(client, chat_id: int) -> ResolvedChat | None:
    async for dialog in client.iter_dialogs():
        if int(getattr(dialog, "id")) != chat_id:
            continue

        entity = dialog.entity
        input_entity = getattr(dialog, "input_entity", None)
        if input_entity is None:
            input_entity = await client.get_input_entity(entity)
        return ResolvedChat(id=chat_id, entity=entity, input_entity=input_entity)

    return None


async def resolve_chat(client, reference: str | int) -> ResolvedChat:
    numeric_reference = _parse_numeric_reference(reference)
    if numeric_reference is not None:
        resolved = await _resolve_from_dialogs(client, numeric_reference)
        if resolved is not None:
            return resolved
        lookup_reference: str | int = numeric_reference
    else:
        lookup_reference = reference

    try:
        entity = await client.get_entity(lookup_reference)
        input_entity = await client.get_input_entity(entity)
        peer_id = int(await client.get_peer_id(entity))
    except Exception as exc:
        raise EntityResolutionError(f"Cannot resolve chat {reference!r}.") from exc

    return ResolvedChat(id=peer_id, entity=entity, input_entity=input_entity)
