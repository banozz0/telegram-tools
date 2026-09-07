"""The two adapters the runner consumes: live events, and the send an alert goes out through.

Spec section 10.5. The runner is a synchronous step loop and Telethon is an
async client, so this module owns the bridge between them and nothing else
does:

* **`ClientLoop`** runs one Telethon client on a thread of its own, with its
  own event loop. Everything the runner asks for -- a replay, an alert send --
  is a coroutine submitted to that loop and waited for, so the runner's own
  loop stays a plain `for` over an iterator (`adapters.EventSource` says so).
* **`TelegramEventSource`** registers Telethon's `NewMessage`, `MessageEdited`,
  `ChatAction` and the two reaction updates, turns each into the mapping
  `rules.Event.from_dict` reads, and puts it on a queue the runner drains.
  `events()` yields `None` when the queue has been quiet for `idle_s`, which is
  how the runner gets its tick and its chance to stop.
* **`TelegramMessageSender`** is the `MessageSender`: an alert or a
  runner-held schedule goes out through this tool's own send, under
  `yes_allowlist`, so `TELEGRAM_SEND_ALLOWLIST` is the gate for an unattended
  send whether a person or a rule asked for it.

Three mappings are worth stating, because they decide what a rule can match:

* **One message can be three events.** A message is always a `message` event;
  it is *also* a `link` event when it carries URLs and a `media` event when it
  carries a file. The dedup key includes the kind, so the three never collide,
  and a rule that triggers on `link` matches without having to trigger on every
  message in the chat.
* **A forum topic is the scope.** The rid of an event in a forum is
  `tg:topic:<chat>:<topic>`, exactly as the archive scopes it, so a rule
  filtered to one topic sees only that topic.
* **`edit_version` is what makes a re-delivery a duplicate.** For an edit it is
  the edit's own timestamp, so each edit is its own event and the same edit
  arriving twice is not. For a reaction Telegram sends no version, so it is a
  digest of the reaction summary: a reaction state already seen fires nothing,
  and a new one does.

Account mode and `--as-bot` both work here: a bot receives updates for the
chats it is in, and `replay` is account-only because a bot has no history
(section 5.2), which the runner reports as coverage rather than hiding.
"""

from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
from typing import Any, Iterator, Mapping, Sequence

from telethon import events as tl_events
from telethon.errors import FloodWaitError
from telethon.tl.types import UpdateBotMessageReaction, UpdateMessageReactions

from telegram_tools._core import rid as _rid
from telegram_tools._core.contract import CodedError
from telegram_tools._core.runner import RateLimited
from telegram_tools.adapters.archive import _iso
from telegram_tools.adapters.media import links_of, media_candidate
from telegram_tools.envelope import PLATFORM, PREFIX
from telegram_tools.resolver import resolve_chat
from telegram_tools.send import SendNotAllowedError, require_send_allowed

# How long `events()` waits for an event before yielding None. The runner ticks
# on every None, so this is also how often a schedule can fire and how quickly
# a stop request is noticed.
IDLE_S = 1.0
# How many events may pile up before the source drops the oldest. A runner that
# cannot keep up is a runner behind on its cursors; the replay on the next start
# is what recovers them, so a bounded queue is safer than unbounded memory.
QUEUE_MAX = 2000
# How long a call submitted to the client's loop may take before the runner
# gives up on it. A send that hangs must not hang the whole runner.
CALL_TIMEOUT_S = 120.0
# What a message-shaped event kind can be, in the order they are yielded.
MESSAGE_KINDS = ("message", "link", "media")


class LoopClosed(RuntimeError):
    """A call was submitted to a client loop that is not running."""


def scope_rid(chat_id: int, topic_id: int | None) -> str:
    """The rid an event's scope carries: a topic when it is in one, else the chat."""
    if topic_id is None:
        return str(_rid.make(PREFIX, "chat", chat_id))
    return str(_rid.make(PREFIX, "topic", chat_id, topic_id))


