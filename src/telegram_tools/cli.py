from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from telegram_tools.client import create_client
from telegram_tools.config import ConfigError, load_config
from telegram_tools.delete import delete_topic_messages
from telegram_tools.discovery import discover_chats
from telegram_tools.exporters import write_records
from telegram_tools.search import search_messages
from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="List dialogs and forum topics")
    discover.add_argument("--json", dest="json_output", help="Write discovery output to this JSON file")

    delete = subparsers.add_parser("delete", help="Delete messages from forum topic(s), dry-run by default")
    delete.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    topic_group = delete.add_mutually_exclusive_group(required=True)
    topic_group.add_argument("--topic", dest="topics", action="append", type=int, help="Topic ID to delete from; repeatable")
    topic_group.add_argument("--all-topics", action="store_true", help="Delete from every forum topic")
    delete.add_argument("--execute", action="store_true", help="Actually delete after typing DELETE")
    delete.add_argument("--batch-size", type=positive_int, default=100, help="Delete batch size")

    search = subparsers.add_parser("search", help="Search and export messages")
    search.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    search.add_argument("--topic", type=int, help="Limit search/export to one topic ID")
    search.add_argument("--keyword", help="Case-insensitive keyword filter")
    search.add_argument("--from-user", help="Sender username, ID, or 'me'")
    search.add_argument("--since", help="Inclusive ISO date or datetime lower bound")
    search.add_argument("--until", help="Inclusive ISO date or datetime upper bound")
    search.add_argument("--limit", type=positive_int, help="Maximum exported messages")
    search.add_argument("--format", choices=("json", "csv"), default="json", help="Export format")
    search.add_argument("--output", help="Output path; prints JSON to stdout when omitted")

    return parser


async def _require_delete_permission(client, chat) -> None:
    me = await client.get_me()
    permissions = await client.get_permissions(chat, me)
    if not (getattr(permissions, "is_creator", False) or getattr(permissions, "delete_messages", False)):
        raise PermissionError("Current user lacks Telegram delete_messages permission in this chat.")


async def _run_discover(client, args) -> int:
    chats = await discover_chats(client)
    payload = [chat.to_dict() for chat in chats]
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    else:
        print(json.dumps(payload, indent=2, default=str))
    return 0


async def _run_delete(client, args) -> int:
    peer = await client.get_input_entity(args.chat)
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
        confirm=lambda: input("Type DELETE to permanently delete matched messages: "),
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 1 if result.cancelled else 0


async def _run_search(client, args) -> int:
    peer = await client.get_input_entity(args.chat)
    chat_id = int(await client.get_peer_id(args.chat))
    records = await search_messages(
        client,
        peer,
        chat_id=chat_id,
        topic_id=args.topic,
        keyword=args.keyword,
        from_user=args.from_user,
        since=args.since,
        until=args.until,
        limit=args.limit,
    )

    if args.output:
        write_records(records, args.output, args.format)
    elif args.format == "json":
        print(json.dumps(records, indent=2, default=str))
    else:
        raise ValueError("--output is required for CSV export")
    return 0


async def run(args) -> int:
    config = load_config()
    client = create_client(config)
    await client.start()
    try:
        if args.command == "discover":
            return await _run_discover(client, args)
        if args.command == "delete":
            return await _run_delete(client, args)
        if args.command == "search":
            return await _run_search(client, args)
        raise ValueError(f"Unknown command: {args.command}")
    finally:
        await client.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ConfigError as exc:
        parser.error(str(exc))
    except ValueError as exc:
        parser.error(str(exc))
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
