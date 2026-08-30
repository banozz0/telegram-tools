from __future__ import annotations

import argparse
import asyncio
import json
import sys
from functools import partial
from pathlib import Path
from typing import Sequence

from telegram_tools.bot_session import apply_bot_edits, bot_client
from telegram_tools.bots import (
    apply_owner_edits,
    build_edit_plan,
    confirm_bot_edits,
    format_bot_profile,
    format_bot_table,
    format_edit_heading,
    get_bot_profile,
    list_bots,
    parse_commands_file,
    parse_rights,
    resolve_bot,
    right_names,
)
from telegram_tools.client import create_client, start_client
from telegram_tools.config import ConfigError, bot_id_from_token, load_config, lookup_bot_token, resolve_bot_token
from telegram_tools.create import confirm_create, create_channel, create_group, create_topic, format_create_preview
from telegram_tools.delete import confirm_clear_topic_messages, delete_topic_messages
from telegram_tools.discovery import discover_chats, filter_chats, format_discovery_table
from telegram_tools.doctor import run_doctor
from telegram_tools.exporters import write_records
from telegram_tools.resolver import EntityResolutionError, resolve_chat
from telegram_tools.search import format_message_records, search_messages
from telegram_tools.send import SendTarget, confirm_send, format_send_preview, require_send_allowed, send_message
from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-tools")
    subparsers = parser.add_subparsers(dest="command")

    discover = subparsers.add_parser("discover", help="List dialogs and forum topics")
    discover.add_argument("--json", dest="json_output", help="Write discovery output to this JSON file")
    discover.add_argument("--all", dest="all_chats", action="store_true", help="Show every chat instead of admin/managed chats only")

    clear_messages = subparsers.add_parser("clear-messages", help="Clear messages from forum topic(s), preserving topics and topic IDs")
    clear_messages.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    topic_group = clear_messages.add_mutually_exclusive_group(required=True)
    topic_group.add_argument("--topic", dest="topics", action="append", type=int, help="Topic ID to clear messages from; repeatable")
    topic_group.add_argument("--all-topics", "--all-topics-in-chat", dest="all_topics", action="store_true", help="Clear messages from every forum topic")
    clear_messages.add_argument("--execute", action="store_true", help="Actually clear messages after typing DELETE")
    clear_messages.add_argument("--batch-size", type=positive_int, default=100, help="Clear-message batch size")

    search = subparsers.add_parser("search", help="Search and export messages")
    search.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    search.add_argument("--topic", type=int, help="Limit search/export to one topic ID")
    search.add_argument("--keyword", "--contains", dest="keyword", help="Case-insensitive text filter")
    search.add_argument("--from-user", help="Sender username, ID, or 'me'")
    search.add_argument("--since", help="Inclusive ISO date or datetime lower bound")
    search.add_argument("--until", help="Inclusive ISO date or datetime upper bound")
    search.add_argument("--limit", type=positive_int, help="Maximum exported messages")
    search.add_argument("--format", choices=("json", "csv"), default="json", help="Export format")
    search.add_argument("--output", help="Output path; prints a readable table when omitted")

    bots_parser = subparsers.add_parser("bots", help="List the bots you own and edit their BotFather settings")
    bots_parser.add_argument("--bot", help="Bot nickname from TELEGRAM_BOT_TOKENS, @username, or numeric ID")
    bots_parser.add_argument("--json", dest="json_output", help="Write bot output to this JSON file")
    bots_parser.add_argument("--name", help="Set the display name shown in chat lists")
    bots_parser.add_argument("--bio", help="Set the short bio shown under the bot profile")
    bots_parser.add_argument("--description", help="Set the 'what can this bot do?' text shown before Start")
    commands_group = bots_parser.add_mutually_exclusive_group()
    commands_group.add_argument("--commands", help="Path to a JSON file of {command, description} objects (needs a bot token)")
    commands_group.add_argument("--clear-commands", action="store_true", help="Remove every command (needs a bot token)")
    photo_group = bots_parser.add_mutually_exclusive_group()
    photo_group.add_argument("--photo", help="Path to a new profile photo")
    photo_group.add_argument("--remove-photo", action="store_true", help="Remove the current profile photo (needs a bot token)")
    valid_rights = ", ".join(right_names())
    bots_parser.add_argument("--group-rights", help=f"Default admin rights for groups, comma-separated, or none (needs a bot token). Valid names: {valid_rights}")
    bots_parser.add_argument("--channel-rights", help=f"Default admin rights for channels, comma-separated, or none (needs a bot token). Valid names: {valid_rights}")
    bots_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    send_parser = subparsers.add_parser("send", help="Send a message to a chat or forum topic")
    send_parser.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    send_parser.add_argument("--topic", type=int, help="Topic ID to post into; omit for the chat itself")
    send_parser.add_argument("--text", help="Message text, or - to read it from stdin; optional when --file is given")
    send_parser.add_argument("--file", dest="files", action="append", metavar="PATH", help="Attach a file; repeatable, several are sent as one album")
    send_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the preview and send; the destination must be in TELEGRAM_SEND_ALLOWLIST",
    )

    create_parser = subparsers.add_parser("create", help="Create a group, channel, or forum topic")
    create_kinds = create_parser.add_subparsers(dest="create_kind")

    create_group_parser = create_kinds.add_parser("group", help="Create a supergroup, optionally with topics")
    create_group_parser.add_argument("--title", required=True, help="Group name")
    create_group_parser.add_argument("--about", help="Group description")
    create_group_parser.add_argument("--forum", action="store_true", help="Enable topics on the new group")

    create_channel_parser = create_kinds.add_parser("channel", help="Create a broadcast channel")
    create_channel_parser.add_argument("--title", required=True, help="Channel name")
    create_channel_parser.add_argument("--about", help="Channel description")

    create_topic_parser = create_kinds.add_parser("topic", help="Create a topic in a forum group")
    create_topic_parser.add_argument("--chat", required=True, help="Forum group username, link, or ID")
    create_topic_parser.add_argument("--title", required=True, help="Topic name")

    for kind_parser in (create_group_parser, create_channel_parser, create_topic_parser):
        kind_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    subparsers.add_parser("doctor", help="Check local setup without printing secrets")

    return parser


