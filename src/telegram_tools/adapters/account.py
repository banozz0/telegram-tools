"""The account-mode adapters: who a run acts as, what it acts on, what it may do.

Three shapes from the shared copy, filled in for a Telethon client signed in
as the person: `IdentityProvider`, `TargetResolver`, `PermissionProbe`. Two
notes on the signatures, both settled here because this is the card that lands
them:

* they are `async`, because every one of them asks Telegram something;
* the probe takes the resolved input entity rather than a `Target`, because
  that is what `get_permissions` accepts and re-resolving a target the caller
  already holds would be a second round trip for no answer -- and beside it
  the chat entity the resolution already fetched, which carries the chat's
  default rights and whether it is a broadcast channel.

None of them ever receives a token, a phone number or a session path -- they
are handed an already-opened client, and the label they build is run through
the shared redaction before it goes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from telethon.tl import types

from telegram_tools._core import rid as _rid
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.redaction import redact_text
from telegram_tools.discovery import classify_entity
from telegram_tools.envelope import PLATFORM, PREFIX
from telegram_tools.resolver import resolve_chat

# Every right this tool asks about or reports.
RIGHT_NAMES = (
    "is_creator",
    "is_admin",
    "send_messages",
    "send_media",
    "delete_messages",
    "edit_messages",
    "post_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "change_info",
    "manage_topics",
    "add_admins",
)

# The three Telethon's `ParticipantPermissions` never spells, whatever the
# chat, which are also the three ChatBannedRights carries under the same names.
MEMBER_RIGHTS = ("send_messages", "send_media", "manage_topics")
# The rest it spells as properties: is_creator, is_admin, and the admin flags it
# reads off the participant's admin_rights (every one of them true for a basic
# group's admin, add_admins only for its creator).
TELETHON_RIGHTS = tuple(name for name in RIGHT_NAMES if name not in MEMBER_RIGHTS)
# A direct chat has no participants, no admins and no defaults, so nothing is
# asked of Telegram there. Either person may post, send media, pin, and delete
# any message for both sides; nobody is its creator or edits the other's words.
DIRECT_RIGHTS = ("send_messages", "send_media", "delete_messages", "pin_messages")
# The peers of a direct chat, the account's own Saved Messages included.
_DIRECT_PEERS = (types.InputPeerUser, types.InputPeerSelf, types.InputPeerUserFromMessage)


def phone_tail(user: Any) -> str | None:
    """The last two digits of this account's number, or None when there are none.

    Two digits and never more. Section 5.1 puts them in the label of an account
    with no username precisely because a display name is not required to be
    distinguishing -- a name of `--`, or the same name on two accounts, leaves
    nothing else to tell them apart. The full number is read here and does not
    leave this function.
    """
    digits = "".join(character for character in str(getattr(user, "phone", "") or "") if character.isdigit())
    return digits[-2:] if len(digits) >= 2 else None


def account_label(user: Any) -> str:
    """What screens call this account: a name, and something to tell it apart.

    A `@username` is the best answer and is used whenever there is one. With no
    username, section 5.1 asks for the name plus the last two digits of the
    number, because a display name is chosen by its owner and can be anything --
    blank, punctuation, the same as another account's.

    Redacted on the way out, not checked afterwards: a display name is text
    someone else chose, and a name that happens to read as a phone number
    should cost a run nothing.
    """
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in parts if part).strip()
    username = getattr(user, "username", None)
    tail = phone_tail(user)

    if username:
        label = f"{name} (@{username})" if name else f"@{username}"
    elif name:
        label = f"{name} (…{tail})" if tail else name
    else:
        # Nothing the account chose; the id is what is left, and two digits
        # would add nothing to a number that is already on screen.
        label = f"user {getattr(user, 'id', '?')}"
    return redact_text(label)


def chat_title(entity: Any, fallback: str) -> str:
    """A chat's name: its title, a person's name, or what the user typed."""
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(part for part in parts if part).strip()
    return name or str(getattr(entity, "username", None) or fallback)


class AccountIdentity:
    """`IdentityProvider` for the signed-in account.

    Opened once per run, because the answer costs a round trip and every
    envelope, plan and audit line wants the same one.
    """

    def __init__(self, user: Any, profile: str = "default") -> None:
        self.user = user
        self.profile = profile

    @classmethod
    async def open(cls, client, profile: str = "default") -> "AccountIdentity":
        return cls(await client.get_me(), profile)

    def identity(self) -> Identity:
        return Identity(
            platform=PLATFORM,
            mode="account",
            label=account_label(self.user),
            id=str(_rid.make(PREFIX, "user", getattr(self.user, "id", 0))),
            profile=self.profile,
        )

    def profiles(self) -> Sequence[tuple[str, str]]:
        # One login today; named profiles are a later card, and this answers in
        # its shape so the caller never has to learn a second one.
        return ((self.profile, account_label(self.user)),)


class ChatTargets:
    """`TargetResolver`: a `--chat` reference, or a topic in one, as a `Target`."""

    def __init__(self, client) -> None:
        self.client = client

    async def resolve(self, reference: str | int, kind: str | None = None):
        """The chat `reference` names, as both this tool's resolution and a `Target`."""
        resolved = await resolve_chat(self.client, reference)
        return resolved, self.chat_target(resolved, reference)

    @staticmethod
    def chat_target(resolved: Any, fallback: str | int = "") -> Target:
        title = chat_title(resolved.entity, str(fallback))
        return Target(
            rid=str(_rid.make(PREFIX, "chat", resolved.id)),
            kind="chat",
            title=title,
            path=(title,),
            platform=PLATFORM,
            ids={"chat": str(resolved.id)},
            type=classify_entity(resolved.entity),
        )

    @staticmethod
    def topic_target(chat: Target, topic: Any) -> Target:
        """A forum topic inside an already-resolved chat."""
        return Target(
            rid=str(_rid.make(PREFIX, "topic", chat.ids["chat"], topic.id)),
            kind="topic",
            title=topic.title,
            path=(*chat.path, topic.title),
            platform=PLATFORM,
            ids={"chat": chat.ids["chat"], "topic": str(topic.id)},
            type="topic",
        )


