from __future__ import annotations

import re
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
# does; a service message carries an action and no text at all, so it reached
# a listing as a row with every column empty. Both reached the archive as an
# empty `text` -- a row `messages_fts` indexes as nothing, which is a message
# that cannot be found again.
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
SERVICE_MARKER = "[event] "

# Telegram's own action classes, as a phrase a person reads. Anything not
# listed falls back to the class name with its words separated, which is never
# wrong and never silent: a Telegram release that adds an action names it here
# the day it arrives, and the row says what it is rather than nothing at all.
SERVICE_LABELS = {
    "topic_create": "topic created",
    "topic_edit": "topic edited",
    "pin_message": "message pinned",
    "chat_create": "group created",
    "channel_create": "channel created",
    "chat_edit_title": "chat renamed",
    "chat_edit_photo": "chat photo changed",
    "chat_delete_photo": "chat photo removed",
    "chat_add_user": "member added",
    "chat_delete_user": "member removed",
    "chat_joined_by_link": "joined by invite link",
    "chat_joined_by_request": "join request approved",
    "chat_migrate_to": "group upgraded to a supergroup",
    "channel_migrate_from": "supergroup made from a group",
    "history_clear": "history cleared",
    "set_messages_ttl": "auto-delete timer changed",
    "set_chat_theme": "chat theme changed",
    "contact_sign_up": "joined Telegram",
    "screenshot_taken": "screenshot taken",
    "phone_call": "call",
    "group_call": "group call",
    "group_call_scheduled": "group call scheduled",
}


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


def _listed(line: str, options: Any) -> str:
    """`ship it? — yes / no`: a prompt and what is under it, as one searchable line."""
    joined = " / ".join(options or ())
    return f"{line} — {joined}" if line and joined else (line or joined)


def poll_text(poll: Mapping[str, Any]) -> str:
    """`[poll] ship it? — yes / no`: the one line that names a poll and can be searched."""
    return _listed((POLL_MARKER + str(poll.get("question") or "")).rstrip(), poll.get("answers"))


def _action_name(class_name: str) -> str:
    """`MessageActionTopicCreate` as `topic_create`; `MessageActionSetMessagesTTL` as `set_messages_ttl`."""
    stem = class_name.removeprefix("MessageAction") or class_name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", stem).lower()


def service_of(message: Any) -> dict[str, Any] | None:
    """The event a service message *is*, or None for a message someone wrote.

    Telegram writes its own events into a chat -- a topic created, a message
    pinned, someone added -- and counts them in the numbering, which is why
    dropping them would make the printed ids look gappy and would hide the
    record of a topic being created. They carry no text of their own, so they
    reached a listing as a row with every column empty and reached the store as
    a row nothing can search for. The action is the text they have.
    """
    action = getattr(message, "action", None)
    if action is None:
        return None
    name = _action_name(type(action).__name__)
    record = {"action": name, "label": SERVICE_LABELS.get(name, name.replace("_", " "))}
    # The one detail worth the row: what a created topic, a renamed chat or a
    # new group is called. Everything else about an action stays on Telegram.
    title = getattr(action, "title", None)
    if isinstance(title, str) and title:
        record["title"] = title
    return record


def service_text(service: Mapping[str, Any]) -> str:
    """`[event] topic created: Deploys`: the named event a blank row used to be."""
    line = SERVICE_MARKER + str(service.get("label") or "")
    title = service.get("title")
    return f"{line}: {title}" if title else line


# -- what a message carries, when that is the only thing it says -----------
#
# A poll was not the only media whose one identifying string never reached a
# row. A file's name, an audio's title and performer, a sticker's emoji, a
# checklist, a shared contact's name, a venue, an invoice, a game, a
# giveaway's prize and a dice's throw are all plain strings Telegram sends
# with the message, and every one of them printed as the same `[media]`
# placeholder and landed in the archive with an empty `text` -- a row
# `messages_fts` indexes as nothing.
#
# Left alone on purpose: a photo, a geo point or a live one, a story, paid
# media, a video stream and an unsupported media have no text of their own to
# lose, and a web page is a preview of a link the person typed, so the message
# already carries the text that finds it.


