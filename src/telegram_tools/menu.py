from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from telethon.errors import ChannelForumMissingError, RPCError

from telegram_tools import cli
from telegram_tools.bots import IMPLICIT_OTHER_RIGHT, format_bot_profile, get_bot_profile, list_bots, resolve_bot, right_names
from telegram_tools.client import create_client
from telegram_tools.config import ConfigError, load_config, lookup_bot_token
from telegram_tools.discovery import list_dialog_choices
from telegram_tools.prompts import BACK, CLEAR, Extra, after_action, ask_int, ask_text, choose, edit_field, pick, pick_many
from telegram_tools.resolver import resolve_chat
from telegram_tools.topics import get_forum_topics

# What the menu turns into a printed line instead of an exit. EntityResolutionError
# is a ValueError and PermissionError is an OSError, so both are already covered;
# anything not named here is a bug and should still be loud.
MENU_ERRORS = (ConfigError, ValueError, OSError, RPCError)

ROOT_TITLE = "telegram-tools"
ROOT_ITEMS = (
    "Chats & topics (find IDs)",
    "Search / export messages",
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
            client = create_client(self.config)
            await client.start()
            self._client = client
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


async def _call(args, *, session, runner, write) -> bool:
    """Run one action. False means it errored and the message is already printed."""
    try:
        client = await session.client() if session is not None else None
        config = session.config if session is not None else None
        await runner(args, client=client, config=config)
        return True
    except MENU_ERRORS as exc:
        write(f"error: {exc}")
        return False


async def _act(args, *, session, runner, read, write) -> bool:
    """Run one action, then ask. False means exit the menu."""
    await _call(args, session=session, runner=runner, write=write)
    return after_action(read=read, write=write)


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


def _chat_label(chat) -> str:
    return f"{chat.title[:32]:<32}  {chat.id}"


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
        chosen = pick(items, title=title, label=_chat_label, read=read, write=write, extras=extras)

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

        return ChatPick(reference=str(chosen.id), title=chosen.title, is_forum=chosen.is_forum)


async def _pick_chat(*, session, read, write, forums_only: bool = False) -> Any:
    chats = await session.chats()

    if forums_only:
        return _pick_from_group(
            [chat for chat in chats if chat.is_forum],
            title="Pick a forum group",
            read=read,
            write=write,
        )

    groups = [(name, [chat for chat in chats if chat.type in types]) for name, types in CHAT_GROUPS]
    groups = [(name, members) for name, members in groups if members]

    while True:
        labels = [f"{name} ({len(members)})" for name, members in groups]
        choice = choose(labels + [_TYPE_A_CHAT], title="Pick a chat", read=read, write=write)
        if choice is BACK:
            return BACK

        if choice == len(groups):
            typed = _ask_reference(read=read, write=write)
            if typed is BACK:
                continue
            return typed

        name, members = groups[choice]
        picked = _pick_from_group(members, title=f"Pick a chat  >  {name}", read=read, write=write)
        if picked is BACK:
            continue
        return picked


async def _flow_discover(*, session, runner, read, write) -> bool:
    scope = choose(["Chats I manage", "Every chat"], title="Chats & topics", read=read, write=write)
    if scope is BACK:
        return True

    where = choose(["Print it here", "Write a JSON file"], title="Where should it go?", read=read, write=write)
    if where is BACK:
        return True

    json_output = None
    if where == 1:
        path = ask_text("JSON file path", read=read, write=write)
        if path is BACK:
            return True
        json_output = path

    all_chats = scope == 1
    args = _namespace(command="discover", json_output=json_output, all_chats=all_chats, admin_only=not all_chats)
    return await _act(args, session=session, runner=runner, read=read, write=write)


async def _flow_doctor(*, session, runner, read, write) -> bool:
    # No session: doctor never opens a connection, which is the point of it.
    return await _act(_namespace(command="doctor"), session=None, runner=runner, read=read, write=write)


_ALL_TOPICS = "All topics"


def _shown(value, empty: str) -> str:
    return empty if value in (None, "") else str(value)


async def _ask_topic(picked, *, session, read, write) -> Any:
    topics = await session.topics(picked.reference)
    if not topics:
        write("That chat has no topics.")
        return BACK

    chosen = pick(
        topics,
        title=f"Topics in {picked.title}",
        label=lambda topic: f"{topic.id:<6}  {topic.title}",
        read=read,
        write=write,
        extras=(Extra("all", _ALL_TOPICS),),
    )
    if chosen is BACK:
        return BACK
    if chosen == "all":
        return CLEAR
    return chosen


def _ask_from_user(*, read, write) -> Any:
    choice = choose(["Anyone", "Me", "Someone else"], title="From", read=read, write=write)
    if choice is BACK:
        return BACK
    if choice == 0:
        return CLEAR
    if choice == 1:
        return "me"
    return ask_text("Username, ID, or me", read=read, write=write)


async def _flow_search(*, session, runner, read, write) -> bool:
    picked = await _pick_chat(session=session, read=read, write=write)
    if picked is BACK:
        return True

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

        choice = choose([label for _key, label in rows], title=f"Search in {picked.title}", read=read, write=write)
        if choice is BACK:
            return True
        key = rows[choice][0]

        if key in ("run", "export"):
            output_path = None
            output_format = "json"
            if key == "export":
                output_path = ask_text("Export file path", read=read, write=write)
                if output_path is BACK:
                    continue
                fmt = choose(["JSON", "CSV"], title="Format", read=read, write=write)
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
            return await _act(args, session=session, runner=runner, read=read, write=write)

        if key == "topic":
            answer = await _ask_topic(picked, session=session, read=read, write=write)
            if answer is BACK:
                continue
            topic_info = None if answer is CLEAR else answer
            staged["topic"] = None if topic_info is None else topic_info.id
            continue

        if key == "from_user":
            answer = _ask_from_user(read=read, write=write)
        elif key == "limit":
            answer = edit_field(
                "Limit",
                _shown(staged["limit"], "(no limit)"),
                read=read,
                write=write,
                ask=lambda: ask_int("Maximum messages", read=read, write=write),
                allow_clear=True,
            )
        else:
            labels = {"keyword": ("Contains", "(anything)"), "since": ("Since", "(any date)"), "until": ("Until", "(any date)")}
            title, empty = labels[key]
            answer = edit_field(
                title,
                _shown(staged[key], empty),
                read=read,
                write=write,
                ask=lambda: ask_text(title, read=read, write=write),
                allow_clear=True,
            )

        if answer is BACK:
            continue
        staged[key] = None if answer is CLEAR else answer


async def _flow_clear(*, session, runner, read, write) -> bool:
    picked = await _pick_chat(session=session, read=read, write=write, forums_only=True)
    if picked is BACK:
        return True

    topics = await session.topics(picked.reference)
    if not topics:
        write("No topics in that chat.")
        return True

    selected = pick_many(
        topics,
        title=f"Topics in {picked.title} - tick what to clear",
        label=lambda topic: f"{topic.id:<6}  {topic.title}",
        read=read,
        write=write,
    )
    if selected is BACK:
        return True

    every_topic = len(selected) == len(topics)
    dry_run = _namespace(
        command="clear-messages",
        chat=picked.reference,
        topics=None if every_topic else [topic.id for topic in selected],
        all_topics=every_topic,
        execute=False,
        batch_size=100,
    )

    # The dry-run always runs first: the menu must never be a shorter path to a
    # deletion than the flags are, and the count is what makes the next screen
    # an informed answer.
    if not await _call(dry_run, session=session, runner=runner, write=write):
        return after_action(read=read, write=write)

    choice = choose(
        ["Clear them for real (asks you to type DELETE)"],
        title="Dry-run done",
        read=read,
        write=write,
        back_label="Back to the menu",
    )
    if choice is BACK:
        return True

    for_real = _namespace(**{**vars(dry_run), "execute": True})
    return await _act(for_real, session=session, runner=runner, read=read, write=write)


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
    if value in ("", "none"):
        return "(cleared)"
    return value


def _ask_rights(title: str, current: list[str], *, read, write) -> Any:
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


async def _flow_bot_edit(profile, *, session, runner, read, write) -> bool:
    token = lookup_bot_token(session.config.bot_tokens, profile.id)
    staged: dict[str, Any] = {}

    while True:
        rows: list[tuple[str, str]] = []
        for key, title, _allow_clear, needs_token in BOT_FIELDS:
            current = _current_bot_value(profile, key)
            pending = _staged_bot_value(key, staged)
            value = current if pending is None else f"{current} -> {pending}"
            gate = "  (needs this bot's token)" if needs_token and token is None else ""
            rows.append((key, f"{title:<16} [{value}]{gate}"))
        rows.append(("apply", "Review & apply"))

        heading = f"Editing @{profile.username} ({profile.id})" if profile.username else f"Editing bot {profile.id}"
        choice = choose([label for _key, label in rows], title=heading, read=read, write=write, back_label="Back (discards)")

        if choice is BACK:
            if staged:
                count = len(staged)
                write(f"Discarded {count} staged change{'s' if count > 1 else ''}.")
            return True

        key = rows[choice][0]

        if key == "apply":
            if not staged:
                write("Nothing staged yet.")
                continue
            args = _bots_namespace(bot=str(profile.id), **staged)
            return await _act(args, session=session, runner=runner, read=read, write=write)

        field = next(entry for entry in BOT_FIELDS if entry[0] == key)
        _key, title, allow_clear, needs_token = field
        if needs_token and token is None:
            # Photo is the odd one: setting it runs on the user session, only
            # removing it needs the token, so it is refused only for clearing.
            if key != "photo":
                write(f"{title} can only be changed with that bot's token. {_NEEDS_TOKEN}")
                continue

        if key in ("group_rights", "channel_rights"):
            ask = lambda: _ask_rights(title, getattr(profile, key), read=read, write=write)
        elif key in ("commands", "photo"):
            ask = lambda: ask_text(f"{title} file path", read=read, write=write)
        else:
            ask = lambda: ask_text(title, read=read, write=write)

        answer = edit_field(
            title,
            _current_bot_value(profile, key),
            read=read,
            write=write,
            ask=ask,
            allow_clear=allow_clear and not (needs_token and token is None),
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


async def _flow_bots(*, session, runner, read, write) -> bool:
    bots = await session.bots()
    if not bots:
        write("No bots found.")
        return True

    chosen = pick(
        bots,
        title="My bots",
        label=lambda bot: f"@{bot.username or bot.id}  {bot.name}",
        read=read,
        write=write,
    )
    if chosen is BACK:
        return True

    profile = await session.bot_profile(str(chosen.id))
    # Printed here rather than through run(): the edit screen needs these values
    # anyway, and fetching the same profile twice to print it would be two more
    # API calls for the same text. Every edit still goes through run().
    write(format_bot_profile(profile))

    while True:
        choice = choose(
            ["Edit this bot", "Save this profile to a JSON file"],
            title=f"@{profile.username or profile.id}",
            read=read,
            write=write,
        )
        if choice is BACK:
            return True
        if choice == 1:
            path = ask_text("JSON file path", read=read, write=write)
            if path is BACK:
                continue
            args = _bots_namespace(bot=str(profile.id), json_output=path)
            return await _act(args, session=session, runner=runner, read=read, write=write)
        return await _flow_bot_edit(profile, session=session, runner=runner, read=read, write=write)


async def run_menu(*, read=input, write=print, session=None, runner=None) -> int:
    """The looping menu. Returns 0 on a normal exit.

    The exit code belongs to the session, not to any one action inside it: a
    session can run a dozen actions and there is no honest way to fold their
    codes into one number.
    """
    session = session if session is not None else MenuSession()
    runner = runner if runner is not None else cli.run
    flows = (_flow_discover, _flow_search, _flow_clear, _flow_bots, _flow_doctor)

    try:
        while True:
            choice = choose(list(ROOT_ITEMS), title=ROOT_TITLE, read=read, write=write, back_label="Exit")
            if choice is BACK:
                return 0
            if not await flows[choice](session=session, runner=runner, read=read, write=write):
                return 0
    finally:
        await session.close()
