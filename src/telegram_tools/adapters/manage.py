"""The administration port: the Telegram calls behind `admin`, `member`, `join-requests`, `invite` and `settings`.

Spec section 13, on the Telethon seam. One class over one signed-in client
(the account's, or a bot's under `--as-bot`), and every method is one raw
request or one page walk: nothing here builds a plan, asks a question or
writes an audit line -- that is `cli._run_manage` around it -- and nothing
here decides who may do what: the hierarchy rule is `manage.require_hierarchy`
on the `Member` records this module reads.

The rights primitives an apply also needs -- one person's admin rights set,
slow mode -- are the blueprint port's (`adapters/blueprint.py`), and this port
wraps them rather than spelling `channels.editAdmin` a second time. What is
only here: `channels.editBanned` (a ban, a restriction, and lifting either:
the same call with different rights and an `until_date`), the participant
reads, `messages.hideChatJoinRequest`, and the three invite calls.

Basic groups are refused (`PLATFORM_UNSUPPORTED`): every call here is a
channel call, and Telegram itself moves a group to a supergroup the moment an
admin touches its settings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from telethon import utils
from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import EditBannedRequest, GetFullChannelRequest, GetParticipantRequest, GetParticipantsRequest
from telethon.tl.functions.messages import (
    EditExportedChatInviteRequest,
    ExportChatInviteRequest,
    GetChatInviteImportersRequest,
    GetExportedChatInvitesRequest,
    HideChatJoinRequestRequest,
)
from telethon.tl.types import (
    ChannelParticipantsAdmins,
    ChannelParticipantsBanned,
    ChannelParticipantsKicked,
    ChannelParticipantsRecent,
    ChannelParticipantsSearch,
    ChatBannedRights,
    InputUserEmpty,
    InputUserSelf,
)

from telegram_tools.adapters.blueprint import ADMIN_RIGHT_NAMES, TelegramBlueprintPort, banned_right_names, chat_kind
from telegram_tools.envelope import CommandError
from telegram_tools.manage import LIST_LIMIT, Member, until_text, user_label

PAGE = 200


def admin_right_names(rights: Any) -> tuple[str, ...]:
    """The admin rights that are on, sorted; absent is none."""
    if rights is None:
        return ()
    return tuple(sorted(name for name in ADMIN_RIGHT_NAMES if getattr(rights, name, False)))


def _peer_user_id(participant: Any) -> int | None:
    """A participant's user id: `user_id` on most, a `peer` on a banned one."""
    user_id = getattr(participant, "user_id", None)
    if user_id is not None:
        return int(user_id)
    peer = getattr(participant, "peer", None)
    if peer is not None and getattr(peer, "user_id", None) is not None:
        return int(peer.user_id)
    return None


def member_of(participant: Any, user: Any) -> Member:
    """One participant object and its user, as a `Member`."""
    name = type(participant).__name__
    admin_rights = getattr(participant, "admin_rights", None)
    banned_rights = getattr(participant, "banned_rights", None)
    until = None
    if "Creator" in name:
        status = "creator"
        rights = admin_right_names(admin_rights)
    elif admin_rights is not None:
        status = "admin"
        rights = admin_right_names(admin_rights)
    elif banned_rights is not None:
        rights = tuple(banned_right_names(banned_rights))
        status = "banned" if "view_messages" in rights else "restricted"
        if getattr(participant, "left", False) and status != "banned":
            status = "left"
        until = until_text(getattr(banned_rights, "until_date", None))
    elif "Left" in name:
        status = "left"
        rights = ()
    else:
        status = "member"
        rights = ()
    return Member(
        id=int(getattr(user, "id", _peer_user_id(participant) or 0)),
        label=user_label(user),
        username=getattr(user, "username", None),
        status=status,
        rights=rights,
        rank=getattr(participant, "rank", None) or None,
        until=until,
        can_edit=bool(getattr(participant, "can_edit", True)) if status == "admin" else True,
        is_bot=bool(getattr(user, "bot", False)),
    )