# The fields worth a row, in the order a person reads them, joined the way a
# poll joins its question to its answers. The kinds whose line reads otherwise
# -- an audio, a checklist, a name, a throw -- are spelled in
# `attachment_detail`.
ATTACHMENT_FIELDS = {
    "sticker": ("emoji",),
    "file": ("file_name",),
    "venue": ("title", "address"),
    "invoice": ("title", "description"),
    "game": ("title",),
    "giveaway": ("prize_description",),
}


def _kept(record: Mapping[str, Any]) -> dict[str, Any]:
    """The record with its empty fields dropped: a key rides only where there is one."""
    return {key: value for key, value in record.items() if value not in (None, "", [], ())}


def _joined(attachment: Mapping[str, Any], *keys: str, separator: str = " — ") -> str:
    """The named fields a message has, in order, as one line."""
    values = (attachment.get(key) for key in keys)
    return separator.join(str(value) for value in values if value not in (None, ""))


def _document_attribute(document: Any, *class_names: str) -> Any:
    """The first of a document's attributes that is one of Telegram's named classes."""
    for attribute in getattr(document, "attributes", None) or ():
        if type(attribute).__name__ in class_names:
            return attribute
    return None


def _document_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaDocument` as what it is: a sticker, a voice note, an audio, a file.

    Every one of those names arrives in the document's own attributes, so
    nothing here is downloaded: a file name, an audio's title and performer and
    a sticker's `alt` emoji are strings the message already carries.
    """
    document = getattr(media, "document", None)
    if document is None:
        return None
    # A sticker's `alt` is the emoji it stands for, which is the only word
    # about it anyone could search for. A custom emoji carries the same field.
    sticker = _document_attribute(document, "DocumentAttributeSticker", "DocumentAttributeCustomEmoji")
    if sticker is not None:
        return _kept({"kind": "sticker", "emoji": getattr(sticker, "alt", None)})
    file_name = getattr(_document_attribute(document, "DocumentAttributeFilename"), "file_name", None)
    audio = _document_attribute(document, "DocumentAttributeAudio")
    if audio is not None:
        # `voice` is Telegram's own flag for a voice note, which is why a voice
        # note and a music file are two kinds and not one: a voice note has no
        # title at all, and the row should say so rather than say `file`.
        return _kept(
            {
                "kind": "voice" if getattr(audio, "voice", False) else "audio",
                "title": getattr(audio, "title", None),
                "performer": getattr(audio, "performer", None),
                "file_name": file_name,
            }
        )
    return _kept({"kind": "file", "file_name": file_name})


def _checklist_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaToDo`: the poll's closest sibling, and the same bug."""
    todo = getattr(media, "todo", None)
    if todo is None:
        return None
    items = [_plain_text(getattr(item, "title", None)) for item in (getattr(todo, "list", None) or ())]
    return _kept(
        {
            "kind": "checklist",
            "title": _plain_text(getattr(todo, "title", None)),
            "items": [item for item in items if item],
        }
    )


def _contact_of(media: Any) -> dict[str, Any] | None:
    """A shared contact's name, and nothing else at all.

    `phone_number`, `vcard` and `user_id` are deliberately never read. The
    printed row, the archive's `text` column, `platform_json` and every export
    column are places someone else's phone number must not turn up, and the
    only way to guarantee that is not to carry it out of this function.
    """
    return _kept(
        {
            "kind": "contact",
            "first_name": getattr(media, "first_name", None),
            "last_name": getattr(media, "last_name", None),
        }
    )


def _venue_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaVenue`: the place's name and its address. The point itself is a geo."""
    return _kept(
        {"kind": "venue", "title": getattr(media, "title", None), "address": getattr(media, "address", None)}
    )


def _invoice_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaInvoice`: what is being sold. The amount is a number, not a name."""
    return _kept(
        {
            "kind": "invoice",
            "title": getattr(media, "title", None),
            "description": getattr(media, "description", None),
        }
    )


def _game_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaGame`: the game's title, off the game itself."""
    return _kept({"kind": "game", "title": getattr(getattr(media, "game", None), "title", None)})


