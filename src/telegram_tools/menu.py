from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon.errors import ChannelForumMissingError, RPCError

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools.bots import IMPLICIT_OTHER_RIGHT, format_bot_profile, get_bot_profile, list_bots, resolve_bot, right_names
from telegram_tools.client import SessionInUseError, create_client, start_client
from telegram_tools._core.columns import cell
from telegram_tools._core.identity import banner as identity_banner
from telegram_tools.adapters import AccountIdentity
from telegram_tools.adapters.archive import scope_rid_for
from telegram_tools import profiles as profile_store
from telegram_tools.config import ConfigError, load_config, lookup_bot_token, resolve_bot_token
from telegram_tools.delete import kind_for_type
from telegram_tools.discovery import list_dialog_choices
from telegram_tools.records import parse_date_bound
from telegram_tools import messages as message_ops
from telegram_tools.prompts import BACK, CLEAR, EXIT, MENU, RULE, Extra, after_action, after_run, ask_int, ask_lines, ask_text, choose, edit_field, pick, pick_many
from telegram_tools.resolver import resolve_chat
from telegram_tools import review as review_ops
from telegram_tools import structure as structure_ops
from telegram_tools import manage as manage_ops
from telegram_tools.topics import get_forum_topics
from telegram_tools import ui
from telegram_tools.ui import crumb

# What the menu turns into a printed line instead of an exit. EntityResolutionError
# is a ValueError and PermissionError is an OSError, so both are already covered;
# anything not named here is a bug and should still be loud.
MENU_ERRORS = (ConfigError, SessionInUseError, ValueError, OSError, RPCError)

ROOT_TITLE = "telegram-tools"
MAIN = "Main"
# The nine-row root of spec section 14, landed once so nobody learns new numbers
# twice. Row 7 still waits for rules and the runner; it keeps its number now so
# that every other row keeps its own when those arrive. A row's hint names what
# is behind it *today* -- a hint that promises something the row cannot do is
# worse than no hint.
ROOT_ITEMS = (
    "Find IDs (chats, topics)",
    "Read (search live, archive, export)",
    "Write (send, reply, message tools)",
    "Build (create, delete, structure)",
    "Clear messages",
    "Manage (admins, members, invites, settings)",
    "Watch (rules, runner, review queue)",
    "Identity (profiles, my bots)",
    "Check setup",
)


class MenuSession:
    """One Telegram connection and its caches, for the life of one menu run.

    Everything is lazy: the menu itself opens without credentials, and `doctor`
    never needs any. The caches are never refreshed — restarting the tool is the
    refresh.
    """

    def __init__(self, config=None, profile: str | None = None) -> None:
        self._config = config
        self._profile = profile
        self._client = None
        self._chats: list[Any] | None = None
        self._bots: list[Any] | None = None
        # The `Acting as:` line every screen carries once there is a connection
        # to learn it from. None until then, which is what keeps a bare
        # `telegram-tools` from needing a login to show its root.
        self.banner: str | None = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config(profile=self._profile)
        return self._config

    async def client(self):
        if self._client is None:
            self._client = await start_client(create_client(self.config))
            provider = await AccountIdentity.open(self._client, getattr(self.config, "profile", "default"))
            self.banner = identity_banner(provider.identity())
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

    def archive_scopes(self) -> list[tuple[str, str]]:
        """The scopes the local archive holds, for its pickers. Reads the file; opens no connection."""
        return archive_store.list_scopes()

    def review_candidates(self, states: tuple[str, ...]) -> list[tuple[str, str]]:
        """Candidates in any of `states` as `(manifest id, label)`, for the review pickers.
        A query over the archive; no host is contacted and nothing is fetched."""
        if not archive_store.archive_exists():
            return []
        with archive_store.open_archive() as archive:
            rows = review_ops.queue_for(archive).list()
        return [(row.manifest_id, _candidate_label(row)) for row in rows if row.state in states]

    def structure_applies(self) -> list[tuple[str, str]]:
        """Every apply the archive holds remap rows for, newest first, as `(apply id, label)`.
        Reads the file; opens no connection."""
        if not archive_store.archive_exists():
            return []
        with archive_store.open_archive() as archive:
            rows = structure_ops.latest_apply_ids(archive)
        return [(apply_id, f"{apply_id}  {created}  blueprint {blueprint_hash}") for apply_id, created, blueprint_hash in rows]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def release(self) -> None:
        """Drop the connection and everything learned through it.

        `auth` opens its own client, and one session file is one connection, so
        logging in or out from the menu has to hand the file back first. The
        caches go with it: after a login the account may not be the same one.
        """
        await self.close()
        self._config = None
        self._chats = None
        self._bots = None
        self.banner = None


