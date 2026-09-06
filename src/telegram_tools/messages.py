"""The `message` verbs: what each one does to a message, and what it shows first.

Spec section 11. Fifteen verbs on one command group, every one a write
behind the same four steps `send` and `delete` already take -- plan,
preflight, re-derivation after the gate, readback and an audit line. That
scaffolding lives in `cli._run_message`; this module holds what is specific
to messages:

* **the selection** -- one `--id`, an `--ids` list, or `--from-search`, an
  archive query whose hits become the ids. Bulk is bounded the way section 7
  says: `--limit` (200) refuses above it with `BULK_LIMIT`, and above
  `BULK_HARD_LIMIT` (1000) nothing runs without `--i-know` *and* the count
  typed at the gate;
* **the preview** -- the resolved chat, and the message or messages the verb
  is about to act on, each on one line, so a y/N or a typed DELETE is never
  answered blind. `@mentions` in outgoing text are named on their own line,
  because Telegram has no other mass-mention control;
* **the call** -- one SDK call per verb, in `perform`, and one fetch per verb
  in `read_back`, so the evidence says what actually happened.

Nothing here decides who may run a verb: bot mode's list is `cli.py`'s, and
the rights a verb needs are declared on its `Op` and checked by the caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Sequence

from telethon.tl.functions.messages import (
    ForwardMessagesRequest,
    GetPeerDialogsRequest,
    MarkDialogUnreadRequest,
    SaveDraftRequest,
    SendReactionRequest,
)
from telethon.tl.types import (
    InputDialogPeer,
    InputMediaPoll,
    InputReplyToMessage,
    Poll,
    PollAnswer,
    ReactionEmoji,
    TextWithEntities,
)

from telegram_tools._core import rid as _rid
from telegram_tools._core.plan import BULK_DEFAULT_LIMIT, BULK_HARD_LIMIT
from telegram_tools.envelope import PREFIX, CommandError
from telegram_tools.mentions import mentions_in, mentions_line
from telegram_tools.records import topic_id_for_message

RULE = "--------------------------------------------"

# The verbs, in the order the menu lists them.
VERBS = (
    "reply",
    "edit",
    "delete",
    "forward",
    "copy",
    "react",
    "unreact",
    "pin",
    "unpin",
    "poll",
    "typing",
    "read",
    "unread",
    "bookmark",
    "draft",
)
# The verbs that take a selection of several messages, from ids or an archive query.
BULK_VERBS = ("delete", "forward", "copy")
# The verbs that act on one message named by --id.
SINGLE_VERBS = ("edit", "react", "unreact", "pin", "unpin", "bookmark")
# The verbs that post somewhere else: they name a destination with --to.
DESTINATION_VERBS = ("forward", "copy")
# Account only. A bot has no dialog of its own to mark, no Saved Messages and
# no drafts (section 11): these refuse under --as-bot before anything connects.
ACCOUNT_ONLY_VERBS = ("read", "unread", "bookmark", "draft")
# What `--as-bot` may run: every verb the platform lets a bot perform, where
# the bot is a member and holds the right the verb needs.
BOT_VERBS = tuple(verb for verb in VERBS if verb not in ACCOUNT_ONLY_VERBS)

@dataclass(frozen=True)
class Op:
    """What a verb is, to the scaffolding around it."""

    verb: str
    # Section 7's rule: bulk deletion is typed_delete; everything else prompt_y,
    # with yes_allowlist for the unattended path.
    approval: str
    # The rights the plan states it needs. `edit` and `delete` add one at run
    # time when a selected message is not the identity's own.
    required: tuple[str, ...] = ()
    # The mutation op the plan records, one per message.
    mutation: str = ""
    # What the human preview calls the action.
    heading: str = ""


OPS: dict[str, Op] = {
    "reply": Op("reply", "prompt_y", ("send_messages",), "reply", "Replying"),
    "edit": Op("edit", "prompt_y", (), "edit_message", "Editing"),
    "delete": Op("delete", "typed_delete", (), "delete_message", "Deleting"),
    "forward": Op("forward", "prompt_y", ("send_messages",), "forward_message", "Forwarding"),
    "copy": Op("copy", "prompt_y", ("send_messages",), "copy_message", "Copying"),
    "react": Op("react", "prompt_y", (), "react", "Reacting"),
    "unreact": Op("unreact", "prompt_y", (), "unreact", "Removing a reaction"),
    "pin": Op("pin", "prompt_y", ("pin_messages",), "pin_message", "Pinning"),
    "unpin": Op("unpin", "prompt_y", ("pin_messages",), "unpin_message", "Unpinning"),
    "poll": Op("poll", "prompt_y", ("send_messages",), "send_poll", "Posting a poll"),
    "typing": Op("typing", "prompt_y", (), "typing", "Showing typing"),
    "read": Op("read", "prompt_y", (), "mark_read", "Marking read"),
    "unread": Op("unread", "prompt_y", (), "mark_unread", "Marking unread"),
    "bookmark": Op("bookmark", "prompt_y", (), "bookmark", "Bookmarking"),
    "draft": Op("draft", "prompt_y", (), "save_draft", "Saving a draft"),
}


# -- the messages a verb acts on ------------------------------------------


@dataclass(frozen=True)
class Brief:
    """One message as the preview shows it: enough to recognise, never the whole thing."""

    id: int
    date: str | None
    sender: str
    text: str
    has_media: bool = False
    own: bool = False
    topic_id: int | None = None
    pinned: bool = False
    reactions: tuple[tuple[str, bool], ...] = ()  # (emoticon, chosen by me)

    @property
    def line(self) -> str:
        """`4812  2026-09-05 10:00  @harry: deploy is green [media]`, cut to fit one row."""
        when = (self.date or "")[:16].replace("T", " ")
        body = " ".join(self.text.split())
        if len(body) > 60:
            body = body[:59] + "…"
        media = " [media]" if self.has_media else ""
        who = f"{self.sender}: " if self.sender else ""
        return f"{self.id:<7} {when:<16} {who}{body or '(no text)'}{media}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "sender": self.sender,
            "text": self.text,
            "has_media": self.has_media,
            "own": self.own,
        }


def _sender_label(message: Any) -> str:
    sender = getattr(message, "sender", None)
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(part for part in parts if part).strip()
    if name:
        return name
    title = getattr(sender, "title", None)
    if title:
        return str(title)
    sender_id = getattr(message, "sender_id", None)
    return f"user {sender_id}" if sender_id is not None else ""


def _date_text(message: Any) -> str | None:
    when = getattr(message, "date", None)
    if not isinstance(when, datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).isoformat(timespec="minutes").replace("+00:00", "Z")


def _reactions_of(message: Any) -> tuple[tuple[str, bool], ...]:
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) or ()
    found = []
    for result in results:
        emoticon = getattr(getattr(result, "reaction", None), "emoticon", None)
        if emoticon:
            found.append((str(emoticon), getattr(result, "chosen_order", None) is not None))
    return tuple(found)


def brief_of(message: Any, *, me_id: int | None = None) -> Brief:
    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""
    own = bool(getattr(message, "out", False))
    if not own and me_id is not None:
        own = getattr(message, "sender_id", None) == me_id
    return Brief(
        id=int(getattr(message, "id")),
        date=_date_text(message),
        sender=_sender_label(message),
        text=str(text),
        has_media=bool(getattr(message, "media", None)),
        own=own,
        topic_id=topic_id_for_message(message),
        pinned=bool(getattr(message, "pinned", False)),
        reactions=_reactions_of(message),
    )


async def fetch_briefs(client, peer: Any, ids: Sequence[int], *, me_id: int | None = None) -> list[Brief]:
    """The messages `ids` name, in that order, or a refusal naming the ids that are not there.

    A message Telegram does not return is a wrong id, a deleted message, or a
    chat this identity cannot read. Acting on the rest would be acting on a
    different selection from the one that was named, so it refuses instead.
    """
    if not ids:
        return []
    found = await client.get_messages(peer, ids=list(ids))
    if not isinstance(found, list):
        found = [found]
    briefs: list[Brief] = []
    missing: list[int] = []
    for wanted, message in zip(ids, found):
        if message is None:
            missing.append(int(wanted))
        else:
            briefs.append(brief_of(message, me_id=me_id))
    if missing:
        shown = ", ".join(str(item) for item in missing[:10]) + (", …" if len(missing) > 10 else "")
        raise CommandError(
            f"No message {shown} in this chat, or it cannot be read as this identity.",
            code="TARGET_NOT_FOUND",
            hint="Check the id with `search` or `archive search`; a deleted message has no id to act on.",
        )
    return briefs


def message_rid(chat_id: int | str, message_id: int | str) -> str:
    return str(_rid.make(PREFIX, "message", chat_id, message_id))


# -- the selection ---------------------------------------------------------


def parse_ids(values: Sequence[str] | None) -> list[int]:
    """`--ids 1,2 --ids 3` as `[1, 2, 3]`, each once, in the order given."""
    ids: list[int] = []
    for raw in values or ():
        for part in str(raw).replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise ValueError(f"--ids takes message ids (whole numbers); {part!r} is not one.")
            number = int(part)
            if number not in ids:
                ids.append(number)
    return ids


def bound_selection(count: int, *, limit: int | None, i_know: bool = False) -> None:
    """Section 7's bulk bound, before anything is fetched or previewed.

    `limit` is the run's `--limit` (200 by default); a selection above it is
    refused rather than cut, because a cut selection is a different selection
    from the one that was asked for. Above the hard limit the answer is
    `--i-know` plus the count typed at the gate, and `--limit` alone does not
    open it.
    """
    limit = BULK_DEFAULT_LIMIT if limit is None else int(limit)
    if count > BULK_HARD_LIMIT and not i_know:
        raise CommandError(
            f"{count} messages is more than the {BULK_HARD_LIMIT} this tool acts on at once.",
            code="BULK_LIMIT",
            hint=f"Narrow the selection, or add --i-know --limit {count} and type {count} at the prompt.",
        )
    if count > limit:
        # The hint names a limit that would actually let this selection
        # through: the count itself, never a cap below it.
        raise CommandError(
            f"{count} messages match, and --limit is {limit}.",
            code="BULK_LIMIT",
            hint=f"Narrow the selection, or pass --limit {count}"
            + ("" if count <= BULK_HARD_LIMIT else " --i-know")
            + " to act on all of them.",
        )


def ids_from_search(archive, query: str, *, scope: Sequence[str], limit: int | None, i_know: bool) -> list[int]:
    """The message ids an archive query selects in this chat, bounded before they are returned.

    Asks the store for one row more than the hard limit can hold, so a selection
    past it is refused by count rather than quietly cut to the store's own cap.
    """
    hits = archive.search(query, scope=list(scope), limit=BULK_HARD_LIMIT + 1)
    ids: list[int] = []
    for hit in hits:
        if hit.deleted_at:
            continue
        number = int(hit.message_id)
        if number not in ids:
            ids.append(number)
    bound_selection(len(ids), limit=limit, i_know=i_know)
    return ids


# -- the preview -----------------------------------------------------------


def format_preview(
    op: Op,
    *,
    actor: str,
    chat_title: str,
    chat_id: int,
    topic: str | None = None,
    messages: Sequence[Brief] = (),
    destination: str | None = None,
    text: str | None = None,
    details: Sequence[str] = (),
    execute: bool | None = None,
) -> str:
    """What is about to happen, to what, as one screen.

    Every verb shows the chat and the message it acts on; a bulk verb lists
    every id, because "the exact ids in the plan" is what the person is
    approving. The outgoing text, when there is one, is the last block, with
    its mentions named above it.
    """
    lines = [f"{op.heading} as {actor}", RULE, f"Chat    {chat_title} ({chat_id})"]
    if topic:
        lines.append(f"Topic   {topic}")
    if destination:
        lines.append(f"To      {destination}")
    lines.extend(details)
    if messages:
        lines.append(RULE)
        label = "Message" if len(messages) == 1 else f"{len(messages)} messages"
        lines.append(label)
        for brief in messages:
            lines.append("  " + brief.line)
    if text is not None:
        lines.append(RULE)
        mentions = mentions_line(text)
        if mentions:
            lines.append(mentions)
        lines.append(text if text else "(no text)")
    lines.append(RULE)
    if execute is False:
        lines.append(f"Dry-run: {len(messages)} message(s) would be deleted. Add --execute to do it; DELETE is asked for then.")
    return "\n".join(lines)


DELETE_WARNING = """\
====================================================
WARNING: DELETE MESSAGES