def _giveaway_of(media: Any) -> dict[str, Any] | None:
    """A giveaway or its results: the prize is the line, and both classes spell it alike."""
    return _kept({"kind": "giveaway", "prize_description": getattr(media, "prize_description", None)})


def _dice_of(media: Any) -> dict[str, Any] | None:
    """A `MessageMediaDice`: the emoji thrown and what it landed on."""
    return _kept({"kind": "dice", "emoticon": getattr(media, "emoticon", None), "value": getattr(media, "value", None)})


# Dispatched on Telegram's own class, never on a field that happens to be
# there: a web page preview carries a `title` and a `description` exactly the
# way an invoice does, and a photo must stay the untouched kind it is.
ATTACHMENT_READERS = {
    "MessageMediaDocument": _document_of,
    "MessageMediaToDo": _checklist_of,
    "MessageMediaContact": _contact_of,
    "MessageMediaVenue": _venue_of,
    "MessageMediaInvoice": _invoice_of,
    "MessageMediaGame": _game_of,
    "MessageMediaGiveaway": _giveaway_of,
    "MessageMediaGiveawayResults": _giveaway_of,
    "MessageMediaDice": _dice_of,
}


def attachment_of(message: Any) -> dict[str, Any] | None:
    """What a message carries, named, or None for a kind that loses no text.

    A poll is not one of these: it keeps the `poll` key it shipped with, and
    the reader table is what says which kinds are read here at all.
    """
    media = getattr(message, "media", None)
    if media is None:
        return None
    reader = ATTACHMENT_READERS.get(type(media).__name__)
    return None if reader is None else reader(media)


def attachment_marker(attachment: Mapping[str, Any]) -> str:
    """`[file]`, `[sticker]`, `[contact]`: the kind's own word, the way `[poll]` names a poll."""
    return f"[{attachment.get('kind') or 'media'}]"


def attachment_detail(attachment: Mapping[str, Any]) -> str:
    """The identifying text itself, with no marker in front of it."""
    kind = attachment.get("kind")
    if kind in ("audio", "voice"):
        # A title is the name of a piece of music; a file name is what is left
        # when Telegram was not told one.
        return _joined(attachment, "title", "performer") or _joined(attachment, "file_name")
    if kind == "checklist":
        return _listed(_joined(attachment, "title"), attachment.get("items"))
    if kind == "contact":
        return _joined(attachment, "first_name", "last_name", separator=" ")
    if kind == "dice":
        return _joined(attachment, "emoticon", "value", separator=" ")
    return _joined(attachment, *ATTACHMENT_FIELDS.get(str(kind), ()))


def attachment_text(attachment: Mapping[str, Any]) -> str:
    """`[file] report.pdf`, `[sticker] 🎉`, `[contact] Alice Smith`: the line a placeholder was.

    A kind with nothing to name still says its kind -- `[voice]` for a voice
    note Telegram gave no name to -- because that word is one an archive search
    can find, and an empty row is not.
    """
    marker = attachment_marker(attachment)
    detail = attachment_detail(attachment)
    return f"{marker} {detail}" if detail else marker


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

    attachment = attachment_of(message)
    if attachment is not None:
        extras["attachment"] = attachment
        if not text.strip():
            text = attachment_text(attachment)

    service = service_of(message)
    if service is not None:
        extras["service"] = service
        if not text.strip():
            text = service_text(service)

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

    An attachment is marked the same way, by its own kind: `[file]`,
    `[sticker]`, `[contact]`. `[media]` is what is left for the kinds that
    carry no name of their own -- a photo, a geo point, a story.

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
    elif record.get("service"):
        if not text.startswith(SERVICE_MARKER):
            marks.append(SERVICE_MARKER.strip())
    elif record.get("attachment"):
        marker = attachment_marker(record["attachment"])
        if not text.startswith(marker):
            marks.append(marker)
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
