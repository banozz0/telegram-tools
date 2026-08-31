from __future__ import annotations

from collections.abc import Iterable

from telethon.errors import RPCError
from telethon.tl.functions.messages import (
    GetCustomEmojiDocumentsRequest,
    GetForumTopicsByIDRequest,
    GetForumTopicsRequest,
)

from telegram_tools.models import TopicInfo


def topic_from_telethon(raw_topic, icons: dict[int, str] | None = None) -> TopicInfo:
    topic_id = int(getattr(raw_topic, "id"))
    emoji_id = getattr(raw_topic, "icon_emoji_id", None)
    return TopicInfo(
        id=topic_id,
        title=str(getattr(raw_topic, "title", topic_id)),
        top_message=getattr(raw_topic, "top_message", topic_id),
        icon_emoji=(icons or {}).get(int(emoji_id)) if emoji_id else None,
    )


async def resolve_icon_emoji(client, raw_topics) -> dict[int, str]:
    """Map every icon_emoji_id in a page of raw topics to its plain-emoji character.

    A topic's title is plain text; the emoji Telegram draws in front of it is a
    custom-emoji document ID on the topic, so it takes a second call to read.
    One call covers the whole page - never one per topic. A topic without an
    icon, an ID Telegram will not hand back, or a failed call all mean no emoji,
    never an error: the topic list is the point, the decoration is not.
    """
    emoji_ids = sorted(
        {int(emoji_id) for raw_topic in raw_topics if (emoji_id := getattr(raw_topic, "icon_emoji_id", None))}
    )
    if not emoji_ids:
        return {}

    try:
        documents = await client(GetCustomEmojiDocumentsRequest(document_id=emoji_ids))
    except RPCError:
        return {}

    icons: dict[int, str] = {}
    for document in documents or []:
        alt = next(
            (attribute.alt for attribute in getattr(document, "attributes", []) or [] if getattr(attribute, "alt", None)),
            None,
        )
        if alt:
            icons[int(document.id)] = alt
    return icons


async def get_forum_topics(client, peer, *, page_size: int = 100) -> list[TopicInfo]:
    topics: list[TopicInfo] = []
    seen: set[int] = set()
    offset_date = None
    offset_id = 0
    offset_topic = 0

    while True:
        result = await client(
            GetForumTopicsRequest(
                peer=peer,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=page_size,
            )
        )
        raw_topics = list(getattr(result, "topics", []) or [])
        if not raw_topics:
            break

        added = 0
        fresh: list = []
        for raw_topic in raw_topics:
            topic_id = getattr(raw_topic, "id", None)
            if topic_id is None or topic_id in seen:
                continue
            seen.add(topic_id)
            fresh.append(raw_topic)
            added += 1

        icons = await resolve_icon_emoji(client, fresh)
        topics.extend(topic_from_telethon(raw_topic, icons) for raw_topic in fresh)

        total_count = getattr(result, "count", None)
        if total_count is not None and len(topics) >= total_count:
            break
        if added == 0 or len(raw_topics) < page_size:
            break

        last = raw_topics[-1]
        offset_date = getattr(last, "date", None)
        offset_id = int(getattr(last, "top_message", 0) or 0)
        offset_topic = int(getattr(last, "id", 0) or 0)

    return topics


async def get_forum_topics_by_ids(client, peer, topic_ids: Iterable[int]) -> list[TopicInfo]:
    ids = [int(topic_id) for topic_id in topic_ids]
    if not ids:
        return []

    result = await client(GetForumTopicsByIDRequest(peer=peer, topics=ids))
    raw_topics = list(getattr(result, "topics", []) or [])
    icons = await resolve_icon_emoji(client, raw_topics)
    topics = [topic_from_telethon(raw_topic, icons) for raw_topic in raw_topics]
    found = {topic.id for topic in topics}
    topics.extend(TopicInfo(id=topic_id, title=str(topic_id), top_message=topic_id) for topic_id in ids if topic_id not in found)
    return topics