def topic_of(message: Any) -> int | None:
    """The forum topic a message belongs to, or None outside a forum.

    Telethon puts the topic on `reply_to`: `forum_topic` marks the message as
    living in one, `reply_to_top_id` is the topic when the message also replies
    to something inside it, and `reply_to_msg_id` is the topic otherwise. A
    forum's General topic carries no id at all, which is 1 in Telegram's own
    numbering.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    top = getattr(reply_to, "reply_to_top_id", None)
    if top:
        return int(top)
    inner = getattr(reply_to, "reply_to_msg_id", None)
    return int(inner) if inner else 1


def sender_rid_of(message: Any) -> str | None:
    sender_id = getattr(message, "sender_id", None)
    if sender_id is None:
        return None
    sender = getattr(message, "sender", None)
    kind = "chat" if sender is not None and type(sender).__name__ in ("Channel", "Chat") else "user"
    return str(_rid.make(PREFIX, kind, sender_id))


def attachments_of(message: Any, rid: str, sender_rid: str | None) -> list[dict[str, Any]]:
    """What the message carried with it, as the platform described it. Never fetched."""
    candidate = media_candidate(message, rid, sender_rid)
    if candidate is None:
        return []
    return [
        {
            "locator": candidate.locator,
            "type": candidate.claimed_type,
            "size": candidate.claimed_size,
            "display_name": candidate.display_name,
        }
    ]


def message_events(message: Any, *, kind: str = "message", chat_id: int | None = None) -> list[dict[str, Any]]:
    """One Telethon message as the events it is: `message`, then `link` and `media` where it has them.

    `kind` is `message` for a new one and `edit` for an edit; an edit yields
    only itself, because a link or a file that arrived with the original was
    already a `link` or `media` event when it did.
    """
    marked = int(chat_id if chat_id is not None else getattr(message, "chat_id", 0) or 0)
    topic = topic_of(message)
    rid = scope_rid(marked, topic)
    sender_rid = sender_rid_of(message)
    sender = getattr(message, "sender", None)
    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""
    links = links_of(message)
    attachments = attachments_of(message, rid, sender_rid)
    message_id = str(int(getattr(message, "id")))
    base = {
        "platform": PLATFORM,
        "rid": rid,
        "subject_id": message_id,
        "sender_rid": sender_rid,
        "sender_is_bot": bool(getattr(sender, "bot", False)),
        "text": text,
        "links": links,
        "attachments": attachments,
        "occurred_at": _iso(getattr(message, "date", None)),
        "cursor": message_id,
        "metadata": {
            "chat_id": marked,
            "topic_id": topic,
            "reply_to": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
            "sender_label": getattr(sender, "username", None) or getattr(sender, "title", None) or getattr(sender, "first_name", None),
        },
    }
    if kind == "edit":
        edited = getattr(message, "edit_date", None)
        return [{**base, "kind": "edit", "edit_version": int(edited.timestamp()) if edited is not None else 0}]
    out = [{**base, "kind": "message", "edit_version": 0}]
    if links:
        out.append({**base, "kind": "link", "edit_version": 0})
    if attachments:
        out.append({**base, "kind": "media", "edit_version": 0})
    return out


def reaction_version(reactions: Any) -> int:
    """A whole number naming the reaction state, so an unchanged state is a duplicate.

    Telegram sends no version with a reaction update, and the event key needs
    one that changes when the reactions do and does not when they do not. The
    summary Telegram *does* send -- each emoji and its count, ordered -- is
    digested into 32 bits, which is what `edit_version` carries.
    """
    parts: list[str] = []
    for result in getattr(reactions, "results", None) or ():
        reaction = getattr(result, "reaction", None)
        emoticon = getattr(reaction, "emoticon", None) or getattr(reaction, "document_id", None) or type(reaction).__name__
        parts.append(f"{emoticon}={int(getattr(result, 'count', 0) or 0)}")
    digest = hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:8]
    return int(digest, 16)


def reaction_event(update: Any, *, chat_id: int, occurred_at: str | None = None) -> dict[str, Any] | None:
    """`UpdateMessageReactions` or `UpdateBotMessageReaction` as a `reaction` event."""
    message_id = getattr(update, "msg_id", None)
    if message_id is None:
        return None
    topic = getattr(update, "top_msg_id", None)
    reactions = getattr(update, "reactions", None)
    if reactions is None:
        # The bot-side update lists the reactions instead of summarising them.
        reactions = type("_Summary", (), {"results": [type("_R", (), {"reaction": item, "count": 1})() for item in (getattr(update, "new_reactions", None) or ())]})()
    actor = getattr(update, "actor", None)
    return {
        "platform": PLATFORM,
        "rid": scope_rid(int(chat_id), int(topic) if topic else None),
        "subject_id": str(int(message_id)),
        "kind": "reaction",
        "sender_rid": None if actor is None else str(_rid.make(PREFIX, "user", getattr(actor, "user_id", 0) or 0)),
        "sender_is_bot": False,
        "edit_version": reaction_version(reactions),
        "text": "",
        "links": [],
        "attachments": [],
        "metadata": {"chat_id": int(chat_id), "topic_id": int(topic) if topic else None},
        "occurred_at": occurred_at,
        # A reaction moves no history cursor: the message it reacts to was
        # already archived, and a replay walks messages, not reactions.
        "cursor": "",
    }


def action_event(event: Any) -> list[dict[str, Any]]:
    """A `ChatAction` as `member_join` or `member_leave` events, one per person it names."""
    if event.user_joined or event.user_added:
        kind = "member_join"
    elif event.user_left or event.user_kicked:
        kind = "member_leave"
    else:
        return []
    marked = int(getattr(event, "chat_id", 0) or 0)
    rid = scope_rid(marked, None)
    out: list[dict[str, Any]] = []
    for user_id in getattr(event, "user_ids", None) or ():
        member = str(_rid.make(PREFIX, "user", user_id))
        out.append(
            {
                "platform": PLATFORM,
                "rid": rid,
                "subject_id": member,
                "kind": kind,
                "sender_rid": member,
                "sender_is_bot": False,
                "edit_version": 0,
                "text": "",
                "links": [],
                "attachments": [],
                "metadata": {"chat_id": marked, "action": kind},
                "occurred_at": _iso(getattr(getattr(event, "action_message", None), "date", None)),
                # Membership is not a point in history to resume from.
                "cursor": "",
            }
        )
    return out


class ClientLoop:
    """One Telethon client on a thread of its own, so the runner can stay synchronous.

    `start()` spawns the thread, brings the loop up and connects; `call()`
    submits a coroutine and waits for it; `stop()` disconnects and joins. The
    runner holds one of these for the whole of `watch run`.
    """

    def __init__(self, client: Any, *, timeout_s: float = CALL_TIMEOUT_S) -> None:
        self.client = client
        self.timeout_s = timeout_s
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="telegram-watch", daemon=True)
        self.thread.start()
        self._ready.wait(timeout=self.timeout_s)
        if self.loop is None:
            raise LoopClosed("the client loop did not come up")
        self.call(self.client.connect())

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def call(self, coroutine: Any, *, timeout_s: float | None = None) -> Any:
        """Run `coroutine` on the client's loop and wait for it here."""
        loop = self.loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise LoopClosed("the client loop is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return future.result(timeout=self.timeout_s if timeout_s is None else timeout_s)

    def drain(self, agen: Any) -> Iterator[Any]:
        """Every item of an async generator, pulled one at a time from this thread."""
        while True:
            try:
                yield self.call(agen.__anext__())
            except StopAsyncIteration:
                return

    def stop(self) -> None:
        loop, thread = self.loop, self.thread
        if loop is None or thread is None:
            return
        try:
            self.call(self.client.disconnect(), timeout_s=10.0)
        except Exception:  # noqa: BLE001 - a teardown failure must not replace the real exit
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10.0)
        self.loop, self.thread = None, None


