"""`ArchiveSource` for a Telethon client: what the account can archive, and how it walks it.

Spec section 8.3. The shared store owns resume, coverage and the schema; this
adapter owns the two things only the platform knows:

* **Which scopes exist.** One per chat, and for a forum group one per topic
  rather than one for the group, because every message in a forum belongs to
  a topic (General included) and listing both would archive each row twice
  under two rids. A scope the account cannot resolve is still listed, invisible,
  with `no_access` as its reason, so the coverage table can say so.
* **How history is walked.** `iter_messages` per chat and, for a topic, with
  `reply_to` set to the topic id -- the documented pattern in
  `docs/telethon-api-notes.md`. Telegram hands history newest first, so the
  cursor this adapter asks the store to keep is a small state, not one id:
  `top:low:open|done` -- every id in `[low, top]` is archived, and `done`
  means everything below `low` is too. A run first fetches what arrived
  above `top`, then continues the walk below `low` if it never finished.
  A run killed anywhere restarts from the last committed state and refetches
  at most one batch, which the store's upsert makes a no-op.

Flood waits are honoured here rather than by Telethon's own sleep, so they can
be counted: the wait is slept, added to `waited_ms`, and the walk resumes from
the last id it saw. A wait longer than `wait_limit` is raised instead, and the
store records that scope as failed rather than blocking the run for an hour.

Account mode only. A bot cannot read history, and `archive sync` refuses
under `--as-bot` before anything connects; the bot-mode branch below exists
so a bot identity can still *list* what it will be able to fill from live
events later, each such scope carrying `bot_live_only`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Mapping, Sequence

from telethon.errors import FloodWaitError

from telegram_tools._core import rid as _rid
from telegram_tools._core.archive import ScopeListing
from telegram_tools._core.identity import Target
from telegram_tools.adapters.account import ChatTargets, chat_title
from telegram_tools.discovery import classify_entity
from telegram_tools.envelope import PLATFORM, PREFIX
from telegram_tools.records import parse_date_bound
from telegram_tools.resolver import resolve_chat
from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids

# The kinds a scope rid may carry. Everything else the store would ask about
# (a user rid, a bot rid) is not a place messages live.
SCOPE_KINDS = ("chat", "topic")
# A flood wait up to this many seconds is slept and counted; a longer one is
# raised, so a scope Telegram is rate-limiting for an hour is a failed scope
# in the report rather than a run that sits silent.
WAIT_LIMIT_S = 600
# `wait_time` handed to iter_messages: Telethon's own pacing between pages.
PAGE_WAIT_S = 1


@dataclass(frozen=True)
class Cursor:
    """Where a scope's walk stands: `[low, top]` archived, and whether the rest is."""

    top: int
    low: int
    done: bool

    def encode(self) -> str:
        return f"{self.top}:{self.low}:{'done' if self.done else 'open'}"

    @classmethod
    def decode(cls, text: str | None) -> "Cursor | None":
        """The state `text` spells, or None for a cursor this adapter did not write."""
        if not text:
            return None
        parts = str(text).split(":")
        if len(parts) != 3 or not (parts[0].isdigit() and parts[1].isdigit()) or parts[2] not in ("done", "open"):
            return None
        return cls(int(parts[0]), int(parts[1]), parts[2] == "done")


@dataclass
class _Scope:
    """What the walk needs for one listed scope: the peer, and the topic id when it is one."""

    target: Target
    peer: Any = None
    topic_id: int | None = None


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def author_of(message: Any) -> Mapping[str, Any] | None:
    """The `author` record of a message: a rid, a label, a username. Never a phone number."""
    sender_id = getattr(message, "sender_id", None)
    if sender_id is None:
        return None
    sender = getattr(message, "sender", None)
    kind = "user"
    if sender is not None and type(sender).__name__ in ("Channel", "Chat"):
        kind = "chat"
    return {
        "rid": str(_rid.make(PREFIX, kind, sender_id)),
        "label": chat_title(sender, str(sender_id)) if sender is not None else str(sender_id),
        "username": getattr(sender, "username", None),
        "is_bot": bool(getattr(sender, "bot", False)),
    }


