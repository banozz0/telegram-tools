from __future__ import annotations

import argparse
import asyncio
import sys
from functools import partial
from pathlib import Path
from typing import Sequence

from telegram_tools._core import rid as _rid
from telegram_tools._core.audit import AuditLog
from telegram_tools._core.identity import Target
from telegram_tools._core.plan import Mutation
from telegram_tools.adapters import AccountIdentity, ChatPermissions, ChatTargets, Rights
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
from telegram_tools.delete import (
    DELETE_KIND_TYPES,
    confirm_clear_topic_messages,
    confirm_delete,
    delete_chat,
    delete_topic,
    delete_topic_messages,
    kind_for_type,
)
from telegram_tools.discovery import classify_entity, discover_chats, filter_chats, format_discovery_table
from telegram_tools.doctor import run_doctor
from telegram_tools.envelope import PLATFORM, PREFIX, CommandError, Reporter, error_for, platform_error
from telegram_tools.exporters import json_text, write_records
from telegram_tools.resolver import EntityResolutionError, resolve_chat
from telegram_tools.search import format_message_records, search_messages
from telegram_tools.send import SendTarget, confirm_send, format_send_preview, require_send_allowed, send_message
from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids
from telegram_tools.writes import build_plan, read_back, recheck_for, require_rights

# Every right a write here needs, by the command that needs it. Named in
# Telegram's own vocabulary so a refusal can be read straight into the app.
CLEAR_RIGHTS = ("delete_messages",)
SEND_RIGHTS = ("send_messages",)
# A topic is opened by posting its service message, so posting is the right.
CREATE_TOPIC_RIGHTS = ("send_messages",)
# Telegram lets only a chat's creator delete it, which is what the preview says.
DELETE_CHAT_RIGHTS = ("is_creator",)
DELETE_TOPIC_RIGHTS = ("delete_messages",)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


