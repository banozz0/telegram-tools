from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path
from typing import Sequence

from telegram_tools.bot_inventory import add_bot_token, format_bot_inventory, load_bot_tokens, validate_bots
from telegram_tools.client import create_client
from telegram_tools.config import ConfigError, load_config
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

    bot_inventory = subparsers.add_parser("bot-inventory", help="Validate configured Telegram bot tokens")
    bot_inventory.add_argument("--bots-json", default="bots.json", help="Local gitignored bot token inventory file")

    bot_add = subparsers.add_parser("bot-add", help="Validate and store a bot token in local bots.json")
    bot_add.add_argument("--bots-json", default="bots.json", help="Local gitignored bot token inventory file")

    doctor = subparsers.add_parser("doctor", help="Check local setup without printing secrets")
    doctor.add_argument("--bots-json", default="bots.json", help="Local gitignored bot token inventory file")

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
                "7. Bot inventory",
                "8. Add bot",
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
        return await run(_namespace(command="bot-inventory", bots_json="bots.json"))
    if choice == "8":
        return await run(_namespace(command="bot-add", bots_json="bots.json"))

    write("Unknown choice.")
    return 2


async def run(args) -> int:
    if args.command == "bot-inventory":
        tokens = load_bot_tokens(bots_json=args.bots_json)
        print(format_bot_inventory(validate_bots(tokens)))
        return 0
    if args.command == "bot-add":
        token = getpass.getpass("Bot token: ")
        item = add_bot_token(token, bots_json=args.bots_json)
        print(format_bot_inventory([item]))
        return 0 if item.ok else 1
    if args.command == "doctor":
        return run_doctor(bots_json=args.bots_json)

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
