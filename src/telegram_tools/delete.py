from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import DeleteChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest, DeleteTopicHistoryRequest
from telethon.tl.types import InputPeerChat, InputUserSelf

from telegram_tools.models import ContainerDeleteResult, DeleteResult, LeaveResult, TopicInfo

CLEAR_TOPIC_MESSAGES_WARNING = """\
====================================================
WARNING: CLEAR TOPIC MESSAGES

This will permanently delete ALL MESSAGES from the selected topic(s).

OK: Forum topics will NOT be deleted.
OK: Topic IDs will NOT change.
OK: Only messages will be removed.
===================================================="""


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
            progress(f"FloodWait: sleeping {seconds}s before retrying clear-message batch")
            await sleep(seconds)


def confirm_clear_topic_messages(*, read=input, write=print) -> str:
    write(CLEAR_TOPIC_MESSAGES_WARNING)
    return read("Type DELETE to continue: ")


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
    recheck: Callable[[], Any] | None = None,
) -> DeleteResult:
    progress = progress or (lambda _message: None)
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    ids: list[int] = []
    seen: set[int] = set()
    for topic in topics:
        progress(f"Scanning topic {topic.id} ({topic.display_title})")
        for message_id in await _collect_topic_message_ids(client, chat, topic):
            if message_id not in seen:
                seen.add(message_id)
                ids.append(message_id)

    if not execute:
        progress(f"Dry-run: {len(ids)} topic messages would be cleared")
        return DeleteResult(matched=len(ids), deleted=0, dry_run=True)

    if confirm() != "DELETE":
        progress("Clear topic messages cancelled")
        return DeleteResult(matched=len(ids), deleted=0, dry_run=False, cancelled=True)

    # The gate has been answered; check the topics are still the topics that
    # were named before a single message is deleted.
    if recheck is not None:
        await recheck()

    deleted = 0
    for batch in _chunks(ids, batch_size):
        progress(f"Clearing batch of {len(batch)} topic messages")
        deleted += await _delete_batch_with_flood_wait(client, chat, batch, sleep=sleep, progress=progress)
        progress(f"Cleared {deleted}/{len(ids)} topic messages")

    return DeleteResult(matched=len(ids), deleted=deleted, dry_run=False)


# -- deleting the chat or topic itself ------------------------------------

RULE = "--------------------------------------------"

# The General topic has no messageActionTopicCreate service message to delete,
# so the delete-history route that removes every other topic cannot remove it.
GENERAL_TOPIC_ID = 1

# Which `delete` noun owns each type `discover` prints. Basic groups are
# deliberately absent: `create` makes supergroups, so removing a basic group
# would be a deletion this tool cannot undo. Telegram itself can do it.
DELETE_KIND_TYPES = {
    "group": ("supergroup", "forum_group"),
    "channel": ("channel",),
}

DELETE_CONSEQUENCES = {
    "group": """\
GONE: The group, for EVERY member - not just for you.
GONE: Every message, topic, file and photo in it.
GONE: Its invite links and its ID. A new group is a new ID.
NOTE: Only the creator can do this.""",
    "channel": """\
GONE: The channel, for EVERY subscriber - not just for you.
GONE: Every post, file and photo in it.
GONE: Its public link and its ID. A new channel is a new ID.
NOTE: Only the creator can do this.""",
    "topic": """\
GONE: The topic and every message in it, for everyone.
OK:   The rest of the group is untouched.
NOTE: Telegram deletes a topic by deleting all of its messages,
      the one that opened it included. There is no undo.""",
}


def format_delete_preview(kind: str, title: str, chat_id: int, *, where: str | None = None) -> str:
    """What is about to stop existing, so the typed title is an informed answer."""
    lines = [
        "====================================================",
        f"WARNING: DELETE {kind.upper()}",
        "",
        "This permanently deletes a real thing on Telegram.",
        "Telegram does not undo this.",
        RULE,
        f"Kind    {kind}",
        f"Title   {title}",
        f"Chat    {chat_id}",
    ]
    if where is not None:
        lines.append(f"In      {where}")
    lines += [RULE, DELETE_CONSEQUENCES[kind], "===================================================="]
    return "\n".join(lines)


