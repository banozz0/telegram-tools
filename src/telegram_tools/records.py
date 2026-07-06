from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any


def parse_date_bound(value: str | None, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None

    if "T" not in value and len(value) == 10:
        parsed_date = date.fromisoformat(value)
        parsed_time = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def topic_id_for_message(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    if not reply_to or not getattr(reply_to, "forum_topic", False):
        return None
    return getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "reply_to_msg_id", None)


def message_to_record(message: Any, *, chat_id: int | None = None, topic_id: int | None = None) -> dict[str, Any]:
    reply_to = getattr(message, "reply_to", None)
    sender = getattr(message, "sender", None)
    message_date = getattr(message, "date", None)
    if isinstance(message_date, datetime):
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=UTC)
        date_value = message_date.astimezone(UTC).isoformat()
    else:
        date_value = None

    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""

    return {
        "id": int(getattr(message, "id")),
        "chat_id": chat_id,
        "topic_id": topic_id if topic_id is not None else topic_id_for_message(message),
        "date": date_value,
        "sender_id": getattr(message, "sender_id", None),
        "sender_username": getattr(sender, "username", None),
        "reply_to_msg_id": getattr(reply_to, "reply_to_msg_id", None) if reply_to else None,
        "reply_to_top_id": getattr(reply_to, "reply_to_top_id", None) if reply_to else None,
        "has_media": bool(getattr(message, "media", None)),
        "text": text,
    }


def message_matches_filters(
    message: Any,
    *,
    keyword: str | None = None,
    from_user_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> bool:
    message_date = getattr(message, "date", None)
    if isinstance(message_date, datetime):
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=UTC)
        message_date = message_date.astimezone(UTC)

    if since and isinstance(message_date, datetime) and message_date < since:
        return False
    if until and isinstance(message_date, datetime) and message_date > until:
        return False
    if from_user_id is not None and getattr(message, "sender_id", None) != from_user_id:
        return False
    if keyword:
        text = (getattr(message, "raw_text", None) or getattr(message, "message", "") or "").lower()
        if keyword.lower() not in text:
            return False
    return True