The messages listed above are deleted for everyone
who can see them. Telegram does not undo this.
===================================================="""


def confirm_prompt_y(preview: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    write(preview)
    answer = read("Do it? [y/N]: ").strip().lower()
    if not answer:
        write("No answer read - cancelled.")
        return False
    return answer == "y"


def confirm_typed_delete(
    preview: str, count: int, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> bool:
    """The `clear-messages` gate, and above the hard limit the count typed too.

    Typing DELETE proves intent; typing 1234 when 1234 messages are listed
    proves the person read how many. Both are asked in the terminal, and
    neither has a flag that answers it.
    """
    write(preview)
    write(DELETE_WARNING)
    if read("Type DELETE to continue: ").strip() != "DELETE":
        write("Cancelled - DELETE was not typed.")
        return False
    if count > BULK_HARD_LIMIT:
        typed = read(f"Type the number of messages ({count}) to continue: ").strip()
        if typed != str(count):
            write("Cancelled - the count did not match.")
            return False
    return True


# -- the calls -------------------------------------------------------------


@dataclass
class Request:
    """Everything one verb needs to run, gathered by the caller."""

    verb: str
    peer: Any
    chat_id: int
    messages: list[Brief] = field(default_factory=list)
    text: str | None = None
    topic_id: int | None = None
    emoji: str | None = None
    to_peer: Any = None
    to_chat_id: int | None = None
    to_topic_id: int | None = None
    question: str | None = None
    options: list[str] = field(default_factory=list)
    multiple: bool = False
    seconds: int = 5
    label: str = ""
    # Where a copied attachment points: the original message's link.
    links: dict[int, str] = field(default_factory=dict)

    @property
    def ids(self) -> list[int]:
        return [brief.id for brief in self.messages]


@dataclass(frozen=True)
class Outcome:
    """What a verb did: the ids it touched, and the ids it made."""

    verb: str
    chat_id: int
    message_ids: tuple[int, ...] = ()
    new_ids: tuple[int, ...] = ()
    done: bool = True
    dry_run: bool = False
    cancelled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb,
            "chat_id": self.chat_id,
            "message_ids": list(self.message_ids),
            "new_message_ids": list(self.new_ids),
            "done": self.done,
            "dry_run": self.dry_run,
            "cancelled": self.cancelled,
            **self.extra,
        }


def message_link(chat_id: int, message_id: int, *, username: str | None = None, topic_id: int | None = None) -> str:
    """The t.me link to a message: by username for a public chat, by internal id otherwise."""
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(chat_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    else:
        internal = internal.lstrip("-")
    if topic_id is not None:
        return f"https://t.me/c/{internal}/{topic_id}/{message_id}"
    return f"https://t.me/c/{internal}/{message_id}"


def copy_text(brief: Brief, link: str | None) -> str:
    """What `copy` posts: the text, and for an attachment a link to where it is.

    Never the bytes. Downloads are the review queue's job (section 9), and a
    copy that fetched media would be a download with no quarantine.
    """
    if not brief.has_media:
        return brief.text
    where = f"(attachment on the original: {link})" if link else "(attachment on the original message)"
    return f"{brief.text}\n{where}" if brief.text else where


def _reply_to(topic_id: int | None, message_id: int | None = None):
    if message_id is not None:
        return InputReplyToMessage(reply_to_msg_id=message_id, top_msg_id=topic_id)
    if topic_id is not None:
        return InputReplyToMessage(reply_to_msg_id=topic_id, top_msg_id=topic_id)
    return None


async def perform(client, request: Request, *, sleep=asyncio.sleep, bookmark_row: Callable[[int], None] | None = None) -> Outcome:
    """One SDK call per verb. The gate has been answered and the plan re-derived by now."""
    verb = request.verb
    ids = request.ids
    peer = request.peer

    if verb == "reply":
        sent = await client.send_message(peer, request.text, reply_to=ids[0])
        return Outcome(verb, request.chat_id, tuple(ids), (int(sent.id),))

    if verb == "edit":
        edited = await client.edit_message(peer, ids[0], request.text)
        return Outcome(verb, request.chat_id, tuple(ids), extra={"text": request.text, "edited_id": int(getattr(edited, "id", ids[0]))})

    if verb == "delete":
        await client.delete_messages(peer, ids, revoke=True)
        return Outcome(verb, request.chat_id, tuple(ids), extra={"deleted": len(ids)})

    if verb == "forward":
        if request.to_topic_id is not None:
            updates = await client(
                ForwardMessagesRequest(from_peer=peer, id=ids, to_peer=request.to_peer, top_msg_id=request.to_topic_id)
            )
            new_ids = tuple(
                int(getattr(getattr(update, "message", None), "id", 0))
                for update in getattr(updates, "updates", ())
                if getattr(getattr(update, "message", None), "id", None)
            )
        else:
            forwarded = await client.forward_messages(request.to_peer, ids, from_peer=peer)
            new_ids = tuple(int(item.id) for item in (forwarded if isinstance(forwarded, list) else [forwarded]) if item is not None)
        return Outcome(verb, request.chat_id, tuple(ids), new_ids, extra={"to_chat_id": request.to_chat_id})

    if verb == "copy":
        new_ids = []
        for brief in request.messages:
            body = copy_text(brief, request.links.get(brief.id))
            sent = await client.send_message(request.to_peer, body, reply_to=request.to_topic_id, link_preview=False)
            new_ids.append(int(sent.id))
        return Outcome(verb, request.chat_id, tuple(ids), tuple(new_ids), extra={"to_chat_id": request.to_chat_id})

    if verb in ("react", "unreact"):
        reaction = [ReactionEmoji(emoticon=request.emoji)] if verb == "react" else []
        await client(SendReactionRequest(peer=peer, msg_id=ids[0], reaction=reaction))
        return Outcome(verb, request.chat_id, tuple(ids), extra={"emoji": request.emoji})

    if verb == "pin":
        await client.pin_message(peer, ids[0])
        return Outcome(verb, request.chat_id, tuple(ids))

    if verb == "unpin":
        await client.unpin_message(peer, ids[0])
        return Outcome(verb, request.chat_id, tuple(ids))

    if verb == "poll":
        # `id` and `hash` are Telegram's on a poll it already holds; a new one
        # sends zeros and gets real ones back.
        poll = Poll(
            id=0,
            hash=0,
            question=TextWithEntities(text=request.question or "", entities=[]),
            answers=[
                PollAnswer(text=TextWithEntities(text=option, entities=[]), option=bytes([index]))
                for index, option in enumerate(request.options)
            ],
            multiple_choice=request.multiple or None,
        )
        sent = await client.send_message(peer, file=InputMediaPoll(poll=poll), reply_to=request.topic_id)
        return Outcome(verb, request.chat_id, (), (int(sent.id),), extra={"question": request.question, "options": list(request.options)})

    if verb == "typing":
        async with client.action(peer, "typing"):
            await sleep(request.seconds)
        return Outcome(verb, request.chat_id, extra={"seconds": request.seconds})

    if verb == "read":
        await client.send_read_acknowledge(peer)
        return Outcome(verb, request.chat_id)

    if verb == "unread":
        await client(MarkDialogUnreadRequest(peer=InputDialogPeer(peer=peer), unread=True))
        return Outcome(verb, request.chat_id)

    if verb == "bookmark":
        saved = await client.forward_messages("me", ids, from_peer=peer)
        new_ids = tuple(int(item.id) for item in (saved if isinstance(saved, list) else [saved]) if item is not None)
        if bookmark_row is not None:
            bookmark_row(ids[0])
        return Outcome(verb, request.chat_id, tuple(ids), new_ids, extra={"label": request.label})

    if verb == "draft":
        await client(SaveDraftRequest(peer=peer, message=request.text or "", reply_to=_reply_to(request.topic_id)))
        return Outcome(verb, request.chat_id, extra={"text": request.text})

    raise ValueError(f"Unknown message verb: {verb}")


async def read_back(client, request: Request, outcome: Outcome, *, where: str, destination: str | None = None) -> str:
    """Fetch what the verb should have left behind and say it, or raise so the caller says `unverified`."""
    verb = request.verb
    peer = request.peer

    if verb in ("reply", "poll", "copy", "forward"):
        target_peer = request.to_peer if verb in DESTINATION_VERBS else peer
        found = await client.get_messages(target_peer, ids=list(outcome.new_ids))
        found = found if isinstance(found, list) else [found]
        if not outcome.new_ids or any(item is None for item in found):
            raise LookupError("Telegram returned no message under the new id")
        ids = ", ".join(str(item) for item in outcome.new_ids)
        return f"message {ids} is in {destination or where}"

    if verb == "edit":
        message = await client.get_messages(peer, ids=outcome.message_ids[0])
        text = getattr(message, "raw_text", None) if message is not None else None
        if text is None and message is not None:
            text = getattr(message, "message", None)
        if text != request.text:
            raise LookupError("the message does not read as the new text")
        return f"message {outcome.message_ids[0]} in {where} now reads the new text"

    if verb == "delete":
        found = await client.get_messages(peer, ids=list(outcome.message_ids))
        found = found if isinstance(found, list) else [found]
        still = [brief for brief in found if brief is not None]
        if still:
            raise LookupError(f"{len(still)} message(s) are still there")
        return f"{len(outcome.message_ids)} message(s) gone from {where}"

    if verb in ("react", "unreact"):
        message = await client.get_messages(peer, ids=outcome.message_ids[0])
        chosen = {emoticon for emoticon, mine in _reactions_of(message) if mine}
        if verb == "react" and request.emoji not in chosen:
            raise LookupError("the reaction is not on the message")
        if verb == "unreact" and (request.emoji in chosen if request.emoji else chosen):
            raise LookupError("the reaction is still on the message")
        return f"message {outcome.message_ids[0]} in {where} " + (
            f"carries {request.emoji}" if verb == "react" else "carries no reaction of yours"
        )

    if verb in ("pin", "unpin"):
        message = await client.get_messages(peer, ids=outcome.message_ids[0])
        pinned = bool(getattr(message, "pinned", False))
        if pinned != (verb == "pin"):
            raise LookupError("the pinned state did not change")
        return f"message {outcome.message_ids[0]} in {where} is {'pinned' if pinned else 'not pinned'}"

    if verb == "typing":
        raise LookupError("a typing status leaves nothing to read back")

    if verb in ("read", "unread", "draft"):
        dialogs = await client(GetPeerDialogsRequest(peers=[InputDialogPeer(peer=peer)]))
        dialog = (getattr(dialogs, "dialogs", None) or [None])[0]
        if dialog is None:
            raise LookupError("Telegram returned no dialog for the chat")
        if verb == "read":
            unread = int(getattr(dialog, "unread_count", 0) or 0)
            if unread:
                raise LookupError(f"{unread} message(s) are still unread")
            return f"{where} has no unread messages"
        if verb == "unread":
            if not getattr(dialog, "unread_mark", False):
                raise LookupError("the chat is not marked unread")
            return f"{where} is marked unread"
        draft = getattr(getattr(dialog, "draft", None), "message", None)
        if draft != (request.text or ""):
            raise LookupError("the draft does not read as the text")
        return f"the draft in {where} reads the text"

    if verb == "bookmark":
        found = await client.get_messages("me", ids=list(outcome.new_ids))
        found = found if isinstance(found, list) else [found]
        if not outcome.new_ids or any(item is None for item in found):
            raise LookupError("Saved Messages holds no copy")
        return f"message {outcome.message_ids[0]} from {where} is in Saved Messages as {', '.join(str(i) for i in outcome.new_ids)}"

    raise LookupError(f"no readback for {verb}")


__all__ = [
    "ACCOUNT_ONLY_VERBS",
    "BOT_VERBS",
    "BULK_VERBS",
    "DESTINATION_VERBS",
    "OPS",
    "SINGLE_VERBS",
    "VERBS",
    "Brief",
    "Op",
    "Outcome",
    "Request",
    "bound_selection",
    "brief_of",
    "confirm_prompt_y",
    "confirm_typed_delete",
    "copy_text",
    "fetch_briefs",
    "format_preview",
    "ids_from_search",
    "mentions_in",
    "mentions_line",
    "message_link",
    "message_rid",
    "parse_ids",
    "perform",
    "read_back",
]
