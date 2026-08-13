from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from telegram_tools.bot_session import apply_bot_edits, bot_client
from telegram_tools.bots import (
    apply_owner_edits,
    build_edit_plan,
    confirm_bot_edits,
    format_bot_profile,
    format_bot_table,
    get_bot_profile,
    list_bots,
    parse_commands_file,
    parse_rights,
    resolve_bot,
)
from telegram_tools.client import create_client
from telegram_tools.config import ConfigError, bot_id_from_token, load_config, lookup_bot_token
from telegram_tools.delete import confirm_clear_topic_messages, delete_topic_messages
from telegram_tools.discovery import discover_chats, filter_chats, format_discovery_table
from telegram_tools.doctor import run_doctor
from telegram_tools.exporters import write_records
from telegram_tools.resolver import EntityResolutionError, resolve_chat
from telegram_tools.search import format_message_records, search_messages
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
    discover.add_argument("--admin-only", action="store_true", help=argparse.SUPPRESS)

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
    bots_parser.add_argument("--group-rights", help="Default admin rights for groups: comma-separated names, or none (needs a bot token)")
    bots_parser.add_argument("--channel-rights", help="Default admin rights for channels: comma-separated names, or none (needs a bot token)")
    bots_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

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
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
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


EDIT_FLAGS = ("name", "bio", "description", "commands", "clear_commands", "photo", "remove_photo", "group_rights", "channel_rights")


def bot_edit_requests(args) -> dict:
    requested = {}
    for flag in EDIT_FLAGS:
        value = getattr(args, flag, None)
        if value is None or value is False:
            continue
        requested[flag] = value
    return requested


def _write_json(payload, path: str | None) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n")


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

    token = lookup_bot_token(config.bot_tokens, args.bot)
    reference = args.bot
    if token is not None:
        reference = bot_id_from_token(token) or args.bot

    resolved = await resolve_bot(client, reference)
    profile = await get_bot_profile(client, resolved)
    token = token or lookup_bot_token(config.bot_tokens, profile.username, profile.id)

    if not requested:
        if args.json_output:
            _write_json(profile.to_dict(), args.json_output)
        else:
            print(format_bot_profile(profile))
        return 0

    if not resolved.is_owned:
        raise PermissionError(f"You do not own @{profile.username or profile.id}; only its owner can edit it.")

    if "commands" in requested:
        requested["commands"] = parse_commands_file(requested["commands"])
    for field in ("group_rights", "channel_rights"):
        if field in requested:
            requested[field] = parse_rights(requested[field])

    plan = build_edit_plan(profile, requested)
    if plan.is_empty:
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": [], "skipped": plan.skipped, "cancelled": False}, indent=2))
        return 0

    if plan.bot_changes and token is None:
        fields = ", ".join(change.field for change in plan.bot_changes)
        raise ValueError(
            f"{fields} can only be changed with that bot's token. "
            "Set TELEGRAM_BOT_TOKENS=nickname:token[,nickname:token] in ~/.telegram-tools/.env."
        )

    if not args.yes and not confirm_bot_edits(plan):
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": [], "skipped": plan.skipped, "cancelled": True}, indent=2))
        return 1

    applied = []
    try:
        applied.extend(await apply_owner_edits(client, resolved.input_user, plan.owner_changes))
        if plan.bot_changes:
            async with bot_client(config, token) as bot:
                applied.extend(await apply_bot_edits(bot, plan.bot_changes))
    finally:
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": applied, "skipped": plan.skipped, "cancelled": False}, indent=2))
    return 0


def _namespace(**kwargs):
    return argparse.Namespace(**kwargs)


def _read_execute(read) -> bool:
    return read("Type DELETE to pass --execute, or press Enter for dry-run: ") == "DELETE"


