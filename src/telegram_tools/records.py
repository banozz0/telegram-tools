from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Mapping


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


# -- what a message says about itself --------------------------------------
#
# A message's identifying text is not always the text it was typed with. A
# poll carries its question and nothing else, and Telegram sends it as a
# media, so it reached a listing as the same `[media]` placeholder a photo
# does and reached the archive as an empty `text` -- a row `messages_fts`
# indexes as nothing, which is a message that cannot be found again.
#
# `message_body` is the one place that derivation happens. The printed line,
# the export column and the archive row all read it, so a message says the
# same thing about itself wherever it is shown. Its `extras` are record keys
# added only when the message has them: an ordinary message's record, and the
# CSV header derived from it, stays exactly what it was.


@dataclass(frozen=True)
class Body:
    """A message's identifying text, and the additive keys that explain it."""

    text: str
    extras: dict[str, Any]


# The derived text names its own kind, because that string is what the archive
# stores, what FTS indexes and what every export column shows: a row that can
# only be read on the screen it was printed on is the bug this fixes.
POLL_MARKER = "[poll] "


def _utc_iso(value: Any) -> str | None:
    """A Telethon datetime as UTC ISO-8601, or None when there is not one."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _plain_text(value: Any) -> str:
    """A `TextWithEntities` or the bare string an older layer carries, as a string."""
    if value is None:
        return ""
    inner = getattr(value, "text", None)
    return str(value if inner is None else inner)


def poll_of(message: Any) -> dict[str, Any] | None:
    """A poll's question and answers, or None when the message is not a poll.

    Telegram hands a poll over as a media, which is why it wore the photo's
    placeholder -- but the question and the answer texts arrive as plain
    strings, so nothing is downloaded and no review-queue rule is involved.
    """
    poll = getattr(getattr(message, "media", None), "poll", None)
    if poll is None:
        return None
    answers = [_plain_text(getattr(answer, "text", None)) for answer in (getattr(poll, "answers", None) or ())]
    return {
        "question": _plain_text(getattr(poll, "question", None)),
        "answers": [answer for answer in answers if answer],
    }


def poll_text(poll: Mapping[str, Any]) -> str:
    """`[poll] ship it? — yes / no`: the one line that names a poll and can be searched."""
    line = (POLL_MARKER + str(poll.get("question") or "")).rstrip()
    answers = " / ".join(poll.get("answers") or ())
    return f"{line} — {answers}" if answers else line


def _entity_label(entity: Any) -> str:
    """A user or chat as a person reads it: `@username`, else a name, else a title."""
    if entity is None:
        return ""
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    name = " ".join(
        part for part in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if part
    ).strip()
    if name:
        return name
    return str(getattr(entity, "title", None) or "")


def forward_of(message: Any) -> dict[str, Any] | None:
    """Telegram's own attribution on a forwarded message, or None on one written here.

    A copy is deliberately not a forward: `message copy` re-posts the text and
    the links and drops the author, which is why the tool has both verbs. The
    absence of this key is therefore a fact about the message and not a gap in
    the record -- it is what tells a message written in a chat from one moved
    there with its author attached.
    """
    forward = getattr(message, "forward", None)
    if forward is None:
        return None
    sender_id = getattr(forward, "sender_id", None)
    chat_id = getattr(forward, "chat_id", None)
    sender = _entity_label(getattr(forward, "sender", None)) or None
    chat = _entity_label(getattr(forward, "chat", None)) or None
    # A hidden forward: the original author restricts forwards, so Telegram
    # sends a display name and no id of any kind. The name is kept -- it is the
    # only thing there is -- and flagged, and no id is invented for it, because
    # an unresolvable name is exactly what `hidden` has to warn a reader about.
    hidden = sender_id is None and chat_id is None
    if hidden:
        sender = str(getattr(forward, "from_name", None) or "") or None
    record = {
        "sender_id": None if sender_id is None else int(sender_id),
        "sender": sender,
        "chat_id": None if chat_id is None else int(chat_id),
        "chat": chat,
        "date": _utc_iso(getattr(forward, "date", None)),
        "hidden": hidden,
    }
    record["label"] = forward_label(record)
    return record


def forward_label(forward: Mapping[str, Any]) -> str:
    """How a forward reads at a glance: `@harry`, `Alerts`, `@harry in Alerts`, `Alice (hidden)`."""
    sender = forward.get("sender") or ""
    chat = forward.get("chat") or ""
    who = f"{sender} in {chat}" if sender and chat else (sender or chat or "someone")
    return f"{who} (hidden)" if forward.get("hidden") else who


def message_body(message: Any) -> Body:
    """The text this message is identified by, and what explains it.

    The message's own text wins whenever it has one: a poll with a caption is
    still that caption. The derivation only fills a body that would otherwise
    be empty, so nothing a person typed is ever displaced.
    """
    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""
    text = str(text)
    extras: dict[str, Any] = {}

    forward = forward_of(message)
    if forward is not None:
        extras["forwarded_from"] = forward

    poll = poll_of(message)
    if poll is not None:
        extras["poll"] = poll
        if not text.strip():
            text = poll_text(poll)

    return Body(text=text, extras=extras)


def record_marks(record: Mapping[str, Any], *, media: bool = True) -> str:
    """The bracketed marks a record's line carries, ready to sit in front of its text.

    Outside any truncation, so a long caption can never push one off the row:
    without it a photo with no caption prints as an empty line and reads as
    "nothing was sent". The exports have carried `has_media` all along.

    Provenance comes first and the kind second -- `[fwd @harry] [media] …` --
    because who a message came from is read before what it carries.

    A poll is marked by its kind rather than by `[media]`, which is the
    placeholder that hid it. It is marked here only when its text is not the
    derived one, which already opens with the marker -- a poll sent with a
    caption shows the caption, so the line is the only place left to name it.

    `media=False` is for the two export formats that carry a media column of
    their own; the provenance and kind marks stay, because no column holds them.
    """
    marks: list[str] = []
    text = str(record.get("text") or "")
    forward = record.get("forwarded_from")
    if forward:
        marks.append(f"[fwd {forward_label(forward)}]")
    if record.get("poll"):
        if not text.startswith(POLL_MARKER):
            marks.append(POLL_MARKER.strip())
    elif media and record.get("has_media"):
        marks.append("[media]")
    return "".join(mark + " " for mark in marks)


def message_to_record(message: Any, *, chat_id: int | None = None, topic_id: int | None = None) -> dict[str, Any]:
    reply_to = getattr(message, "reply_to", None)
    sender = getattr(message, "sender", None)
    date_value = _utc_iso(getattr(message, "date", None))

    body = message_body(message)

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
        "text": body.text,
        # Last, and only when the message has them: the shipped keys keep their
        # places and a plain message's CSV header is the header it always was.
        **body.extras,
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
