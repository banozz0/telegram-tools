from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from telethon.errors import FloodWaitError

from telegram_tools.models import DeleteResult, TopicInfo


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


async def _delete_batch_with_flood_wait(client, chat: Any, batch: list[int], *, sleep=asyncio.sleep, progress: Callable[[str], None]) -> int:
    while True:
        try:
            await client.delete_messages(chat, batch)
            return len(batch)
        except FloodWaitError as exc:
            seconds = int(getattr(exc, "seconds", 0))
            progress(f"FloodWait: sleeping {seconds}s before retrying delete batch")
            await sleep(seconds)


async def _collect_topic_message_ids(client, chat: Any, topic: TopicInfo) -> list[int]:
    ids: list[int] = []
    skip_ids = {topic.id}
    if topic.top_message is not None:
        skip_ids.add(topic.top_message)

    async for message in client.iter_messages(chat, reply_to=topic.id, wait_time=1):
        message_id = int(getattr(message, "id"))
        if message_id not in skip_ids:
            ids.append(message_id)
    return ids


async def delete_topic_messages(
    client,
    chat: Any,
    topics: list[TopicInfo],
    *,
    execute: bool = False,
    confirm: Callable[[], str] = input,
    batch_size: int = 100,
    progress: Callable[[str], None] | None = None,
    sleep=asyncio.sleep,
) -> DeleteResult:
    progress = progress or (lambda _message: None)
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    ids: list[int] = []
    seen: set[int] = set()
    for topic in topics:
        progress(f"Scanning topic {topic.id} ({topic.title})")
        for message_id in await _collect_topic_message_ids(client, chat, topic):
            if message_id not in seen:
                seen.add(message_id)
                ids.append(message_id)

    if not execute:
        progress(f"Dry-run: {len(ids)} messages would be deleted")
        return DeleteResult(matched=len(ids), deleted=0, dry_run=True)

    if confirm() != "DELETE":
        progress("Delete cancelled")
        return DeleteResult(matched=len(ids), deleted=0, dry_run=False, cancelled=True)

    deleted = 0
    for batch in _chunks(ids, batch_size):
        progress(f"Deleting batch of {len(batch)} messages")
        deleted += await _delete_batch_with_flood_wait(client, chat, batch, sleep=sleep, progress=progress)
        progress(f"Deleted {deleted}/{len(ids)} messages")

    return DeleteResult(matched=len(ids), deleted=deleted, dry_run=False)