async def run_interactive_menu(*, read=input, write=print) -> int:
    write(
        "\n".join(
            [
                "telegram-tools",
                "--------------------------------------------",
                "1. Discover chats/topics",
                "2. Search messages",
                "3. Export messages",
                "4. Clear topic messages",
                "5. Clear multiple topics",
                "6. Clear all topic messages",
                "7. List my bots",
                "8. Edit a bot",
                "0. Exit",
            ]
        )
    )
    choice = read("Choose: ").strip()
    if choice == "0":
        return 0
    if choice == "1":
        all_chats = read("Show all chats instead of admin/managed only? [y/N]: ").strip().lower() == "y"
        return await run(_namespace(command="discover", json_output=None, all_chats=all_chats, admin_only=not all_chats))
    if choice == "2":
        chat = read("Chat (@username, t.me link, or numeric ID): ")
        keyword = read("Contains text: ")
        topic = read("Topic ID (optional): ").strip()
        return await run(
            _namespace(command="search", chat=chat, topic=int(topic) if topic else None, keyword=keyword, from_user=None, since=None, until=None, limit=None, format="json", output=None)
        )
    if choice == "3":
        chat = read("Chat (@username, t.me link, or numeric ID): ")
        topic = read("Topic ID (optional): ").strip()
        output = read("Output file: ")
        fmt = read("Format [json/csv, default json]: ").strip() or "json"
        return await run(
            _namespace(command="search", chat=chat, topic=int(topic) if topic else None, keyword=None, from_user=None, since=None, until=None, limit=None, format=fmt, output=output)
        )
    if choice == "4":
        chat = read("Chat (@username, t.me link, or numeric ID): ")
        topic = int(read("Topic ID: "))
        return await run(_namespace(command="clear-messages", chat=chat, topics=[topic], all_topics=False, execute=_read_execute(read), batch_size=100))
    if choice == "5":
        chat = read("Chat (@username, t.me link, or numeric ID): ")
        raw_topics = read("Topic IDs (space or comma separated): ")
        topics = [int(value) for value in raw_topics.replace(",", " ").split()]
        return await run(_namespace(command="clear-messages", chat=chat, topics=topics, all_topics=False, execute=_read_execute(read), batch_size=100))
    if choice == "6":
        chat = read("Chat (@username, t.me link, or numeric ID): ")
        return await run(_namespace(command="clear-messages", chat=chat, topics=None, all_topics=True, execute=_read_execute(read), batch_size=100))
    if choice == "7":
        return await run(_namespace(command="bots", bot=None, json_output=None, name=None, bio=None, description=None, commands=None, clear_commands=False, photo=None, remove_photo=False, group_rights=None, channel_rights=None, yes=False))
    if choice == "8":
        bot = read("Bot (nickname, @username, or numeric ID): ")
        name = read("New display name (blank to keep): ").strip() or None
        bio = read("New bio (blank to keep): ").strip() or None
        description = read("New description (blank to keep): ").strip() or None
        return await run(_namespace(command="bots", bot=bot, json_output=None, name=name, bio=bio, description=description, commands=None, clear_commands=False, photo=None, remove_photo=False, group_rights=None, channel_rights=None, yes=False))

    write("Unknown choice.")
    return 2


async def run(args) -> int:
    if args.command == "doctor":
        return run_doctor()

    config = load_config()
    client = create_client(config)
    await client.start()
    try:
        if args.command == "discover":
            return await _run_discover(client, args)
        if args.command == "clear-messages":
            return await _run_clear_messages(client, args)
        if args.command == "search":
            return await _run_search(client, args)
        if args.command == "bots":
            return await _run_bots(client, args, config)
        raise ValueError(f"Unknown command: {args.command}")
    finally:
        await client.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return asyncio.run(run_interactive_menu())
        return asyncio.run(run(args))
    except ConfigError as exc:
        parser.error(str(exc))
    except EntityResolutionError as exc:
        parser.error(str(exc))
    except ValueError as exc:
        parser.error(str(exc))
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