@dataclass(frozen=True)
class Rights:
    """What a probe found: what is held, what was answered for, and why not when not.

    `answered` is the distinction that matters. A right the platform did not
    report is unknown, not absent -- a chat whose defaults could not be read
    says nothing about a member's send rights -- and refusing a write over an
    unknown right would break sends this tool has always made.
    """

    held: frozenset[str]
    answered: frozenset[str]
    unreadable: str | None = None

    def missing(self, required: Sequence[str]) -> tuple[str, ...]:
        """The required rights the platform said this account does not have."""
        return tuple(name for name in required if name in self.answered and name not in self.held)

    def unknown(self, required: Sequence[str]) -> tuple[str, ...]:
        """The required rights the platform would not answer for."""
        return tuple(name for name in required if name not in self.answered)


EVERY_RIGHT = frozenset(RIGHT_NAMES)
DIRECT = Rights(frozenset(DIRECT_RIGHTS), EVERY_RIGHT)


def is_direct(peer: Any, chat: Any = None) -> bool:
    """True for a chat with one person (or with oneself): there are no rights to ask about."""
    return isinstance(peer, _DIRECT_PEERS) or isinstance(chat, types.User)


def rights_from(permissions: Any, chat: Any = None) -> Rights:
    """The rights `get_permissions`' answer and the chat entity say this identity holds.

    Telethon's `ParticipantPermissions` answers `TELETHON_RIGHTS` itself. The
    three `MEMBER_RIGHTS` come from the participant object it wraps
    (`.participant`, `.is_chat`) and from `chat`:

    * a creator holds every right there is;
    * an admin of a basic group holds all three; any other admin holds
      `manage_topics` exactly when `admin_rights.manage_topics` is on, posts in
      a broadcast channel only with `admin_rights.post_messages`, and in a
      supergroup sends whatever its members may not, because restrictions bind
      members only;
    * anyone else holds none of them in a broadcast channel or once out of the
      chat (`ChannelParticipantLeft`, a ban with `view_messages` or `left`).
      Otherwise a send right is held unless `participant.banned_rights` or
      `chat.default_banned_rights` sets it, and `manage_topics` set on either
      is a refusal -- but unset it is left unanswered, because it lets a member
      open a topic and never edit someone else's, and the preflight's one name
      covers both.

    A member right the chat cannot settle -- no chat entity, or a `min` one
    that carries no rights -- stays unanswered rather than guessed.
    """
    answered = {name for name in TELETHON_RIGHTS if hasattr(permissions, name)}
    held = {name for name in answered if getattr(permissions, name)}
    if "is_creator" in held:
        return Rights(EVERY_RIGHT, EVERY_RIGHT)
    for name, holds in _member_rights(permissions, chat).items():
        answered.add(name)
        if holds:
            held.add(name)
    return Rights(frozenset(held), frozenset(answered))


