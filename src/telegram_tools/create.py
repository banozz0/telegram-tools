from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import CreateForumTopicRequest

RULE = "--------------------------------------------"

KIND_LABELS = {
    "group": "supergroup",
    "forum": "forum group (a group with topics)",
    "channel": "broadcast channel",
    "topic": "forum topic",
}


@dataclass(frozen=True)
class CreatedChat:
    kind: str
    title: str
    id: int | None = None
    topic_id: int | None = None
    forum: bool = False
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "chat_id": self.id,
            "topic_id": self.topic_id,
            "forum": self.forum,
            "created": not self.cancelled and self.id is not None,
            "cancelled": self.cancelled,
        }


def format_create_preview(kind: str, title: str, *, about: str | None, forum: bool, chat_title: str | None = None) -> str:
    label = KIND_LABELS["forum" if kind == "group" and forum else kind]
    lines = [f"Creating a {label}", RULE, f"Title   {title}"]
    if chat_title is not None:
        lines.append(f"In      {chat_title}")
    if about:
        lines.append(f"About   {about}")
    lines.append(RULE)
    return "\n".join(lines)


def confirm_create(preview: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    write(preview)
    answer = read("Create it? [y/N]: ").strip().lower()
    if not answer:
        write("No answer read - cancelled.")
        return False
    return answer == "y"


def _new_chat_id(result: Any, title: str) -> int:
    chats = list(getattr(result, "chats", []) or [])
    if not chats:
        raise ValueError(f"Telegram accepted the request for {title!r} but returned no chat; nothing was recorded.")
    return utils.get_peer_id(chats[0])


def _new_topic_id(result: Any, title: str) -> int:
    # Creating a topic posts its service message, and that message's id *is* the
    # topic id — Telegram never returns the topic itself here.
    for update in getattr(result, "updates", []) or []:
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if message_id is not None:
            return int(message_id)
    raise ValueError(f"Telegram accepted the topic {title!r} but returned no message to take its id from.")


def _cancelled(kind: str, title: str, *, forum: bool = False) -> CreatedChat:
    return CreatedChat(kind=kind, title=title, forum=forum, cancelled=True)


async def _create_channel_like(
    client,
    title: str,
    *,
    about: str | None,
    megagroup: bool,
    broadcast: bool,
    forum: bool,
    kind: str,
    confirm: Callable[[], bool] | None,
) -> CreatedChat:
    if confirm is not None and not confirm():
        return _cancelled(kind, title, forum=forum)

    result = await client(
        CreateChannelRequest(
            title=title,
            about=about or "",
            megagroup=megagroup or None,
            broadcast=broadcast or None,
            forum=forum,
        )
    )
    return CreatedChat(kind=kind, title=title, id=_new_chat_id(result, title), forum=forum)


async def create_group(
    client,
    title: str,
    *,
    about: str | None = None,
    forum: bool = False,
    confirm: Callable[[], bool] | None = None,
) -> CreatedChat:
    """Create a supergroup, optionally with topics already switched on.

    `forum` is part of the create call, so a topics group never exists as a
    plain group first — there is no window where a second toggle could fail and
    leave a half-made thing behind.
    """
    return await _create_channel_like(
        client,
        title,
        about=about,
        megagroup=True,
        broadcast=False,
        forum=forum,
        kind="group",
        confirm=confirm,
    )


async def create_channel(
    client,
    title: str,
    *,
    about: str | None = None,
    confirm: Callable[[], bool] | None = None,
) -> CreatedChat:
    return await _create_channel_like(
        client,
        title,
        about=about,
        megagroup=False,
        broadcast=True,
        forum=False,
        kind="channel",
        confirm=confirm,
    )


async def create_topic(
    client,
    peer: Any,
    *,
    chat_id: int,
    title: str,
    confirm: Callable[[], bool] | None = None,
) -> CreatedChat:
    if confirm is not None and not confirm():
        return _cancelled("topic", title)

    result = await client(CreateForumTopicRequest(peer=peer, title=title))
    return CreatedChat(kind="topic", title=title, id=chat_id, topic_id=_new_topic_id(result, title), forum=True)