class TelegramEventSource:
    """`EventSource` over one connected client: live updates, and the replay a restart needs.

    The handlers run on the client's own loop and do nothing but map and
    enqueue; every mapping is a pure function above, so the tests never need a
    loop to check what a message becomes.
    """

    def __init__(self, client: Any, loop: ClientLoop, *, mode: str = "account", idle_s: float = IDLE_S, maxsize: int = QUEUE_MAX) -> None:
        self.client = client
        self.loop = loop
        self.mode = mode
        self.idle_s = idle_s
        self.queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self.closed = False
        # How many events the queue dropped because the runner fell behind.
        # Reported by `watch status`, never swallowed.
        self.dropped = 0

    # -- the handlers ------------------------------------------------------

    def register(self) -> None:
        """Add every handler this source reads. Called once, before `events()`."""
        self.client.add_event_handler(self._on_message, tl_events.NewMessage())
        self.client.add_event_handler(self._on_edit, tl_events.MessageEdited())
        self.client.add_event_handler(self._on_action, tl_events.ChatAction())
        self.client.add_event_handler(self._on_raw, tl_events.Raw(types=(UpdateMessageReactions, UpdateBotMessageReaction)))

    def put(self, record: Mapping[str, Any]) -> None:
        try:
            self.queue.put_nowait(dict(record))
        except queue.Full:
            self.dropped += 1

    async def _on_message(self, event: Any) -> None:
        for record in message_events(event.message, kind="message"):
            self.put(record)

    async def _on_edit(self, event: Any) -> None:
        for record in message_events(event.message, kind="edit"):
            self.put(record)

    async def _on_action(self, event: Any) -> None:
        for record in action_event(event):
            self.put(record)

    async def _on_raw(self, update: Any) -> None:
        peer = getattr(update, "peer", None)
        chat_id = _peer_id(peer)
        if chat_id is None:
            return
        record = reaction_event(update, chat_id=chat_id)
        if record is not None:
            self.put(record)

    # -- the Protocol -------------------------------------------------------

    def events(self) -> Iterator[Mapping[str, Any] | None]:
        """Events as they arrive, and `None` every `idle_s` of quiet so the runner ticks."""
        while not self.closed:
            try:
                yield self.queue.get(timeout=self.idle_s)
            except queue.Empty:
                yield None

    def replay(self, rid: str, cursor: str) -> Iterator[Mapping[str, Any]]:
        """Every message of `rid` after `cursor`, oldest first (section 10.5).

        `min_id` is Telegram's own "after this id", so the walk asks the server
        for exactly the gap a restart left. A bot has no history and replays
        nothing; the runner records that as coverage.
        """
        if self.mode != "account":
            return
        try:
            parsed = _rid.parse(rid)
        except _rid.RidError:
            return
        min_id = _min_id(cursor)
        if min_id is None:
            return
        chat_id = int(parsed.ids[0])
        topic_id = int(parsed.ids[1]) if parsed.kind == "topic" else None
        resolved = self.loop.call(resolve_chat(self.client, chat_id))
        walk = self.client.iter_messages(
            resolved.input_entity,
            min_id=min_id,
            reverse=True,
            **({"reply_to": topic_id} if topic_id is not None else {}),
        )
        for message in self.loop.drain(walk.__aiter__()):
            for record in message_events(message, kind="message", chat_id=chat_id):
                yield record

    def close(self) -> None:
        self.closed = True


