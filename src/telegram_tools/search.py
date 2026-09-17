from __future__ import annotations

from typing import Any

from telethon.errors.rpcbaseerrors import BadRequestError

from telegram_tools.envelope import CommandError
from telegram_tools.records import (
    message_matches_filters,
    message_to_record,
    parse_date_bound,
    record_marks,
)
from telegram_tools.topics import get_forum_topics, in_id_order


# How deep a whole-chat keyword search reads for a body this tool derived.
#
# Telegram answers `search=` out of its own index, so it finds a typed word at
# any depth of a chat -- and it can never find `[poll] ship it?`,
# `[file] flange.pdf` or `[event] message pinned`, strings that exist only
# here. So a keyword search asks Telegram *and* reads the chat itself, and the
# reading pass is the one with a cost: this many messages, newest first, about
# ten requests at Telethon's hundred a page. The server pass is unchanged and
# still unbounded, so nothing that was found before stops being found; what is
# bounded is only how far back a derived body is looked for.
DERIVED_SCAN = 1000


def _truncate(value: str, max_length: int = 80) -> str:
    value = " ".join(value.split())
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "..."


def format_message_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No messages found."

    lines = ["Messages", "--------------------------------------------"]
    for record in records:
        sender = record.get("sender_username") or record.get("sender_id") or ""
        topic = record.get("topic_id") or ""
        date = record.get("date") or ""
        text = _truncate(str(record.get("text") or ""))
        # `records.record_marks` is the one place a record's marks are derived,
        # so this row and the export formats mark the same message the same way.
        marks = record_marks(record)
        lines.append(
            f"{record.get('id')}\t{date}\ttopic={topic}\tsender={sender}\t{marks}{text}"
        )
    return "\n".join(lines)


async def _topic_not_found(client, chat: Any, topic_id: int, chat_title: str | None) -> CommandError:
    """The refusal for a topic the chat does not have, naming the ones it does.

    Telegram answers `GetReplies` for a missing topic with a bare 400
    `TOPIC_ID_INVALID` -- Telethon has no class for it, so the message is the
    only handle -- and before this every other bad target was refused by name
    while this one was a forty-line traceback. The list is read once, for the
    hint; when it cannot be (a chat that is not a forum), the refusal still
    names the topic and points at `discover`.
    """
    where = chat_title or "this chat"
    try:
        topic_ids = [str(topic.id) for topic in in_id_order(await get_forum_topics(client, chat))]
    except Exception:  # noqa: BLE001 - the list is a courtesy; the refusal stands without it
        topic_ids = []
    if topic_ids:
        hint = f"its topics are {', '.join(topic_ids)} - telegram-tools discover names them"
    else:
        hint = "telegram-tools discover lists its topics"
    return CommandError(f"No topic {topic_id} in {where}.", code="TARGET_NOT_FOUND", hint=hint)


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
    chat_title: str | None = None,
) -> list[dict[str, Any]]:
    since_dt = parse_date_bound(since, end_of_day=False)
    until_dt = parse_date_bound(until, end_of_day=True)
    records: list[dict[str, Any]] = []

    if topic_id is not None:
        from_user_id = await _resolve_from_user_id(client, from_user)
        iterator = client.iter_messages(chat, reply_to=topic_id, wait_time=1)
        try:
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
        except BadRequestError as exc:
            if getattr(exc, "message", "") != "TOPIC_ID_INVALID":
                raise
            raise await _topic_not_found(client, chat, topic_id, chat_title) from None
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

    if not keyword:
        return records

    # The second pass: the same window with no `search=`, filtered here instead,
    # because a derived body is text Telegram was never sent and cannot match.
    # Bounded by `DERIVED_SCAN` messages read, and stopped early once it holds
    # as many matches as were asked for -- both passes come back newest first,
    # so a match dropped by that stop is older than `limit` matches already
    # held and could not have made the answer anyway.
    found = {record["id"]: record for record in records}
    scan = {key: value for key, value in kwargs.items() if key != "search"}
    scan["limit"] = None
    read = 0
    matched = 0
    async for message in client.iter_messages(chat, **scan):
        read += 1
        if message_matches_filters(message, keyword=keyword, since=since_dt, until=until_dt):
            matched += 1
            message_id = int(getattr(message, "id"))
            if message_id not in found:
                found[message_id] = message_to_record(message, chat_id=chat_id)
            if limit is not None and matched >= limit:
                break
        if read >= DERIVED_SCAN:
            break

    # Newest first, the order one pass returned, and never more rows than asked.
    return sorted(found.values(), key=lambda record: record["id"], reverse=True)[:limit]
