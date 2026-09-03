from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon.errors import ChannelForumMissingError, RPCError

from telegram_tools import cli
from telegram_tools.bots import IMPLICIT_OTHER_RIGHT, format_bot_profile, get_bot_profile, list_bots, resolve_bot, right_names
from telegram_tools.client import SessionInUseError, create_client, start_client
from telegram_tools._core.columns import cell
from telegram_tools.config import ConfigError, load_config, lookup_bot_token, resolve_bot_token
from telegram_tools.delete import kind_for_type
from telegram_tools.discovery import list_dialog_choices
from telegram_tools.prompts import BACK, CLEAR, EXIT, Extra, after_action, after_run, ask_int, ask_lines, ask_text, choose, edit_field, pick, pick_many
from telegram_tools.resolver import resolve_chat
from telegram_tools.topics import get_forum_topics
from telegram_tools import ui
from telegram_tools.ui import crumb

# What the menu turns into a printed line instead of an exit. EntityResolutionError
# is a ValueError and PermissionError is an OSError, so both are already covered;
# anything not named here is a bug and should still be loud.
MENU_ERRORS = (ConfigError, SessionInUseError, ValueError, OSError, RPCError)

ROOT_TITLE = "telegram-tools"
MAIN = "Main"
ROOT_ITEMS = (
    "Chats & topics (find IDs)",
    "Search / export messages",
    "Send a message",
    "Create a group, channel, or topic",
    "Delete a group, channel, or topic",
    "Clear topic messages",
    "My bots",
    "Check setup",
)