def _peer_id(peer: Any) -> int | None:
    """A `Peer*` as the marked id every rid here uses, or None for a peer with no chat."""
    channel_id = getattr(peer, "channel_id", None)
    if channel_id is not None:
        return -1000000000000 - int(channel_id)
    chat_id = getattr(peer, "chat_id", None)
    if chat_id is not None:
        return -int(chat_id)
    user_id = getattr(peer, "user_id", None)
    return int(user_id) if user_id is not None else None


def _min_id(cursor: str) -> int | None:
    """The message id a replay resumes after. A cursor this source did not write replays nothing."""
    text = str(cursor or "").strip()
    return int(text) if text.isdigit() else None


class TelegramMessageSender:
    """`MessageSender`: an alert or a runner-held schedule, through this tool's own send.

    `approval` is always `yes_allowlist` here, so the destination has to be in
    `TELEGRAM_SEND_ALLOWLIST` -- the same list an unattended `send --yes`
    answers to, refused with the same `NOT_ALLOWLISTED`. A flood wait becomes
    `RateLimited`, which the runner honours and reports; it is not slept here,
    because a runner that sleeps inside a send stops ticking.
    """

    def __init__(self, client: Any, loop: ClientLoop, allowlist: Sequence[Any] = ()) -> None:
        self.client = client
        self.loop = loop
        self.allowlist = tuple(allowlist)

    def send(self, rid: str, text: str, *, approval: str = "yes_allowlist") -> Mapping[str, Any]:
        if approval != "yes_allowlist":
            raise CodedError(
                "APPROVAL_REQUIRED",
                f"the runner sends only under yes_allowlist, not {approval!r}",
                hint="an unattended send is gated by TELEGRAM_SEND_ALLOWLIST",
            )
        try:
            parsed = _rid.parse(rid)
        except _rid.RidError as exc:
            raise CodedError("PLATFORM_UNSUPPORTED", f"destination {rid!r}: {exc}") from exc
        if parsed.kind not in ("chat", "topic"):
            raise CodedError("PLATFORM_UNSUPPORTED", f"{rid} is not a chat or a topic")
        chat_id = int(parsed.ids[0])
        topic_id = int(parsed.ids[1]) if parsed.kind == "topic" else None
        try:
            return self.loop.call(self._send(chat_id, topic_id, text))
        except FloodWaitError as flood:
            raise RateLimited(float(getattr(flood, "seconds", 0) or 0), platform=PLATFORM) from flood
        except SendNotAllowedError as refused:
            raise CodedError(
                "NOT_ALLOWLISTED",
                str(refused),
                hint="add the destination to TELEGRAM_SEND_ALLOWLIST, or point the rule at a command destination",
            ) from refused

    async def _send(self, chat_id: int, topic_id: int | None, text: str) -> dict[str, Any]:
        resolved = await resolve_chat(self.client, chat_id)
        require_send_allowed(
            self.allowlist,
            chat_id=resolved.id,
            username=getattr(resolved.entity, "username", None),
            topic_id=topic_id,
        )
        sent = await self.client.send_message(resolved.input_entity, text, reply_to=topic_id)
        return {
            "status": "ok",
            "rid": scope_rid(resolved.id, topic_id),
            "message_id": int(getattr(sent, "id")),
            "chat_id": resolved.id,
            "topic_id": topic_id,
        }


__all__ = [
    "ClientLoop",
    "LoopClosed",
    "TelegramEventSource",
    "TelegramMessageSender",
    "action_event",
    "attachments_of",
    "message_events",
    "reaction_event",
    "reaction_version",
    "scope_rid",
    "sender_rid_of",
    "topic_of",
]
