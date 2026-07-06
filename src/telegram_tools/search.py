from __future__ import annotations

from typing import Any

from telegram_tools.records import message_matches_filters, message_to_record, parse_date_bound


async def _resolve_from_user_id(client, from_user: str | int | None) -> int | None:
    if from_user is None:
        return None
    return int(await client.get_peer_id(from_user))


async def search_messages(
    client,
    chat: Any,
    *,
    chat_id: int | None = None,
    topic_id: int | None = None,
    keyword: str | None = None,
    from_user: str | int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    since_dt = parse_date_bound(since, end_of_day=False)
    until_dt = parse_date_bound(until, end_of_day=True)
    records: list[dict[str, Any]] = []

    if topic_id is not None:
        from_user_id = await _resolve_from_user_id(client, from_user)
        iterator = client.iter_messages(chat, reply_to=topic_id, wait_time=1)
        async for message in iterator:
            if message_matches_filters(
                message,
                keyword=keyword,
                from_user_id=from_user_id,
                since=since_dt,
                until=until_dt,
            ):
                records.append(message_to_record(message, chat_id=chat_id, topic_id=topic_id))
                if limit is not None and len(records) >= limit:
                    break
        return records

    kwargs: dict[str, Any] = {"limit": limit, "wait_time": 1}
    if keyword:
        kwargs["search"] = keyword
    if from_user:
        kwargs["from_user"] = from_user
    if until_dt:
        kwargs["offset_date"] = until_dt

    async for message in client.iter_messages(chat, **kwargs):
        if message_matches_filters(message, keyword=keyword, since=since_dt, until=until_dt):
            records.append(message_to_record(message, chat_id=chat_id))

    return records
