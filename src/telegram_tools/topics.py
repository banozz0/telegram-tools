from __future__ import annotations

from collections.abc import Iterable

from telethon.tl.functions.messages import GetForumTopicsByIDRequest, GetForumTopicsRequest

from telegram_tools.models import TopicInfo


def topic_from_telethon(raw_topic) -> TopicInfo:
    topic_id = int(getattr(raw_topic, "id"))
    return TopicInfo(
        id=topic_id,
        title=str(getattr(raw_topic, "title", topic_id)),
        top_message=getattr(raw_topic, "top_message", topic_id),
    )


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
        for raw_topic in raw_topics:
            topic_id = getattr(raw_topic, "id", None)
            if topic_id is None or topic_id in seen:
                continue
            seen.add(topic_id)
            topics.append(topic_from_telethon(raw_topic))
            added += 1

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
    topics = [topic_from_telethon(topic) for topic in getattr(result, "topics", []) or []]
    found = {topic.id for topic in topics}
    topics.extend(TopicInfo(id=topic_id, title=str(topic_id), top_message=topic_id) for topic_id in ids if topic_id not in found)
    return topics