def _member_rights(permissions: Any, chat: Any) -> dict[str, bool]:
    participant = getattr(permissions, "participant", None)
    if participant is None:
        return {}
    broadcast = None if chat is None else bool(getattr(chat, "broadcast", False))
    if getattr(permissions, "is_admin", False):
        if getattr(permissions, "is_chat", False):
            return dict.fromkeys(MEMBER_RIGHTS, True)
        admin_rights = getattr(participant, "admin_rights", None)
        found = {"manage_topics": bool(getattr(admin_rights, "manage_topics", False))}
        if broadcast is not None:
            posts = bool(getattr(admin_rights, "post_messages", False)) if broadcast else True
            found.update(send_messages=posts, send_media=posts)
        return found
    if _out_of_chat(participant) or broadcast:
        return dict.fromkeys(MEMBER_RIGHTS, False)
    defaults = _defaults(chat)
    if defaults is None:
        return {}
    own = getattr(participant, "banned_rights", None)
    found = {name: not (getattr(own, name, False) or getattr(defaults, name, False)) for name in MEMBER_RIGHTS}
    if found["manage_topics"]:
        del found["manage_topics"]
    return found


def _defaults(chat: Any) -> Any:
    """The chat's default banned rights; an empty set when it has none, None when it cannot say."""
    if chat is None or getattr(chat, "min", False):
        return None
    return getattr(chat, "default_banned_rights", None) or types.ChatBannedRights(until_date=None)


def _out_of_chat(participant: Any) -> bool:
    if isinstance(participant, types.ChannelParticipantLeft):
        return True
    if isinstance(participant, types.ChannelParticipantBanned):
        return bool(participant.left) or bool(getattr(participant.banned_rights, "view_messages", False))
    return False


class ChatPermissions:
    """`PermissionProbe`: the rights this account holds in a chat."""

    def __init__(self, client, user: Any) -> None:
        self.client = client
        self.user = user

    async def probe(self, peer: Any, chat: Any = None) -> Rights:
        if is_direct(peer, chat):
            return DIRECT
        get_permissions = getattr(self.client, "get_permissions", None)
        if get_permissions is None:
            return Rights(frozenset(), frozenset(), "this client reports no permissions")
        try:
            permissions = await get_permissions(peer, self.user)
        except Exception as exc:  # noqa: BLE001 - any refusal to answer is the same answer
            return Rights(frozenset(), frozenset(), f"{type(exc).__name__} reading permissions")
        return rights_from(permissions, chat)

    async def rights(self, peer: Any) -> frozenset[str]:
        return (await self.probe(peer)).held