def _users_by_id(result: Any) -> dict[int, Any]:
    return {int(getattr(user, "id", 0)): user for user in getattr(result, "users", []) or []}


def invite_row(invite: Any) -> dict[str, Any]:
    """One `ChatInviteExported` as the screens and the envelope carry it, link included."""
    return {
        "link": str(getattr(invite, "link", "") or ""),
        "title": getattr(invite, "title", None),
        "revoked": bool(getattr(invite, "revoked", False)),
        "permanent": bool(getattr(invite, "permanent", False)),
        "request_needed": bool(getattr(invite, "request_needed", False)),
        "expires": until_text(getattr(invite, "expire_date", None)),
        "usage_limit": getattr(invite, "usage_limit", None),
        "usage": getattr(invite, "usage", None),
        "requested": getattr(invite, "requested", None),
    }


class TelegramManagePort:
    """The administration calls over one client. `rights` is the blueprint port they share."""

    def __init__(self, client, *, rights: TelegramBlueprintPort | None = None) -> None:
        self.client = client
        self.rights = rights or TelegramBlueprintPort(client)

    # -- people ---------------------------------------------------------------

    async def resolve_user(self, reference: str | int) -> tuple[Any, Any]:
        """A `--user` reference (numeric id or @username) as the user entity and its input form."""
        text = str(reference).strip()
        lookup: str | int = int(text) if text.lstrip("-").isdigit() else "@" + text.lstrip("@")
        try:
            entity = await self.client.get_entity(lookup)
            input_user = await self.client.get_input_entity(entity)
        except Exception as exc:
            raise CommandError(
                f"Cannot resolve user {reference!r}: a numeric id or a @username this account has seen.",
                code="TARGET_NOT_FOUND",
                hint="`member list --chat …` shows ids and usernames.",
            ) from exc
        if type(entity).__name__ != "User":
            raise CommandError(f"{reference!r} is a chat, not a person.", code="TARGET_KIND_MISMATCH")
        return entity, input_user

    async def participant(self, channel: Any, user: Any, input_user: Any) -> Member:
        """`user` as a participant of `channel`; status `none` when they are not in it."""
        try:
            result = await self.client(GetParticipantRequest(channel=channel, participant=input_user))
        except UserNotParticipantError:
            return Member(id=int(user.id), label=user_label(user), username=getattr(user, "username", None), status="none", is_bot=bool(getattr(user, "bot", False)))
        users = _users_by_id(result)
        return member_of(result.participant, users.get(int(user.id), user))

    async def _walk(self, channel: Any, filter_: Any, *, limit: int) -> list[Member]:
        rows: list[Member] = []
        offset = 0
        while len(rows) < limit:
            page = await self.client(GetParticipantsRequest(channel=channel, filter=filter_, offset=offset, limit=min(PAGE, limit - len(rows)), hash=0))
            participants = list(getattr(page, "participants", []) or [])
            if not participants:
                break
            users = _users_by_id(page)
            for participant in participants:
                user_id = _peer_user_id(participant)
                user = users.get(user_id) if user_id is not None else None
                if user is None:
                    continue
                rows.append(member_of(participant, user))
            offset += len(participants)
            if len(participants) < PAGE:
                break
        return rows

    async def admins(self, channel: Any) -> list[Member]:
        rows = await self._walk(channel, ChannelParticipantsAdmins(), limit=LIST_LIMIT)
        order = {"creator": 0, "admin": 1}
        return sorted(rows, key=lambda member: (order.get(member.status, 2), member.label.casefold()))

    async def members(self, channel: Any, *, query: str | None = None, limit: int = LIST_LIMIT, banned: bool = False) -> list[Member]:
        if banned:
            kicked = await self._walk(channel, ChannelParticipantsKicked(q=query or ""), limit=limit)
            restricted = await self._walk(channel, ChannelParticipantsBanned(q=query or ""), limit=limit)
            seen = {member.id for member in kicked}
            return kicked + [member for member in restricted if member.id not in seen]
        filter_ = ChannelParticipantsSearch(q=query) if query else ChannelParticipantsRecent()
        return await self._walk(channel, filter_, limit=limit)

    # -- rights and restrictions ------------------------------------------------

    async def set_admin(self, channel: Any, input_user: Any, names: Sequence[str], *, rank: str | None = None) -> None:
        await self.rights.set_admin_rights(channel, input_user, names, rank=rank)

    async def set_banned(self, channel: Any, input_user: Any, names: Sequence[str], *, until: datetime | None = None) -> None:
        """`channels.editBanned`: a ban, a restriction, or lifting either (no names, no date)."""
        rights = ChatBannedRights(until_date=until, **{name: True for name in sorted(set(names))})
        await self.client(EditBannedRequest(channel=channel, participant=input_user, banned_rights=rights))

    # -- join requests ----------------------------------------------------------

    async def join_requests(self, peer: Any) -> list[dict[str, Any]]:
        result = await self.client(
            GetChatInviteImportersRequest(peer=peer, offset_date=None, offset_user=InputUserEmpty(), limit=LIST_LIMIT, requested=True)
        )
        users = _users_by_id(result)
        rows = []
        for importer in getattr(result, "importers", []) or []:
            user = users.get(int(importer.user_id))
            rows.append(
                {
                    "id": int(importer.user_id),
                    "label": user_label(user) if user is not None else f"user {importer.user_id}",
                    "username": getattr(user, "username", None),
                    "date": until_text(getattr(importer, "date", None)),
                    "about": getattr(importer, "about", None),
                }
            )
        return rows

    async def answer_join_request(self, peer: Any, input_user: Any, *, approved: bool) -> None:
        await self.client(HideChatJoinRequestRequest(peer=peer, user_id=input_user, approved=approved))

    # -- invite links -----------------------------------------------------------

    async def invites(self, peer: Any, *, revoked: bool = False) -> list[dict[str, Any]]:
        result = await self.client(GetExportedChatInvitesRequest(peer=peer, admin_id=InputUserSelf(), limit=LIST_LIMIT, revoked=revoked or None))
        return [invite_row(invite) for invite in getattr(result, "invites", []) or [] if getattr(invite, "link", None)]

    async def create_invite(self, peer: Any, *, title: str | None, expires: datetime | None, usage_limit: int | None, request_needed: bool) -> dict[str, Any]:
        invite = await self.client(
            ExportChatInviteRequest(peer=peer, request_needed=request_needed or None, expire_date=expires, usage_limit=usage_limit, title=title or None)
        )
        return invite_row(invite)

    async def revoke_invite(self, peer: Any, link: str) -> dict[str, Any]:
        result = await self.client(EditExportedChatInviteRequest(peer=peer, link=link, revoked=True))
        invite = getattr(result, "invite", result)
        return invite_row(invite)

    # -- settings ---------------------------------------------------------------

    async def settings(self, resolved: Any) -> dict[str, Any]:
        """What `settings show` prints: the same full read the blueprint port makes, fewer fields."""
        kind = chat_kind(resolved.entity)
        full = await self.client(GetFullChannelRequest(channel=resolved.input_entity))
        channel = next(
            (chat for chat in getattr(full, "chats", []) or [] if utils.get_peer_id(chat) == int(resolved.id)),
            resolved.entity,
        )
        full_chat = getattr(full, "full_chat", None)
        settings: dict[str, Any] = {
            "kind": kind,
            "title": str(getattr(channel, "title", "")),
            "join_request": bool(getattr(channel, "join_request", False)),
            "participants_count": getattr(full_chat, "participants_count", None),
            "admins_count": getattr(full_chat, "admins_count", None),
        }
        if kind != "channel":
            settings["default_banned_rights"] = banned_right_names(getattr(channel, "default_banned_rights", None))
            settings["slow_mode_seconds"] = int(getattr(full_chat, "slowmode_seconds", 0) or 0)
        return settings

    async def set_slow_mode(self, channel: Any, seconds: int) -> None:
        await self.rights.set_slow_mode(channel, seconds)


__all__ = ["TelegramManagePort", "admin_right_names", "invite_row", "member_of"]