async def _require_delete_permission(client, chat) -> None:
    me = await client.get_me()
    permissions = await client.get_permissions(chat, me)
    if not (getattr(permissions, "is_creator", False) or getattr(permissions, "delete_messages", False)):
        raise PermissionError("Current user lacks Telegram delete_messages permission in this chat.")


async def _run_discover(client, args) -> int:
    chats = filter_chats(await discover_chats(client), admin_only=not args.all_chats)
    payload = [chat.to_dict() for chat in chats]
    if args.json_output:
        _write_json(payload, args.json_output)
    else:
        print(format_discovery_table(chats))
    return 0


async def _run_clear_messages(client, args) -> int:
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    await _require_delete_permission(client, peer)

    if args.all_topics:
        topics = await get_forum_topics(client, peer)
    else:
        topics = await get_forum_topics_by_ids(client, peer, args.topics)

    result = await delete_topic_messages(
        client,
        peer,
        topics,
        execute=args.execute,
        batch_size=args.batch_size,
        progress=print,
        confirm=confirm_clear_topic_messages,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 1 if result.cancelled else 0


async def _run_search(client, args) -> int:
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    records = await search_messages(
        client,
        peer,
        chat_id=resolved.id,
        topic_id=args.topic,
        keyword=args.keyword,
        from_user=args.from_user,
        since=args.since,
        until=args.until,
        limit=args.limit,
    )

    if args.output:
        write_records(records, args.output, args.format)
    elif args.format == "csv":
        raise ValueError("--output is required for CSV export")
    else:
        print(format_message_records(records))
    return 0


def _entity_title(entity, fallback: str) -> str:
    """A chat's name for the preview: a title, a person's name, or what was typed."""
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or str(getattr(entity, "username", None) or fallback)


def _message_text(raw: str | None, *, has_files: bool) -> str | None:
    # `-` is how a multi-line body gets in: quoting newlines through a shell flag is
    # the kind of thing that silently sends half a message.
    if raw is None:
        if not has_files:
            raise ValueError("Nothing to send: pass --text, or --file to send an attachment.")
        return None
    text = (sys.stdin.read() if raw == "-" else raw).strip()
    if not text and not has_files:
        raise ValueError("Nothing to send: the message text is empty.")
    return text or None


def _attachments(paths: list[str] | None) -> list[str]:
    # Checked before the confirm, never mid-send: a typo in the fourth path should
    # not surface after the first three have already reached Telegram.
    files = list(paths or [])
    missing = [path for path in files if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError("No file at " + ", ".join(missing) + ".")
    return files


async def _run_send(client, args, config) -> int:
    files = _attachments(getattr(args, "files", None))
    text = _message_text(args.text, has_files=bool(files))
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity

    topic = None
    if args.topic is not None:
        topics = await get_forum_topics_by_ids(client, peer, [args.topic])
        topic = topics[0] if topics else None

    target = SendTarget(chat_id=resolved.id, chat_title=_entity_title(resolved.entity, args.chat), topic=topic)

    confirm = None
    if args.yes:
        require_send_allowed(
            config.send_allowlist,
            chat_id=resolved.id,
            username=getattr(resolved.entity, "username", None),
            topic_id=args.topic,
        )
    else:
        preview = format_send_preview(target, text, sender=_entity_title(await client.get_me(), "you"), files=files)
        confirm = partial(confirm_send, preview)

    result = await send_message(client, peer, target, text, files=files, confirm=confirm)
    print(json.dumps(result.to_dict(), indent=2))
    return 1 if result.cancelled else 0


async def _run_create(client, args) -> int:
    if args.create_kind is None:
        raise ValueError("create needs one of: group, channel, topic.")

    chat_title = None
    peer = None
    chat_id = None
    if args.create_kind == "topic":
        resolved = await resolve_chat(client, args.chat)
        peer = resolved.input_entity
        chat_id = resolved.id
        chat_title = _entity_title(resolved.entity, args.chat)

    forum = bool(getattr(args, "forum", False))
    confirm = None
    if not args.yes:
        preview = format_create_preview(
            args.create_kind,
            args.title,
            about=getattr(args, "about", None),
            forum=forum,
            chat_title=chat_title,
        )
        confirm = partial(confirm_create, preview)

    if args.create_kind == "group":
        created = await create_group(client, args.title, about=args.about, forum=forum, confirm=confirm)
    elif args.create_kind == "channel":
        created = await create_channel(client, args.title, about=args.about, confirm=confirm)
    else:
        created = await create_topic(client, peer, chat_id=chat_id, title=args.title, confirm=confirm)

    print(json.dumps(created.to_dict(), indent=2))
    return 1 if created.cancelled else 0


EDIT_FLAGS = ("name", "bio", "description", "commands", "clear_commands", "photo", "remove_photo", "group_rights", "channel_rights")


def bot_edit_requests(args) -> dict:
    requested = {}
    for flag in EDIT_FLAGS:
        value = getattr(args, flag, None)
        if value is None or value is False:
            continue
        requested[flag] = value
    return requested


def _write_json(payload, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _bot_result(profile, plan, applied, *, cancelled: bool) -> dict:
    return {
        "bot_id": profile.id,
        "username": profile.username,
        "applied": list(applied),
        "skipped": list(plan.skipped),
        "cancelled": cancelled,
    }


def _emit_bot_result(result: dict, json_output: str | None) -> None:
    if json_output:
        _write_json(result, json_output)
    else:
        print(json.dumps(result, indent=2))


async def _run_bots(client, args, config) -> int:
    requested = bot_edit_requests(args)
    if requested and not args.bot:
        raise ValueError("--bot is required when editing a bot.")

    if not args.bot:
        bots = await list_bots(client)
        if args.json_output:
            _write_json([bot.to_dict() for bot in bots], args.json_output)
        else:
            print(format_bot_table(bots))
        return 0

    token, reference = resolve_bot_token(config.bot_tokens, args.bot)

    resolved = await resolve_bot(client, reference)
    profile = await get_bot_profile(client, resolved)
    # A nickname can name the wrong bot, so the token is only kept if its own bot id
    # is the bot that was resolved. Ids only - never any part of a token in an error.
    if token is None:
        token = lookup_bot_token(config.bot_tokens, profile.id)
    if token is not None and bot_id_from_token(token) != profile.id:
        raise ValueError(
            f"The stored token is for bot {bot_id_from_token(token)}, not {profile.id}. Check TELEGRAM_BOT_TOKENS."
        )

    if not requested:
        if args.json_output:
            _write_json(profile.to_dict(), args.json_output)
        else:
            print(format_bot_profile(profile))
        return 0

    if not resolved.is_owned:
        raise PermissionError(f"You do not own {f'@{profile.username}' if profile.username else f'bot {profile.id}'}; only its owner can edit it.")

    if "commands" in requested:
        requested["commands"] = parse_commands_file(requested["commands"])
    if "photo" in requested and not Path(requested["photo"]).is_file():
        # Checked here so a missing file fails before the confirm, not mid-apply.
        raise FileNotFoundError(f"No photo file at {requested['photo']}.")
    for field in ("group_rights", "channel_rights"):
        if field in requested:
            requested[field] = parse_rights(requested[field])

    plan = build_edit_plan(profile, requested)
    if plan.is_empty:
        _emit_bot_result(_bot_result(profile, plan, [], cancelled=False), args.json_output)
        return 0

    if plan.bot_changes and token is None:
        fields = ", ".join(change.field for change in plan.bot_changes)
        raise ValueError(
            f"{fields} can only be changed with that bot's token. "
            "Set TELEGRAM_BOT_TOKENS=nickname:token[,nickname:token] in ~/.telegram-tools/.env."
        )

    # Named on every edit run, --yes included: it is the one mode with no confirm diff,
    # so a mistyped token nickname acting on the wrong bot would otherwise go unnamed.
    print(format_edit_heading(profile))
    if not args.yes:
        if not confirm_bot_edits(plan):
            _emit_bot_result(_bot_result(profile, plan, [], cancelled=True), args.json_output)
            return 1

    applied: list[str] = []
    try:
        await apply_owner_edits(client, resolved.input_user, plan.owner_changes, applied)
        if plan.bot_changes:
            async with bot_client(config, token) as bot:
                await apply_bot_edits(bot, plan.bot_changes, applied)
    finally:
        _emit_bot_result(_bot_result(profile, plan, applied, cancelled=False), args.json_output)
    return 0


async def run(args, *, client=None, config=None) -> int:
    """Run one command.

    The menu passes its own already-started client so a whole menu session is one
    connection: two Telethon clients against one SQLite session file is a lock
    error waiting to happen. A caller that passes a client owns it, so it is not
    disconnected here.
    """
    if args.command == "doctor":
        return run_doctor()

    if config is None:
        config = load_config()

    owns_client = client is None
    if owns_client:
        client = await start_client(create_client(config))

    try:
        if args.command == "discover":
            return await _run_discover(client, args)
        if args.command == "clear-messages":
            return await _run_clear_messages(client, args)
        if args.command == "search":
            return await _run_search(client, args)
        if args.command == "bots":
            return await _run_bots(client, args, config)
        if args.command == "send":
            return await _run_send(client, args, config)
        if args.command == "create":
            return await _run_create(client, args)
        raise ValueError(f"Unknown command: {args.command}")
    finally:
        if owns_client:
            await client.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if not sys.stdin.isatty():
                # A menu needs a human. Scripts and agents get the help they
                # actually wanted instead of a blocked input() prompt.
                parser.print_help()
                return 0
            try:
                # `input()` only gets line editing when readline is imported.
                # Without it every arrow key echoes its raw escape sequence
                # (^[[A) into the answer. Menu-only, and optional: readline is
                # absent on some platforms and the menu works fine without it.
                import readline  # noqa: F401
            except ImportError:
                pass

            # Imported here, not at module scope: menu.py imports cli, and a
            # top-level import either way closes the cycle.
            from telegram_tools.menu import run_menu

            return asyncio.run(run_menu())
        return asyncio.run(run(args))
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    except ConfigError as exc:
        parser.error(str(exc))
    except EntityResolutionError as exc:
        parser.error(str(exc))
    except ValueError as exc:
        parser.error(str(exc))
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # A missing or unreadable path is a usage mistake, not a crash. Must stay
        # below PermissionError, which is an OSError subclass with its own exit.
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
