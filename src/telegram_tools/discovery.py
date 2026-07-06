from __future__ import annotations

from typing import Any

from telegram_tools.models import ChatInfo, TopicInfo
from telegram_tools.topics import get_forum_topics


def classify_entity(entity: Any) -> str:
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False) and getattr(entity, "forum", False):
        return "forum_group"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    if entity.__class__.__name__ == "User":
        return "user"
    return "group"


def dialog_to_chat_info(
    dialog: Any,
    *,
    is_admin: bool,
    topics: list[TopicInfo] | None = None,
) -> ChatInfo:
    entity = dialog.entity
    return ChatInfo(
        id=int(dialog.id),
        title=str(getattr(dialog, "title", None) or getattr(dialog, "name", None) or ""),
        username=getattr(entity, "username", None),
        type=classify_entity(entity),
        is_admin=is_admin,
        topics=topics or [],
    )


def filter_chats(chats: list[ChatInfo], *, admin_only: bool) -> list[ChatInfo]:
    if not admin_only:
        return chats
    return [chat for chat in chats if chat.is_admin]


async def is_admin(client, entity, user) -> bool:
    try:
        permissions = await client.get_permissions(entity, user)
    except Exception:
        return False
    return bool(getattr(permissions, "is_admin", False))


async def discover_chats(client) -> list[ChatInfo]:
    user = await client.get_me()
    chats: list[ChatInfo] = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        topics: list[TopicInfo] = []
        if getattr(entity, "forum", False):
            peer = getattr(dialog, "input_entity", entity)
            topics = await get_forum_topics(client, peer)

        chats.append(
            dialog_to_chat_info(
                dialog,
                is_admin=await is_admin(client, entity, user),
                topics=topics,
            )
        )

    return chats