def message_record(message: Any, scope: _Scope, cursor: Cursor) -> dict[str, Any]:
    """One message as the store takes it, carrying the cursor to resume from."""
    reply_to = getattr(message, "reply_to", None)
    reply_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""
    return {
        "message_id": str(int(getattr(message, "id"))),
        "date": _iso(getattr(message, "date", None)),
        "text": text,
        "author": author_of(message),
        # Inside a topic every message replies to the topic's root, which is
        # the topic id itself; that is structure, not a reply worth recording.
        "reply_to": None if reply_id is None or reply_id == scope.topic_id else str(reply_id),
        "edited": _iso(getattr(message, "edit_date", None)),
        "platform_json": {
            "has_media": bool(getattr(message, "media", None)),
            "topic_id": scope.topic_id,
            "chat_id": scope.target.ids.get("chat"),
        },
        "cursor": cursor.encode(),
    }


def scope_rid_for(reference: str, topic: int | None = None) -> str:
    """The rid `--scope` accepts for a chat id, or a topic in one."""
    if topic is None:
        return str(_rid.make(PREFIX, "chat", reference))
    return str(_rid.make(PREFIX, "topic", reference, topic))


class TelegramArchiveSource:
    """`ArchiveSource` over one signed-in client: scopes it can see, history it can walk."""

    def __init__(
        self,
        client,
        *,
        only: Sequence[str] | None = None,
        mode: str = "account",
        wait_limit: float = WAIT_LIMIT_S,
        sleep=asyncio.sleep,
    ) -> None:
        self.client = client
        self.only = tuple(only or ())
        self.mode = mode
        self.wait_limit = wait_limit
        self._sleep = sleep
        self._scopes: dict[str, _Scope] = {}
        # Every flood wait this source slept, in milliseconds, for `meta.waited_ms`.
        self.waited_ms = 0

    # -- scopes ------------------------------------------------------------

    async def scopes(self) -> AsyncIterator[ScopeListing]:
        if self.mode == "bot":
            # A bot has no dialog list and no history; what it can name is
            # what it will fill from live events, and coverage says so.
            for rid in self.only:
                target = self._bare_target(rid)
                if target is not None:
                    yield ScopeListing(target=target, visible=False, skipped_reason="bot_live_only")
            return
        if self.only:
            for rid in self.only:
                async for listing in self._listings_for(rid):
                    yield listing
            return
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            peer = getattr(dialog, "input_entity", None) or entity
            resolved = _Resolved(int(dialog.id), entity, peer)
            async for listing in self._listings_of(resolved, only_topic=None):
                yield listing

    def _bare_target(self, rid: str) -> Target | None:
        try:
            parsed = _rid.parse(rid)
        except _rid.RidError:
            return None
        if parsed.kind not in SCOPE_KINDS:
            return None
        return Target(rid=rid, kind=parsed.kind, title=parsed.id, path=(parsed.id,), platform=PLATFORM)

    async def _listings_for(self, rid: str) -> AsyncIterator[ScopeListing]:
        """The listings one `--scope` rid names: a chat, its topics, or one topic."""
        try:
            parsed = _rid.parse(rid)
        except _rid.RidError as exc:
            raise ValueError(f"--scope {rid!r} is not a rid: {exc}") from exc
        if parsed.kind not in SCOPE_KINDS:
            target = Target(rid=rid, kind=parsed.kind, title=parsed.id, path=(parsed.id,), platform=PLATFORM)
            yield ScopeListing(target=target, visible=False, skipped_reason="unsupported_kind")
            return
        chat_ref, topic_id = parsed.ids[0], (int(parsed.ids[1]) if parsed.kind == "topic" else None)
        try:
            resolved = await resolve_chat(self.client, chat_ref)
        except Exception:  # noqa: BLE001 - any refusal to resolve is the same fact: not readable as this account
            target = self._bare_target(rid)
            yield ScopeListing(target=target, visible=False, skipped_reason="no_access")
            return
        async for listing in self._listings_of(resolved, only_topic=topic_id):
            yield listing

    async def _listings_of(self, resolved, *, only_topic: int | None) -> AsyncIterator[ScopeListing]:
        chat = ChatTargets.chat_target(resolved, str(resolved.id))
        entity = resolved.entity
        if not getattr(entity, "forum", False):
            if only_topic is not None:
                # A topic named in a chat that has none: listed, so the
                # coverage table says why nothing was archived under it.
                target = self._bare_target(scope_rid_for(str(resolved.id), only_topic))
                yield ScopeListing(target=target, visible=False, skipped_reason="unsupported_kind", parent_rid=chat.rid)
                return
            self._scopes[chat.rid] = _Scope(chat, resolved.input_entity)
            yield ScopeListing(target=chat, platform_json=self._extras(entity))
            return
        try:
            if only_topic is None:
                topics = await get_forum_topics(self.client, resolved.input_entity)
            else:
                topics = await get_forum_topics_by_ids(self.client, resolved.input_entity, [only_topic])
        except Exception:  # noqa: BLE001 - a forum whose topics cannot be read is a forum this account cannot read
            yield ScopeListing(target=chat, visible=False, skipped_reason="no_access", platform_json=self._extras(entity))
            return
        for topic in topics:
            target = ChatTargets.topic_target(chat, topic)
            self._scopes[target.rid] = _Scope(target, resolved.input_entity, topic.id)
            yield ScopeListing(target=target, parent_rid=chat.rid, platform_json=self._extras(entity))

    @staticmethod
    def _extras(entity: Any) -> dict[str, Any]:
        return {"type": classify_entity(entity), "username": getattr(entity, "username", None)}

    # -- messages ----------------------------------------------------------

    async def messages(
        self, scope: Target, cursor: str | None = None, *, since: str | None = None
    ) -> AsyncIterator[Mapping[str, Any]]:
        """History of `scope` as records, each carrying the state to resume from.

        Two phases over one lookahead: everything above the last known top,
        then the rest of the walk below the last known low when it never
        finished. A record's cursor is the state *after* that record is
        committed, and only the record after it knows whether it was the last
        of its phase or of the whole walk -- hence the lookahead.
        """
        known = self._scopes.get(scope.rid)
        if known is None or known.peer is None:
            raise LookupError(f"{scope.rid} was not listed by this source; scopes() runs first")
        state = Cursor.decode(cursor)
        floor = parse_date_bound(since, end_of_day=False)

        top = state.top if state is not None else None
        low = state.low if state is not None else None
        pending: dict[str, Any] | None = None
        async for message, phase in self._stream(known, state, floor):
            message_id = int(message.id)
            if pending is not None:
                yield pending
            if phase == "above":
                # Above the old top the state stays the old one until the phase
                # ends: a kill here refetches only what was new anyway.
                top = max(top or 0, message_id)
                pending = message_record(message, known, state)
            else:
                if top is None:
                    top = message_id
                low = message_id
                pending = message_record(message, known, Cursor(top, low, False))
        if pending is None:
            return
        # The last record of the run: the new top is known, and everything
        # below `low` has either been walked now or was walked before.
        yield {**pending, "cursor": Cursor(top, low if low is not None else top, True).encode()}

    async def _stream(self, scope: _Scope, state: Cursor | None, floor):
        """Messages newest first, each tagged `above` (the old top) or `below` (the walk)."""
        if state is None:
            async for message in self._page(scope, start_below=None, stop_at=0, floor=floor):
                yield message, "below"
            return
        async for message in self._page(scope, start_below=None, stop_at=state.top, floor=floor):
            yield message, "above"
        if state.done:
            return
        async for message in self._page(scope, start_below=state.low, stop_at=0, floor=floor):
            yield message, "below"

    async def _page(self, scope: _Scope, *, start_below: int | None, stop_at: int, floor):
        """Messages of `scope` newest first, ids in (stop_at, start_below), not older than `floor`.

        A flood wait inside the limit is slept and counted and the page is
        re-opened just below the last id seen; nothing is skipped and nothing
        is served twice.
        """
        offset = start_below
        while True:
            kwargs: dict[str, Any] = {"wait_time": PAGE_WAIT_S}
            if offset is not None:
                kwargs["offset_id"] = offset
            if stop_at:
                kwargs["min_id"] = stop_at
            if scope.topic_id is not None:
                kwargs["reply_to"] = scope.topic_id
            try:
                async for message in self.client.iter_messages(scope.peer, **kwargs):
                    date = getattr(message, "date", None)
                    if floor is not None and isinstance(date, datetime):
                        when = date if date.tzinfo is not None else date.replace(tzinfo=UTC)
                        if when < floor:
                            return
                    offset = int(message.id)
                    yield message
                return
            except FloodWaitError as exc:
                seconds = int(getattr(exc, "seconds", 0))
                if seconds > self.wait_limit:
                    raise
                self.waited_ms += seconds * 1000
                await self._sleep(seconds)


@dataclass(frozen=True)
class _Resolved:
    """The shape `resolve_chat` returns, built from a dialog so one code path lists both."""

    id: int
    entity: Any
    input_entity: Any


__all__ = ["Cursor", "TelegramArchiveSource", "author_of", "message_record", "scope_rid_for"]
