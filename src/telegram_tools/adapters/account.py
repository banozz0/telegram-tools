"""The account-mode adapters: who a run acts as, what it acts on, what it may do.

Three shapes from the shared copy, filled in for a Telethon client signed in
as the person: `IdentityProvider`, `TargetResolver`, `PermissionProbe`. Two
notes on the signatures, both settled here because this is the card that lands
them:

* they are `async`, because every one of them asks Telegram something;
* the probe takes the resolved input entity rather than a `Target`, because
  that is what `get_permissions` accepts and re-resolving a target the caller
  already holds would be a second round trip for no answer.

None of them ever receives a token, a phone number or a session path -- they
are handed an already-opened client, and the label they build is run through
the shared redaction before it goes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from telegram_tools._core import rid as _rid
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.redaction import redact_text
from telegram_tools.discovery import classify_entity
from telegram_tools.envelope import PLATFORM, PREFIX
from telegram_tools.resolver import resolve_chat

# Every right this tool asks about or reports. Telethon spells them on the
# permissions object it returns; a name it does not spell is a right this
# account's chat cannot answer for, which is not the same as one it lacks.
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
)


def account_label(user: Any) -> str:
    """What screens call this account: a name, its @username when there is one.

    Redacted on the way out, not checked afterwards: a display name is text
    someone else chose, and a name that happens to read as a phone number
    should cost a run nothing.
    """
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in parts if part).strip()
    username = getattr(user, "username", None)
    if name and username:
        label = f"{name} (@{username})"
    else:
        label = name or (f"@{username}" if username else f"user {getattr(user, 'id', '?')}")
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
    report is unknown, not absent -- a private chat has no participant
    permissions at all -- and refusing a write over an unknown right would
    break sends this tool has always made.
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


class ChatPermissions:
    """`PermissionProbe`: the rights this account holds in a chat."""

    def __init__(self, client, user: Any) -> None:
        self.client = client
        self.user = user

    async def probe(self, peer: Any) -> Rights:
        get_permissions = getattr(self.client, "get_permissions", None)
        if get_permissions is None:
            return Rights(frozenset(), frozenset(), "this client reports no permissions")
        try:
            permissions = await get_permissions(peer, self.user)
        except Exception as exc:  # noqa: BLE001 - any refusal to answer is the same answer
            return Rights(frozenset(), frozenset(), f"{type(exc).__name__} reading permissions")
        answered = {name for name in RIGHT_NAMES if hasattr(permissions, name)}
        held = {name for name in answered if getattr(permissions, name)}
        # A creator holds everything, whatever the participant object says.
        if "is_creator" in held:
            held |= answered
        return Rights(frozenset(held), frozenset(answered))

    async def rights(self, peer: Any) -> frozenset[str]:
        return (await self.probe(peer)).held
