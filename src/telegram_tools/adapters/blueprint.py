"""The blueprint port: a chat's structure as Telegram shows it, and one apply step at a time.

The shared copy's `BlueprintPort` (`_core/adapters.py`), filled in for a
Telethon client signed in as the person. `read()` returns the raw shape the
engine filters through this tool's allowlist (`structure.ALLOWLIST`): the
container's title, kind, description and the settings a fresh chat can be
given, plus its topics. `apply()` performs one create or update the engine
planned, with every handle already resolved to a rid, and answers with the
rid it made or touched. The engine decides what transfers, what order the
steps run in and when to stop; this module only knows Telegram's calls.

Two things are settled here because this is the card that lands them:

* **The rights primitives live in this adapter** (spec section 12): the default
  banned rights of a chat, its slow mode, whether joining needs approval, and
  one admin's rights set by name. `apply()` uses the first three; the admin one
  is here so the administration commands wrap the same call rather than write
  a second, and it is never reached by an apply, because an admin is a person
  and people never transfer.
* **A basic group is refused** (`PLATFORM_UNSUPPORTED`), for the reason `delete`
  refuses one: `create` makes supergroups, so a blueprint of a basic group
  could never be applied back to what it came from.

Nothing here reads members, admins as people, messages, history or invite
links: a full-chat read carries some of those (an exported invite, a linked
chat id) and they are left out of the raw shape or dropped by the allowlist,
which reports each drop by name.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from telethon import utils
from telethon.tl.functions.channels import (
    EditAdminRequest,
    EditTitleRequest,
    GetFullChannelRequest,
    ToggleJoinRequestRequest,
    ToggleSlowModeRequest,
)
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    EditChatAboutRequest,
    EditChatDefaultBannedRightsRequest,
    EditForumTopicRequest,
)
from telethon.tl.types import ChatAdminRights, ChatBannedRights

from telegram_tools._core import rid as _rid
from telegram_tools._core.blueprint import BlueprintError, slug
from telegram_tools._core.identity import Target
from telegram_tools.create import _new_topic_id
from telegram_tools.discovery import classify_entity
from telegram_tools.envelope import PREFIX, CommandError
from telegram_tools.resolver import ResolvedChat, resolve_chat
from telegram_tools.topics import get_forum_topics

# The chat kinds a blueprint names, from what `discover` calls the chat. A
# basic group is absent on purpose (see the module docstring).
CHAT_KINDS = {"supergroup": "supergroup", "forum_group": "forum", "channel": "channel"}
# Every banned right Telegram spells on `ChatBannedRights`, minus `until_date`,
# which is a time and not a right. A blueprint carries the ones that are on, as
# a sorted list, so two chats with the same restrictions read the same.
BANNED_RIGHT_NAMES = tuple(
    sorted(name for name in ChatBannedRights.__init__.__annotations__ if name not in ("until_date", "return"))
)
# Every admin right Telegram spells on `ChatAdminRights`, for the primitive below.
ADMIN_RIGHT_NAMES = tuple(sorted(name for name in ChatAdminRights.__init__.__annotations__ if name != "return"))
# What an apply may change on the container, each to the call that changes it.
CONTAINER_FIELDS = ("name", "about", "default_banned_rights", "slow_mode_seconds", "join_request")

Resolver = Callable[[Any, str | int], Awaitable[ResolvedChat]]
StepHook = Callable[[Mapping[str, Any], str], None]


def chat_kind(entity: Any) -> str:
    """`supergroup`, `forum` or `channel`, or a refusal for anything else."""
    type_name = classify_entity(entity)
    kind = CHAT_KINDS.get(type_name)
    if kind is None:
        raise CommandError(
            f"{getattr(entity, 'title', 'This chat')} is a {type_name}, which has no blueprint: "
            "`create` makes supergroups, so a blueprint of it could never be applied back.",
            code="PLATFORM_UNSUPPORTED",
            hint="Telegram can upgrade a basic group to a supergroup; export it after that.",
        )
    return kind


def banned_right_names(rights: Any) -> list[str]:
    """The banned rights that are on, sorted; an absent object is no restriction."""
    if rights is None:
        return []
    return sorted(name for name in BANNED_RIGHT_NAMES if getattr(rights, name, False))


def banned_rights(names: Any) -> ChatBannedRights:
    """`ChatBannedRights` with exactly `names` on. A name Telegram does not spell is refused."""
    wanted = set(names or ())
    unknown = sorted(wanted - set(BANNED_RIGHT_NAMES))
    if unknown:
        raise BlueprintError(f"unknown banned right(s): {', '.join(unknown)}")
    return ChatBannedRights(until_date=None, **{name: True for name in sorted(wanted)})


def admin_rights(names: Any) -> ChatAdminRights:
    """`ChatAdminRights` with exactly `names` on, for the administration commands."""
    wanted = set(names or ())
    unknown = sorted(wanted - set(ADMIN_RIGHT_NAMES))
    if unknown:
        raise BlueprintError(f"unknown admin right(s): {', '.join(unknown)}")
    return ChatAdminRights(**{name: True for name in sorted(wanted)})


def topic_rid(chat_id: int | str, topic_id: int) -> str:
    return str(_rid.make(PREFIX, "topic", chat_id, topic_id))


class TelegramBlueprintPort:
    """`BlueprintPort` over one signed-in client.

    `on_step` is called after every step the platform accepted, with the step
    and the rid it produced: the command hangs its per-step audit line on it.
    Peers are resolved once per container rid and kept for the run.
    """

    def __init__(self, client, *, resolver: Resolver = resolve_chat, on_step: StepHook | None = None) -> None:
        self.client = client
        self.resolver = resolver
        self.on_step = on_step
        self._peers: dict[str, ResolvedChat] = {}

    async def peer(self, container_rid: str) -> ResolvedChat:
        if container_rid not in self._peers:
            parsed = _rid.parse(container_rid)
            if parsed.kind != "chat":
                raise BlueprintError(f"{container_rid} is not a chat")
            self._peers[container_rid] = await self.resolver(self.client, int(parsed.ids[0]))
        return self._peers[container_rid]

    # -- read ----------------------------------------------------------------

    async def read(self, container: Target) -> dict[str, Any]:
        """The chat's structure, references as rids, everything the allowlist may want.

        One `channels.getFullChannel` for the settings, one topic walk when the
        chat is a forum. Keys the allowlist does not name are dropped by the
        engine and reported; nothing secret is put in the shape to begin with.
        """
        resolved = await self.peer(container.rid)
        kind = chat_kind(resolved.entity)
        full = await self.client(GetFullChannelRequest(channel=resolved.input_entity))
        # The full read carries the channel object too, fresher than the one the
        # resolver handed over; it is the one whose marked id is the container's.
        channel = next(
            (chat for chat in getattr(full, "chats", []) or [] if utils.get_peer_id(chat) == int(resolved.id)),
            resolved.entity,
        )
        full_chat = getattr(full, "full_chat", None)

        settings: dict[str, Any] = {
            "rid": container.rid,
            "name": str(getattr(channel, "title", container.title)),
            "kind": kind,
            "about": str(getattr(full_chat, "about", "") or ""),
            "join_request": bool(getattr(channel, "join_request", False)),
            # Present so the drop report names them; never allowed through.
            "participants_count": getattr(full_chat, "participants_count", None),
            "linked_chat_id": getattr(full_chat, "linked_chat_id", None),
            "username": getattr(channel, "username", None),
        }
        if kind != "channel":
            # A broadcast channel has no default rights and no slow mode: the
            # keys are absent rather than empty so two channels read the same.
            settings["default_banned_rights"] = banned_right_names(getattr(channel, "default_banned_rights", None))
            settings["slow_mode_seconds"] = int(getattr(full_chat, "slowmode_seconds", 0) or 0)

        objects: dict[str, list[dict[str, Any]]] = {}
        if kind == "forum":
            # Telegram gives topics no order a client can set (the app sorts them
            # by last activity, pinned first), so a blueprint lists them by title:
            # position is then a function of the names on both sides, and two
            # forums holding the same topics never differ in order. Ties (two
            # topics with one title) fall back to id, which is creation order.
            topics = sorted(
                await get_forum_topics(self.client, resolved.input_entity),
                key=lambda topic: (slug(topic.title), topic.id),
            )
            objects["topics"] = [
                {
                    "rid": topic_rid(resolved.id, topic.id),
                    "name": topic.title,
                    "position": position,
                    "icon_emoji_id": topic.icon_emoji_id,
                }
                for position, topic in enumerate(topics)
            ]
        return {"container": settings, "objects": objects}

    # -- apply ---------------------------------------------------------------

    async def apply(self, step: Mapping[str, Any]) -> dict[str, str]:
        """One step, already resolved: a topic made or edited, or the container's settings."""
        resolved = await self.peer(str(step["container_rid"]))
        if step["kind"] == "topic":
            target_rid = await self._apply_topic(resolved, step)
        elif step["kind"] == "chat":
            target_rid = await self._apply_container(resolved, step)
        else:
            raise BlueprintError(f"a Telegram blueprint has no {step['kind']} objects")
        if self.on_step is not None:
            self.on_step(step, target_rid)
        return {"target_rid": target_rid}

    async def _apply_topic(self, resolved: ResolvedChat, step: Mapping[str, Any]) -> str:
        fields = dict(step.get("fields") or {})
        if step["op"] == "create":
            result = await self.client(
                CreateForumTopicRequest(
                    peer=resolved.input_entity,
                    title=str(fields["name"]),
                    icon_emoji_id=fields.get("icon_emoji_id") or None,
                )
            )
            return topic_rid(resolved.id, _new_topic_id(result, str(fields["name"])))
        topic_id = int(_rid.parse(str(step["target_rid"])).ids[-1])
        edits: dict[str, Any] = {}
        if "name" in fields:
            edits["title"] = str(fields["name"])
        if "icon_emoji_id" in fields:
            # Telegram takes 0 for "no icon"; None would mean "leave it".
            edits["icon_emoji_id"] = int(fields["icon_emoji_id"] or 0)
        if edits:
            await self.client(EditForumTopicRequest(peer=resolved.input_entity, topic_id=topic_id, **edits))
        return str(step["target_rid"])

    async def _apply_container(self, resolved: ResolvedChat, step: Mapping[str, Any]) -> str:
        if step["op"] != "update":
            raise BlueprintError("a chat is created by `structure apply --create`, never as a step")
        fields = dict(step.get("fields") or {})
        if "kind" in fields:
            raise BlueprintError(
                f"the target is not a {fields['kind']}; a chat's kind cannot be changed by an apply"
            )
        unknown = sorted(set(fields) - set(CONTAINER_FIELDS))
        if unknown:
            raise BlueprintError(f"no call changes {', '.join(unknown)} on a chat")
        channel = resolved.input_entity
        if "name" in fields:
            await self.client(EditTitleRequest(channel=channel, title=str(fields["name"])))
        if "about" in fields:
            await self.client(EditChatAboutRequest(peer=channel, about=str(fields["about"] or "")))
        if "default_banned_rights" in fields:
            await self.set_default_banned_rights(channel, fields["default_banned_rights"])
        if "slow_mode_seconds" in fields:
            await self.set_slow_mode(channel, int(fields["slow_mode_seconds"] or 0))
        if "join_request" in fields:
            await self.set_join_request(channel, bool(fields["join_request"]))
        return str(step["target_rid"])

    # -- the rights primitives (spec section 12; the administration commands wrap these)

    async def set_default_banned_rights(self, channel: Any, names: Any) -> None:
        await self.client(EditChatDefaultBannedRightsRequest(peer=channel, banned_rights=banned_rights(names)))

    async def set_slow_mode(self, channel: Any, seconds: int) -> None:
        await self.client(ToggleSlowModeRequest(channel=channel, seconds=int(seconds)))

    async def set_join_request(self, channel: Any, enabled: bool) -> None:
        await self.client(ToggleJoinRequestRequest(channel=channel, enabled=bool(enabled)))

    async def set_admin_rights(self, channel: Any, user: Any, names: Any, *, rank: str | None = None) -> None:
        """One person's admin rights set by name. Not reached by an apply: an admin is a
        person, and the blueprint carries none; the administration commands call this."""
        await self.client(EditAdminRequest(channel=channel, user_id=user, admin_rights=admin_rights(names), rank=rank or ""))


__all__ = [
    "ADMIN_RIGHT_NAMES",
    "BANNED_RIGHT_NAMES",
    "CHAT_KINDS",
    "CONTAINER_FIELDS",
    "TelegramBlueprintPort",
    "admin_rights",
    "banned_right_names",
    "banned_rights",
    "chat_kind",
    "topic_rid",
]
