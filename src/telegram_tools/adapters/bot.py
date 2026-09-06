"""The bot-mode adapters: acting as an owned bot, through the account that owns it.

Spec section 5.2. `--as-bot NICK` connects with that bot's token on a
`MemorySession` -- the token never touches disk -- and everything a run then
does is done *as the bot*: the identity is the bot's, the rights are the
bot's, and the chats it can reach are the chats it is in. The account is
still named on every screen, as the `via` the bot acts through, because the
person reading the banner is that account's owner and the question they are
answering is "which of my things is about to do this".

Three shapes again, filled in for a client signed in with a bot token:

* `BotIdentity` -- mode `bot`, id `tg:bot:<id>`, `via` the account's rid;
* `resolve_chat_as_bot` -- by id or `@username` only. A bot has no dialog
  list (`messages.getDialogs` is user-only; Telethon raises
  `BotMethodInvalidError`), so the dialog walk the account resolver starts
  with is not available, and a link is refused rather than guessed at:
  Telethon can resolve a channel for a bot from a bare id (`GetChannelsRequest`
  with an empty access hash), and a username through `ResolveUsername`, and
  those are the two forms this accepts.
* `BotPermissions` -- the rights the bot holds in a chat, and one refusal the
  account probe never makes: a bot that is not in the chat at all is refused
  by name before any preview, because "add the bot to the chat" is the fix
  and Telegram's own answer would arrive after the gate.

Nothing here ever holds the token. The client it is handed was opened with it;
the bot's label is its `@username`, which is public.
"""

from __future__ import annotations

from typing import Any, Sequence

from telethon.errors import UserNotParticipantError

from telegram_tools._core import rid as _rid
from telegram_tools._core.identity import Identity
from telegram_tools._core.redaction import redact_text
from telegram_tools.adapters.account import RIGHT_NAMES, Rights
from telegram_tools.envelope import PLATFORM, PREFIX, CommandError
from telegram_tools.resolver import EntityResolutionError, ResolvedChat, _parse_numeric_reference

# A Telegram username: letters, digits and underscores, five to thirty-two of
# them. Anything else that is not a number is a link or a typo, and a bot
# cannot look either up.
_USERNAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def bot_label(user: Any) -> str:
    """What screens call the bot: its `@username`, which every bot has, or its id."""
    username = getattr(user, "username", None)
    label = f"@{username}" if username else f"bot {getattr(user, 'id', '?')}"
    return redact_text(label)


def is_username_reference(reference: Any) -> bool:
    """True for `@name` or a bare `name`; False for a number, a link, or anything else."""
    text = str(reference).strip().lstrip("@")
    return 5 <= len(text) <= 32 and set(text) <= _USERNAME_CHARS


async def resolve_chat_as_bot(client, reference: str | int) -> ResolvedChat:
    """The chat `reference` names, as the bot sees it: an id or a username, nothing else."""
    numeric = _parse_numeric_reference(reference)
    if numeric is not None:
        lookup: str | int = numeric
    elif is_username_reference(reference):
        lookup = "@" + str(reference).strip().lstrip("@")
    else:
        raise CommandError(
            f"Bot mode resolves a chat by id or @username only; {reference!r} is neither.",
            code="TARGET_NOT_FOUND",
            hint="Pass the chat's numeric id or its @username, or run it without --as-bot to use a link.",
        )
    try:
        entity = await client.get_entity(lookup)
        input_entity = await client.get_input_entity(entity)
        peer_id = int(await client.get_peer_id(entity))
    except Exception as exc:
        raise EntityResolutionError(
            f"Cannot resolve chat {reference!r} as the bot: a bot only sees chats it has been added to."
        ) from exc
    return ResolvedChat(id=peer_id, entity=entity, input_entity=input_entity)


class BotIdentity:
    """`IdentityProvider` for a bot acting through the account that owns it.

    `via_id` and `via_label` are the account's: read from the profile record
    when it has one, else learned from the account session once. The bot
    itself is asked who it is through the bot client, so the id in the
    identity is the id Telegram answered with, not the one in the nickname.
    """

    def __init__(self, user: Any, profile: str, *, via_id: int, via_label: str) -> None:
        self.user = user
        self.profile = profile
        self.via_id = via_id
        self.via_label = via_label

    @classmethod
    async def open(cls, client, profile: str, *, via_id: int, via_label: str) -> "BotIdentity":
        return cls(await client.get_me(), profile, via_id=via_id, via_label=via_label)

    @property
    def id(self) -> int:
        return int(getattr(self.user, "id", 0))

    def identity(self) -> Identity:
        return Identity(
            platform=PLATFORM,
            mode="bot",
            label=bot_label(self.user),
            id=str(_rid.make(PREFIX, "bot", self.id)),
            profile=self.profile,
            via=str(_rid.make(PREFIX, "user", self.via_id)),
        )

    def profiles(self) -> Sequence[tuple[str, str]]:
        return ((self.profile, f"{bot_label(self.user)} (via {self.via_label})"),)


class BotNotInChatError(CommandError):
    """The bot is not a member of the chat it was asked to act in."""

    def __init__(self, label: str) -> None:
        super().__init__(
            f"{label} is not a member of that chat, so it cannot act there.",
            code="PERMISSION_DENIED",
            hint=f"Add {label} to the chat in Telegram (as an admin, for a channel), then run it again.",
        )


class BotPermissions:
    """`PermissionProbe`: the rights the bot holds in a chat.

    The same answer shape as the account probe, with one difference: not
    being in the chat is a refusal, not an unknown. An account that cannot
    read its permissions in a private chat has always been let through to
    Telegram's own answer; a bot that is not in a group or channel has one
    fix, and naming it before the preview is the point of a preflight.
    """

    def __init__(self, client, user: Any) -> None:
        self.client = client
        self.user = user

    async def probe(self, peer: Any) -> Rights:
        get_permissions = getattr(self.client, "get_permissions", None)
        if get_permissions is None:
            return Rights(frozenset(), frozenset(), "this client reports no permissions")
        try:
            permissions = await get_permissions(peer, self.user)
        except UserNotParticipantError:
            raise BotNotInChatError(bot_label(self.user)) from None
        except Exception as exc:  # noqa: BLE001 - a private chat, or a refusal to answer, is unknown
            return Rights(frozenset(), frozenset(), f"{type(exc).__name__} reading permissions")
        answered = {name for name in RIGHT_NAMES if hasattr(permissions, name)}
        held = {name for name in answered if getattr(permissions, name)}
        if "is_creator" in held:
            held |= answered
        return Rights(frozenset(held), frozenset(answered))

    async def rights(self, peer: Any) -> frozenset[str]:
        return (await self.probe(peer)).held