def format_delete_summary(kind: str, title: str, chat_id: int, *, where: str | None = None) -> str:
    """The dry-run's version: what it is and what would go, without the banner.

    The banner belongs to the confirm. Printing it twice in one menu flow --
    once for the dry-run, once to confirm -- is how people learn to skim it.
    """
    target = f"{kind} {title} ({chat_id})"
    if where is not None:
        target += f", in {where}"
    return "\n".join(
        [
            f"Dry-run: {target}.",
            DELETE_CONSEQUENCES[kind],
            "Nothing has been deleted. Re-run with --execute to do it for real.",
        ]
    )


def confirm_delete(
    preview: str, title: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> str:
    """Ask for the target's own title.

    Typing DELETE would only prove intent to delete something; typing the title
    proves intent to delete *this* one, which is the mistake worth catching.
    """
    write(preview)
    return read(f"Type the exact title ({title}) to continue: ")


def _titles_match(typed: str, title: str) -> bool:
    # Case-insensitive for the same reason the DELETE gate is: the proof of
    # intent is knowing which chat you picked, not holding the shift key.
    return typed.strip().casefold() == title.casefold()


def kind_for_type(type_name: str) -> str | None:
    """Which `delete` noun owns a chat type, or None if this tool cannot delete it."""
    for kind, types in DELETE_KIND_TYPES.items():
        if type_name in types:
            return kind
    return None


async def delete_chat(
    client,
    peer: Any,
    *,
    kind: str,
    title: str,
    chat_id: int,
    execute: bool = False,
    confirm: Callable[[str, str], str] = confirm_delete,
    progress: Callable[[str], None] | None = None,
    recheck: Callable[[], Any] | None = None,
) -> ContainerDeleteResult:
    """Delete a supergroup or broadcast channel after a dry-run and a typed title."""
    progress = progress or (lambda _message: None)

    if not execute:
        progress(format_delete_summary(kind, title, chat_id))
        return ContainerDeleteResult(kind=kind, id=chat_id, title=title, dry_run=True)

    preview = format_delete_preview(kind, title, chat_id)

    if not _titles_match(confirm(preview, title), title):
        progress(f"Delete {kind} cancelled - the typed title did not match.")
        return ContainerDeleteResult(kind=kind, id=chat_id, title=title, dry_run=False, cancelled=True)

    # The last thing before the point of no return: the title that was typed
    # belongs to the chat that is about to go, not to one it was renamed from.
    if recheck is not None:
        await recheck()

    await client(DeleteChannelRequest(channel=peer))
    progress(f"Deleted {kind} {title} ({chat_id})")
    return ContainerDeleteResult(kind=kind, id=chat_id, title=title, dry_run=False, deleted=True)


async def delete_topic(
    client,
    peer: Any,
    topic: TopicInfo,
    *,
    chat_id: int,
    chat_title: str,
    execute: bool = False,
    confirm: Callable[[str, str], str] = confirm_delete,
    progress: Callable[[str], None] | None = None,
    recheck: Callable[[], Any] | None = None,
) -> ContainerDeleteResult:
    """Delete a forum topic after a dry-run and a typed title.

    Telegram has no delete-topic method: a client deletes a topic by deleting
    every message in it, the service message that opened it included, after
    which the topic is gone from the list. That is what this does.
    """
    progress = progress or (lambda _message: None)

    if topic.id == GENERAL_TOPIC_ID:
        raise ValueError(
            "The General topic cannot be deleted - Telegram has no message that opened it to delete. "
            "Use `clear-messages` to empty it instead."
        )

    if not execute:
        progress(format_delete_summary("topic", topic.title, topic.id, where=chat_title))
        return ContainerDeleteResult(
            kind="topic", id=chat_id, topic_id=topic.id, title=topic.title, dry_run=True
        )

    preview = format_delete_preview("topic", topic.title, chat_id, where=chat_title)

    if not _titles_match(confirm(preview, topic.title), topic.title):
        progress("Delete topic cancelled - the typed title did not match.")
        return ContainerDeleteResult(
            kind="topic", id=chat_id, topic_id=topic.id, title=topic.title, dry_run=False, cancelled=True
        )

    if recheck is not None:
        await recheck()

    await client(DeleteTopicHistoryRequest(peer=peer, top_msg_id=topic.id))
    progress(f"Deleted topic {topic.title} ({topic.id}) in {chat_title}")
    return ContainerDeleteResult(
        kind="topic", id=chat_id, topic_id=topic.id, title=topic.title, dry_run=False, deleted=True
    )


# -- leaving a chat -----------------------------------------------------------
#
# The counterpart of the sibling tool's leave-server. Nothing is deleted: the
# chat stays for everyone else, and what this account loses is its seat in it.
# The gate is `delete`'s anyway, because a chat this account created and left
# is a chat it may never get back into -- and, with no other admin, one nobody
# can run.

# Which `leave` noun owns each type `discover` prints. A user is absent on
# purpose: a private chat is not left, and its history is deleted in Telegram.
LEAVE_KIND_TYPES = {
    "group": ("supergroup", "forum_group", "group"),
    "channel": ("channel",),
}

LEAVE_CONSEQUENCES = """\
OK:   The chat stays, for everyone else in it. Nothing is deleted.
GONE: Your seat in it: its messages stop reaching you, and you stop
      being able to post, read or administer it.
NOTE: Getting back in takes an invite link, a public username, or
      someone still inside adding you."""

CREATOR_NOTE = """\
NOTE: You created this chat. Leaving does not delete it and does not
      hand it to anyone: it keeps running without an owner, and you
      cannot come back as its creator. If it has no other admin, no
      one can run it afterwards."""


def leave_kind_for_type(type_name: str) -> str | None:
    """Which `leave` noun owns a chat type, or None if this tool cannot leave it."""
    for kind, types in LEAVE_KIND_TYPES.items():
        if type_name in types:
            return kind
    return None


def _leave_lines(kind: str, *, creator: bool) -> list[str]:
    lines = [LEAVE_CONSEQUENCES]
    if creator:
        lines.append(CREATOR_NOTE)
    return lines


def format_leave_summary(kind: str, title: str, chat_id: int, *, creator: bool) -> str:
    """The dry-run's version: what would be left and what that costs, without the banner."""
    return "\n".join(
        [
            f"Dry-run: leave {kind} {title} ({chat_id}).",
            *_leave_lines(kind, creator=creator),
            "Nothing has changed. Re-run with --execute to leave for real.",
        ]
    )


def format_leave_preview(kind: str, title: str, chat_id: int, *, creator: bool) -> str:
    """What is about to be left, so the typed title is an informed answer."""
    lines = [
        "====================================================",
        f"WARNING: LEAVE {kind.upper()}",
        "",
        "This account leaves a real chat on Telegram.",
        "Nothing in it is deleted; getting back in is not up to you.",
        RULE,
        f"Kind    {kind}",
        f"Title   {title}",
        f"Chat    {chat_id}",
        RULE,
        *_leave_lines(kind, creator=creator),
        "====================================================",
    ]
    return "\n".join(lines)


async def leave_chat(
    client,
    peer: Any,
    *,
    kind: str,
    title: str,
    chat_id: int,
    creator: bool = False,
    execute: bool = False,
    confirm: Callable[[str, str], str] = confirm_delete,
    progress: Callable[[str], None] | None = None,
    recheck: Callable[[], Any] | None = None,
) -> LeaveResult:
    """Leave a group or channel after a dry-run and a typed title.

    A supergroup or channel is left through `channels.leaveChannel`; a basic
    group has no such call, so it is left by removing this account from it
    (`messages.deleteChatUser` with the account itself as the user).
    """
    progress = progress or (lambda _message: None)

    if not execute:
        progress(format_leave_summary(kind, title, chat_id, creator=creator))
        return LeaveResult(kind=kind, id=chat_id, title=title, creator=creator, dry_run=True)

    preview = format_leave_preview(kind, title, chat_id, creator=creator)

    if not _titles_match(confirm(preview, title), title):
        progress(f"Leave {kind} cancelled - the typed title did not match.")
        return LeaveResult(kind=kind, id=chat_id, title=title, creator=creator, dry_run=False, cancelled=True)

    # The typed title belongs to the chat about to be left, not to one it was
    # renamed from in the meantime.
    if recheck is not None:
        await recheck()

    if isinstance(peer, InputPeerChat):
        await client(DeleteChatUserRequest(chat_id=peer.chat_id, user_id=InputUserSelf()))
    else:
        await client(LeaveChannelRequest(channel=peer))
    progress(f"Left {kind} {title} ({chat_id})")
    return LeaveResult(kind=kind, id=chat_id, title=title, creator=creator, dry_run=False, left=True)