def _namespace(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


async def _call(args, *, session, runner, write, connect: bool = True) -> int | None:
    """Run one action. Returns its exit code, or None when it errored and the
    message is already printed.

    `connect=False` runs it against a client of its own: `auth` writes the very
    session file the menu is holding open, and two clients on one file is the
    lock error this menu exists to avoid.
    """
    try:
        client = await session.client() if session is not None and connect else None
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


async def _act(args, *, session, runner, read, write, trail: str = MAIN, rows=(RUN_AGAIN, TWEAK), connect: bool = True) -> Any:
    """Run one action, then the after-run screen. Returns STAY, MENU or EXIT.

    The title says what happened: Done on exit code 0, Not done when a confirm
    was declined (the CLI returns 1), Failed after a printed error.
    """
    while True:
        code = await _call(args, session=session, runner=runner, write=write, connect=connect)
        outcome = "Done" if code == 0 else ("Failed" if code is None else "Not done")
        result = after_run(read=read, write=write, title=crumb(trail, outcome), rows=rows)
        if result is not AGAIN:
            return result


def _leave(result: Any) -> Any:
    """What a flow hands its group screen after an after-run answer.

    False ends the session, MENU means the person asked for the main menu and
    the group screen must not catch them on the way, and anything else --
    BACK, a row key -- returns to the group screen. Truthy either way, which is
    what the root loop reads.
    """
    if result is EXIT:
        return False
    return MENU if result is MENU else True


def _leave_action(keep_going: bool) -> Any:
    """`after_action`'s answer as a flow hands it on: Enter is the main menu, 0 is exit."""
    return MENU if keep_going else False


async def _group(flow, *, session, runner, read, write) -> Any:
    """Run one row of a group screen; True to redraw the group, MENU to go to the root, False to exit."""
    outcome = await flow(session=session, runner=runner, read=read, write=write)
    if outcome is False:
        return False
    return MENU if outcome is MENU else True


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
            return _leave(result)


async def _flow_doctor(*, session, runner, read, write) -> bool:
    # No session: doctor never opens a connection, which is the point of it. And
    # no after-run screen: running doctor again tells you nothing new.
    profile = getattr(session.config, "profile", None) if session is not None else None
    await _call(_namespace(command="doctor", profile=profile), session=None, runner=runner, write=write)
    return _leave_action(after_action(read=read, write=write))


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


# The five formats `search --format` and `archive export --format` take, in
# the order the flags list them: json and csv first, as they always were.
EXPORT_FORMAT_ROWS = (
    ("json", "JSON"),
    ("csv", "CSV"),
    ("jsonl", "JSON lines (one record per line)"),
    ("markdown", "Markdown"),
    ("html", "HTML (one self-contained page)"),
)


async def _flow_search(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Read", "Search")
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
                    fmt = choose([label for _key, label in EXPORT_FORMAT_ROWS], title=crumb(form, "Format"), read=read, write=write)
                    if fmt is BACK:
                        continue
                    output_format = EXPORT_FORMAT_ROWS[fmt][0]

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
                    return _leave(result)
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
                    ask=lambda: (ask_date if key in ("since", "until") else ask_text)(title, read=read, write=write),
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
    trail = crumb(MAIN, "Write", "Send")
    while True:
        picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
        if picked is BACK:
            return True

        form = crumb(trail, picked.title)
        topic_info = None
        text: str | None = None
        files: list[str] = []
        reply_to: int | None = None
        while True:
            rows: list[tuple[str, str]] = []
            if picked.is_forum is not False:
                topic_shown = "(the chat itself)" if topic_info is None else f"{topic_info.id} {topic_info.title}"
                rows.append(("topic", f"Topic     [{topic_shown}]"))
            rows.extend(
                [
                    ("text", f"Message   [{_preview_line(text)}]"),
                    ("files", f"Files     [{_files_label(files)}]"),
                    ("reply_to", f"Reply to  [{_shown(reply_to, '(nothing - a new message)')}]"),
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
                staged = text or files or topic_info is not None or reply_to is not None
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

            if key == "reply_to":
                answer = edit_field(
                    crumb(form, "Reply to"),
                    str(reply_to),
                    read=read,
                    write=write,
                    ask=lambda: ask_int("Message id to reply to", read=read, write=write),
                    allow_clear=True,
                    is_set=reply_to is not None,
                )
                if answer is CLEAR:
                    reply_to = None
                elif answer is not BACK:
                    reply_to = answer
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
                reply_to=reply_to,
                # The menu is never the shorter path past a gate: the preview and
                # its y/N run exactly as they do for the flags.
                yes=False,
            )
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=form)
            if result is not STAY:
                return _leave(result)
            continue


# -- the message verbs -----------------------------------------------------

# What each verb's form stages, in row order: (namespace key, row label, kind).
# Kinds: int, text, lines (a body ended by `.`), ids (comma-separated ids),
# chat (a second chat picker), topic (a topic in the picked chat), toggle,
# options (poll answers, one per line). The chat itself is always picked first,
# and the last row is always the run.
MESSAGE_FORMS = {
    "reply": (("message_id", "Reply to message", "int"), ("text", "Reply", "lines")),
    "edit": (("message_id", "Message", "int"), ("text", "New text", "lines")),
    "delete": (
        ("ids", "Message ids", "ids"),
        ("from_search", "Archive query (selects every match here)", "text"),
        ("limit", "Limit", "int"),
        ("i_know", "Allow more than 1000", "toggle"),
        ("execute", "Delete for real (asks you to type DELETE)", "toggle"),
    ),
    "forward": (
        ("ids", "Message ids", "ids"),
        ("from_search", "Archive query (selects every match here)", "text"),
        ("limit", "Limit", "int"),
        ("i_know", "Allow more than 1000", "toggle"),
        ("to_chat", "Send them to", "chat"),
        ("to_topic", "Topic there", "int"),
    ),
    "copy": (
        ("ids", "Message ids", "ids"),
        ("from_search", "Archive query (selects every match here)", "text"),
        ("limit", "Limit", "int"),
        ("i_know", "Allow more than 1000", "toggle"),
        ("to_chat", "Copy them to", "chat"),
        ("to_topic", "Topic there", "int"),
    ),
    "react": (("message_id", "Message", "int"), ("emoji", "Emoji", "text")),
    "unreact": (("message_id", "Message", "int"), ("emoji", "Emoji (none = every reaction of yours)", "text")),
    "pin": (("message_id", "Message", "int"),),
    "unpin": (("message_id", "Message", "int"),),
    "poll": (
        ("topic", "Topic", "topic"),
        ("question", "Question", "text"),
        ("options", "Answers", "options"),
        ("multiple", "Several answers allowed", "toggle"),
    ),
    "typing": (("seconds", "Seconds", "int"),),
    "read": (),
    "unread": (),
    "bookmark": (("message_id", "Message", "int"), ("label", "Label", "text")),
    "draft": (("topic", "Topic", "topic"), ("text", "Draft", "lines")),
}
# What has to be staged before the run row does anything.
MESSAGE_REQUIRED = {
    "reply": ("message_id", "text"),
    "edit": ("message_id", "text"),
    "delete": (),
    "forward": ("to_chat",),
    "copy": ("to_chat",),
    "react": ("message_id", "emoji"),
    "unreact": ("message_id",),
    "pin": ("message_id",),
    "unpin": ("message_id",),
    "poll": ("question", "options"),
    "typing": (),
    "read": (),
    "unread": (),
    "bookmark": ("message_id",),
    "draft": ("text",),
}
MESSAGE_RUN_ROW = {
    "delete": "Run it (dry-run unless 'Delete for real' is on)",
}
MESSAGE_TITLES = {
    "reply": "Reply",
    "edit": "Edit",
    "delete": "Delete messages",
    "forward": "Forward",
    "copy": "Copy",
    "react": "React",
    "unreact": "Remove a reaction",
    "pin": "Pin",
    "unpin": "Unpin",
    "poll": "Poll",
    "typing": "Typing",
    "read": "Mark read",
    "unread": "Mark unread",
    "bookmark": "Bookmark",
    "draft": "Draft",
}


def _staged_label(kind: str, value: Any) -> str:
    if kind == "toggle":
        return "yes" if value else "no"
    if value in (None, "", [], ()):
        return "(none)" if kind not in ("topic",) else "(the chat itself)"
    if kind == "lines":
        return _preview_line(value)
    if kind == "ids":
        return ", ".join(str(number) for number in value)
    if kind == "options":
        return " / ".join(value)
    if kind == "chat":
        return value.title
    if kind == "topic":
        return f"{value.id} {value.title}"
    return str(value)


async def _ask_message_field(key: str, label: str, kind: str, current: Any, *, picked, session, read, write, trail: str) -> Any:
    """The new value for one row, CLEAR to empty it, or BACK to leave it alone."""
    if kind == "toggle":
        return not current
    if kind == "int":
        return ask_int(label, read=read, write=write, current=current)
    if kind == "text":
        answer = ask_text(label, read=read, write=write, current=current or None)
        return answer
    if kind == "lines":
        return ask_lines(label, read=read, write=write, current=_preview_line(current) if current else None)
    if kind == "ids":
        typed = ask_text("Message ids, comma-separated", read=read, write=write, current=_staged_label(kind, current) if current else None)
        if typed is BACK:
            return BACK
        try:
            return message_ops.parse_ids([typed])
        except ValueError as exc:
            write(str(exc))
            return BACK
    if kind == "options":
        body = ask_lines("Answers, one per line", read=read, write=write)
        if body is BACK:
            return BACK
        return [line.strip() for line in body.split("\n") if line.strip()]
    if kind == "chat":
        chosen = await _pick_chat(session=session, read=read, write=write, trail=trail)
        return chosen
    if kind == "topic":
        answer = await _ask_send_topic(picked, session=session, read=read, write=write, trail=trail)
        return answer
    raise ValueError(kind)


def _flow_message(verb: str):
    """One row under Write: pick the chat, stage the verb's fields, run it behind its gate."""
    fields = MESSAGE_FORMS[verb]
    title = MESSAGE_TITLES[verb]

    async def flow(*, session, runner, read, write) -> bool:
        trail = crumb(MAIN, "Write", title)
        while True:
            picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
            if picked is BACK:
                return True
            form = crumb(trail, picked.title)
            staged: dict[str, Any] = {key: (False if kind == "toggle" else None) for key, _label, kind in fields}
            if verb == "typing":
                staged["seconds"] = 5
            while True:
                rows = [(key, f"{label:<12} [{_staged_label(kind, staged[key])}]") for key, label, kind in fields]
                rows.append(("run", MESSAGE_RUN_ROW.get(verb, "Do it (shows the preview, then asks)")))
                choice = choose([label for _key, label in rows], title=form, read=read, write=write, back_label="Back (discards)")
                if choice is BACK:
                    dirty = any(staged[key] not in (None, False, 5 if verb == "typing" else None) for key, _l, _k in fields)
                    if dirty and not _confirm_discard(form, title="Unfinished form", said="Discarded it.", read=read, write=write):
                        continue
                    break
                key = rows[choice][0]
                if key != "run":
                    _key, label, kind = fields[choice]
                    answer = await _ask_message_field(
                        key, label, kind, staged[key], picked=picked, session=session, read=read, write=write, trail=form
                    )
                    if answer is BACK:
                        continue
                    staged[key] = None if answer is CLEAR else answer
                    continue

                missing = [label for key, label, _kind in fields if key in MESSAGE_REQUIRED[verb] and staged[key] in (None, "", [])]
                if verb in message_ops.BULK_VERBS and not staged.get("ids") and not staged.get("from_search"):
                    missing.append("Message ids or an archive query")
                if missing:
                    write("Fill in first: " + ", ".join(missing) + ".")
                    continue

                values = dict(staged)
                if "to_chat" in values and values["to_chat"] is not None:
                    values["to_chat"] = values["to_chat"].reference
                if "topic" in values and values["topic"] is not None:
                    values["topic"] = values["topic"].id
                if "ids" in values:
                    values["ids"] = [",".join(str(number) for number in values["ids"])] if values["ids"] else None
                if "label" in values:
                    values["label"] = values["label"] or ""
                # The menu is never the shorter path past a gate: no verb here
                # sets yes, and delete's execute is the person's own toggle.
                args = _namespace(command="message", message_verb=verb, chat=picked.reference, yes=False, **values)
                result = await _act(args, session=session, runner=runner, read=read, write=write, trail=form)
                if result is not STAY:
                    return _leave(result)
                continue

    return flow


WRITE_ROWS = (
    ("Send a message", _flow_send),
    ("Reply to a message", _flow_message("reply")),
    ("Edit a message", _flow_message("edit")),
    ("Delete messages (dry-run first)", _flow_message("delete")),
    ("Forward messages", _flow_message("forward")),
    ("Copy messages (text and links, never the bytes)", _flow_message("copy")),
    ("React to a message", _flow_message("react")),
    ("Remove a reaction", _flow_message("unreact")),
    ("Pin a message", _flow_message("pin")),
    ("Unpin a message", _flow_message("unpin")),
    ("Post a poll", _flow_message("poll")),
    ("Show typing", _flow_message("typing")),
    ("Mark a chat read", _flow_message("read")),
    ("Mark a chat unread", _flow_message("unread")),
    ("Bookmark a message (Saved Messages)", _flow_message("bookmark")),
    ("Save a draft", _flow_message("draft")),
)


async def _flow_write(*, session, runner, read, write) -> bool:
    """Row 3. Send, and every message verb."""
    trail = crumb(MAIN, "Write")
    while True:
        choice = choose([label for label, _flow in WRITE_ROWS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        outcome = await _group(WRITE_ROWS[choice][1], session=session, runner=runner, read=read, write=write)
        if outcome is not True:
            return outcome


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
                return _leave(result)
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
            return _leave(result)


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
            return _leave_action(after_action(read=read, write=write))

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
            return _leave(result)


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
                    return _leave_action(after_action(read=read, write=write))
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
                    return _leave(result)
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
                return _leave(result)
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
        return _leave(result)


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


# -- the archive -----------------------------------------------------------

_TYPE_A_RID = Extra("typed", "Type a rid (tg:chat:ID or tg:topic:ID:TOPIC)")


def _scope_label(scope: tuple[str, str]) -> str:
    rid, title = scope
    return f"{cell(title or '(untitled)', 28)}  {rid}"


def _pick_scope(session, *, read, write, trail: str) -> Any:
    """A scope rid from the archive's own list, or one typed; BACK to leave it alone.

    The archive knows what it holds, so its picker is its scopes table; a rid
    it does not hold yet can still be typed, which is how the first sync of a
    single chat is asked for.
    """
    scopes = session.archive_scopes()
    if scopes:
        chosen = pick(scopes, title=trail, label=_scope_label, read=read, write=write, extras=(_TYPE_A_RID,))
        if chosen is BACK:
            return BACK
        if chosen != _TYPE_A_RID.key:
            return chosen[0]
    else:
        write("The archive holds no scopes yet.")
        choice = choose([_TYPE_A_RID.label], title=trail, read=read, write=write)
        if choice is BACK:
            return BACK
    return ask_text("Scope rid", read=read, write=write)


async def _pick_live_scope(session, *, read, write, trail: str) -> Any:
    """A scope rid from the account's own chats and topics -- what a sync can reach.

    A sync archives what Telegram has, not what the archive already holds, so
    its picker is the live chat list: a chat, and for a forum group one of its
    topics or the whole group. The archive's own list is the right picker for
    search, retention and forget, which act on what has already landed.
    """
    picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
    if picked is BACK:
        return BACK
    if picked.is_forum is False:
        return scope_rid_for(picked.reference)
    topics = await session.topics(picked.reference)
    if not topics:
        return scope_rid_for(picked.reference)
    chosen = pick(
        topics,
        title=crumb(trail, picked.title, "Topic"),
        label=lambda topic: f"{topic.id:<6}  {topic.display_title}",
        read=read,
        write=write,
        extras=(Extra("all", "Every topic in this group"),),
    )
    if chosen is BACK:
        return BACK
    if chosen == "all":
        return scope_rid_for(picked.reference)
    return scope_rid_for(picked.reference, chosen.id)


_DATE_HINT = "Dates are YYYY-MM-DD (DD/MM/YYYY is accepted and shown as ISO), e.g. 2026-09-05"


def ask_date(label: str, *, read, write) -> Any:
    """A date as the flags take it: ISO, or a European DD/MM/YYYY turned into ISO.

    Sven typed 05/09/2026 at an "ISO date" prompt (2026-09-06). The flags
    take ISO and the archive would have refused it at the run, one screen too
    late, so the prompt converts the one other shape a person here types and
    asks again for anything else.
    """
    while True:
        typed = ask_text(label, read=read, write=write)
        if typed is BACK:
            return BACK
        value = typed.strip()
        european = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
        if european:
            day, month, year = (int(part) for part in european.groups())
            value = f"{year:04d}-{month:02d}-{day:02d}"
        try:
            parse_date_bound(value, end_of_day=False)
        except ValueError:
            write(_DATE_HINT)
            continue
        return value


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


async def _flow_archive_sync(*, session, runner, read, write) -> bool:
    """Sync: every chat by default, one scope, a date floor, or the walk from the top again."""
    trail = crumb(MAIN, "Read", "Sync the archive")
    staged: dict[str, Any] = {"scope": None, "since": None, "full": False}
    while True:
        rows = [
            ("scope", f"Chat or topic  [{_shown(staged['scope'], '(every chat this account can read; press 1 to pick one)')}]"),
            ("since", f"Since          [{_shown(staged['since'], '(all history)')}]"),
            ("full", f"Start over     [{_yes_no(staged['full'])}]"),
            ("run", "Sync now (one progress line per scope, then the coverage table)"),
        ]
        choice = choose([label for _key, label in rows], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        key = rows[choice][0]

        if key == "run":
            args = _namespace(
                command="archive",
                archive_kind="sync",
                scope=None if staged["scope"] is None else [staged["scope"]],
                since=staged["since"],
                full=staged["full"],
            )
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail)
            if result is not STAY:
                return _leave(result)
            continue

        if key == "full":
            staged["full"] = not staged["full"]
            continue

        if key == "scope":
            if staged["scope"] is None:
                # Nothing set yet: straight to the live picker, as Search does.
                answer = await _pick_live_scope(session, read=read, write=write, trail=crumb(trail, "Chat or topic"))
            else:
                choice = choose(["Pick another chat or topic", "Clear (every chat)"], title=crumb(trail, "Chat or topic"), read=read, write=write)
                if choice is BACK:
                    continue
                answer = CLEAR if choice == 1 else await _pick_live_scope(session, read=read, write=write, trail=crumb(trail, "Chat or topic"))
        else:
            answer = edit_field(
                crumb(trail, "Since"),
                _shown(staged["since"], "(all history)"),
                read=read,
                write=write,
                ask=lambda: ask_date("Since (YYYY-MM-DD)", read=read, write=write),
                allow_clear=True,
                is_set=staged["since"] is not None,
            )
        if answer is BACK:
            continue
        staged[key] = None if answer is CLEAR else answer


async def _flow_archive_status(*, session, runner, read, write) -> bool:
    """Status: one screen, straight back. Reads the file, opens no connection."""
    identity = ask_text("Only this identity (tg:user:ID; blank for all)", read=read, write=write)
    args = _namespace(command="archive", archive_kind="status", identity=None if identity is BACK else identity)
    await _call(args, session=session, runner=runner, write=write, connect=False)
    return _leave_action(after_action(read=read, write=write))


_QUERY_FIELDS = (
    ("query", "Query", "(required)"),
    ("regex", "Regex", "(none)"),
    ("scope", "Chat or topic", "(every one archived)"),
    ("identity", "Identity", "(every identity)"),
    ("author", "From", "(anyone)"),
    ("since", "Since", "(any date)"),
    ("until", "Until", "(any date)"),
    ("context", "Context", "(none)"),
    ("limit", "Limit", "(50)"),
)


async def _flow_archive_query(*, session, runner, read, write) -> bool:
    """Search the archive, or export one search: the same form, two last rows."""
    trail = crumb(MAIN, "Read", "Search the archive")
    staged: dict[str, Any] = {key: None for key, _title, _empty in _QUERY_FIELDS}
    while True:
        rows = [(key, f"{title:<15}[{_shown(staged[key], empty)}]") for key, title, empty in _QUERY_FIELDS]
        rows += [("run", "Search (print here)"), ("export", "Export to a file")]
        choice = choose([label for _key, label in rows], title=trail, read=read, write=write, back_label="Back (discards)")
        if choice is BACK:
            count = sum(1 for value in staged.values() if value is not None)
            if count and not _confirm_discard(trail, title=_staged_changes(count), said=f"Discarded {_staged_changes(count)}.", read=read, write=write):
                continue
            return True
        key = rows[choice][0]

        if key in ("run", "export"):
            if not staged["query"]:
                write("Type a query first.")
                continue
            fields = {
                "query": staged["query"],
                "regex": staged["regex"],
                "scope": None if staged["scope"] is None else [staged["scope"]],
                "identity": staged["identity"],
                "author": staged["author"],
                "since": staged["since"],
                "until": staged["until"],
                "context": staged["context"] or 0,
                "limit": staged["limit"] or 50,
            }
            if key == "export":
                output = ask_text("Output file (a bare name lands in ~/.telegram-tools/exports/)", read=read, write=write)
                if output is BACK:
                    continue
                fmt = choose([label for _key, label in EXPORT_FORMAT_ROWS], title=crumb(trail, "Format"), read=read, write=write)
                if fmt is BACK:
                    continue
                args = _namespace(command="archive", archive_kind="export", format=EXPORT_FORMAT_ROWS[fmt][0], output=output, **fields)
            else:
                args = _namespace(command="archive", archive_kind="search", **fields)
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, connect=False)
            if result is not STAY:
                return _leave(result)
            continue

        title, empty = next((title, empty) for field, title, empty in _QUERY_FIELDS if field == key)
        if key == "scope":
            ask = lambda: _pick_scope(session, read=read, write=write, trail=crumb(trail, title))
        elif key in ("context", "limit"):
            ask = lambda: ask_int(title, read=read, write=write)
        elif key in ("since", "until"):
            ask = lambda: ask_date(title, read=read, write=write)
        else:
            ask = lambda: ask_text(title, read=read, write=write)
        answer = edit_field(
            crumb(trail, title),
            _shown(staged[key], empty),
            read=read,
            write=write,
            ask=ask,
            allow_clear=key != "query",
            is_set=staged[key] is not None,
        )
        if answer is BACK:
            continue
        staged[key] = None if answer is CLEAR else answer


PRUNE_ANOTHER = (STAY, "Prune something else")


async def _flow_archive_retention(*, session, runner, read, write) -> bool:
    """Retention: the scope, the window, a dry-run, then the exact title typed back."""
    trail = crumb(MAIN, "Read", "Prune the archive")
    while True:
        scope = _pick_scope(session, read=read, write=write, trail=crumb(trail, "Scope"))
        if scope is BACK:
            return True
        keep = ask_text("Keep (a window like 90d, or a number of newest messages)", read=read, write=write)
        if keep is BACK:
            continue
        dry_run = _namespace(command="archive", archive_kind="retention", scope=scope, keep=keep, execute=False)
        # The dry-run always runs first: the menu must never be a shorter path
        # past a gate than the flags are.
        if await _call(dry_run, session=session, runner=runner, write=write, connect=False) is None:
            return _leave_action(after_action(read=read, write=write))
        choice = choose(
            ["Prune it for real - the next screen asks for the scope's exact title"],
            title=crumb(trail, "Dry-run done"),
            read=read,
            write=write,
            back_label="Back to the scope list",
        )
        if choice is BACK:
            continue
        for_real = _namespace(**{**vars(dry_run), "execute": True})
        result = await _act(for_real, session=session, runner=runner, read=read, write=write, trail=trail, rows=(PRUNE_ANOTHER,), connect=False)
        if result is not STAY:
            return _leave(result)


async def _flow_archive_forget(*, session, runner, read, write) -> bool:
    """Forget: a scope or an identity, a dry-run, then the exact title typed back."""
    trail = crumb(MAIN, "Read", "Forget")
    while True:
        what = choose(["A scope (one chat or topic)", "An identity (everything it archived)"], title=trail, read=read, write=write)
        if what is BACK:
            return True
        if what == 0:
            scope = _pick_scope(session, read=read, write=write, trail=crumb(trail, "Scope"))
            if scope is BACK:
                continue
            dry_run = _namespace(command="archive", archive_kind="forget", scope=scope, identity=None, execute=False)
        else:
            identity = ask_text("Identity (tg:user:ID)", read=read, write=write)
            if identity is BACK:
                continue
            dry_run = _namespace(command="archive", archive_kind="forget", scope=None, identity=identity, execute=False)
        if await _call(dry_run, session=session, runner=runner, write=write, connect=False) is None:
            return _leave_action(after_action(read=read, write=write))
        choice = choose(
            ["Forget it for real - the next screen asks for its exact title"],
            title=crumb(trail, "Dry-run done"),
            read=read,
            write=write,
            back_label="Back to what to forget",
        )
        if choice is BACK:
            continue
        for_real = _namespace(**{**vars(dry_run), "execute": True})
        result = await _act(for_real, session=session, runner=runner, read=read, write=write, trail=trail, rows=((STAY, "Forget something else"),), connect=False)
        if result is not STAY:
            return _leave(result)


# -- the grouped rows ------------------------------------------------------


READ_ROWS = (
    ("Search live (asks Telegram)", _flow_search),
    ("Sync the archive (everything, or one chat or topic)", _flow_archive_sync),
    ("Archive status", _flow_archive_status),
    ("Search or export the archive", _flow_archive_query),
    ("Prune old rows (retention)", _flow_archive_retention),
    ("Forget a scope or identity", _flow_archive_forget),
)


async def _flow_read(*, session, runner, read, write) -> bool:
    """Row 2. The live search, and everything the local archive does."""
    trail = crumb(MAIN, "Read")
    while True:
        choice = choose([label for label, _flow in READ_ROWS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        outcome = await _group(READ_ROWS[choice][1], session=session, runner=runner, read=read, write=write)
        if outcome is not True:
            return outcome


# -- structure blueprints ------------------------------------------------------

_APPLY_ANOTHER = (STAY, "Apply to another chat")


def _ask_blueprint(read, write) -> Any:
    return ask_text("Blueprint file (written by structure export)", read=read, write=write)


async def _flow_structure_export(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Build", "Export blueprint")
    while True:
        picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
        if picked is BACK:
            return True
        # Blank cancels out of ask_text, which for an optional path is "print it".
        output = ask_text("Write it to (blank prints it here)", read=read, write=write)
        args = _namespace(command="structure", structure_kind="export", chat=picked.reference, output=None if output is BACK else output)
        result = await _act(args, session=session, runner=runner, read=read, write=write, trail=crumb(trail, picked.title), rows=(RUN_AGAIN, (STAY, "Export another chat")))
        if result is not STAY:
            return _leave(result)


async def _flow_structure_diff(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Build", "Diff blueprint")
    while True:
        blueprint = _ask_blueprint(read, write)
        if blueprint is BACK:
            return True
        picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
        if picked is BACK:
            continue
        args = _namespace(command="structure", structure_kind="diff", blueprint=blueprint, chat=picked.reference)
        result = await _act(args, session=session, runner=runner, read=read, write=write, trail=crumb(trail, picked.title), rows=(RUN_AGAIN, (STAY, "Diff another")))
        if result is not STAY:
            return _leave(result)


async def _flow_structure_apply(*, session, runner, read, write) -> bool:
    """Apply a blueprint. The dry-run always runs first, and the exact title is typed
    at the CLI's own prompt: the menu is never a shorter path past that gate."""
    trail = crumb(MAIN, "Build", "Apply blueprint")
    while True:
        blueprint = _ask_blueprint(read, write)
        if blueprint is BACK:
            return True
        where = choose(["An existing chat of the blueprint's kind", "A new chat made from the blueprint"], title=trail, read=read, write=write)
        if where is BACK:
            continue
        if where == 0:
            picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
            if picked is BACK:
                continue
            dry_run = _namespace(command="structure", structure_kind="apply", blueprint=blueprint, chat=picked.reference, create=False, execute=False)
            label = picked.title
        else:
            dry_run = _namespace(command="structure", structure_kind="apply", blueprint=blueprint, chat=None, create=True, execute=False)
            label = "New chat"

        if await _call(dry_run, session=session, runner=runner, write=write) is None:
            return _leave_action(after_action(read=read, write=write))

        choice = choose(
            ["Apply it for real - the next screen asks for the chat's exact title"],
            title=crumb(trail, label, "Dry-run done"),
            read=read,
            write=write,
            back_label="Back to what to apply",
        )
        if choice is BACK:
            continue
        for_real = _namespace(**{**vars(dry_run), "execute": True})
        result = await _act(for_real, session=session, runner=runner, read=read, write=write, trail=crumb(trail, label), rows=(_APPLY_ANOTHER,))
        if result is not STAY:
            return _leave(result)


async def _flow_structure_remap(*, session, runner, read, write) -> bool:
    trail = crumb(MAIN, "Build", "Remap table")
    while True:
        applies = session.structure_applies()
        if applies:
            chosen = pick(applies, title=trail, label=lambda row: row[1], read=read, write=write, extras=(Extra("manual", "Type an apply id"),))
            if chosen is BACK:
                return True
            apply_id = ask_text("Apply id", read=read, write=write) if isinstance(chosen, str) else chosen[0]
        else:
            apply_id = ask_text("Apply id (the archive holds no remap rows yet)", read=read, write=write)
        if apply_id is BACK:
            return True
        args = _namespace(command="structure", structure_kind="remap", apply_id=apply_id)
        result = await _act(args, session=session, runner=runner, read=read, write=write, trail=crumb(trail, apply_id), rows=(RUN_AGAIN, (STAY, "Another apply")), connect=False)
        if result is not STAY:
            return _leave(result)


BUILD_ROWS = (
    ("Create a group, channel, or topic", _flow_create),
    ("Delete a group, channel, or topic", _flow_delete),
    ("Export a chat's blueprint (topics and settings)", _flow_structure_export),
    ("Diff a blueprint against a chat", _flow_structure_diff),
    ("Apply a blueprint (dry-run first, then its exact title)", _flow_structure_apply),
    ("Show the remap table of an apply", _flow_structure_remap),
)


async def _flow_build(*, session, runner, read, write) -> bool:
    """Row 4. What makes, unmakes and copies the shape of a chat, under one roof."""
    trail = crumb(MAIN, "Build")
    while True:
        choice = choose([label for label, _flow in BUILD_ROWS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        outcome = await _group(BUILD_ROWS[choice][1], session=session, runner=runner, read=read, write=write)
        if outcome is not True:
            return outcome


def _candidate_label(row) -> str:
    what = row.url if row.kind == "link" else (row.extra.get("display_name") or row.extra.get("locator") or "")
    verdict = f"  verdict={row.verdict}" if row.verdict else ""
    return f"{row.manifest_id}  {row.kind}  {row.state}  {what}{verdict}"


def _pick_candidates(session, *, states: tuple[str, ...], read, write, trail: str) -> Any:
    """Tick candidates from the queue's own rows; the ids, or BACK."""
    rows = session.review_candidates(states)
    if not rows:
        write(f"No candidate is {' or '.join(states)}.")
        read("Enter = back: ")
        return BACK
    picked = pick_many(rows, title=trail, label=lambda row: row[1], read=read, write=write)
    if picked is BACK or picked == "all" or isinstance(picked, str):
        return BACK
    return [row[0] for row in picked]


async def _flow_review_list(*, session, runner, read, write) -> bool:
    """The queue: an optional kind, an optional state, then the list. Fetches nothing."""
    trail = crumb(MAIN, "Watch", "Review queue")
    staged: dict[str, Any] = {"kind": None, "state": None}
    while True:
        rows = [
            ("kind", f"Kind   [{_shown(staged['kind'], '(links and files)')}]"),
            ("state", f"State  [{_shown(staged['state'], '(every state)')}]"),
            ("run", "Show the queue (asks nothing of any host)"),
        ]
        choice = choose([label for _key, label in rows], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        key = rows[choice][0]
        if key == "run":
            args = _namespace(command="review", review_kind="list", kind=staged["kind"], state=staged["state"])
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, connect=False)
            if result is not STAY:
                return _leave(result)
            continue
        options = review_ops.kinds() if key == "kind" else review_ops.states()
        picked = choose(list(options), title=crumb(trail, "Kind" if key == "kind" else "State"), read=read, write=write, back_label="Any")
        staged[key] = None if picked is BACK else options[picked]


async def _flow_review_approve(*, session, runner, read, write) -> bool:
    """Approve: the CLI's own pick and y/N on this terminal, then the fetch. The
    menu passes no ids and no answer; it is not a shorter path past the gate."""
    trail = crumb(MAIN, "Watch", "Approve downloads")
    args = _namespace(command="review", review_kind="approve", ids=None)
    result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, rows=((STAY, "Approve more"),))
    while result is STAY:
        result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, rows=((STAY, "Approve more"),))
    return _leave(result)


def _flow_review_move(kind: str, *, title: str, states: tuple[str, ...], connect: bool):
    """Accept, reject, retry: tick the candidates, then the CLI shows them and asks."""

    async def flow(*, session, runner, read, write) -> bool:
        trail = crumb(MAIN, "Watch", title)
        while True:
            ids = _pick_candidates(session, states=states, read=read, write=write, trail=trail)
            if ids is BACK:
                return True
            args = _namespace(command="review", review_kind=kind, ids=ids)
            result = await _act(args, session=session, runner=runner, read=read, write=write, trail=trail, rows=((STAY, f"{title} again"),), connect=connect)
            if result is not STAY:
                return _leave(result)

    return flow


async def _flow_review_status(*, session, runner, read, write) -> bool:
    """Status: one screen, straight back. Reads the archive and two directories."""
    args = _namespace(command="review", review_kind="status")
    await _call(args, session=session, runner=runner, write=write, connect=False)
    return _leave_action(after_action(read=read, write=write))


WATCH_ROWS = (
    ("Review queue (what is waiting; fetches nothing)", _flow_review_list),
    ("Approve downloads (pick, y/N, then the fetch runs into quarantine)", _flow_review_approve),
    ("Accept a quarantined download (shows the verdict, then y/N)", _flow_review_move("accept", title="Accept", states=("quarantined",), connect=False)),
    ("Reject a candidate (deletes its quarantined bytes, after y/N)", _flow_review_move("reject", title="Reject", states=("queued", "approved", "fetching", "quarantined", "failed"), connect=False)),
    ("Retry a failed download (from where it stopped)", _flow_review_move("retry", title="Retry", states=("failed",), connect=True)),
    ("Review status (counts, quarantine, scanner)", _flow_review_status),
)


async def _flow_watch(*, session, runner, read, write) -> bool:
    """Row 7. The review queue; rules and the runner join it in a later version."""
    trail = crumb(MAIN, "Watch")
    while True:
        choice = choose([label for label, _flow in WATCH_ROWS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        outcome = await _group(WATCH_ROWS[choice][1], session=session, runner=runner, read=read, write=write)
        if outcome is not True:
            return outcome


# -- row 6: Manage ------------------------------------------------------------

# What each administration verb asks for, after the chat. The verb lands in
# admin_kind, member_kind, join_kind, invite_kind or settings_kind, exactly as
# the flags land it, and every flag of section 13 has its row here.
_PERSON = ("user", "Person (id or @username)", "text")
_UNTIL = ("until", "Until (30m, 2h, 7d, 1w, or a date)", "text")
MANAGE_FORMS = {
    ("admin", "list"): (),
    ("admin", "promote"): (_PERSON, ("rights", "Rights (comma-separated)", "text"), ("rank", "Rank (custom title)", "text")),
    ("admin", "rights"): (_PERSON, ("rights", "Rights (comma-separated)", "text"), ("rank", "Rank (custom title)", "text")),
    ("admin", "demote"): (_PERSON,),
    ("member", "list"): (("query", "Name or username contains", "text"), ("limit", "Limit", "int"), ("banned", "The banned and restricted instead", "toggle")),
    ("member", "ban"): (_PERSON, ("reason", "Reason (kept in the local audit line only)", "text")),
    ("member", "unban"): (_PERSON,),
    ("member", "mute"): (_PERSON, _UNTIL),
    ("member", "unmute"): (_PERSON,),
    ("member", "restrict"): (_PERSON, ("rights", "Rights to take away (comma-separated)", "text"), _UNTIL),
    ("join-requests", "list"): (),
    ("join-requests", "approve"): (_PERSON,),
    ("join-requests", "decline"): (_PERSON,),
    ("invite", "list"): (("revoked", "The revoked ones instead", "toggle"),),
    ("invite", "create"): (
        ("title", "Title (admins see it)", "text"),
        ("expires", "Expires (2h, 7d, or a date; blank = never)", "text"),
        ("usage_limit", "How many may join through it", "int"),
        ("request_needed", "Joining needs an admin's approval", "toggle"),
    ),
    ("invite", "revoke"): (("link", "Link, as invite list printed it", "text"),),
    ("settings", "show"): (),
    ("settings", "set"): (("slow_mode", "Slow mode seconds (0, 10, 30, 60, 300, 900, 3600)", "int"),),
}
MANAGE_REQUIRED = {
    ("admin", "promote"): ("user", "rights"),
    ("admin", "rights"): ("user", "rights"),
    ("admin", "demote"): ("user",),
    ("member", "ban"): ("user",),
    ("member", "unban"): ("user",),
    ("member", "mute"): ("user", "until"),
    ("member", "unmute"): ("user",),
    ("member", "restrict"): ("user", "rights", "until"),
    ("join-requests", "approve"): ("user",),
    ("join-requests", "decline"): ("user",),
    ("invite", "revoke"): ("link",),
    ("settings", "set"): ("slow_mode",),
}
# The two typed_name verbs: the dry-run runs first, and the exact label is typed
# at the CLI's own prompt on the run that follows. The menu never sets execute
# on the first run.
MANAGE_TYPED = {("admin", "demote"), ("member", "ban")}
MANAGE_ROWS = {
    "admin": (
        ("list", "List the creator and admins, with their rights"),
        ("promote", "Promote a member to admin"),
        ("rights", "Change an admin's rights"),
        ("demote", "Demote an admin (dry-run first, then their exact label)"),
    ),
    "member": (
        ("list", "List members (or the banned and restricted)"),
        ("ban", "Ban a person (dry-run first, then their exact label)"),
        ("unban", "Lift a ban"),
        ("mute", "Mute a person until a moment"),
        ("unmute", "Lift a mute or restriction"),
        ("restrict", "Take named rights off a person until a moment"),
    ),
    "join-requests": (("list", "Who is waiting to join"), ("approve", "Let a person in"), ("decline", "Turn a request down")),
    "invite": (("list", "List invite links (shown in full)"), ("create", "Make a new invite link"), ("revoke", "Revoke an invite link")),
    "settings": (("show", "Show slow mode, join approval, default rights"), ("set", "Set slow mode")),
}
MANAGE_GROUPS = (
    ("admin", "Admins", "Admins: list, promote, change rights, demote"),
    ("member", "Members", "Members: list, ban, unban, mute, unmute, restrict"),
    ("join-requests", "Join requests", "Join requests: list, approve, decline"),
    ("invite", "Invite links", "Invite links: list, create, revoke"),
    ("settings", "Chat settings", "Chat settings: show, set slow mode"),
)
_MANAGE_ANOTHER = (STAY, "Another chat")


def _manage_namespace(group: str, verb: str, chat: str, **values) -> argparse.Namespace:
    return _namespace(command=group, chat=chat, **{manage_ops.VERB_DESTS[group]: verb}, **values)


def _flow_manage_verb(group: str, verb: str, title: str):
    """One administration verb: pick the chat, stage its fields, run it behind its own gate."""
    fields = MANAGE_FORMS[(group, verb)]
    required = MANAGE_REQUIRED.get((group, verb), ())
    typed = (group, verb) in MANAGE_TYPED
    group_title = next(name for key, name, _label in MANAGE_GROUPS if key == group)

    async def run_it(values: dict, *, picked, session, runner, read, write, form: str) -> Any:
        if not typed:
            args = _manage_namespace(group, verb, picked.reference, **values)
            return await _act(args, session=session, runner=runner, read=read, write=write, trail=form, rows=(RUN_AGAIN, _MANAGE_ANOTHER))
        dry_run = _manage_namespace(group, verb, picked.reference, execute=False, **values)
        if await _call(dry_run, session=session, runner=runner, write=write) is None:
            return _leave_action(after_action(read=read, write=write))
        choice = choose(
            ["Do it for real - the next screen asks for the person's exact label"],
            title=crumb(form, "Dry-run done"),
            read=read,
            write=write,
            back_label="Back to the form",
        )
        if choice is BACK:
            return STAY
        for_real = _namespace(**{**vars(dry_run), "execute": True})
        return await _act(for_real, session=session, runner=runner, read=read, write=write, trail=form, rows=(_MANAGE_ANOTHER,))

    async def flow(*, session, runner, read, write) -> bool:
        trail = crumb(MAIN, "Manage", group_title, title)
        while True:
            picked = await _pick_chat(session=session, read=read, write=write, trail=trail)
            if picked is BACK:
                return True
            form = crumb(trail, picked.title)
            if not fields:
                result = await run_it({}, picked=picked, session=session, runner=runner, read=read, write=write, form=form)
                if result is not STAY:
                    return _leave(result)
                continue
            staged: dict[str, Any] = {key: (False if kind == "toggle" else None) for key, _label, kind in fields}
            if "limit" in staged:
                staged["limit"] = manage_ops.LIST_LIMIT
            while True:
                rows = [(key, f"{label:<44} [{_staged_label(kind, staged[key])}]") for key, label, kind in fields]
                rows.append(("run", "Run it (dry-run first)" if typed else "Do it (shows the preview, then asks)"))
                choice = choose([label for _key, label in rows], title=form, read=read, write=write, back_label="Back (discards)")
                if choice is BACK:
                    break
                key = rows[choice][0]
                if key != "run":
                    _key, label, kind = fields[choice]
                    if kind == "toggle":
                        staged[key] = not staged[key]
                        continue
                    if kind == "int":
                        answer = ask_int(label, read=read, write=write, current=staged[key])
                    else:
                        answer = ask_text(label, read=read, write=write, current=staged[key] or None)
                    if answer is BACK:
                        continue
                    staged[key] = None if answer is CLEAR else answer
                    continue
                missing = [label for key, label, _kind in fields if key in required and staged[key] in (None, "")]
                if missing:
                    write("Fill in first: " + ", ".join(missing) + ".")
                    continue
                result = await run_it(dict(staged), picked=picked, session=session, runner=runner, read=read, write=write, form=form)
                if result is STAY:
                    break
                return _leave(result)

    return flow


def _flow_manage_group(group: str):
    """One of the five Manage screens: its verbs as rows."""
    group_title = next(name for key, name, _label in MANAGE_GROUPS if key == group)
    rows = tuple((label, _flow_manage_verb(group, verb, label)) for verb, label in MANAGE_ROWS[group])

    async def flow(*, session, runner, read, write) -> bool:
        trail = crumb(MAIN, "Manage", group_title)
        while True:
            choice = choose([label for label, _flow in rows], title=trail, read=read, write=write)
            if choice is BACK:
                return True
            outcome = await _group(rows[choice][1], session=session, runner=runner, read=read, write=write)
            if outcome is not True:
                return outcome

    return flow


MANAGE_FLOWS = tuple((label, _flow_manage_group(key)) for key, _name, label in MANAGE_GROUPS)


async def _flow_manage(*, session, runner, read, write) -> bool:
    """Row 6. Admins, members, join requests, invite links and a chat's settings."""
    trail = crumb(MAIN, "Manage")
    while True:
        choice = choose([label for label, _flow in MANAGE_FLOWS], title=trail, read=read, write=write)
        if choice is BACK:
            return True
        outcome = await _group(MANAGE_FLOWS[choice][1], session=session, runner=runner, read=read, write=write)
        if outcome is not True:
            return outcome


IDENTITY_ROWS = (
    "Profiles on this machine",
    "Log in (phone and code)",
    "Log in by scanning a QR code",
    "Log out",
    "Move the pre-profile session into a profile",
    "My bots",
)


async def _flow_identity(*, session, runner, read, write) -> bool:
    """Row 8. Which login this is, and the bots that login owns.

    Every row but the last runs against a client of its own: `auth` writes the
    session file the menu holds open, and `profiles` needs no connection at all.
    Afterwards the menu drops what it learned, because a login can change who
    the account is.
    """
    trail = crumb(MAIN, "Identity")
    while True:
        choice = choose(list(IDENTITY_ROWS), title=trail, read=read, write=write)
        if choice is BACK:
            return True
        if choice == 5:
            outcome = await _group(_flow_bots, session=session, runner=runner, read=read, write=write)
            if outcome is not True:
                return outcome
            continue

        profile = getattr(session.config, "profile", profile_store.DEFAULT_PROFILE)
        if choice == 0:
            args = _namespace(command="profiles", profile=profile)
            label = "Profiles"
        else:
            args = _namespace(
                command="auth",
                profile=profile,
                qr=choice == 2,
                logout=choice == 3,
                migrate=choice == 4,
            )
            label = IDENTITY_ROWS[choice]

        # `auth` opens its own client on the session file the menu is holding,
        # so the file has to be free first, and afterwards what the menu cached
        # was learned as whoever was logged in before this screen. `profiles`
        # reads the store and opens nothing, so it costs the menu neither.
        if choice != 0:
            await session.release()
        result = await _act(
            args,
            session=session,
            runner=runner,
            read=read,
            write=write,
            trail=crumb(trail, label),
            rows=((STAY, "Back to Identity"),),
            connect=False,
        )
        if choice != 0:
            await session.release()
        if result is not STAY:
            return _leave(result)


# -- the loop --------------------------------------------------------------


def _screen_with_banner(text: str, banner: str | None) -> str:
    """A screen with the acting identity between its title and its rule.

    Section 5.1 puts the line under the trail, and `prompts._screen` is the one
    shape that has one: a title, the rule, then rows. Anything else a command
    printed passes through untouched.
    """
    if banner is None:
        return text
    lines = text.split("\n")
    # Title, rule, rows, and a last row that is always 0. Requiring the 0 as well
    # as the rule is what keeps a command's own output -- which can print a rule
    # of its own on its second line -- from being decorated as a screen.
    if len(lines) < 3 or lines[1] != RULE or not lines[-1].startswith("0. "):
        return text
    return "\n".join([lines[0], banner, *lines[1:]])


async def run_menu(*, read=None, write=None, session=None, runner=None, profile=None) -> int:
    """The looping menu. Returns 0 on a normal exit.

    The exit code belongs to the session, not to any one action inside it: a
    session can run a dozen actions and there is no honest way to fold their
    codes into one number.

    Colour is applied here and nowhere else: the default read and write paint
    what the prompts hand them, so a caller that injects its own (every test)
    gets plain text.
    """
    read = ui.reader() if read is None else read
    painted = ui.writer() if write is None else write
    session = session if session is not None else MenuSession(profile=profile)
    runner = runner if runner is not None else cli.run

    def write(text: Any = "") -> None:
        painted(_screen_with_banner(str(text), session.banner))

    flows = (
        _flow_discover,
        _flow_read,
        _flow_write,
        _flow_build,
        _flow_clear,
        _flow_manage,
        _flow_watch,
        _flow_identity,
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