class JsonOutput(argparse.Action):
    """`--json PATH` writes the file it always wrote; a bare `--json` asks for the envelope.

    One flag, two jobs, because the path form predates the envelope and every
    script that passes one has to keep working.
    """

    def __call__(self, parser, namespace, value, option_string=None):
        if value is None:
            setattr(namespace, "json_envelope", True)
        else:
            setattr(namespace, self.dest, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-tools")
    parser.add_argument(
        "--json",
        dest="json_envelope",
        action="store_true",
        help="Emit one machine-readable envelope on stdout instead of the human output",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Stream one JSON line per record, then the envelope as the last line",
    )
    subparsers = parser.add_subparsers(dest="command")

    discover = subparsers.add_parser("discover", help="List dialogs and forum topics")
    discover.add_argument("--json", dest="json_output", nargs="?", action=JsonOutput, help="Write discovery output to this JSON file")
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
    bots_parser.add_argument("--json", dest="json_output", nargs="?", action=JsonOutput, help="Write bot output to this JSON file")
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

    delete_parser = subparsers.add_parser(
        "delete", help="Delete a group, channel, or forum topic (dry-run by default)"
    )
    delete_kinds = delete_parser.add_subparsers(dest="delete_kind")

    delete_group_parser = delete_kinds.add_parser("group", help="Delete a supergroup, for everyone in it")
    delete_channel_parser = delete_kinds.add_parser("channel", help="Delete a broadcast channel, for every subscriber")
    delete_topic_parser = delete_kinds.add_parser("topic", help="Delete a topic in a forum group")
    delete_topic_parser.add_argument("--topic", required=True, type=positive_int, help="Topic ID to delete")

    for kind_parser in (delete_group_parser, delete_channel_parser, delete_topic_parser):
        kind_parser.add_argument("--chat", required=True, help="Chat username, link, or ID")
        kind_parser.add_argument(
            "--execute", action="store_true", help="Actually delete it after typing its exact title"
        )

    subparsers.add_parser("doctor", help="Check local setup without printing secrets")

    return parser


# -- the pieces every command needs ---------------------------------------


async def _acting(client, report: Reporter):
    """The account this run acts as, fetched once and reused by plan, envelope and audit."""
    if report.acting is None:
        provider = await AccountIdentity.open(client)
        report.set_identity(provider.identity(), me=provider.user)
    return report.acting


async def _rights(client, report: Reporter, peer) -> Rights:
    me = report.me
    if me is None:
        me = await client.get_me()
        report.me = me
    return await ChatPermissions(client, me).probe(peer)


def _require_delete_permission(rights: Rights, *, what: str) -> None:
    """The gate `clear-messages` and `delete topic` have always had, now named by right."""
    if "delete_messages" in rights.held:
        return
    if rights.unknown(("delete_messages",)):
        raise CommandError(
            f"Telegram would not report your permissions in this chat ({rights.unreadable}), "
            f"and {what} needs delete_messages.",
            code="PERMISSION_DENIED",
            hint="Open the chat in Telegram and check you are an admin who can delete messages.",
        )
    raise CommandError(
        "Current user lacks Telegram delete_messages permission in this chat.",
        code="PERMISSION_DENIED",
        hint=f"Ask an admin for delete_messages, or run {what} as an account that has it.",
    )


def _entity_title(entity, fallback: str) -> str:
    """A chat's name for the preview: a title, a person's name, or what was typed."""
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or str(getattr(entity, "username", None) or fallback)


def _write_json(payload, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json_text(payload) + "\n", encoding="utf-8")


# -- the commands ----------------------------------------------------------


async def _run_discover(client, args, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    chats = filter_chats(await discover_chats(client), admin_only=not args.all_chats)
    payload = [chat.to_dict() for chat in chats]
    if args.json_output:
        _write_json(payload, args.json_output)
    elif not report.machine:
        print(format_discovery_table(chats))
    for chat in payload:
        report.record(chat)
    report.result({"chats": payload}, status="ok" if payload else "empty")
    return 0


async def _run_clear_messages(client, args, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    chat = ChatTargets.chat_target(resolved, args.chat)
    report.set_target(chat)

    rights = await _rights(client, report, peer)
    _require_delete_permission(rights, what="clearing messages")

    if args.all_topics:
        topics = await get_forum_topics(client, peer)
    else:
        topics = await get_forum_topics_by_ids(client, peer, args.topics)

    identity = await _acting(client, report)
    targets = [ChatTargets.topic_target(chat, topic) for topic in topics]
    plan, warnings = build_plan(
        identity=identity,
        command="clear-messages",
        targets=targets,
        mutations=[Mutation("clear_messages", target.rid) for target in targets],
        approval="typed_delete",
        rights=rights,
        required=CLEAR_RIGHTS,
    )
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)

    async def rebuild():
        fresh = await get_forum_topics_by_ids(client, peer, [topic.id for topic in topics])
        return build_plan(
            identity=identity,
            command="clear-messages",
            targets=[ChatTargets.topic_target(chat, topic) for topic in fresh],
            mutations=[Mutation("clear_messages", ChatTargets.topic_target(chat, topic).rid) for topic in fresh],
            approval="typed_delete",
            rights=rights,
            required=CLEAR_RIGHTS,
        )[0]

    confirm = partial(confirm_clear_topic_messages, **(report.confirm_io() if args.execute else {}))
    result = await delete_topic_messages(
        client,
        peer,
        topics,
        execute=args.execute,
        batch_size=args.batch_size,
        progress=report.info,
        confirm=confirm,
        recheck=recheck_for(plan, rebuild) if args.execute else None,
    )

    status = "dry_run" if result.dry_run else "cancelled" if result.cancelled else "ok"
    if status == "ok":
        evidence = await read_back(
            "topic message counts",
            lambda: _remaining_messages(client, peer, topics),
        )
        report.set_evidence(evidence)
        report.audit(plan, status=status, evidence=evidence)
    report.printed_result(result.to_dict(), status=status)
    return 1 if result.cancelled else 0


async def _remaining_messages(client, peer, topics) -> str:
    counts = []
    for topic in topics:
        remaining = 0
        async for _message in client.iter_messages(peer, reply_to=topic.id, wait_time=1):
            remaining += 1
        # The message that opened the topic is never cleared, so an emptied
        # topic reads as one, not zero. Say what is actually there.
        counts.append(f"topic {topic.id} now holds {remaining} message(s)")
    return "; ".join(counts) or "no topics were named"


async def _run_search(client, args, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    report.set_target(ChatTargets.chat_target(resolved, args.chat))
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

    for record in records:
        report.record(record)

    if args.output:
        write_records(records, args.output, args.format)
    elif args.format == "csv":
        raise ValueError("--output is required for CSV export")
    elif not report.machine:
        print(format_message_records(records))

    result = {"matched": len(records), "format": args.format, "output": args.output}
    if not args.output:
        # No file was written, so the envelope is the only place the messages
        # can be: the same rows the table would have shown.
        result["messages"] = records
    report.result(result, status="ok" if records else "empty")
    return 0


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


async def _run_send(client, args, config, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    files = _attachments(getattr(args, "files", None))
    text = _message_text(args.text, has_files=bool(files))
    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    chat = ChatTargets.chat_target(resolved, args.chat)

    topic = None
    if args.topic is not None:
        topics = await get_forum_topics_by_ids(client, peer, [args.topic])
        topic = topics[0] if topics else None

    destination = chat if topic is None else ChatTargets.topic_target(chat, topic)
    report.set_target(destination)
    target = SendTarget(chat_id=resolved.id, chat_title=chat.title, topic=topic)

    rights = await _rights(client, report, peer)
    identity = await _acting(client, report)
    plan, warnings = build_plan(
        identity=identity,
        command="send",
        targets=[destination],
        mutations=[Mutation("send_message", destination.rid, {"files": len(files), "text": bool(text)})],
        approval="yes_allowlist" if args.yes else "prompt_y",
        rights=rights,
        required=SEND_RIGHTS,
    )
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    require_rights(plan, rights, SEND_RIGHTS)

    confirm = None
    if args.yes:
        require_send_allowed(
            config.send_allowlist,
            chat_id=resolved.id,
            username=getattr(resolved.entity, "username", None),
            topic_id=args.topic,
        )
    else:
        sender = _entity_title(report.me or await client.get_me(), "you")
        preview = format_send_preview(target, text, sender=sender, files=files)
        confirm = partial(confirm_send, preview, **report.confirm_io())

    async def rebuild():
        again = await resolve_chat(client, args.chat)
        fresh_chat = ChatTargets.chat_target(again, args.chat)
        fresh = fresh_chat
        if args.topic is not None:
            found = await get_forum_topics_by_ids(client, again.input_entity, [args.topic])
            fresh = ChatTargets.topic_target(fresh_chat, found[0]) if found else fresh_chat
        return build_plan(
            identity=identity,
            command="send",
            targets=[fresh],
            mutations=[Mutation("send_message", fresh.rid, {"files": len(files), "text": bool(text)})],
            approval="yes_allowlist" if args.yes else "prompt_y",
            rights=rights,
            required=SEND_RIGHTS,
        )[0]

    result = await send_message(
        client, peer, target, text, files=files, confirm=confirm, recheck=recheck_for(plan, rebuild)
    )

    status = "cancelled" if result.cancelled else "ok"
    if status == "ok":
        evidence = await read_back(
            "the sent message",
            lambda: _sent_message(client, peer, destination, result.message_id),
        )
        report.set_evidence(evidence)
        report.audit(plan, status=status, evidence=evidence)
    report.printed_result(result.to_dict(), status=status)
    return 1 if result.cancelled else 0


async def _sent_message(client, peer, destination, message_id) -> str:
    message = await client.get_messages(peer, ids=message_id)
    if message is None:
        raise LookupError("Telegram returned no message under that id")
    return f"message {int(getattr(message, 'id'))} is in {destination.display}"


async def _run_create(client, args, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    if args.create_kind is None:
        raise ValueError("create needs one of: group, channel, topic.")

    chat_title = None
    peer = None
    chat_id = None
    chat = None
    rights = Rights(frozenset(), frozenset())
    if args.create_kind == "topic":
        resolved = await resolve_chat(client, args.chat)
        peer = resolved.input_entity
        chat_id = resolved.id
        chat = ChatTargets.chat_target(resolved, args.chat)
        chat_title = chat.title
        report.set_target(chat)
        rights = await _rights(client, report, peer)

    forum = bool(getattr(args, "forum", False))
    identity = await _acting(client, report)
    command = f"create {args.create_kind}"
    required = CREATE_TOPIC_RIGHTS if args.create_kind == "topic" else ()
    if args.create_kind == "topic":
        mutations = [Mutation("create_topic", chat.rid, {"title": args.title})]
        targets = [chat]
    else:
        # Nothing exists yet to point a mutation at, so it points at the
        # account doing the creating -- which is also the only thing a
        # preflight could be about.
        mutations = [Mutation(f"create_{args.create_kind}", identity.id, {"title": args.title, "forum": forum})]
        targets = []
    plan, warnings = build_plan(
        identity=identity,
        command=command,
        targets=targets,
        mutations=mutations,
        approval="prompt_y",
        rights=rights,
        required=required,
    )
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    require_rights(plan, rights, required)

    confirm = None
    if not args.yes:
        preview = format_create_preview(
            args.create_kind,
            args.title,
            about=getattr(args, "about", None),
            forum=forum,
            chat_title=chat_title,
        )
        confirm = partial(confirm_create, preview, **report.confirm_io())

    recheck = None
    if args.create_kind == "topic":

        async def rebuild():
            again = await resolve_chat(client, args.chat)
            fresh = ChatTargets.chat_target(again, args.chat)
            return build_plan(
                identity=identity,
                command=command,
                targets=[fresh],
                mutations=[Mutation("create_topic", fresh.rid, {"title": args.title})],
                approval="prompt_y",
                rights=rights,
                required=required,
            )[0]

        recheck = recheck_for(plan, rebuild)

    if args.create_kind == "group":
        created = await create_group(client, args.title, about=args.about, forum=forum, confirm=confirm)
    elif args.create_kind == "channel":
        created = await create_channel(client, args.title, about=args.about, confirm=confirm)
    else:
        created = await create_topic(
            client, peer, chat_id=chat_id, title=args.title, confirm=confirm, recheck=recheck
        )

    status = "cancelled" if created.cancelled else "ok"
    if status == "ok":
        evidence = await read_back("the new " + args.create_kind, lambda: _created(client, peer, created))
        report.set_evidence(evidence)
        report.audit(plan, status=status, evidence=evidence)
    report.printed_result(created.to_dict(), status=status)
    return 1 if created.cancelled else 0


async def _created(client, peer, created) -> str:
    if created.kind == "topic":
        found = await get_forum_topics_by_ids(client, peer, [created.topic_id])
        if not found or found[0].title != created.title:
            raise LookupError("the new topic is not in the group's topic list yet")
        return f"topic {created.topic_id} ({created.title}) is in chat {created.id}"
    entity = await client.get_entity(created.id)
    return f"{created.kind} {_entity_title(entity, created.title)} exists as {created.id}"


async def _run_delete(client, args, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    if args.delete_kind is None:
        raise ValueError("delete needs one of: group, channel, topic.")

    resolved = await resolve_chat(client, args.chat)
    peer = resolved.input_entity
    chat = ChatTargets.chat_target(resolved, args.chat)
    title = chat.title
    identity = await _acting(client, report)
    rights = await _rights(client, report, peer)
    command = f"delete {args.delete_kind}"

    if args.delete_kind == "topic":
        topics = await get_forum_topics_by_ids(client, peer, [args.topic])
        if not topics:
            raise CommandError(
                f"No topic {args.topic} in {title} - list them with `discover`.",
                code="TARGET_NOT_FOUND",
                hint="telegram-tools discover",
            )
        target = ChatTargets.topic_target(chat, topics[0])
        report.set_target(target)
        required = DELETE_TOPIC_RIGHTS
        mutations = [Mutation("delete_topic", target.rid)]
    else:
        # The kind names what the user believes this chat is. Checking it against
        # what Telegram says is the second lock on the gate, and it is also how a
        # basic group gets refused rather than silently mishandled.
        chat_type = classify_entity(resolved.entity)
        actual = kind_for_type(chat_type)
        if actual != args.delete_kind:
            if actual is None:
                raise CommandError(
                    f"{title} is a {chat_type}, which telegram-tools does not delete - "
                    "`create` cannot make one back, so `delete` will not take one away. "
                    "Delete it in Telegram itself.",
                    code="PLATFORM_UNSUPPORTED",
                )
            raise CommandError(
                f"{title} is a {chat_type}, not a {args.delete_kind}. "
                f"`delete {args.delete_kind}` accepts: {', '.join(DELETE_KIND_TYPES[args.delete_kind])}.",
                code="TARGET_KIND_MISMATCH",
                hint=f"telegram-tools delete {actual} --chat {args.chat}",
            )
        target = chat
        report.set_target(target)
        required = DELETE_CHAT_RIGHTS
        mutations = [Mutation("delete_chat", target.rid, {"kind": args.delete_kind})]

    plan, warnings = build_plan(
        identity=identity,
        command=command,
        targets=[target],
        mutations=mutations,
        approval="typed_name",
        rights=rights,
        required=required,
    )
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    require_rights(plan, rights, required)

    async def rebuild():
        again = await resolve_chat(client, args.chat)
        fresh_chat = ChatTargets.chat_target(again, args.chat)
        if args.delete_kind == "topic":
            found = await get_forum_topics_by_ids(client, again.input_entity, [args.topic])
            fresh = ChatTargets.topic_target(fresh_chat, found[0]) if found else fresh_chat
            fresh_mutations = [Mutation("delete_topic", fresh.rid)]
        else:
            fresh = fresh_chat
            fresh_mutations = [Mutation("delete_chat", fresh.rid, {"kind": args.delete_kind})]
        return build_plan(
            identity=identity,
            command=command,
            targets=[fresh],
            mutations=fresh_mutations,
            approval="typed_name",
            rights=rights,
            required=required,
        )[0]

    gate = partial(confirm_delete, **(report.confirm_io() if args.execute else {}))
    recheck = recheck_for(plan, rebuild) if args.execute else None

    if args.delete_kind == "topic":
        result = await delete_topic(
            client,
            peer,
            topics[0],
            chat_id=resolved.id,
            chat_title=title,
            execute=args.execute,
            confirm=gate,
            progress=report.info,
            recheck=recheck,
        )
    else:
        result = await delete_chat(
            client,
            peer,
            kind=args.delete_kind,
            title=title,
            chat_id=resolved.id,
            execute=args.execute,
            confirm=gate,
            progress=report.info,
            recheck=recheck,
        )

    status = "dry_run" if result.dry_run else "cancelled" if result.cancelled else "ok"
    if status == "ok":
        evidence = await read_back("the deleted " + result.kind, lambda: _gone(client, peer, result))
        report.set_evidence(evidence)
        report.audit(plan, status=status, evidence=evidence)
    report.printed_result(result.to_dict(), status=status)
    return 1 if result.cancelled else 0


async def _gone(client, peer, result) -> str:
    if result.kind == "topic":
        found = await get_forum_topics_by_ids(client, peer, [result.topic_id])
        # A topic Telegram no longer knows comes back as the placeholder this
        # tool builds for an id it did not answer for: title is the bare id.
        if found and found[0].title != str(result.topic_id):
            raise LookupError("the topic is still in the group's topic list")
        return f"topic {result.topic_id} is gone from chat {result.id}"
    try:
        await client.get_entity(result.id)
    except Exception:  # noqa: BLE001 - Telegram refusing to find it is the confirmation
        return f"{result.kind} {result.title} ({result.id}) is gone"
    raise LookupError("Telegram still lists the chat; it may be answering from a cache")


EDIT_FLAGS = ("name", "bio", "description", "commands", "clear_commands", "photo", "remove_photo", "group_rights", "channel_rights")


def bot_edit_requests(args) -> dict:
    requested = {}
    for flag in EDIT_FLAGS:
        value = getattr(args, flag, None)
        if value is None or value is False:
            continue
        requested[flag] = value
    return requested


def _bot_result(profile, plan, applied, *, cancelled: bool) -> dict:
    return {
        "bot_id": profile.id,
        "username": profile.username,
        "applied": list(applied),
        "skipped": list(plan.skipped),
        "cancelled": cancelled,
    }


def _emit_bot_result(report: Reporter, result: dict, json_output: str | None, *, status: str) -> None:
    report.result(result, status=status)
    if json_output:
        _write_json(result, json_output)
    elif not report.machine:
        print(json_text(result))


async def _run_bots(client, args, config, *, report: Reporter | None = None) -> int:
    report = report or Reporter()
    requested = bot_edit_requests(args)
    if requested and not args.bot:
        raise ValueError("--bot is required when editing a bot.")

    if not args.bot:
        bots = await list_bots(client)
        payload = [bot.to_dict() for bot in bots]
        if args.json_output:
            _write_json(payload, args.json_output)
        elif not report.machine:
            print(format_bot_table(bots))
        for bot in payload:
            report.record(bot)
        report.result({"bots": payload}, status="ok" if payload else "empty")
        return 0

    token, reference = resolve_bot_token(config.bot_tokens, args.bot)

    resolved = await resolve_bot(client, reference)
    profile = await get_bot_profile(client, resolved)
    # A nickname can name the wrong bot, so the token is only kept if its own bot id
    # is the bot that was resolved. Ids only - never any part of a token in an error.
    if token is None:
        token = lookup_bot_token(config.bot_tokens, profile.id)
    if token is not None and bot_id_from_token(token) != profile.id:
        raise CommandError(
            f"The stored token is for bot {bot_id_from_token(token)}, not {profile.id}. Check TELEGRAM_BOT_TOKENS.",
            code="IDENTITY_MISMATCH",
            hint="Fix the nickname in TELEGRAM_BOT_TOKENS in ~/.telegram-tools/.env.",
        )

    bot_target = _bot_target(profile)
    report.set_target(bot_target)

    if not requested:
        if args.json_output:
            _write_json(profile.to_dict(), args.json_output)
        elif not report.machine:
            print(format_bot_profile(profile))
        report.result(profile.to_dict())
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

    edits = build_edit_plan(profile, requested)
    if edits.is_empty:
        _emit_bot_result(report, _bot_result(profile, edits, [], cancelled=False), args.json_output, status="ok")
        return 0

    if edits.bot_changes and token is None:
        fields = ", ".join(change.field for change in edits.bot_changes)
        raise CommandError(
            f"{fields} can only be changed with that bot's token. "
            "Set TELEGRAM_BOT_TOKENS=nickname:token[,nickname:token] in ~/.telegram-tools/.env.",
            code="CONFIG_MISSING",
            hint="Add that bot's token to TELEGRAM_BOT_TOKENS in ~/.telegram-tools/.env.",
        )

    identity = await _acting(client, report)
    plan, _warnings = build_plan(
        identity=identity,
        command="bots",
        targets=[bot_target],
        mutations=[
            Mutation("edit_bot", bot_target.rid, {"field": change.field})
            for change in (*edits.owner_changes, *edits.bot_changes)
        ],
        approval="prompt_y",
        rights=Rights(frozenset(), frozenset()),
        required=(),
    )
    report.set_plan(plan)

    # Named on every edit run, --yes included: it is the one mode with no confirm diff,
    # so a mistyped token nickname acting on the wrong bot would otherwise go unnamed.
    report.info(format_edit_heading(profile))
    if not args.yes:
        if not confirm_bot_edits(edits, **report.confirm_io()):
            _emit_bot_result(report, _bot_result(profile, edits, [], cancelled=True), args.json_output, status="cancelled")
            return 1

    applied: list[str] = []
    try:
        await apply_owner_edits(client, resolved.input_user, edits.owner_changes, applied)
        if edits.bot_changes:
            async with bot_client(config, token) as bot:
                await apply_bot_edits(bot, edits.bot_changes, applied)
    finally:
        if applied:
            evidence = await read_back("the bot profile", lambda: _bot_readback(client, resolved, applied))
            report.set_evidence(evidence)
            report.audit(plan, status="ok", evidence=evidence)
        _emit_bot_result(report, _bot_result(profile, edits, applied, cancelled=False), args.json_output, status="ok")
    return 0


def _bot_target(profile) -> Target:
    """An owned bot as a target: what `bots` edits, named the way its screens name it."""
    label = f"@{profile.username}" if profile.username else f"bot {profile.id}"
    return Target(
        rid=str(_rid.make(PREFIX, "bot", profile.id)),
        kind="bot",
        title=profile.name or label,
        path=(label,),
        platform=PLATFORM,
        ids={"bot": str(profile.id)},
    )


async def _bot_readback(client, resolved, applied) -> str:
    profile = await get_bot_profile(client, resolved)
    return f"bot {profile.id} now reads name={profile.name!r}, applied {', '.join(applied)}"


# -- running one -----------------------------------------------------------


async def run(args, *, client=None, config=None, report: Reporter | None = None) -> int:
    """Run one command.

    The menu passes its own already-started client so a whole menu session is one
    connection: two Telethon clients against one SQLite session file is a lock
    error waiting to happen. A caller that passes a client owns it, so it is not
    disconnected here. It passes no reporter either, which is what keeps the
    menu on the human path.
    """
    report = report or Reporter()

    if args.command == "doctor":
        return run_doctor(report=report)

    if config is None:
        config = load_config()

    audit_path = getattr(config, "audit_path", None)
    if audit_path is not None and report.audit_log is None:
        report.audit_log = AuditLog(audit_path)

    owns_client = client is None
    if owns_client:
        client = await start_client(create_client(config))

    try:
        if report.machine:
            await _acting(client, report)
        if args.command == "discover":
            return await _run_discover(client, args, report=report)
        if args.command == "clear-messages":
            return await _run_clear_messages(client, args, report=report)
        if args.command == "search":
            return await _run_search(client, args, report=report)
        if args.command == "bots":
            return await _run_bots(client, args, config, report=report)
        if args.command == "send":
            return await _run_send(client, args, config, report=report)
        if args.command == "create":
            return await _run_create(client, args, report=report)
        if args.command == "delete":
            return await _run_delete(client, args, report=report)
        raise ValueError(f"Unknown command: {args.command}")
    finally:
        if owns_client:
            await client.disconnect()


def command_name(args) -> str:
    """What the envelope calls this run: the subcommand, and its kind where it has one."""
    kind = getattr(args, "create_kind", None) or getattr(args, "delete_kind", None)
    return f"{args.command} {kind}" if kind else str(args.command or "")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    report = Reporter(
        machine=bool(getattr(args, "json_envelope", False)),
        jsonl=bool(getattr(args, "jsonl", False)),
        command=command_name(args),
        args=args,
        argv=argv,
    )
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
        return report.finish(asyncio.run(run(args, report=report)))
    except (KeyboardInterrupt, EOFError) as exc:
        if report.machine:
            return report.failed(error_for(exc))
        print()
        return 130
    except (ConfigError, EntityResolutionError, ValueError) as exc:
        error = error_for(exc)
        if report.machine and error is not None:
            return report.failed(error)
        parser.error(str(exc))
    except PermissionError as exc:
        if report.machine:
            return report.failed(error_for(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # A missing or unreadable path is a usage mistake, not a crash. Must stay
        # below PermissionError, which is an OSError subclass with its own exit.
        parser.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - a platform failure is an answer under --json
        if not report.machine:
            raise
        return report.failed(platform_error(exc))


if __name__ == "__main__":
    raise SystemExit(main())