class MenuSession:
    """One Telegram connection and its caches, for the life of one menu run.

    Everything is lazy: the menu itself opens without credentials, and `doctor`
    never needs any. The caches are never refreshed — restarting the tool is the
    refresh.
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._client = None
        self._chats: list[Any] | None = None
        self._bots: list[Any] | None = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config()
        return self._config

    async def client(self):
        if self._client is None:
            self._client = await start_client(create_client(self.config))
        return self._client

    async def chats(self):
        if self._chats is None:
            self._chats = await list_dialog_choices(await self.client())
        return self._chats

    async def topics(self, reference: str):
        client = await self.client()
        resolved = await resolve_chat(client, reference)
        try:
            return await get_forum_topics(client, resolved.input_entity)
        except ChannelForumMissingError:
            # "This chat has no topics" is an answer, not a failure: Telegram
            # rejects the request outright for a non-forum chat. Anything else
            # is a real error and must surface.
            return []

    async def bots(self):
        if self._bots is None:
            self._bots = await list_bots(await self.client())
        return self._bots

    async def bot_profile(self, reference: str):
        client = await self.client()
        return await get_bot_profile(client, await resolve_bot(client, reference))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


def _namespace(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


async def _call(args, *, session, runner, write) -> int | None:
    """Run one action. Returns its exit code, or None when it errored and the
    message is already printed."""
    try:
        client = await session.client() if session is not None else None
        config = session.config if session is not None else None
        return await runner(args, client=client, config=config)
    except MENU_ERRORS as exc:
        write(f"error: {exc}")
        return None


# After-run row keys. AGAIN is answered inside _act. STAY is the flow's own next
# step -- back to its filled form, or whatever "another" means there -- which only
# the flow can answer, so _act hands it back. Anything else (MENU, EXIT) leaves the
# flow, and a flow turns that into its keep-going bool with `result is not EXIT`.
AGAIN = object()
STAY = object()
RUN_AGAIN = (AGAIN, "Run it again")
TWEAK = (STAY, "Tweak it")


async def _act(args, *, session, runner, read, write, trail: str = MAIN, rows=(RUN_AGAIN, TWEAK)) -> Any:
    """Run one action, then the after-run screen. Returns STAY, MENU or EXIT.

    The title says what happened: Done on exit code 0, Not done when a confirm
    was declined (the CLI returns 1), Failed after a printed error.
    """
    while True:
        code = await _call(args, session=session, runner=runner, write=write)
        outcome = "Done" if code == 0 else ("Failed" if code is None else "Not done")
        result = after_run(read=read, write=write, title=crumb(trail, outcome), rows=rows)
        if result is not AGAIN:
            return result


def _confirm_discard(trail: str, *, title: str, said: str, read, write) -> bool:
    """Ask before a form with something typed in it is dropped. True to drop it.

    Pressing 0 on this screen is the second, deliberate press; 1 keeps editing.
    One idiom for the whole menu -- numbers and 0 -- rather than a y/N here.
    """
    keep = choose(["Keep editing"], title=crumb(trail, title), read=read, write=write, back_label="Discard it and go back")
    if keep == 0:
        return False
    write(said)
    return True


def _staged_changes(count: int) -> str:
    return f"{count} staged change{'s' if count > 1 else ''}"


CHAT_GROUPS = (
    ("Forum groups", ("forum_group",)),
    ("Channels", ("channel",)),
    ("Groups", ("group", "supergroup")),
    ("Direct chats", ("user",)),
)

_TYPE_A_CHAT = "Type an ID or @username"


@dataclass(frozen=True)
class ChatPick:
    """A chosen chat: what to pass as --chat, what to call it on screen.

    `is_forum` is None for a typed reference — nothing has looked it up, and
    guessing would put a topic row on a screen that cannot have one.
    """

    reference: str
    title: str
    is_forum: bool | None
    # None for a typed reference, for the same reason `is_forum` is: nothing
    # has looked it up, and `delete` must ask rather than assume.
    type: str | None = None


def _chat_label(chat) -> str:
    # `cell`, not a slice and a `:<32}`: an emoji title measures wider on
    # screen than `len()` says, and the ID column has to line up.
    return f"{cell(chat.title, 32)}  {chat.id}"


def _ask_reference(*, read, write) -> Any:
    typed = ask_text("Chat ID or @username", read=read, write=write)
    if typed is BACK:
        return BACK
    return ChatPick(reference=typed, title=typed, is_forum=None)


def _pick_from_group(chats, *, title, read, write) -> Any:
    """Page one group, with a name filter and a manual escape hatch."""
    items = chats
    extras = (Extra("filter", "Filter by name"), Extra("manual", _TYPE_A_CHAT))
    while True:
        if items:
            chosen = pick(items, title=title, label=_chat_label, read=read, write=write, extras=extras)
        else:
            # `pick` bails out with "Nothing to pick from." before it ever
            # renders extras, which would take the manual escape hatch down
            # with the (rightly) absent picker rows. Offer the extras on
            # their own instead, so an account with no forum groups still
            # has a way to type a chat by hand.
            choice = choose([extra.label for extra in extras], title=title, read=read, write=write)
            chosen = BACK if choice is BACK else extras[choice].key

        if chosen is BACK:
            if items is not chats:
                # A filter is a view of the group, so back drops the filter first.
                items = chats
                continue
            return BACK

        if chosen == "filter":
            needle = ask_text("Part of the name", read=read, write=write)
            if needle is BACK:
                continue
            matches = [chat for chat in chats if needle.lower() in chat.title.lower()]
            if not matches:
                write(f"Nothing matches {needle!r}.")
                continue
            items = matches
            continue

        if chosen == "manual":
            typed = _ask_reference(read=read, write=write)
            if typed is BACK:
                continue
            return typed

        return ChatPick(
            reference=str(chosen.id), title=chosen.title, is_forum=chosen.is_forum, type=chosen.type
        )


async def _pick_chat(*, session, read, write, forums_only: bool = False, trail: str = MAIN) -> Any:
    chats = await session.chats()

    if forums_only:
        return _pick_from_group(
            [chat for chat in chats if chat.is_forum],
            title=crumb(trail, "Pick a forum group"),
            read=read,
            write=write,
        )

    groups = [(name, [chat for chat in chats if chat.type in types]) for name, types in CHAT_GROUPS]
    groups = [(name, members) for name, members in groups if members]

    while True:
        labels = [f"{name} ({len(members)})" for name, members in groups]
        choice = choose(labels + [_TYPE_A_CHAT], title=crumb(trail, "Pick a chat"), read=read, write=write)
        if choice is BACK:
            return BACK

        if choice == len(groups):
            typed = _ask_reference(read=read, write=write)
            if typed is BACK:
                continue
            return typed

        name, members = groups[choice]
        picked = _pick_from_group(members, title=crumb(trail, "Pick a chat", name), read=read, write=write)
        if picked is BACK:
            continue
        return picked


async def _flow_discover(*, session, runner, read, write) -> bool:
    # Two required answers with good defaults: a straight run of questions, not
    # a form. Sven's try-it on 2026-08-31 found the form version read as broken
    # -- he picked the scope and waited for the list.
    trail = crumb(MAIN, "Chats & topics")
    while True:
        scope = choose(["Chats I manage", "Every chat"], title=trail, read=read, write=write)
        if scope is BACK:
            return True

        while True:
            where = choose(["Print it here", "Write a JSON file"], title=crumb(trail, "Where should it go?"), read=read, write=write)
            if where is BACK:
                break

            json_output = None
            if where == 1:
                path = ask_text("JSON file path", read=read, write=write)
                if path is BACK:
                    # Cancelling the path steps back one screen, same as every
                    # other cancel -- not all the way out to the root menu.
                    continue
                json_output = path

            args = _namespace(command="discover", json_output=json_output, all_chats=scope == 1)
            # No Tweak row: with two questions there is no form to go back to,
            # and Main menu then 1 is the same two keystrokes.
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, rows=(RUN_AGAIN,))
            return result is not EXIT


async def _flow_doctor(*, session, runner, read, write) -> bool:
    # No session: doctor never opens a connection, which is the point of it. And
    # no after-run screen: running doctor again tells you nothing new.
    await _call(_namespace(command="doctor"), session=None, runner=runner, write=write)
    return after_action(read=read, write=write)


_ALL_TOPICS = "All topics"


def _shown(value, empty: str) -> str:
    return empty if value in (None, "") else str(value)


async def _ask_topic(picked, *, session, read, write, trail: str) -> Any:
    topics = await session.topics(picked.reference)
    if not topics:
        write("That chat has no topics.")
        return BACK

    chosen = pick(
        topics,
        title=crumb(trail, "Topic"),
        label=lambda topic: f"{topic.id:<6}  {topic.display_title}",
        read=read,
        write=write,
        extras=(Extra("all", _ALL_TOPICS),),
    )
    if chosen is BACK:
        return BACK
    if chosen == "all":
        return CLEAR
    return chosen


def _ask_from_user(*, read, write, trail: str) -> Any:
    choice = choose(["Anyone", "Me", "Someone else"], title=crumb(trail, "From"), read=read, write=write)
    if choice is BACK:
        return BACK
    if choice == 0:
        return CLEAR
    if choice == 1:
        return "me"
    return ask_text("Username, ID, or me", read=read, write=write)


async def _flow_search(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Search")
    while True:
        picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
        if picked is BACK:
            return True

        form = crumb(trail, picked.title)
        staged: dict[str, Any] = {"topic": None, "keyword": None, "from_user": None, "since": None, "until": None, "limit": None}
        topic_info = None  # The picked TopicInfo, kept only for display; staged["topic"] holds its id.

        while True:
            rows: list[tuple[str, str]] = []
            if picked.is_forum is not False:
                topic_shown = "all topics" if topic_info is None else f"{topic_info.id} {topic_info.title}"
                rows.append(("topic", f"Topic          [{topic_shown}]"))
            rows.extend(
                [
                    ("keyword", f"Contains       [{_shown(staged['keyword'], '(anything)')}]"),
                    ("from_user", f"From           [{_shown(staged['from_user'], '(anyone)')}]"),
                    ("since", f"Since          [{_shown(staged['since'], '(any date)')}]"),
                    ("until", f"Until          [{_shown(staged['until'], '(any date)')}]"),
                    ("limit", f"Limit          [{_shown(staged['limit'], '(no limit)')}]"),
                    ("run", "Run it (print here)"),
                    ("export", "Export to a file"),
                ]
            )

            choice = choose(
                [label for _key, label in rows],
                title=form,
                read=read,
                write=write,
                back_label="Back (discards)",
            )
            if choice is BACK:
                count = sum(1 for value in staged.values() if value is not None)
                if count and not _confirm_discard(form, title=_staged_changes(count), said=f"Discarded {_staged_changes(count)}.", read=read, write=write):
                    continue
                break
            key = rows[choice][0]

            if key in ("run", "export"):
                output_path = None
                output_format = "json"
                if key == "export":
                    output_path = ask_text("Export file path", read=read, write=write)
                    if output_path is BACK:
                        continue
                    fmt = choose(["JSON", "CSV"], title=crumb(form, "Format"), read=read, write=write)
                    if fmt is BACK:
                        continue
                    output_format = ("json", "csv")[fmt]

                args = _namespace(
                    command="search",
                    chat=picked.reference,
                    topic=staged["topic"],
                    keyword=staged["keyword"],
                    from_user=staged["from_user"],
                    since=staged["since"],
                    until=staged["until"],
                    limit=staged["limit"],
                    format=output_format,
                    output=output_path,
                )
                result = await _act(args, session=session, runner=runner, read=read, write=write, trail=form)
                if result is not STAY:
                    return result is not EXIT
                continue

            if key == "topic":
                answer = await _ask_topic(picked, session=session, read=read, write=write, trail=form)
                if answer is BACK:
                    continue
                topic_info = None if answer is CLEAR else answer
                staged["topic"] = None if topic_info is None else topic_info.id
                continue

            if key == "from_user":
                answer = _ask_from_user(read=read, write=write, trail=form)
            elif key == "limit":
                answer = edit_field(
                    crumb(form, "Limit"),
                    _shown(staged["limit"], "(no limit)"),
                    read=read,
                    write=write,
                    ask=lambda: ask_int("Maximum messages", read=read, write=write),
                    allow_clear=True,
                    is_set=staged["limit"] is not None,
                )
            else:
                labels = {"keyword": ("Contains", "(anything)"), "since": ("Since", "(any date)"), "until": ("Until", "(any date)")}
                title, empty = labels[key]
                answer = edit_field(
                    crumb(form, title),
                    _shown(staged[key], empty),
                    read=read,
                    write=write,
                    ask=lambda: ask_text(title, read=read, write=write),
                    allow_clear=True,
                    is_set=staged[key] is not None,
                )

            if answer is BACK:
                continue
            staged[key] = None if answer is CLEAR else answer


_NO_TOPIC = "The chat itself (no topic)"


def _preview_line(text: str | None, width: int = 40) -> str:
    """One line of a staged message: newlines shown, long bodies cut."""
    if not text:
        return "(nothing yet)"
    flat = text.replace("\n", " / ")
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _files_label(files: list[str]) -> str:
    if not files:
        return "(none)"
    first = Path(files[0]).name
    return first if len(files) == 1 else f"{first} +{len(files) - 1} more"


def _ask_files(files: list[str], *, read, write, trail: str) -> Any:
    """The new attachment list, or BACK to leave it alone."""
    if not files:
        path = ask_text("File path", read=read, write=write)
        return BACK if path is BACK else [path]

    choice = choose(
        ["Add another file", "Remove them all"],
        title=crumb(trail, f"Files ({len(files)})"),
        read=read,
        write=write,
    )
    if choice is BACK:
        return BACK
    if choice == 1:
        return []
    path = ask_text("File path", read=read, write=write)
    return BACK if path is BACK else [*files, path]


async def _ask_send_topic(picked, *, session, read, write, trail: str) -> Any:
    """A topic to post into, CLEAR for the chat itself, or BACK to cancel.

    A chat with no topics is an answer here, not the failure it is for `clear`:
    the message simply goes to the chat.
    """
    topics = await session.topics(picked.reference)
    if not topics:
        write("That chat has no topics - the message goes to the chat itself.")
        return CLEAR

    chosen = pick(
        topics,
        title=crumb(trail, "Topic"),
        label=lambda topic: f"{topic.id:<6}  {topic.display_title}",
        read=read,
        write=write,
        extras=(Extra("chat", _NO_TOPIC),),
    )
    if chosen is BACK:
        return BACK
    return CLEAR if chosen == "chat" else chosen


async def _flow_send(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Send")
    while True:
        picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
        if picked is BACK:
            return True

        form = crumb(trail, picked.title)
        topic_info = None
        text: str | None = None
        files: list[str] = []
        while True:
            rows: list[tuple[str, str]] = []
            if picked.is_forum is not False:
                topic_shown = "(the chat itself)" if topic_info is None else f"{topic_info.id} {topic_info.title}"
                rows.append(("topic", f"Topic     [{topic_shown}]"))
            rows.extend(
                [
                    ("text", f"Message   [{_preview_line(text)}]"),
                    ("files", f"Files     [{_files_label(files)}]"),
                    ("send", "Send it (shows the whole message, then asks y/N)"),
                ]
            )

            choice = choose(
                [label for _key, label in rows],
                title=form,
                read=read,
                write=write,
                back_label="Back (discards)",
            )
            if choice is BACK:
                staged = text or files or topic_info is not None
                if staged and not _confirm_discard(form, title="Unsent message", said="Discarded the unsent message.", read=read, write=write):
                    continue
                break
            key = rows[choice][0]

            if key == "topic":
                answer = await _ask_send_topic(picked, session=session, read=read, write=write, trail=form)
                if answer is BACK:
                    continue
                topic_info = None if answer is CLEAR else answer
                continue

            if key == "text":
                # The staged body is shown flattened and cut: it goes in the prompt
                # header, where a real multi-line message would wreck the line. It is
                # display only — cancelling keeps what is already there.
                answer = ask_lines("Message", read=read, write=write, current=_preview_line(text) if text else None)
                if answer is not BACK:
                    text = answer
                continue

            if key == "files":
                answer = _ask_files(files, read=read, write=write, trail=form)
                if answer is not BACK:
                    files = answer
                continue

            if not text and not files:
                write("Type a message or attach a file first.")
                continue

            args = _namespace(
                command="send",
                chat=picked.reference,
                topic=None if topic_info is None else topic_info.id,
                text=text,
                files=files or None,
                # The menu is never the shorter path past a gate: the preview and
                # its y/N run exactly as they do for the flags.
                yes=False,
            )
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=form)
            if result is not STAY:
                return result is not EXIT
            continue


# Running a create again would make a second, identical object, so the row after
# one is "another", back at the kind list: a new thing gets a new name anyway.
CREATE_ANOTHER = (STAY, "Create another")

CREATE_KINDS = (
    ("group", False, "Group"),
    ("group", True, "Forum group (a group with topics)"),
    ("channel", False, "Broadcast channel"),
    ("topic", False, "Topic in a forum group"),
)


async def _flow_create(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Create")
    while True:
        choice = choose([label for _kind, _forum, label in CREATE_KINDS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        kind, forum, label = CREATE_KINDS[choice]

        if kind == "topic":
            picked = await _pick_chat(session=session, read=read, write=write, forums_only=True, trail=crumb(trail, "Topic"))
            if picked is BACK:
                continue
            title = ask_text("Topic name", read=read, write=write)
            if title is BACK:
                continue
            args = _namespace(command="create", create_kind="topic", chat=picked.reference, title=title, yes=False)
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=crumb(trail, "Topic"), rows=(CREATE_ANOTHER,))
            if result is not STAY:
                return result is not EXIT
            continue

        title = ask_text(f"{label} name", read=read, write=write)
        if title is BACK:
            continue
        # Blank cancels out of ask_text, which for an optional description is the
        # same answer as "leave it empty".
        about = ask_text("Description (blank for none)", read=read, write=write)
        args = _namespace(
            command="create",
            create_kind=kind,
            title=title,
            about=None if about is BACK else about,
            forum=forum,
            yes=False,
        )
        result = await _act(args, session=session, runner=runner, read=read, write=write, trail=crumb(trail, label), rows=(CREATE_ANOTHER,))
        if result is not STAY:
            return result is not EXIT


# Deleting twice is never what anyone means -- the thing is gone -- so the row
# after one is "delete something else", back at the scope screen.
DELETE_ANOTHER = (STAY, "Delete something else")


async def _flow_delete(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Delete")
    while True:
        scope = choose(
            ["A group or channel", "A topic in a forum group"], title=trail, read=read, write=write
        )
        if scope is BACK:
            return True

        if scope == 1:
            picked = await _pick_chat(session=session, read=read, write=write, forums_only=True, trail=trail)
            if picked is BACK:
                continue
            topics = await session.topics(picked.reference)
            if not topics:
                # One screen back -- the forum picker -- not all the way out.
                write("That chat has no topics.")
                continue
            chosen = pick(
                topics,
                title=crumb(trail, picked.title, "Pick a topic to delete"),
                label=lambda topic: f"{topic.id:<6}  {topic.display_title}",
                read=read,
                write=write,
            )
            if chosen is BACK:
                continue
            where = crumb(trail, picked.title, chosen.display_title)
            target_title = chosen.title
            dry_run = _namespace(
                command="delete",
                delete_kind="topic",
                chat=picked.reference,
                topic=chosen.id,
                execute=False,
            )
        else:
            picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
            if picked is BACK:
                continue

            if picked.type is None:
                # A typed reference has not been looked up, so the menu asks
                # rather than guesses; the command still checks the answer
                # against what Telegram says before anything is deleted.
                answer = choose(
                    ["Group", "Channel"], title=crumb(trail, picked.title, "Which is it?"), read=read, write=write
                )
                if answer is BACK:
                    continue
                kind = ("group", "channel")[answer]
            else:
                kind = kind_for_type(picked.type)
                if kind is None:
                    write(
                        f"error: {picked.title} is a {picked.type}, which telegram-tools does not delete - "
                        "`create` cannot make one back. Delete it in Telegram itself."
                    )
                    if not after_action(read=read, write=write):
                        return False
                    continue

            where = crumb(trail, picked.title)
            target_title = picked.title
            dry_run = _namespace(
                command="delete", delete_kind=kind, chat=picked.reference, execute=False
            )

        # The dry-run always runs first: the menu must never be a shorter path
        # to a deletion than the flags are.
        if await _call(dry_run, session=session, runner=runner, write=write) is None:
            return after_action(read=read, write=write)

        choice = choose(
            ["Delete it for real - the next screen asks for its exact title"],
            title=crumb(where, "Dry-run done"),
            read=read,
            write=write,
            back_label="Back to what to delete",
        )
        if choice is BACK:
            continue

        for_real = _namespace(**{**vars(dry_run), "execute": True})
        result = await _act(
            for_real,
            session=session,
            runner=runner,
            read=read,
            write=write,
            trail=where,
            rows=(DELETE_ANOTHER,),
        )
        if result is not STAY:
            return result is not EXIT


_ALL_TOPICS_ROW = Extra("every", "All topics (no need to tick)")
DEFAULT_BATCH_SIZE = 100


async def _flow_clear(*, session, runner, read, write) -> bool:
    # What was ticked, per chat, so backing out to the chat picker and coming
    # back does not mean ticking again; which set the last dry-run scanned, so
    # Continue with the same ticks does not scan it all a second time; and the
    # batch size, which is an advanced knob that lives on the dry-run screen.
    ticks: dict[str, list] = {}
    scanned: tuple | None = None
    batch_size = DEFAULT_BATCH_SIZE
    trail = crumb(MAIN, "Clear")
    while True:
        picked = await _pick_chat(session=session, read=read, write=write, forums_only=True, trail=trail)
        if picked is BACK:
            return True
        chat = crumb(trail, picked.title)

        topics = await session.topics(picked.reference)
        if not topics:
            # One screen back -- the forum picker -- not all the way out.
            write("That chat has no topics.")
            continue

        while True:
            selected = pick_many(
                topics,
                title=crumb(chat, "Tick what to clear"),
                label=lambda topic: f"{topic.id:<6}  {topic.display_title}",
                read=read,
                write=write,
                preselected=ticks.get(picked.reference, []),
                extras=(_ALL_TOPICS_ROW,),
            )
            if selected is BACK:
                break

            # The explicit row is the --all-topics flag; ticking every topic by
            # hand still means the same thing, as it always has.
            every_topic = selected == _ALL_TOPICS_ROW.key or len(selected) == len(topics)
            chosen = list(topics) if selected == _ALL_TOPICS_ROW.key else selected
            ticks[picked.reference] = chosen

            dry_run = _namespace(
                command="clear-messages",
                chat=picked.reference,
                topics=None if every_topic else [topic.id for topic in chosen],
                all_topics=every_topic,
                execute=False,
                batch_size=batch_size,
            )

            # The dry-run always runs first: the menu must never be a shorter path to a
            # deletion than the flags are, and the count is what makes the next screen
            # an informed answer.
            key = (picked.reference, tuple(topic.id for topic in chosen))
            if key == scanned:
                write("Same topics as the last dry-run; its count still stands.")
            else:
                if await _call(dry_run, session=session, runner=runner, write=write) is None:
                    return after_action(read=read, write=write)
                scanned = key

            while True:
                choice = choose(
                    ["Clear them for real (asks you to type DELETE)", f"Batch size [{batch_size}]"],
                    title=crumb(chat, "Dry-run done"),
                    read=read,
                    write=write,
                    back_label="Back to the topic list",
                )
                if choice is BACK:
                    # The ticks survive the trip back: they are in `ticks`.
                    break
                if choice == 1:
                    answer = ask_int("Messages per delete call", read=read, write=write, current=batch_size)
                    if answer is not BACK:
                        batch_size = answer
                    continue

                for_real = _namespace(
                    **{
                        **vars(dry_run),
                        "execute": True,
                        "batch_size": batch_size,
                        "topics": list(dry_run.topics) if dry_run.topics is not None else None,
                    }
                )
                result = await _act(for_real, session=session, runner=runner, read=read, write=write, trail=chat, rows=((STAY, "Clear more topics"),))
                if result is not STAY:
                    return result is not EXIT
                # Those topics are empty now: the ticks and the scan are stale.
                ticks.pop(picked.reference, None)
                scanned = None
                break


BOT_FIELDS = (
    ("name", "Name", False, False),
    ("bio", "Bio", True, False),
    ("description", "Description", True, False),
    ("commands", "Commands", True, True),
    ("photo", "Profile photo", True, True),
    ("group_rights", "Group rights", True, True),
    ("channel_rights", "Channel rights", True, True),
)

_NEEDS_TOKEN = "Set TELEGRAM_BOT_TOKENS=nickname:token in ~/.telegram-tools/.env to change this."

_BOTS_DEFAULTS = {
    "command": "bots",
    "bot": None,
    "json_output": None,
    "name": None,
    "bio": None,
    "description": None,
    "commands": None,
    "clear_commands": False,
    "photo": None,
    "remove_photo": False,
    "group_rights": None,
    "channel_rights": None,
    "yes": False,
}


def _bots_namespace(**overrides) -> argparse.Namespace:
    """A bots namespace with every flag defaulted, so no field is ever missing."""
    return _namespace(**{**_BOTS_DEFAULTS, **overrides})


def _current_bot_value(profile, key: str) -> str:
    if key == "commands":
        return ", ".join(f"/{command.command}" for command in profile.commands) or "(none)"
    if key == "photo":
        return "set" if profile.has_photo else "not set"
    if key in ("group_rights", "channel_rights"):
        return ", ".join(getattr(profile, key)) or "(none)"
    return _shown(getattr(profile, key), "(not set)")


def _staged_bot_value(key: str, staged: dict) -> str | None:
    """How a staged edit reads on the field list, or None when nothing is staged."""
    if key == "commands":
        if staged.get("clear_commands"):
            return "(cleared)"
        return staged.get("commands")
    if key == "photo":
        if staged.get("remove_photo"):
            return "(cleared)"
        return staged.get("photo")
    value = staged.get(key)
    if value is None:
        return None
    if key in ("group_rights", "channel_rights"):
        return "(cleared)" if value == "none" else value
    if value == "":
        return "(cleared)"
    return value


def _bot_field_is_set(profile, key: str) -> bool:
    """Whether a bot field has a current value -- the thing keep/clear would act on.

    Mirrors `_current_bot_value`'s notion of empty rather than string-matching its
    display text: a name always has one (Telegram requires it), an unset photo and
    empty command/rights lists are not strings at all, and bio/description treat ""
    the same as None.
    """
    if key == "name":
        return True
    if key == "commands":
        return bool(profile.commands)
    if key == "photo":
        return profile.has_photo
    if key in ("group_rights", "channel_rights"):
        return bool(getattr(profile, key))
    return getattr(profile, key) not in (None, "")


def _ask_rights(title: str, current: list[str], *, read, write) -> Any:
    """`title` is the full crumb: this screen is one step below a field."""
    names = [name for name in right_names() if name != IMPLICIT_OTHER_RIGHT]
    chosen = pick_many(
        names,
        title=title,
        label=str,
        read=read,
        write=write,
        preselected=[name for name in names if name in current],
    )
    if chosen is BACK:
        return BACK
    return ",".join(chosen)


async def _flow_bot_edit(profile, *, session, runner, read, write, trail: str) -> Any:
    """An after-run answer once an edit is applied; BACK when the field list is
    backed out of untouched, so the caller can redisplay the bot's own screen
    instead of bubbling all the way up to the root menu."""
    token = lookup_bot_token(session.config.bot_tokens, profile.id)
    staged: dict[str, Any] = {}
    edit = crumb(trail, "Edit")

    while True:
        rows: list[tuple[str, str]] = []
        for key, title, _allow_clear, needs_token in BOT_FIELDS:
            current = _current_bot_value(profile, key)
            pending = _staged_bot_value(key, staged)
            value = current if pending is None else f"{current} -> {pending}"
            if needs_token and token is None:
                # Photo is the odd one: only clearing it needs the token, setting
                # one does not, so its row says so instead of the blanket message.
                gate = "  (clearing needs this bot's token)" if key == "photo" else "  (needs this bot's token)"
            else:
                gate = ""
            rows.append((key, f"{title:<16} [{value}]{gate}"))
        rows.append(("apply", "Review & apply"))

        choice = choose([label for _key, label in rows], title=edit, read=read, write=write, back_label="Back (discards)")

        if choice is BACK:
            if staged and not _confirm_discard(edit, title=_staged_changes(len(staged)), said=f"Discarded {_staged_changes(len(staged))}.", read=read, write=write):
                continue
            return BACK

        key = rows[choice][0]

        if key == "apply":
            if not staged:
                write("Nothing staged yet.")
                continue
            args = _bots_namespace(bot=str(profile.id), **staged)
            return await _act(args, session=session, runner=runner, read=read, write=write, trail=edit, rows=((STAY, "Edit more"),))

        field = next(entry for entry in BOT_FIELDS if entry[0] == key)
        _key, title, allow_clear, needs_token = field
        if needs_token and token is None:
            # Photo is the odd one: setting it runs on the user session, only
            # removing it needs the token, so it is refused only for clearing.
            if key != "photo":
                write(f"{title} can only be changed with that bot's token. {_NEEDS_TOKEN}")
                continue

        if key in ("group_rights", "channel_rights"):
            ask = lambda: _ask_rights(crumb(edit, title), getattr(profile, key), read=read, write=write)
        elif key in ("commands", "photo"):
            ask = lambda: ask_text(f"{title} file path", read=read, write=write)
        else:
            ask = lambda: ask_text(title, read=read, write=write)

        answer = edit_field(
            crumb(edit, title),
            _current_bot_value(profile, key),
            read=read,
            write=write,
            ask=ask,
            allow_clear=allow_clear and not (needs_token and token is None),
            is_set=_bot_field_is_set(profile, key),
        )
        if answer is BACK:
            continue

        if answer is CLEAR:
            if key == "commands":
                staged["clear_commands"] = True
                staged.pop("commands", None)
            elif key == "photo":
                staged["remove_photo"] = True
                staged.pop("photo", None)
            elif key in ("group_rights", "channel_rights"):
                staged[key] = "none"
            else:
                staged[key] = ""
            continue

        staged[key] = answer
        if key == "commands":
            staged.pop("clear_commands", None)
        if key == "photo":
            staged.pop("remove_photo", None)


_TYPE_A_BOT = Extra("typed", "Type a bot @username, ID or nickname")
_SAVE_BOT_LIST = Extra("save", "Save the bot list to a JSON file")


def _bot_label(bot) -> str:
    return f"{'@' + bot.username if bot.username else '(no username)'}  {bot.name}"


def _pick_bot(bots, *, read, write, trail: str) -> Any:
    """A bot from the list, an extra's key, or BACK.

    The list only ever holds bots you own. A bot you do not own can still be
    looked at, read-only, by typing it -- so that row is there even when the
    list is empty, instead of a dead end.
    """
    if bots:
        return pick(bots, title=trail, label=_bot_label, read=read, write=write, extras=(_SAVE_BOT_LIST, _TYPE_A_BOT))
    write("No bots of your own. One you do not own can still be looked up, read-only.")
    choice = choose([_TYPE_A_BOT.label], title=trail, read=read, write=write)
    return BACK if choice is BACK else _TYPE_A_BOT.key


async def _flow_bots(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "My bots")
    while True:
        chosen = _pick_bot(await session.bots(), read=read, write=write, trail=trail)
        if chosen is BACK:
            return True

        if chosen == _SAVE_BOT_LIST.key:
            path = ask_text("JSON file path", read=read, write=write)
            if path is BACK:
                continue
            args = _bots_namespace(json_output=path)
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, rows=((STAY, "Back to the bot list"),))
            if result is not STAY:
                return result is not EXIT
            continue

        if chosen == _TYPE_A_BOT.key:
            typed = ask_text("Bot @username, ID or nickname", read=read, write=write)
            if typed is BACK:
                continue
            # Same resolution as `bots --bot`: a TELEGRAM_BOT_TOKENS nickname
            # becomes its token's bot id, anything else goes to Telegram as typed.
            _token, reference = resolve_bot_token(session.config.bot_tokens, typed)
            reference = str(reference)
        else:
            reference = str(chosen.id)

        try:
            profile = await session.bot_profile(reference)
        except MENU_ERRORS as exc:
            # A typo in a typed username is this screen to redo, not a reason
            # to drop to the root menu.
            write(f"error: {exc}")
            continue

        result = await _flow_bot_screen(profile, session=session, runner=runner, read=read, write=write, trail=trail)
        if result is BACK:
            continue
        return result is not EXIT


async def _flow_bot_screen(profile, *, session, runner, read, write, trail: str) -> Any:
    """One bot: its profile, then edit it or save it. BACK returns to the bot
    list; anything else is an after-run answer for the caller."""
    # Printed here rather than through run(): the edit screen needs these values
    # anyway, and fetching the same profile twice to print it would be two more
    # API calls for the same text. Every edit still goes through run().
    # The profile itself says "not owned by you - read-only" when that is so; the
    # missing Edit row below is the same fact.
    write(format_bot_profile(profile))

    bot = crumb(trail, f"@{profile.username}" if profile.username else f"bot {profile.id}")
    while True:
        rows = [("save", "Save this profile to a JSON file")]
        if profile.is_owned:
            rows.insert(0, ("edit", "Edit this bot"))
        choice = choose([label for _key, label in rows], title=bot, read=read, write=write)
        if choice is BACK:
            return BACK

        if rows[choice][0] == "save":
            path = ask_text("JSON file path", read=read, write=write)
            if path is BACK:
                continue
            args = _bots_namespace(bot=str(profile.id), json_output=path)
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=bot, rows=((STAY, "Back to this bot"),))
            if result is not STAY:
                return result
            continue

        result = await _flow_bot_edit(profile, session=session, runner=runner, read=read, write=write, trail=bot)
        while result is STAY:
            # Edit more: the profile just changed, so fetch it again before the
            # field list shows current values.
            profile = await session.bot_profile(str(profile.id))
            result = await _flow_bot_edit(profile, session=session, runner=runner, read=read, write=write, trail=bot)
        if result is BACK:
            continue
        return result


async def run_menu(*, read=None, write=None, session=None, runner=None) -> int:
    """The looping menu. Returns 0 on a normal exit.

    The exit code belongs to the session, not to any one action inside it: a
    session can run a dozen actions and there is no honest way to fold their
    codes into one number.

    Colour is applied here and nowhere else: the default read and write paint
    what the prompts hand them, so a caller that injects its own (every test)
    gets plain text.
    """
    read = ui.reader() if read is None else read
    write = ui.writer() if write is None else write
    session = session if session is not None else MenuSession()
    runner = runner if runner is not None else cli.run
    flows = (
        _flow_discover,
        _flow_search,
        _flow_send,
        _flow_create,
        _flow_delete,
        _flow_clear,
        _flow_bots,
        _flow_doctor,
    )

    try:
        while True:
            choice = choose(list(ROOT_ITEMS), title=ROOT_TITLE, read=read, write=write, back_label="Exit")
            if choice is BACK:
                return 0
            try:
                keep_going = await flows[choice](session=session, runner=runner, read=read, write=write)
            except MENU_ERRORS as exc:
                # A picker's own fetch can fail too: a flood-wait, an expired
                # session, a chat that vanished. The menu says so and stays open.
                write(f"error: {exc}")
                keep_going = after_action(read=read, write=write)
            if not keep_going:
                return 0
    finally:
        await session.close()
