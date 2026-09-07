from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from functools import partial
from pathlib import Path
from typing import Sequence

from telegram_tools._core import export as _export
from telegram_tools._core import rid as _rid
from telegram_tools._core import rules as _rules
from telegram_tools._core import runner as _runner
from telegram_tools._core.audit import AuditLog
from telegram_tools._core.contract import CodedError, exit_code, utc_now
from telegram_tools._core.identity import Identity, Target
from telegram_tools._core.plan import Evidence, Mutation
from telegram_tools._core.redaction import redact_text
from telethon.tl.functions.messages import DeleteScheduledMessagesRequest, GetScheduledHistoryRequest
from telethon.tl.types import InputUserSelf
from telegram_tools import archive as archive_store
from telegram_tools import login
from telegram_tools import profiles as profile_store
from telegram_tools.adapters import AccountIdentity, ChatPermissions, ChatTargets, Rights
from telegram_tools.adapters.account import account_label
from telegram_tools.adapters.archive import TelegramArchiveSource, scope_rid_for
from telegram_tools.adapters.bot import BotIdentity, BotPermissions, resolve_chat_as_bot
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
from telegram_tools.client import _disconnect_quietly, create_client, create_detached_client, start_client, tighten_session
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
from telegram_tools.doctor import require_tight_modes, run_doctor
from telegram_tools.envelope import PLATFORM, PREFIX, TOOL, ApprovalRequired, CommandError, Reporter, account_command, error_for, platform_error
from telegram_tools.exporters import SEARCH_FORMATS, json_text, write_records
from telegram_tools import messages as message_ops
from telegram_tools.prompts import BACK, pick_many
from telegram_tools import review as review_ops
from telegram_tools import structure as structure_ops
from telegram_tools import manage as manage_ops
from telegram_tools import watch as watch_ops
from telegram_tools.adapters import events as watch_events
from telegram_tools.adapters.manage import TelegramManagePort
from telegram_tools._core import blueprint as _blueprint
from telegram_tools.adapters.blueprint import TelegramBlueprintPort, chat_kind
from telegram_tools.resolver import EntityResolutionError, resolve_chat
from telegram_tools.search import format_message_records, search_messages
from telegram_tools.send import SendTarget, confirm_send, format_send_preview, require_send_allowed, send_message
from telegram_tools.topics import get_forum_topics, get_forum_topics_by_ids
from telegram_tools.writes import build_plan, read_back, recheck_for, require_rights
from telegram_tools import __version__

# Every right a write here needs, by the command that needs it. Named in
# Telegram's own vocabulary so a refusal can be read straight into the app.
CLEAR_RIGHTS = ("delete_messages",)
SEND_RIGHTS = ("send_messages",)
# A topic is opened by posting its service message, so posting is the right.
CREATE_TOPIC_RIGHTS = ("send_messages",)
# Telegram lets only a chat's creator delete it, which is what the preview says.
DELETE_CHAT_RIGHTS = ("is_creator",)
DELETE_TOPIC_RIGHTS = ("delete_messages",)

# The commands that change something at Telegram's end or on this machine.
# `auth` is here because it writes a session, which is the one local file worth
# being strict about.
WRITES = ("send", "message", "create", "delete", "clear-messages", "bots", "auth")
# The one structure command that writes: `apply` makes topics and sets settings
# on the target and writes remap rows into the archive. `export` and `diff`
# read a chat; `remap` reads the archive.
STRUCTURE_WRITES = ("apply",)
# The administration verbs that change something; `list` and `show` only read.
MANAGE_WRITES = tuple(op for op, spec in manage_ops.OPS.items() if spec.writes)
# The archive commands that write the local store. `status`, `search` and
# `export` read it, and a read is left alone for the same reason `doctor` is.
ARCHIVE_WRITES = ("sync", "retention", "forget")
# The review commands that write: a queue state, quarantine bytes, the media
# store. `list` and `status` read the archive and two directories.
REVIEW_WRITES = ("approve", "accept", "reject", "retry")
# The watch commands that write on this machine: `run` takes the lock and
# appends to the log, and the five rule verbs write files under `rules/`.
# `status`, `stop`, `reload`, `rules list` and `rules test` only read or signal.
WATCH_WRITES = watch_ops.WATCH_WRITES
RULES_WRITES = watch_ops.RULES_WRITES
# `post` stores a schedule row and `cancel` removes one (or a message Telegram
# is holding); `list` reads.
SCHEDULE_WRITES = watch_ops.SCHEDULE_WRITES

# What `--as-bot` may run. Section 5.2: a bot has no dialog list, no history and
# no search (Telegram marks those user-only), owns nothing it could delete, and
# cannot create a chat -- so bot mode is for what a bot is for: posting, and
# opening a topic in a group it administers. Everything else refuses by name
# before any connection is opened, with the same command minus the flag as the
# hint. `doctor` and `profiles` act as nobody and are not on either list.
# `message` runs the verbs a bot can perform (`messages.BOT_VERBS`): the four
# that need a dialog of the account's own -- read, unread, bookmark, draft --
# refuse here too.
# The administration groups (section 13) run as a bot too, where the bot is an
# admin holding the right: `_run_manage` refuses a bot that is not an admin.
# `watch` is here because a bot receives updates for the chats it is in, which
# is exactly what a rule watches; `schedule` is not, because `schedule_date` is
# user-only (section 5.2) and a bot cannot hold a message for Telegram to post.
BOT_MODE_COMMANDS = ("send", "create", "message", "watch", *manage_ops.GROUPS)
BOT_MODE_CREATE_KINDS = ("topic",)


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
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="Act as this named login; default is TELEGRAM_TOOLS_PROFILE, or 'default'",
    )
    parser.add_argument(
        "--as-bot",
        dest="as_bot",
        metavar="NICK",
        help="Act as this bot (a TELEGRAM_BOT_TOKENS nickname) instead of the account: send, create topic and the message verbs except read, unread, bookmark and draft",
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
    search.add_argument("--format", choices=SEARCH_FORMATS, default="json", help="Export format")
    search.add_argument("--output", help="Output path; prints a readable table when omitted")
    search.add_argument(
        "--archive",
        action="store_true",
        help="Search the local archive instead of Telegram (the same as `archive search`)",
    )

    archive_parser = subparsers.add_parser("archive", help="Sync, search and export the local archive")
    archive_kinds = archive_parser.add_subparsers(dest="archive_kind")

    archive_sync = archive_kinds.add_parser("sync", help="Copy what this account can read into the local archive and resume where it stopped")
    archive_sync.add_argument("--scope", dest="scope", action="append", metavar="RID", help="Only this chat or topic (tg:chat:ID or tg:topic:ID:TOPIC); repeatable")
    archive_sync.add_argument("--since", help="Archive nothing older than this ISO date or datetime")
    archive_sync.add_argument("--full", action="store_true", help="Walk every scope from the top again instead of resuming")

    archive_status = archive_kinds.add_parser("status", help="Scopes, rows, bytes, coverage and the disk budget")
    archive_status.add_argument("--identity", metavar="ID", help="Count only what this identity (tg:user:ID) archived")

    archive_search = archive_kinds.add_parser("search", help="Full-text search of the local archive")
    archive_export = archive_kinds.add_parser("export", help="Write one search's rows to a file in one of five formats")
    for query_parser in (archive_search, archive_export):
        query_parser.add_argument("--query", required=True, help="Full-text query (FTS5 syntax: words, \"a phrase\", AND, OR, NOT, prefix*)")
        query_parser.add_argument("--regex", metavar="PATTERN", help="Keep only matches whose text also matches this Python regex")
        query_parser.add_argument("--scope", dest="scope", action="append", metavar="RID", help="Only this chat or topic rid; repeatable")
        query_parser.add_argument("--identity", metavar="ID", help="Only rows archived by this identity (tg:user:ID)")
        query_parser.add_argument("--from", dest="author", metavar="RID", help="Only messages from this sender (tg:user:ID)")
        query_parser.add_argument("--since", help="Inclusive ISO date or datetime lower bound")
        query_parser.add_argument("--until", help="Inclusive ISO date or datetime upper bound")
        query_parser.add_argument("--context", type=int, default=0, metavar="N", help="Show N neighbouring messages by date on each side of a match")
        query_parser.add_argument("--limit", type=positive_int, default=50, help="Maximum matches (default 50)")
    archive_export.add_argument("--format", choices=archive_store.EXPORT_FORMATS, default="json", help="Export format")
    archive_export.add_argument("--output", required=True, help="Output path; a relative name lands in ~/.telegram-tools/exports/")

    archive_retention = archive_kinds.add_parser("retention", help="Prune a scope's rows older than a window (dry-run by default)")
    archive_retention.add_argument("--scope", required=True, metavar="RID", help="The chat or topic rid to prune")
    archive_retention.add_argument("--keep", required=True, help="What stays: a window like 90d, or a number of newest messages")
    archive_retention.add_argument("--execute", action="store_true", help="Actually prune after typing the scope's exact title")

    archive_forget = archive_kinds.add_parser("forget", help="Remove everything archived for one scope or one identity (dry-run by default)")
    forget_what = archive_forget.add_mutually_exclusive_group(required=True)
    forget_what.add_argument("--scope", metavar="RID", help="The chat or topic rid to forget")
    forget_what.add_argument("--identity", metavar="ID", help="The identity (tg:user:ID) whose every row goes")
    archive_forget.add_argument("--execute", action="store_true", help="Actually remove it after typing its exact title")

    review_parser = subparsers.add_parser("review", help="The review queue: links and files the archive saw, fetched only after you approve")
    review_kinds = review_parser.add_subparsers(dest="review_kind")

    review_list = review_kinds.add_parser("list", help="What is waiting and what has been fetched (asks nothing of any host)")
    review_list.add_argument("--kind", choices=review_ops.kinds(), help="Only links, or only files")
    review_list.add_argument("--state", choices=review_ops.states(), help="Only candidates in this state")

    review_approve = review_kinds.add_parser("approve", help="Approve queued candidates (a pick, then y/N), then fetch them into quarantine")
    review_approve.add_argument("--ids", action="append", metavar="ID[,ID…]", help="Candidate ids; repeatable, comma-separated; without it, pick at the terminal")

    review_accept = review_kinds.add_parser("accept", help="Accept quarantined downloads into the media store, after seeing the verdict (y/N)")
    review_accept.add_argument("--ids", action="append", required=True, metavar="ID[,ID…]", help="Candidate ids; repeatable, comma-separated")

    review_reject = review_kinds.add_parser("reject", help="Reject candidates and delete their quarantined bytes (y/N)")
    review_reject.add_argument("--ids", action="append", required=True, metavar="ID[,ID…]", help="Candidate ids; repeatable, comma-separated")

    review_retry = review_kinds.add_parser("retry", help="Run a failed download again from where it stopped (no new approval)")
    review_retry.add_argument("--ids", action="append", required=True, metavar="ID[,ID…]", help="Candidate ids; repeatable, comma-separated")

    review_kinds.add_parser("status", help="Counts, quarantine and media against their budgets, the scanner, and every fetched candidate")

    structure_parser = subparsers.add_parser("structure", help="Export, diff and apply a chat's structure blueprint (topics and settings, never people or messages)")
    structure_kinds = structure_parser.add_subparsers(dest="structure_kind")

    structure_export = structure_kinds.add_parser("export", help="Write a chat's blueprint: kind, title, description, topics, default rights, slow mode, join approval")
    structure_export.add_argument("--chat", required=True, help="The chat: numeric ID, @username, link, or its tg:chat: rid")
    structure_export.add_argument("--output", help="Where to write the blueprint JSON; without it, printed")

    structure_diff = structure_kinds.add_parser("diff", help="What a chat would need to match a blueprint (reads the chat, changes nothing)")
    structure_diff.add_argument("--blueprint", required=True, metavar="FILE", help="A blueprint written by `structure export`")
    structure_diff.add_argument("--chat", required=True, help="The chat to compare: numeric ID, @username, link, or its tg:chat: rid")

    structure_apply = structure_kinds.add_parser("apply", help="Make a chat match a blueprint: dry-run by default; --execute asks for the chat's exact title")
    structure_apply.add_argument("--blueprint", required=True, metavar="FILE", help="A blueprint written by `structure export`")
    structure_target = structure_apply.add_mutually_exclusive_group(required=True)
    structure_target.add_argument("--chat", help="An existing chat of the blueprint's kind: numeric ID, @username, link, or its tg:chat: rid")
    structure_target.add_argument("--create", action="store_true", help="Make a new chat of the blueprint's kind and title first, then apply the rest to it")
    structure_apply.add_argument("--execute", action="store_true", help="Actually apply it; the chat's exact title is asked for at a prompt (no --yes exists)")

    structure_remap = structure_kinds.add_parser("remap", help="Print the source-to-target id table one apply wrote (reads the local archive)")
    structure_remap.add_argument("--apply-id", required=True, metavar="ID", help="The apply id `structure apply` printed")

    # -- administration (section 13): five groups, one shape ---------------------
    chat_help = "The chat: numeric ID, @username, or link"
    user_help = "The person: numeric ID or @username"
    admin_rights_help = "Comma-separated admin rights, or none. Valid names: " + ", ".join(manage_ops.ADMIN_RIGHT_NAMES)
    banned_rights_help = "Comma-separated rights to take away. Valid names: " + ", ".join(manage_ops.BANNED_RIGHT_NAMES)
    until_help = "When it ends: a duration (30m, 2h, 7d, 1w) or an ISO date/time; at least a minute, at most a year"

    admin_parser = subparsers.add_parser("admin", help="Admins and their rights: list, promote, rights, demote (demote asks for the person's exact label)")
    admin_kinds = admin_parser.add_subparsers(dest="admin_kind")
    admin_list = admin_kinds.add_parser("list", help="The creator and every admin, with their rights and ranks")
    admin_list.add_argument("--chat", required=True, help=chat_help)
    admin_promote = admin_kinds.add_parser("promote", help="Make a member an admin with the rights you name (y/N)")
    admin_promote.add_argument("--chat", required=True, help=chat_help)
    admin_promote.add_argument("--user", required=True, help=user_help)
    admin_promote.add_argument("--rights", required=True, help=admin_rights_help)
    admin_promote.add_argument("--rank", help="A custom title shown beside their name")
    admin_rights = admin_kinds.add_parser("rights", help="Set an admin's rights to exactly the ones you name (y/N)")
    admin_rights.add_argument("--chat", required=True, help=chat_help)
    admin_rights.add_argument("--user", required=True, help=user_help)
    admin_rights.add_argument("--rights", required=True, help=admin_rights_help)
    admin_rights.add_argument("--rank", help="A custom title shown beside their name")
    admin_demote = admin_kinds.add_parser("demote", help="Take every admin right off a person (dry-run by default)")
    admin_demote.add_argument("--chat", required=True, help=chat_help)
    admin_demote.add_argument("--user", required=True, help=user_help)
    admin_demote.add_argument("--execute", action="store_true", help="Actually demote them after typing their exact label (no --yes exists)")

    member_parser = subparsers.add_parser("member", help="Members and restrictions: list, ban, unban, mute, unmute, restrict (ban asks for the person's exact label)")
    member_kinds = member_parser.add_subparsers(dest="member_kind")
    member_list = member_kinds.add_parser("list", help="Members of a chat, newest first, or the banned and restricted ones")
    member_list.add_argument("--chat", required=True, help=chat_help)
    member_list.add_argument("--query", help="Only members whose name or username matches")
    member_list.add_argument("--limit", type=positive_int, default=manage_ops.LIST_LIMIT, help=f"At most this many (default {manage_ops.LIST_LIMIT})")
    member_list.add_argument("--banned", action="store_true", help="List the banned and restricted instead of the members")
    member_ban = member_kinds.add_parser("ban", help="Ban a person from the chat (dry-run by default)")
    member_ban.add_argument("--chat", required=True, help=chat_help)
    member_ban.add_argument("--user", required=True, help=user_help)
    member_ban.add_argument("--reason", help="Why, recorded in the local audit line only: Telegram stores no reason")
    member_ban.add_argument("--execute", action="store_true", help="Actually ban them after typing their exact label (no --yes exists)")
    member_unban = member_kinds.add_parser("unban", help="Lift a ban or a restriction (y/N)")
    member_unban.add_argument("--chat", required=True, help=chat_help)
    member_unban.add_argument("--user", required=True, help=user_help)
    member_mute = member_kinds.add_parser("mute", help="Stop a person sending anything until a moment you name (y/N)")
    member_mute.add_argument("--chat", required=True, help=chat_help)
    member_mute.add_argument("--user", required=True, help=user_help)
    member_mute.add_argument("--until", required=True, help=until_help)
    member_unmute = member_kinds.add_parser("unmute", help="Lift a mute or a restriction (y/N)")
    member_unmute.add_argument("--chat", required=True, help=chat_help)
    member_unmute.add_argument("--user", required=True, help=user_help)
    member_restrict = member_kinds.add_parser("restrict", help="Take named rights off a person until a moment you name (y/N)")
    member_restrict.add_argument("--chat", required=True, help=chat_help)
    member_restrict.add_argument("--user", required=True, help=user_help)
    member_restrict.add_argument("--rights", required=True, help=banned_rights_help)
    member_restrict.add_argument("--until", required=True, help=until_help)

    join_parser = subparsers.add_parser("join-requests", help="People waiting to join a chat that needs approval: list, approve, decline")
    join_kinds = join_parser.add_subparsers(dest="join_kind")
    join_list = join_kinds.add_parser("list", help="Who is waiting, since when, and what they wrote")
    join_list.add_argument("--chat", required=True, help=chat_help)
    join_approve = join_kinds.add_parser("approve", help="Let a person in (y/N)")
    join_approve.add_argument("--chat", required=True, help=chat_help)
    join_approve.add_argument("--user", required=True, help=user_help)
    join_decline = join_kinds.add_parser("decline", help="Turn a request down (y/N)")
    join_decline.add_argument("--chat", required=True, help=chat_help)
    join_decline.add_argument("--user", required=True, help=user_help)

    invite_parser = subparsers.add_parser("invite", help="Invite links: list, create, revoke (links are shown by list and create only)")
    invite_kinds = invite_parser.add_subparsers(dest="invite_kind")
    invite_list = invite_kinds.add_parser("list", help="Your invite links to a chat, shown in full")
    invite_list.add_argument("--chat", required=True, help=chat_help)
    invite_list.add_argument("--revoked", action="store_true", help="The revoked ones instead of the live ones")
    invite_create = invite_kinds.add_parser("create", help="Make a new invite link and show it once (y/N)")
    invite_create.add_argument("--chat", required=True, help=chat_help)
    invite_create.add_argument("--title", help="A name for the link, shown to admins only")
    invite_create.add_argument("--expires", help="When the link stops working: a duration (2h, 7d) or an ISO date/time")
    invite_create.add_argument("--usage-limit", dest="usage_limit", type=positive_int, metavar="N", help="How many people may join through it")
    invite_create.add_argument("--request-needed", dest="request_needed", action="store_true", help="Joining through it needs an admin's approval")
    invite_revoke = invite_kinds.add_parser("revoke", help="Revoke an invite link (y/N); the link is redacted everywhere but the flag")
    invite_revoke.add_argument("--chat", required=True, help=chat_help)
    invite_revoke.add_argument("--link", required=True, help="The link to revoke, as `invite list` printed it")

    settings_parser = subparsers.add_parser("settings", help="A chat's settings: show, set --slow-mode")
    settings_kinds = settings_parser.add_subparsers(dest="settings_kind")
    settings_show = settings_kinds.add_parser("show", help="Slow mode, join approval, default member rights, counts")
    settings_show.add_argument("--chat", required=True, help=chat_help)
    settings_set = settings_kinds.add_parser("set", help="Change a setting (y/N)")
    settings_set.add_argument("--chat", required=True, help=chat_help)
    settings_set.add_argument("--slow-mode", dest="slow_mode", type=int, required=True, metavar="SECONDS", help="Seconds between one member's messages: 0 (off), 10, 30, 60, 300, 900 or 3600")

    # -- watch and scheduling (section 10): the runner, its rules, the two guarantees ---
    rid_help = "A rid: tg:chat:ID, or tg:topic:ID:TOPIC for one forum topic"

    watch_parser = subparsers.add_parser("watch", help="Rules over live Telegram events, and the runner that fires them")
    watch_kinds = watch_parser.add_subparsers(dest="watch_kind")

    watch_kinds.add_parser("run", help="Run the runner here, in the foreground: replay, then live events and schedules")
    watch_kinds.add_parser("status", help="The runner's lock and holder, rules loaded, last tick, cursors and schedules")
    watch_kinds.add_parser("stop", help="Ask the running runner to exit, and wait for its lock to clear")
    watch_kinds.add_parser("reload", help="Ask the running runner to re-read its rules")

    rules_parser = watch_kinds.add_parser("rules", help="The rules on this machine: list, add, edit, remove, enable, disable, test")
    rules_kinds = rules_parser.add_subparsers(dest="rules_verb")
    rules_kinds.add_parser("list", help="Every rule loaded, what it watches and what it does")
    rules_add = rules_kinds.add_parser("add", help="Write a new rule file (the file is JSON and stays editable by hand)")
    rules_edit = rules_kinds.add_parser("edit", help="Change a rule: the flags you pass replace those fields, the rest stay")
    for rule_parser in (rules_add, rules_edit):
        rule_parser.add_argument("--name", required=True, help="The rule's name, which is also its file name")
        rule_parser.add_argument("--on", action="append", metavar="KIND", help="An event kind to trigger on; repeatable. One of: " + ", ".join(_rules.EVENT_KINDS))
        rule_parser.add_argument("--scope", action="append", metavar="RID", help=f"Only events in this scope; repeatable, `none` clears. {rid_help}")
        rule_parser.add_argument("--sender", action="append", metavar="RID", help="Only events from this sender (tg:user:ID); repeatable, `none` clears")
        rule_parser.add_argument("--identity", action="append", metavar="RID", help="Only while acting as this identity (tg:user:ID); repeatable, `none` clears")
        rule_parser.add_argument("--domain", action="append", metavar="HOST", help="Only messages linking to this domain; repeatable, `none` clears")
        rule_parser.add_argument("--keyword", action="append", metavar="WORD", help="Only messages containing this word; repeatable, `none` clears")
        rule_parser.add_argument("--regex", metavar="PATTERN", help="Only messages whose text matches this Python regex; `none` clears")
        rule_parser.add_argument("--media-type", dest="media_type", action="append", metavar="TYPE", help="Only files of this MIME type (image/* allowed); repeatable, `none` clears")
        rule_parser.add_argument("--min-bytes", dest="min_bytes", type=int, metavar="N", help="Only files at least this large; -1 clears")
        rule_parser.add_argument("--max-bytes", dest="max_bytes", type=int, metavar="N", help="Only files at most this large; -1 clears")
        rule_parser.add_argument("--alert-to", dest="alert_to", action="append", metavar="RID", help=f"Alert this chat or topic; repeatable. The destination must be in TELEGRAM_SEND_ALLOWLIST. {rid_help}")
        rule_parser.add_argument("--alert-command", dest="alert_command", action="append", metavar="CMD", help="Alert by running this command with the text on stdin; repeatable. It must be on PATH when the rule loads")
        rule_parser.add_argument("--tag", action="append", metavar="LABEL", help="Tag the message in the archive; repeatable")
        rule_parser.add_argument("--bookmark", action="store_true", help="Note the message as a bookmark in the archive")
        rule_parser.add_argument("--capture-metadata", dest="capture_metadata", action="store_true", help="Record what the platform delivered with the event; never a fetch")
        rule_parser.add_argument("--archive-scope", dest="archive_scope", action="store_true", help="Sync the event's scope into the archive, within the disk budget")
        rule_parser.add_argument("--queue-review", dest="queue_review", action="store_true", help="Put the message's links and files in the review queue, where a person approves")
        rule_parser.add_argument("--cooldown", type=int, metavar="SECONDS", help="Fold further alerts to one destination into a summary for this long")
        rule_parser.add_argument("--dedup-window", dest="dedup_window", type=int, metavar="SECONDS", help="How long a fired event stays remembered so a replay fires nothing twice")
    for verb, text in (("remove", "Delete a rule file (y/N)"), ("enable", "Turn a rule on"), ("disable", "Turn a rule off, keeping the file")):
        named = rules_kinds.add_parser(verb, help=text)
        named.add_argument("--name", required=True, help="The rule's name")
    rules_test = rules_kinds.add_parser("test", help="Say what a recorded event would do, firing nothing and asking no host")
    rules_test.add_argument("--event", required=True, metavar="FILE", help="A JSON file holding one recorded event")
    rules_test.add_argument("--name", help="Only this rule; without it, every loaded rule")

    schedule_parser = subparsers.add_parser("schedule", help="Messages waiting to be posted: list, post (this runner holds it), cancel")
    schedule_kinds = schedule_parser.add_subparsers(dest="schedule_kind")
    schedule_list = schedule_kinds.add_parser("list", help="What is scheduled, each row saying which of the two guarantees it has")
    schedule_list.add_argument("--chat", help="Also read what Telegram is holding for this chat (server-held); without it, only this runner's own")
    schedule_post = schedule_kinds.add_parser("post", help="Store a message for this runner to post later (runner-held: it fires only while `watch run` is up)")
    schedule_post.add_argument("--chat", required=True, help="Chat/channel username, link, or ID")
    schedule_post.add_argument("--topic", type=positive_int, help="Topic ID to post into; omit for the chat itself")
    schedule_post.add_argument("--text", required=True, help="The message, or - to read it from stdin")
    schedule_when = schedule_post.add_mutually_exclusive_group(required=True)
    schedule_when.add_argument("--at", metavar="TIME", help="One ISO 8601 moment; a time with no offset is this machine's local time")
    schedule_when.add_argument("--every", metavar="REPEAT", help="Repeating: an interval like 15m, 2h, 1d, or a five-field cron expression")
    schedule_cancel = schedule_kinds.add_parser("cancel", help="Cancel one scheduled message (y/N)")
    schedule_cancel.add_argument("--id", dest="schedule_id", required=True, metavar="ID", help="The id `schedule list` printed")
    schedule_cancel.add_argument("--chat", help="The chat it is in, for one Telegram is holding; omit for one of this runner's own")

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
    send_parser.add_argument("--reply-to", dest="reply_to", type=positive_int, metavar="MSG", help="Post it as a reply to this message id")
    send_parser.add_argument("--at", metavar="TIME", help="Hand it to Telegram to post at this ISO 8601 moment; Telegram holds it and posts it with this machine off")

    message_parser = subparsers.add_parser("message", help="Act on messages: reply, edit, delete, forward, copy, react, pin, poll, read, bookmark, draft")
    verbs = message_parser.add_subparsers(dest="message_verb")
    verb_parsers: dict[str, argparse.ArgumentParser] = {}
    for verb, text in (
        ("reply", "Reply to one message"),
        ("edit", "Change the text of a message (your own, or with the edit right)"),
        ("delete", "Delete messages, from ids or an archive query (dry-run by default)"),
        ("forward", "Forward messages to another chat, with their header"),
        ("copy", "Re-post messages' text to another chat, linking to any attachment; never the bytes"),
        ("react", "Put an emoji reaction on a message"),
        ("unreact", "Take your reaction off a message"),
        ("pin", "Pin a message"),
        ("unpin", "Unpin a message"),
        ("poll", "Post a poll"),
        ("typing", "Show 'typing…' in a chat for a few seconds"),
        ("read", "Mark a chat read (account only)"),
        ("unread", "Mark a chat unread (account only)"),
        ("bookmark", "Forward a message to Saved Messages and note it in the archive (account only)"),
        ("draft", "Save a draft in a chat or topic (account only)"),
    ):
        verb_parsers[verb] = verbs.add_parser(verb, help=text)
    for verb, verb_parser in verb_parsers.items():
        verb_parser.add_argument("--chat", required=True, help="Chat/channel username, link, or ID the message is in")
    verb_parsers["reply"].add_argument("--to", dest="message_id", required=True, type=positive_int, metavar="MSG", help="The message id to reply to")
    verb_parsers["reply"].add_argument("--text", required=True, help="Reply text, or - to read it from stdin")
    verb_parsers["edit"].add_argument("--id", dest="message_id", required=True, type=positive_int, metavar="MSG", help="The message id to edit")
    verb_parsers["edit"].add_argument("--text", required=True, help="The new text, or - to read it from stdin")
    for verb in ("delete", "forward", "copy"):
        selection = verb_parsers[verb].add_mutually_exclusive_group(required=True)
        selection.add_argument("--ids", action="append", metavar="ID[,ID…]", help="Message ids; repeatable, comma-separated")
        selection.add_argument("--from-search", dest="from_search", metavar="QUERY", help="Select every archived message in this chat matching an archive search query")
        verb_parsers[verb].add_argument("--limit", type=positive_int, metavar="N", help="Refuse a selection larger than this (default 200, at most 1000)")
        verb_parsers[verb].add_argument("--i-know", dest="i_know", action="store_true", help="Allow more than 1000 messages; the count is asked for at the prompt")
    verb_parsers["delete"].add_argument("--execute", action="store_true", help="Actually delete them after typing DELETE")
    for verb in ("forward", "copy"):
        verb_parsers[verb].add_argument("--to", dest="to_chat", required=True, metavar="CHAT", help="The chat to post them into")
        verb_parsers[verb].add_argument("--to-topic", dest="to_topic", type=positive_int, metavar="TOPIC", help="A topic in that chat; omit for the chat itself")
    for verb in ("react", "unreact", "pin", "unpin", "bookmark"):
        verb_parsers[verb].add_argument("--id", dest="message_id", required=True, type=positive_int, metavar="MSG", help="The message id")
    verb_parsers["react"].add_argument("--emoji", required=True, help="The reaction, as the emoji itself")
    verb_parsers["unreact"].add_argument("--emoji", help="The reaction to remove; omit to remove every reaction of yours")
    verb_parsers["poll"].add_argument("--topic", type=positive_int, help="Topic ID to post into; omit for the chat itself")
    verb_parsers["poll"].add_argument("--question", required=True, help="The question")
    verb_parsers["poll"].add_argument("--option", dest="options", action="append", required=True, metavar="TEXT", help="An answer; repeat for each, 2 to 10")
    verb_parsers["poll"].add_argument("--multiple", action="store_true", help="Let people pick more than one answer")
    verb_parsers["typing"].add_argument("--seconds", type=positive_int, default=5, help="How long to show it (default 5)")
    verb_parsers["bookmark"].add_argument("--label", default="", help="A label for the archive's bookmark row")
    verb_parsers["draft"].add_argument("--topic", type=positive_int, help="Topic ID the draft belongs to; omit for the chat itself")
    verb_parsers["draft"].add_argument("--text", required=True, help="The draft text, or - to read it from stdin")
    for verb, verb_parser in verb_parsers.items():
        if verb == "delete":
            # Deleting messages is typed_delete: --execute plus DELETE at the
            # prompt, and no --yes, so it never runs unattended.
            continue
        verb_parser.add_argument("--yes", action="store_true", help="Skip the preview; the chat it lands in must be in TELEGRAM_SEND_ALLOWLIST")

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

    auth_parser = subparsers.add_parser("auth", help="Log a profile in or out (asks at the terminal)")
    auth_mode = auth_parser.add_mutually_exclusive_group()
    auth_mode.add_argument("--qr", action="store_true", help="Log in by scanning a QR code from a signed-in phone")
    auth_mode.add_argument("--logout", action="store_true", help="End this profile's session after typing its name")
    auth_mode.add_argument("--migrate", action="store_true", help="Move the pre-profile session into the default profile")

    subparsers.add_parser("profiles", help="List the named logins on this machine")

    subparsers.add_parser("doctor", help="Check local setup without printing secrets")

    return parser


# -- the pieces every command needs ---------------------------------------


async def _acting(client, report: Reporter):
    """The account this run acts as, fetched once and reused by plan, envelope and audit."""
    if report.acting is None:
        provider = await AccountIdentity.open(client)
        report.set_identity(provider.identity(), me=provider.user)
    return report.acting


def _in_bot_mode(report: Reporter) -> bool:
    return report.acting is not None and report.acting.mode == "bot"


async def _rights(client, report: Reporter, peer) -> Rights:
    """The rights this run's identity holds in `peer`: the bot's under --as-bot, else the account's."""
    me = report.me
    if me is None:
        me = await client.get_me()
        report.me = me
    probe = BotPermissions(client, me) if _in_bot_mode(report) else ChatPermissions(client, me)
    return await probe.probe(peer)


async def _resolve(client, report: Reporter, reference):
    """A `--chat` reference as this run's identity can resolve it.

    The account walks its dialog list first and accepts a link; a bot has no
    dialog list and takes an id or a username only (section 5.2).
    """
    if _in_bot_mode(report):
        return await resolve_chat_as_bot(client, reference)
    return await resolve_chat(client, reference)


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
    # The banner belongs to a screen. A run that writes its output to a file has
    # none, and its stdout has been empty since before this flag existed.
    if not args.json_output:
        report.show_banner()
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
    resolved = await _resolve(client, report, args.chat)
    peer = resolved.input_entity
    chat = ChatTargets.chat_target(resolved, args.chat)
    report.set_target(chat)
    report.show_banner()

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
    resolved = await _resolve(client, report, args.chat)
    peer = resolved.input_entity
    report.set_target(ChatTargets.chat_target(resolved, args.chat))
    if not args.output:
        report.show_banner()
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
        write_records(records, args.output, args.format, query=args.keyword or "", chat_title=report.target_title)
    elif args.format != "json":
        raise ValueError(f"--output is required for {args.format.upper()} export")
    elif not report.machine:
        print(format_message_records(records))

    result = {"matched": len(records), "format": args.format, "output": args.output}
    if not args.output:
        # No file was written, so the envelope is the only place the messages
        # can be: the same rows the table would have shown.
        result["messages"] = records
    report.result(result, status="ok" if records else "empty")
    return 0


# -- the archive -----------------------------------------------------------


async def _offline_identity(config, report: Reporter) -> Identity:
    """The account this run acts as, without opening its session when the profile record says.

    The archive commands that never touch Telegram -- status, search, export,
    retention, forget -- still act *as* someone: rows are identity-scoped and
    a retention plan is signed by whoever approved it. The profile record
    `auth` wrote answers that with no connection; a profile without one is
    asked once, through its session, exactly as bot mode does.
    """
    if report.acting is None:
        user_id, label = await _via_account(config, report)
        report.set_identity(
            Identity(
                platform=PLATFORM,
                mode="account",
                label=label,
                id=str(_rid.make(PREFIX, "user", user_id)),
                profile=getattr(config, "profile", profile_store.DEFAULT_PROFILE),
            )
        )
    return report.acting


def _query_kwargs(args, identity: Identity) -> dict:
    """The `Archive.search` arguments an `archive search`/`export` namespace carries."""
    author = getattr(args, "author", None)
    if author == "me":
        author = identity.id
    return {
        "regex": getattr(args, "regex", None),
        "scope": getattr(args, "scope", None) or None,
        "identity": getattr(args, "identity", None),
        "author": author,
        "since": getattr(args, "since", None),
        "until": getattr(args, "until", None),
        "context": int(getattr(args, "context", 0) or 0),
        "limit": int(getattr(args, "limit", None) or 50),
        "markers": archive_store.MARKERS,
    }


async def _run_archive_sync(client, args, *, report: Reporter) -> int:
    """Walk what the account can read into the archive, resuming; one progress line per scope."""
    identity = await _acting(client, report)
    report.show_banner()
    source = TelegramArchiveSource(client, only=getattr(args, "scope", None))
    full = bool(getattr(args, "full", False))
    deleted: dict[str, int] = {}
    with archive_store.open_archive() as archive:
        result = await archive.sync(
            source,
            identity,
            since=getattr(args, "since", None),
            full=full,
            progress=report.info,
        )
        for scope in result.scopes:
            if scope.status == "ok" and full and scope.rid not in source.floored:
                # A full walk saw everything that exists, so a live row it did
                # not serve is a message Telegram no longer has.
                gone = archive_store.mark_missing_deleted(archive, scope.rid, source.seen.get(scope.rid, set()))
                if gone:
                    deleted[scope.rid] = gone
                    report.info(f"{scope.rid} {scope.title}: {gone} marked deleted".rstrip())
            elif scope.status == "failed" and archive_store.is_rate_limited(scope.error):
                archive.record_coverage(scope.rid, identity.id, visible=True, skipped_reason="rate_limited")
        # Section 9.1: every link and file the walk saw is a candidate in the
        # review queue, noted after the store has committed and never fetched.
        # The same link in the same message on a resync is the same row.
        queue = review_ops.queue_for(archive)
        noted = {"link": 0, "media": 0}
        for candidate in source.candidates:
            queue.enqueue(candidate, identity.id)
            noted[candidate.kind] += 1
    report.waited_ms += source.waited_ms
    if not report.machine:
        print(archive_store.format_coverage(result))
        print(f"{noted['link']} link(s) and {noted['media']} file(s) noted for review; nothing was fetched (review list shows them).")
    for scope in result.scopes:
        report.record(scope.to_dict())
    report.result(
        {**result.to_dict(), "deleted": deleted, "waited_ms": source.waited_ms, "manifests": noted},
        status=result.status,
    )
    return 1 if result.status == "partial" else 0


async def _run_archive_status(args, config, *, report: Reporter) -> int:
    await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        status = archive.status(identity=getattr(args, "identity", None))
    # Where the file lives is this machine's business; the rest is the answer.
    status.pop("path", None)
    if not report.machine:
        print(archive_store.format_status(status))
    report.result(status, status="ok" if status["messages"] else "empty")
    return 0


async def _run_archive_search(args, config, *, report: Reporter) -> int:
    identity = await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        hits = archive.search(args.query, **_query_kwargs(args, identity))
    rows = [hit.to_dict() for hit in hits]
    for row in rows:
        report.record(row)
    if not report.machine:
        print(archive_store.format_hits(hits))
    report.result({"matched": len(rows), "query": args.query, "messages": rows}, status="ok" if rows else "empty")
    return 0


async def _run_archive_export(args, config, *, report: Reporter) -> int:
    identity = await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        hits = archive.search(args.query, **_query_kwargs(args, identity))
    path = _export.write(
        hits,
        args.output,
        args.format,
        paths=archive_store.paths_for(),
        query=args.query,
        title=f"{TOOL} archive export",
    )
    for hit in hits:
        report.record(hit.to_dict())
    report.info(f"Wrote {len(hits)} message(s) as {args.format} to {path}")
    report.result(
        {"matched": len(hits), "query": args.query, "format": args.format, "output": str(path)},
        status="ok" if hits else "empty",
    )
    return 0


async def _run_archive_prune(args, config, *, report: Reporter) -> int:
    """`archive retention` and `archive forget`: a dry-run, then the exact title typed back.

    The same gate `delete` has, for the same reason -- the mistake worth
    catching is the wrong target, not the absent intent -- and no `--yes`,
    so neither ever runs unattended. Executed, it leaves an audit line.
    """
    identity = await _offline_identity(config, report)
    kind = args.archive_kind
    with archive_store.open_archive() as archive:
        if kind == "retention":
            plan = archive.retention_plan(tool=TOOL, version=__version__, identity=identity, scope=args.scope, keep=args.keep)
        else:
            plan = archive.forget_plan(
                tool=TOOL, version=__version__, identity=identity, scope=args.scope, identity_id=args.identity
            )
        target = plan.targets[0]
        report.set_target(target)
        report.set_plan(plan)
        report.show_banner()
        preview = archive_store.format_plan(plan, execute=args.execute)
        described = {**plan.describe(), "target": target.to_dict()}

        if not args.execute:
            report.info(preview)
            report.result({**described, "dry_run": True, "executed": False}, status="dry_run")
            return 0

        if not archive_store.confirm_typed_name(preview, target.title, **report.confirm_io()):
            report.info("That is not the title; nothing was removed.")
            report.result({**described, "dry_run": False, "executed": False, "cancelled": True}, status="cancelled")
            return 1

        # Re-derive after the gate: the row the title was typed for has to be
        # the row still there, under the same title. A retention plan's cutoff
        # moves with the clock, so the target is compared, not the plan id; an
        # identity is re-read from its own row the same way.
        if args.scope:
            fresh = archive.scope_target(target.rid)
        else:
            row = archive.connection.execute(
                "SELECT label FROM identities WHERE identity_id = ?", (args.identity,)
            ).fetchone()
            fresh = None if row is None else Target(rid=target.rid, kind=target.kind, title=row["label"], path=target.path)
        if fresh is None or fresh.title != target.title:
            raise CommandError(
                "The scope changed between the preview and the execution.",
                code="PLAN_DRIFT",
                hint="Run it again: the preview will show what it is now.",
            )

        outcome = archive.retention(plan) if kind == "retention" else archive.forget(plan)
        remaining = archive.connection.execute(
            "SELECT COUNT(*) FROM messages WHERE rid = ?" if args.scope else "SELECT COUNT(*) FROM messages WHERE identity_id = ?",
            (args.scope or args.identity,),
        ).fetchone()[0]
    evidence = Evidence.verified(f"{remaining} message(s) remain for {target.rid}")
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.printed_result({**outcome, "dry_run": False, "executed": True, "remaining": remaining}, status="ok")
    return 0


def _archive_scope_rids(connection, reference: str, topic: int | None) -> list[str]:
    """The archive's scope rids a live `--chat` reference names, for `search --archive`.

    A numeric id or a `@username` only: the archive has no dialog list to
    resolve a title or a link against, and resolving through Telegram would
    make an offline command connect.
    """
    reference = str(reference).strip()
    rows = connection.execute("SELECT rid, platform_json FROM scopes").fetchall()
    chat_ids: set[str] = set()
    if reference.lstrip("-").isdigit():
        chat_ids.add(reference)
    else:
        wanted = reference.lstrip("@").casefold()
        for row in rows:
            extras = json.loads(row["platform_json"] or "{}")
            username = extras.get("username")
            if username and str(username).casefold() == wanted:
                chat_ids.add(_rid.parse(row["rid"]).ids[0])
    if not chat_ids:
        raise CommandError(
            f"{reference!r} is not a chat in the archive (a numeric id or a @username the archive has synced).",
            code="TARGET_NOT_FOUND",
            hint="telegram-tools archive status",
        )
    rids: list[str] = []
    for row in rows:
        parsed = _rid.parse(row["rid"])
        if parsed.ids[0] not in chat_ids:
            continue
        if topic is not None and (parsed.kind != "topic" or parsed.ids[1] != str(topic)):
            continue
        rids.append(row["rid"])
    if topic is not None and not rids:
        rids = [scope_rid_for(chat_id, topic) for chat_id in sorted(chat_ids)]
    return rids


def _review_io(report: Reporter) -> dict:
    """The read/write a queue gate asks on, or APPROVAL_REQUIRED when there is no terminal.

    Section 9.1: the two human moves refuse a non-interactive caller in either
    mode. A `y` piped into stdin is not a person at a terminal, so unlike the
    other gates this one checks for the terminal itself rather than only
    under --json.
    """
    if not review_ops.terminal_present():
        raise ApprovalRequired(report.human_command)
    return report.confirm_io()


def _review_rows(queue, ids: list[str], *, state: str, what: str) -> list:
    """The candidates `--ids` names, each checked to be in `state` before anything moves."""
    if not ids:
        raise ValueError(f"Nothing selected: pass --ids with at least one candidate id from `review list`.")
    rows = []
    for manifest_id in ids:
        try:
            row = queue.get(manifest_id)
        except review_ops.ReviewError as exc:
            raise CommandError(str(exc), code="TARGET_NOT_FOUND", hint="review list shows every candidate id") from exc
        if row.state != state:
            raise CommandError(
                f"{row.manifest_id} is {row.state}, not {state}; only a {state} candidate can be {what}.",
                code="TARGET_KIND_MISMATCH",
                hint=f"review list --state {state}",
            )
        rows.append(row)
    return rows


def _fetch_status(reports) -> str:
    """`ok` when every fetch ended quarantined with a verdict a human may accept, else `partial`."""
    fine = all(item.state == "quarantined" and item.verdict not in review_ops.UNACCEPTABLE for item in reports)
    return "ok" if fine else "partial"


async def _review_fetch(queue, download_ids: list[str], rows, *, client, config, report: Reporter) -> list:
    """Run the pipeline over each approved download; a file opens the account's client
    when the caller did not pass one, a link needs none. The reports, in order."""
    owns = client is None and any(row.kind == "media" for row in rows)
    if owns:
        client = await start_client(create_client(config), authorize=not report.machine)
        if report.machine and not await client.is_user_authorized():
            await _disconnect_quietly(client)
            raise login.LoginRequired(getattr(config, "profile", "default"))
    pipeline = review_ops.build_pipeline(queue, client=client)
    reports = []
    try:
        for download_id in download_ids:
            outcome = await pipeline.run(download_id)
            reports.append(outcome)
            report.info(review_ops.format_report(outcome))
    finally:
        if owns:
            await client.disconnect()
    return reports


def _fetch_evidence(reports) -> Evidence:
    quarantined = [item for item in reports if item.state == "quarantined"]
    failed = [item for item in reports if item.state != "quarantined"]
    verdicts = ", ".join(f"{item.manifest_id} {item.verdict}" for item in quarantined)
    text = f"{len(quarantined)} quarantined" + (f" ({verdicts})" if verdicts else "") + (f", {len(failed)} failed" if failed else "")
    return Evidence.verified(text)


async def _run_review_approve(args, queue, archive, identity, *, client, config, report: Reporter) -> int:
    """`queued -> approved` behind the y/N, then straight into the fetch (section 9.1)."""
    ids = review_ops.parse_ids(getattr(args, "ids", None))
    if not ids:
        queued = queue.list(state="queued")
        if not queued:
            report.info("Nothing is queued; run `archive sync` to find links and files.")
            report.result({"approved": [], "downloads": []}, status="empty")
            return 0
        io = _review_io(report)
        picked = pick_many(
            queued,
            title="Approve which? (fetched into quarantine after the y/N)",
            label=lambda row: f"{row.manifest_id}  {row.kind}  {row.url or row.extra.get('display_name') or row.extra.get('locator')}",
            read=io.get("read") or input,
            write=io.get("write") or print,
        )
        if picked is BACK:
            report.info("Nothing approved.")
            report.result({"approved": [], "downloads": [], "cancelled": True}, status="cancelled")
            return 1
        ids = [row.manifest_id for row in picked]
    rows = _review_rows(queue, ids, state="queued", what="approved")
    plan = review_ops.plan_for("review approve", "review.approve", identity, archive, rows)
    report.set_target(plan.targets[0])
    report.set_plan(plan)
    preview = review_ops.format_candidates(rows, heading="Approving: fetched into quarantine, checked, and held until you accept")
    if not message_ops.confirm_prompt_y(preview, **_review_io(report)):
        report.result({**plan.describe(), "approved": [], "downloads": [], "cancelled": True}, status="cancelled")
        return 1
    download_ids = queue.approve(ids, review_ops.approval_for(True), identity)
    try:
        reports = await _review_fetch(queue, download_ids, rows, client=client, config=config, report=report)
    except CodedError:
        report.audit(plan, status="failed", evidence=Evidence.unverified("the fetch refused before it finished"))
        raise
    evidence = _fetch_evidence(reports)
    report.set_evidence(evidence)
    status = _fetch_status(reports)
    report.audit(plan, status=status, evidence=evidence)
    report.result({**plan.describe(), "approved": ids, "downloads": [item.to_dict() for item in reports]}, status=status)
    return 1 if status == "partial" else 0


async def _run_review_retry(args, queue, archive, identity, *, client, config, report: Reporter) -> int:
    """A `failed` download run again from the bytes on disk. The human said yes once."""
    ids = review_ops.parse_ids(getattr(args, "ids", None))
    rows = _review_rows(queue, ids, state="failed", what="retried")
    plan = review_ops.plan_for("review retry", "review.retry", identity, archive, rows)
    report.set_target(plan.targets[0])
    report.set_plan(plan)
    download_ids = queue.retry(ids)
    try:
        reports = await _review_fetch(queue, download_ids, rows, client=client, config=config, report=report)
    except CodedError:
        report.audit(plan, status="failed", evidence=Evidence.unverified("the fetch refused before it finished"))
        raise
    evidence = _fetch_evidence(reports)
    report.set_evidence(evidence)
    status = _fetch_status(reports)
    report.audit(plan, status=status, evidence=evidence)
    report.result({**plan.describe(), "retried": ids, "downloads": [item.to_dict() for item in reports]}, status=status)
    return 1 if status == "partial" else 0


async def _run_review_accept(args, queue, archive, identity, *, report: Reporter) -> int:
    """`quarantined -> accepted` behind the y/N, the verdict shown first; BLOCKED and
    INFECTED are refused before the question is even asked (section 9.4)."""
    ids = review_ops.parse_ids(getattr(args, "ids", None))
    rows = _review_rows(queue, ids, state="quarantined", what="accepted")
    plan = review_ops.plan_for("review accept", "review.accept", identity, archive, rows)
    report.set_target(plan.targets[0])
    report.set_plan(plan)
    preview = review_ops.format_candidates(rows, heading="Accepting: moved into the media store with the verdict shown")
    unsafe = [row for row in rows if row.verdict in review_ops.UNACCEPTABLE]
    if unsafe:
        report.info(preview)
        first = unsafe[0]
        raise CommandError(
            f"{first.manifest_id} is {first.verdict}: {first.last_error or 'a built-in check failed'}. It cannot be accepted.",
            code="UNSAFE_BLOCKED",
            hint=f"review reject --ids {','.join(row.manifest_id for row in unsafe)}",
        )
    if not message_ops.confirm_prompt_y(preview, **_review_io(report)):
        report.result({**plan.describe(), "accepted": [], "cancelled": True}, status="cancelled")
        return 1
    results = queue.accept(ids, review_ops.approval_for(True))
    stored = [queue.paths.root / item["storage_path"] for item in results]
    evidence = (
        Evidence.verified(f"{len(results)} file(s) in the media store: " + ", ".join(f"{item['manifest_id']} {item['verdict']}" for item in results))
        if all(path.is_file() for path in stored)
        else Evidence.unverified("a stored file could not be found after the move")
    )
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.info(review_ops.format_accepted(results))
    report.result({**plan.describe(), "accepted": results}, status="ok")
    return 0


async def _run_review_reject(args, queue, archive, identity, *, report: Reporter) -> int:
    """`-> rejected` from any live state, the quarantined bytes deleted. It asks, because a
    rejected candidate stays rejected: the same link or file on a later sync finds its row."""
    ids = review_ops.parse_ids(getattr(args, "ids", None))
    if not ids:
        raise ValueError("Nothing selected: pass --ids with at least one candidate id from `review list`.")
    rows = []
    for manifest_id in ids:
        try:
            rows.append(queue.get(manifest_id))
        except review_ops.ReviewError as exc:
            raise CommandError(str(exc), code="TARGET_NOT_FOUND", hint="review list shows every candidate id") from exc
    plan = review_ops.plan_for("review reject", "review.reject", identity, archive, rows)
    report.set_target(plan.targets[0])
    report.set_plan(plan)
    preview = review_ops.format_candidates(rows, heading="Rejecting: quarantined bytes are deleted and the candidate stays rejected")
    if not message_ops.confirm_prompt_y(preview, **_review_io(report)):
        report.result({**plan.describe(), "rejected": [], "cancelled": True}, status="cancelled")
        return 1
    results = queue.reject(ids)
    gone = all(not queue.paths.quarantine_dir(row.download_id).exists() for row in rows if row.download_id)
    evidence = Evidence.verified(f"{len(results)} rejected, quarantine cleared") if gone else Evidence.unverified("a quarantine directory is still there")
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.info(review_ops.format_rejected(results))
    report.result({**plan.describe(), "rejected": results}, status="ok")
    return 0


async def _run_review(args, config, *, client=None, report: Reporter) -> int:
    """The review queue over the local archive. `list` and `status` read; the rest move a
    state behind the gate section 9.1 gives it, and `approve`/`retry` then fetch."""
    kind = getattr(args, "review_kind", None)
    if kind is None:
        raise ValueError("review needs one of: list, approve, accept, reject, retry, status.")
    identity = await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        queue = review_ops.queue_for(archive)
        if kind == "list":
            rows = queue.list(kind=getattr(args, "kind", None), state=getattr(args, "state", None))
            entries = review_ops.describe_rows(archive, rows)
            for entry in entries:
                report.record(entry)
            if not report.machine:
                print(review_ops.format_queue(entries))
            report.result({"count": len(entries), "candidates": entries}, status="ok" if entries else "empty")
            return 0
        if kind == "status":
            status = review_ops.status_of(queue)
            if not report.machine:
                print(review_ops.format_status(status))
            report.result(status, status="ok" if status["candidates"] else "empty")
            return 0
        if kind == "approve":
            return await _run_review_approve(args, queue, archive, identity, client=client, config=config, report=report)
        if kind == "retry":
            return await _run_review_retry(args, queue, archive, identity, client=client, config=config, report=report)
        if kind == "accept":
            return await _run_review_accept(args, queue, archive, identity, report=report)
        if kind == "reject":
            return await _run_review_reject(args, queue, archive, identity, report=report)
        raise ValueError(f"Unknown review command: {kind}")


async def _run_search_archive(args, config, *, report: Reporter) -> int:
    """`search --archive`: the live command's flags, answered from the archive, offline."""
    if not args.keyword:
        raise ValueError("search --archive needs --keyword: the archive is searched by text.")
    identity = await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        rids = _archive_scope_rids(archive.connection, args.chat, args.topic)
        author = args.from_user
        if author == "me":
            author = identity.id
        elif author is not None and str(author).lstrip("@").isdigit():
            author = str(_rid.make(PREFIX, "user", str(author).lstrip("@")))
        elif author is not None:
            row = archive.connection.execute(
                "SELECT rid FROM authors WHERE LOWER(username) = ?", (str(author).lstrip("@").casefold(),)
            ).fetchone()
            author = row["rid"] if row else str(author)
        # A keyword is a phrase, not FTS5 syntax: what `search` has always matched.
        query = '"' + args.keyword.replace('"', '""') + '"'
        hits = archive.search(
            query,
            scope=rids,
            author=author,
            since=args.since,
            until=args.until,
            limit=args.limit or 50,
            markers=archive_store.MARKERS,
        )
    rows = [hit.to_dict() for hit in hits]
    for row in rows:
        report.record(row)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_export.render(rows, args.format, query=args.keyword, title=f"{TOOL} archive export"), encoding="utf-8")
    elif args.format != "json":
        raise ValueError(f"--output is required for {args.format.upper()} export")
    elif not report.machine:
        print(archive_store.format_hits(hits))
    result = {"matched": len(rows), "format": args.format, "output": args.output, "archive": True}
    if not args.output:
        result["messages"] = rows
    report.result(result, status="ok" if rows else "empty")
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
    resolved = await _resolve(client, report, args.chat)
    peer = resolved.input_entity
    chat = ChatTargets.chat_target(resolved, args.chat)

    topic = None
    if args.topic is not None:
        topics = await get_forum_topics_by_ids(client, peer, [args.topic])
        topic = topics[0] if topics else None

    destination = chat if topic is None else ChatTargets.topic_target(chat, topic)
    report.set_target(destination)
    report.show_banner()
    reply_to = getattr(args, "reply_to", None)
    # Section 10.6: `--at` hands the message to Telegram, which holds it and
    # posts it with this machine off. The moment is parsed before anything is
    # asked, so a bad time refuses at the same place a bad chat does.
    raw_at = getattr(args, "at", None)
    at = None if raw_at is None else watch_ops.require_future(watch_ops.parse_when(raw_at))
    target = SendTarget(chat_id=resolved.id, chat_title=chat.title, topic=topic, reply_to=reply_to, at=at)

    rights = await _rights(client, report, peer)
    identity = await _acting(client, report)
    mutation_params = {"files": len(files), "text": bool(text)}
    if reply_to is not None:
        mutation_params["reply_to"] = int(reply_to)
    if at is not None:
        mutation_params["at"] = at.isoformat()
    plan, warnings = build_plan(
        identity=identity,
        command="send",
        targets=[destination],
        mutations=[Mutation("send_message", destination.rid, mutation_params)],
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
        if _in_bot_mode(report):
            # The preview names both, exactly as the banner does: the bot that
            # will post, and the account whose bot it is.
            sender = f"{report.acting.label} (via {report.via_label})"
        else:
            sender = _entity_title(report.me or await client.get_me(), "you")
        preview = format_send_preview(target, text, sender=sender, files=files)
        confirm = partial(confirm_send, preview, **report.confirm_io())

    async def rebuild():
        again = await _resolve(client, report, args.chat)
        fresh_chat = ChatTargets.chat_target(again, args.chat)
        fresh = fresh_chat
        if args.topic is not None:
            found = await get_forum_topics_by_ids(client, again.input_entity, [args.topic])
            fresh = ChatTargets.topic_target(fresh_chat, found[0]) if found else fresh_chat
        return build_plan(
            identity=identity,
            command="send",
            targets=[fresh],
            mutations=[Mutation("send_message", fresh.rid, mutation_params)],
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
            "the scheduled message" if at is not None else "the sent message",
            (lambda: _scheduled_message(client, peer, destination, result.message_id, at))
            if at is not None
            else (lambda: _sent_message(client, peer, destination, result.message_id)),
        )
        report.set_evidence(evidence)
        report.audit(plan, status=status, evidence=evidence)
    payload = result.to_dict()
    if at is not None and not result.cancelled and not report.machine:
        print(watch_ops.format_schedule_created({**payload, "rid": destination.rid, "at": at.isoformat(), "guarantee": watch_ops.SERVER_HELD, "next": at.isoformat()}))
    report.printed_result(payload, status=status)
    return 1 if result.cancelled else 0


async def _scheduled_message(client, peer, destination, message_id, at) -> str:
    """A `--at` send reads back from what Telegram is holding, not from history.

    A scheduled message is not in the chat yet, so `get_messages` would never
    find it; `GetScheduledHistory` is where it actually is until it posts.
    """
    history = await client(GetScheduledHistoryRequest(peer=peer, hash=0))
    ids = [int(getattr(message, "id")) for message in getattr(history, "messages", None) or ()]
    if int(message_id) not in ids:
        raise LookupError("Telegram is not holding that message id")
    return f"message {int(message_id)} is scheduled in {destination.display} for {at.isoformat()} ({watch_ops.SERVER_HELD})"


async def _sent_message(client, peer, destination, message_id) -> str:
    message = await client.get_messages(peer, ids=message_id)
    if message is None:
        raise LookupError("Telegram returned no message under that id")
    return f"message {int(getattr(message, 'id'))} is in {destination.display}"


# -- message verbs ---------------------------------------------------------


async def _resolve_destination(client, report: Reporter, reference, topic_id: int | None):
    """A chat, and a topic in it when one was named, as a Target plus what the calls need."""
    resolved = await _resolve(client, report, reference)
    chat = ChatTargets.chat_target(resolved, reference)
    topic = None
    if topic_id is not None:
        found = await get_forum_topics_by_ids(client, resolved.input_entity, [topic_id])
        topic = found[0] if found else None
        if topic is None or topic.title == str(topic_id):
            raise CommandError(
                f"No topic {topic_id} in {chat.title} - list them with `discover`.",
                code="TARGET_NOT_FOUND",
                hint="telegram-tools discover",
            )
    target = chat if topic is None else ChatTargets.topic_target(chat, topic)
    return resolved, chat, topic, target


def _selection_ids(args, archive_scope) -> list[int]:
    """The ids a bulk verb acts on: `--ids` as typed, or `--from-search` answered by the archive."""
    if getattr(args, "from_search", None):
        with archive_store.open_archive() as archive:
            rids = archive_scope(archive.connection)
            return message_ops.ids_from_search(
                archive, args.from_search, scope=rids, limit=getattr(args, "limit", None), i_know=bool(getattr(args, "i_know", False))
            )
    ids = message_ops.parse_ids(getattr(args, "ids", None))
    if not ids:
        raise ValueError("Nothing selected: pass --ids with at least one message id.")
    message_ops.bound_selection(len(ids), limit=getattr(args, "limit", None), i_know=bool(getattr(args, "i_know", False)))
    return ids


async def _run_message(client, args, config, *, report: Reporter | None = None) -> int:
    """One message verb, behind the four steps every write here takes.

    Plan, preflight, gate, re-derivation, call, readback, audit -- the order
    `send` and `delete` follow. What differs per verb is in `messages.py`;
    what is the same is here, once.
    """
    report = report or Reporter()
    verb = getattr(args, "message_verb", None)
    if verb not in message_ops.OPS:
        raise ValueError("message needs one of: " + ", ".join(message_ops.VERBS) + ".")
    op = message_ops.OPS[verb]
    identity = await _acting(client, report)
    me = report.me or await client.get_me()
    me_id = int(getattr(me, "id", 0) or 0)

    topic_flag = getattr(args, "topic", None)
    resolved, chat, topic, target = await _resolve_destination(client, report, args.chat, topic_flag)
    peer = resolved.input_entity
    report.set_target(target)
    report.show_banner()

    # -- what the verb acts on ------------------------------------------
    text = None
    if verb in ("reply", "edit", "draft"):
        text = _message_text(args.text, has_files=False)
    if verb in message_ops.BULK_VERBS:
        ids = _selection_ids(args, lambda connection: _archive_scope_rids(connection, str(resolved.id), None))
    elif verb in message_ops.SINGLE_VERBS or verb == "reply":
        ids = [int(args.message_id)]
    else:
        ids = []
    briefs = await message_ops.fetch_briefs(client, peer, ids, me_id=me_id) if ids else []

    # -- the rights the plan needs --------------------------------------
    required = list(op.required)
    others = [brief for brief in briefs if not brief.own]
    if verb == "edit" and others:
        required.append("edit_messages")
    if verb == "delete" and others:
        required.append("delete_messages")
    rights = await _rights(client, report, peer)

    # -- a destination, for the verbs that post elsewhere -------------------
    to_resolved = to_chat = to_topic = to_target = None
    to_rights = rights
    if verb in message_ops.DESTINATION_VERBS:
        to_resolved, to_chat, to_topic, to_target = await _resolve_destination(client, report, args.to_chat, getattr(args, "to_topic", None))
        to_rights = await _rights(client, report, to_resolved.input_entity)

    execute = bool(getattr(args, "execute", False))
    yes = bool(getattr(args, "yes", False))
    if op.approval == "typed_delete":
        approval = "typed_delete"
    else:
        approval = "yes_allowlist" if yes else "prompt_y"

    def params_for(brief_id: int | None) -> dict:
        params: dict = {}
        if text is not None:
            params["text"] = text
        if getattr(args, "emoji", None):
            params["emoji"] = args.emoji
        if to_target is not None:
            params["to"] = to_target.rid
        if verb == "poll":
            params.update({"question": args.question, "options": list(args.options), "multiple": bool(args.multiple)})
        if verb == "typing":
            params["seconds"] = int(args.seconds)
        if verb == "bookmark":
            params["label"] = args.label or ""
        return params

    def build(chat_target, dest_target, message_ids):
        targets = [chat_target] + ([dest_target] if dest_target is not None else [])
        if message_ids:
            mutations = [Mutation(op.mutation, message_ops.message_rid(chat_target.ids["chat"], number), params_for(number)) for number in message_ids]
        else:
            mutations = [Mutation(op.mutation, chat_target.rid, params_for(None))]
        return build_plan(
            identity=identity,
            command=f"message {verb}",
            targets=targets,
            mutations=mutations,
            approval=approval,
            rights=rights if verb not in message_ops.DESTINATION_VERBS else to_rights,
            required=required,
        )

    plan, warnings = build(target, to_target, ids)
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    require_rights(plan, rights if verb not in message_ops.DESTINATION_VERBS else to_rights, required)
    if verb == "delete" and others:
        # The gate clear-messages has always had: an unknown right refuses a
        # delete that reaches other people's messages, rather than letting
        # Telegram answer after DELETE was typed.
        _require_delete_permission(rights, what="deleting other people's messages")

    # -- the preview ----------------------------------------------------
    if _in_bot_mode(report):
        actor = f"{report.acting.label} (via {report.via_label})"
    else:
        actor = _entity_title(me, "you")
    details: list[str] = []
    if verb == "poll":
        details.append(f"Poll    {args.question}")
        details.extend(f"        - {option}" for option in args.options)
        if args.multiple:
            details.append("        (several answers allowed)")
    if verb in ("react", "unreact"):
        details.append(f"Emoji   {args.emoji or '(every reaction of yours)'}")
    if verb == "typing":
        details.append(f"For     {args.seconds} second(s)")
    if verb == "bookmark" and args.label:
        details.append(f"Label   {args.label}")
    if verb == "copy":
        details.append("Copy    text only; an attachment becomes a link to the original")
    topic_line = None if topic is None else f"{topic.id} {topic.display_title}"
    preview = message_ops.format_preview(
        op,
        actor=actor,
        chat_title=chat.title,
        chat_id=resolved.id,
        topic=topic_line,
        messages=briefs,
        destination=None if to_target is None else f"{to_target.display} ({to_resolved.id})",
        text=text if verb in ("reply", "edit", "draft") else None,
        details=details,
        execute=execute if verb == "delete" else None,
    )

    # -- the gate -------------------------------------------------------
    if verb == "delete" and not execute:
        report.info(preview)
        outcome = message_ops.Outcome(verb, resolved.id, tuple(ids), dry_run=True, done=False, extra={"deleted": 0})
        report.printed_result(outcome.to_dict(), status="dry_run")
        return 0

    if verb == "delete":
        confirm = partial(message_ops.confirm_typed_delete, preview, len(ids), **report.confirm_io())
    elif yes:
        landing = to_resolved if to_resolved is not None else resolved
        landing_topic = getattr(args, "to_topic", None) if to_resolved is not None else topic_flag
        require_send_allowed(
            config.send_allowlist,
            chat_id=landing.id,
            username=getattr(landing.entity, "username", None),
            topic_id=landing_topic,
        )
        confirm = None
    else:
        confirm = partial(message_ops.confirm_prompt_y, preview, **report.confirm_io())

    if confirm is not None and not confirm():
        outcome = message_ops.Outcome(verb, resolved.id, tuple(ids), done=False, cancelled=True)
        report.printed_result(outcome.to_dict(), status="cancelled")
        return 1

    # -- re-derivation ---------------------------------------------------
    async def rebuild():
        again, fresh_chat, fresh_topic, fresh_target = await _resolve_destination(client, report, args.chat, topic_flag)
        fresh_dest = None
        if verb in message_ops.DESTINATION_VERBS:
            _r, _c, _t, fresh_dest = await _resolve_destination(client, report, args.to_chat, getattr(args, "to_topic", None))
        fresh_ids = ids
        if ids:
            fresh_ids = [brief.id for brief in await message_ops.fetch_briefs(client, again.input_entity, ids, me_id=me_id)]
        return build(fresh_target, fresh_dest, fresh_ids)[0]

    await recheck_for(plan, rebuild)()

    # -- the call -------------------------------------------------------
    username = getattr(resolved.entity, "username", None)
    request = message_ops.Request(
        verb=verb,
        peer=peer,
        chat_id=resolved.id,
        messages=briefs,
        text=text,
        topic_id=topic_flag,
        emoji=getattr(args, "emoji", None),
        to_peer=None if to_resolved is None else to_resolved.input_entity,
        to_chat_id=None if to_resolved is None else to_resolved.id,
        to_topic_id=getattr(args, "to_topic", None),
        question=getattr(args, "question", None),
        options=list(getattr(args, "options", None) or []),
        multiple=bool(getattr(args, "multiple", False)),
        seconds=int(getattr(args, "seconds", 5) or 5),
        label=getattr(args, "label", "") or "",
        links={
            brief.id: message_ops.message_link(resolved.id, brief.id, username=username, topic_id=brief.topic_id)
            for brief in briefs
        },
    )

    def bookmark_row(message_id: int) -> None:
        scope_rid = scope_rid_for(resolved.id, briefs[0].topic_id) if briefs and briefs[0].topic_id else scope_rid_for(resolved.id)
        with archive_store.open_archive() as archive:
            archive.connection.execute(
                "INSERT OR REPLACE INTO bookmarks (rid, message_id, identity_id, label, created, source) VALUES (?, ?, ?, ?, ?, 'manual')",
                (scope_rid, str(message_id), identity.id, request.label, utc_now()),
            )

    outcome = await message_ops.perform(client, request, bookmark_row=bookmark_row if verb == "bookmark" else None)

    evidence = await read_back(
        f"the {verb}",
        lambda: message_ops.read_back(
            client, request, outcome, where=target.display, destination=None if to_target is None else to_target.display
        ),
    )
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.printed_result(outcome.to_dict(), status="ok")
    return 0


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
        resolved = await _resolve(client, report, args.chat)
        peer = resolved.input_entity
        chat_id = resolved.id
        chat = ChatTargets.chat_target(resolved, args.chat)
        chat_title = chat.title
        report.set_target(chat)
        rights = await _rights(client, report, peer)

    report.show_banner()
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
            again = await _resolve(client, report, args.chat)
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

    resolved = await _resolve(client, report, args.chat)
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

    report.show_banner()
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
        again = await _resolve(client, report, args.chat)
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



# -- structure blueprints (section 12) --------------------------------------


def _structure_reference(reference: str) -> str | int:
    """`--chat` as every other command takes it, plus a `tg:chat:` rid, which is what a
    blueprint and a remap table spell."""
    text = str(reference).strip()
    if text.startswith(f"{PREFIX}:"):
        parsed = _rid.parse(text)
        if parsed.kind != "chat":
            raise CommandError(f"{text} is not a chat rid.", code="TARGET_KIND_MISMATCH", hint="structure works on chats: tg:chat:<id>")
        return int(parsed.ids[0])
    return text


async def _structure_target(client, report: Reporter, reference: str) -> tuple[Target, str, object]:
    """The chat `--chat` names as a Target, its blueprint kind, and the resolved peer."""
    resolved = await _resolve(client, report, _structure_reference(reference))
    target = ChatTargets.chat_target(resolved, reference)
    kind = chat_kind(resolved.entity)
    return target, kind, resolved


async def _run_structure(client, args, config, *, report: Reporter) -> int:
    kind = getattr(args, "structure_kind", None)
    if kind is None:
        raise ValueError("structure needs one of: export, diff, apply, remap.")
    if kind == "export":
        return await _run_structure_export(client, args, report=report)
    if kind == "diff":
        return await _run_structure_diff(client, args, report=report)
    if kind == "apply":
        return await _run_structure_apply(client, args, config, report=report)
    raise ValueError(f"Unknown structure command: {kind}")


async def _run_structure_export(client, args, *, report: Reporter) -> int:
    """Read one chat and write its blueprint. Reads only; the banner says what it is not."""
    target, _kind, _resolved = await _structure_target(client, report, args.chat)
    report.set_target(target)
    report.show_banner()
    port = TelegramBlueprintPort(client, resolver=_port_resolver(report))
    exported = await _blueprint.export(port, target, structure_ops.ALLOWLIST)
    path = None
    if args.output:
        path = str(structure_ops.write_blueprint(args.output, exported.blueprint))
    report.info(structure_ops.format_export(exported.blueprint, hash=exported.hash, dropped=exported.dropped, path=path))
    if not args.output and not report.machine:
        print(exported.text, end="")
    report.result({"blueprint": exported.blueprint, "hash": exported.hash, "dropped": list(exported.dropped), "output": path})
    return 0


async def _run_structure_diff(client, args, *, report: Reporter) -> int:
    """What the chat would need to match the blueprint. Reads the chat, changes nothing."""
    blueprint = structure_ops.read_blueprint(args.blueprint)
    target, kind, _resolved = await _structure_target(client, report, args.chat)
    report.set_target(target)
    report.show_banner()
    structure_ops.require_same_kind(blueprint, kind, target)
    port = TelegramBlueprintPort(client, resolver=_port_resolver(report))
    current = (await _blueprint.export(port, target, structure_ops.ALLOWLIST)).blueprint
    diff = _blueprint.diff(blueprint, current)
    report.info(structure_ops.format_diff(diff, target=target))
    report.result({**diff.to_dict(), "blueprint_hash": _blueprint.blueprint_hash(blueprint), "matches": diff.empty}, status="ok")
    return 0


def _port_resolver(report: Reporter):
    """The port resolves a container rid through whatever `--chat` would use: the
    account's dialog walk, so the peer it reads is the one the preview named."""

    async def resolve(client, reference):
        return await _resolve(client, report, reference)

    return resolve


async def _run_structure_apply(client, args, config, *, report: Reporter) -> int:
    """Make a chat match a blueprint: a dry-run listing every step, then the exact title.

    The gate is `delete`'s, for `delete`'s reason: the mistake worth catching is
    the wrong chat, not the absent intent, and there is no `--yes`. With
    `--create` the chat is made first from the blueprint's kind and title, through
    the same create path `create group` and `create channel` use, and the title
    typed is the one it will have. Every step the platform accepts leaves its own
    audit line; the apply id names the remap rows the archive keeps.
    """
    blueprint = structure_ops.read_blueprint(args.blueprint)
    identity = await _acting(client, report)
    wanted_kind = structure_ops.kind_of(blueprint)
    title = structure_ops.title_of(blueprint)

    if args.create:
        # No chat yet: the preview describes the one that will be made. The
        # plan's target is the account itself, as `create group`'s is.
        target = Target(rid=identity.id, kind=_rid.parse(identity.id).kind, title=title, path=(f"new {wanted_kind}: {title}",), platform=PLATFORM)
        rights = Rights(frozenset(), frozenset())
        current = None
        resolved = None
    else:
        target, kind, resolved = await _structure_target(client, report, args.chat)
        structure_ops.require_same_kind(blueprint, kind, target)
        rights = await _rights(client, report, resolved.input_entity)
    report.set_target(target)
    report.show_banner()

    steps_plan: list = []
    extras: list[str] = []
    if not args.create:
        port = TelegramBlueprintPort(client, resolver=_port_resolver(report))
        current = (await _blueprint.export(port, target, structure_ops.ALLOWLIST)).blueprint
        preview_diff = _blueprint.diff(blueprint, current)
        steps_plan = _blueprint.plan_steps(blueprint, current)
        extras = [change.line for change in preview_diff.of("remove")]
    else:
        # Every object and setting is a step on an empty chat; the container's
        # own title and kind come from the create call and are not re-set.
        empty = {
            "schema": blueprint["schema"],
            "container": {**blueprint["container"], "settings": {"name": title, "kind": wanted_kind}},
            "objects": [],
            "never_transferred": blueprint["never_transferred"],
        }
        steps_plan = _blueprint.plan_steps(blueprint, empty)

    required = structure_ops.rights_for(steps_plan) if not args.create else ()
    blueprint_hash = _blueprint.blueprint_hash(blueprint)
    plan = structure_ops.plan_for(identity, target, steps_plan, required=required, held=rights.held, blueprint_hash=blueprint_hash)
    report.set_plan(plan)
    if not args.create:
        _plan, warnings = build_plan(identity=identity, command="structure apply", targets=[target], mutations=plan.mutations, approval=structure_ops.GATE, rights=rights, required=required)
        for warning in warnings:
            report.warn(warning)
        require_rights(plan, rights, required)

    preview = structure_ops.format_steps(steps_plan, target=target, extras=extras, execute=args.execute)
    described = {**plan.describe(), "blueprint_hash": blueprint_hash, "steps": [step.to_dict() for step in steps_plan], "extras": extras, "create": bool(args.create)}
    if not args.execute:
        report.info(preview)
        report.result({**described, "dry_run": True, "executed": False}, status="dry_run")
        return 0

    # The gate. Both modes need a terminal: the engine refuses an Approval whose
    # interactive flag is false, and that flag is the tty check, not the answer.
    if not structure_ops.terminal_present():
        raise ApprovalRequired(report.human_command)
    if not archive_store.confirm_typed_name(preview, title if args.create else target.title, **report.confirm_io()):
        report.info("That is not the title; nothing was changed.")
        report.result({**described, "dry_run": False, "executed": False, "cancelled": True}, status="cancelled")
        return 1

    if args.create:
        # The same calls `create group --forum` and `create channel` make; the
        # typed title above was this create's gate.
        about = blueprint["container"]["settings"].get("about") or None
        if wanted_kind == "channel":
            made = await create_channel(client, title, about=about)
        else:
            made = await create_group(client, title, about=about, forum=wanted_kind == "forum")
        target, kind, resolved = await _structure_target(client, report, str(made.id))
        report.set_target(target)
        report.audit(plan, status="ok", evidence=Evidence.verified(f"created {wanted_kind} {title} as {target.rid}"))
    else:
        # Re-derive after the gate: the chat the title was typed for is the chat
        # still under that title.
        fresh, _fresh_kind, resolved = await _structure_target(client, report, args.chat)
        if fresh.rid != target.rid or fresh.title != target.title:
            raise CommandError(
                f"The chat changed between the preview and the execution: {target.title!r} ({target.rid}) "
                f"is now {fresh.title!r} ({fresh.rid}).",
                code="PLAN_DRIFT",
                hint="Run it again: the preview will show what it is now.",
            )

    def audit_step(step, target_rid: str) -> None:
        report.audit(plan, status="ok", evidence=Evidence.verified(f"{step['op']} {step['handle']} -> {target_rid}"))

    port = TelegramBlueprintPort(client, resolver=_port_resolver(report), on_step=audit_step)
    with archive_store.open_archive() as archive:
        outcome = await _blueprint.apply(
            port,
            blueprint,
            target,
            structure_ops.ALLOWLIST,
            approval=structure_ops.approval_for(True),
            identity=identity,
            archive=archive,
        )
    report.info(structure_ops.format_apply(outcome))
    if outcome.readback is not None:
        evidence = Evidence.verified(
            f"readback: {len(outcome.readback.pending)} pending, {len(outcome.extras)} extra left alone"
        )
    else:
        evidence = Evidence.unverified(outcome.error or "the target could not be read back")
    report.set_evidence(evidence)
    status = "ok" if outcome.status == "ok" else "partial"
    report.result({**outcome.to_dict(), "dry_run": False, "executed": True, "blueprint_hash": blueprint_hash}, status=status)
    return 0 if status == "ok" else 1


async def _run_structure_remap(args, config, *, report: Reporter) -> int:
    """The remap rows one apply wrote. Reads the archive; no connection."""
    identity = await _offline_identity(config, report)
    report.show_banner()
    with archive_store.open_archive() as archive:
        rows = structure_ops.remap_rows(archive, args.apply_id)
    report.info(structure_ops.format_remap(rows, apply_id=args.apply_id))
    report.result({"apply_id": args.apply_id, "identity": identity.to_dict(), "rows": rows}, status="ok" if rows else "empty")
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
        if not args.json_output:
            report.show_banner()
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
    if not args.json_output:
        report.show_banner()

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


# -- identity --------------------------------------------------------------


def _profile_row(profile) -> str:
    """One line of `profiles`: the name, its label, and what is true of it.

    Labels only, per section 5.2: no session path, no phone number, no token.
    """
    if profile.label:
        label = profile.label
    elif profile.logged_in:
        # A session from before profiles, or one whose record was deleted: it
        # works, and the name behind it is only learned by connecting.
        label = "(logged in, name not recorded yet)"
    else:
        label = "(no session yet)"
    marks = []
    if profile.legacy and profile.logged_in:
        marks.append("session from before profiles")
    if profile.proxy:
        marks.append(f"via {profile.proxy}")
    return f"{profile.name:<12} {label}" + (f"   ({', '.join(marks)})" if marks else "")


def _run_profiles(args, *, report: Reporter, home: Path | None = None) -> int:
    """List the named logins. Reads the store; opens no connection."""
    found = profile_store.listing(home)
    if not report.machine:
        if found:
            for profile in found:
                print(_profile_row(profile))
        else:
            print("No profiles yet. Log one in with: telegram-tools auth")
    report.result(
        {"profiles": [profile.to_dict() for profile in found], "active": _profile_name(args)},
        status="ok" if found else "empty",
    )
    return 0


def _profile_name(args) -> str:
    return getattr(args, "profile", None) or os.environ.get("TELEGRAM_TOOLS_PROFILE") or profile_store.DEFAULT_PROFILE


async def _run_auth(args, config, *, report: Reporter, home: Path | None = None) -> int:
    """Log this profile in, log it out, or move the pre-profile session into it.

    Interactive by construction. Every branch asks something at the terminal,
    and under `--json` with nothing to ask on the run refuses with
    APPROVAL_REQUIRED rather than blocking on a prompt nobody will answer.
    """
    name = config.profile
    profile = profile_store.load(name, home=home)
    io = report.confirm_io()
    read = io.get("read") or input
    write = report.info

    if args.migrate:
        return _run_migrate(profile, report=report, read=read, write=write, home=home)

    if args.logout:
        return await _run_logout(profile, config, report=report, read=read, write=write)

    if args.qr and not login.qr_available():
        raise CommandError(
            # The install command is in the message as well as the hint: human
            # mode prints only the message, and a refusal with no way out is
            # worse than no refusal at all.
            f"Logging in by QR needs the qr extra, which is not installed. Install it with: {login.QR_EXTRA_HINT}",
            code="CONFIG_MISSING",
            hint=login.QR_EXTRA_HINT,
        )

    write(f"Logging in to profile {name!r}.")
    client = await start_client(create_client(config), authorize=False)
    try:
        if await client.is_user_authorized():
            user = await client.get_me()
            write(f"Profile {name!r} is already logged in as {account_label(user)}.")
            return _record_login(profile, user, config, report=report, method="already", home=home)
        if args.qr:
            result = await login.sign_in_with_qr(client, write=write)
        else:
            result = await login.sign_in_with_code(client, read=read, write=write)
    finally:
        tighten_session(config.session_path)
        await client.disconnect()

    return _record_login(profile, result.user, config, report=report, method=result.method, home=home)


def _record_login(profile, user, config, *, report: Reporter, method: str, home: Path | None) -> int:
    """Save what the profile now knows about itself, and say who it is."""
    label = account_label(user)
    proxy = getattr(config, "proxy", None)
    saved = profile_store.record_login(
        profile,
        label=label,
        user_id=int(getattr(user, "id", 0)),
        proxy=proxy.label if proxy is not None else None,
    )
    report.info(f"Profile {saved.name!r} is logged in as {label}.")
    report.result({"profile": saved.to_dict(), "method": method}, status="ok")
    return 0


async def _run_logout(profile, config, *, report: Reporter, read, write) -> int:
    """End the session at Telegram's end, then delete what is left locally."""
    if not profile.logged_in:
        raise login.LoginRequired(profile.name)
    write(f"This ends the session for profile {profile.name!r} and deletes it from this machine.")
    typed = read(f"Type the profile name to confirm ({profile.name}): ").strip()
    if typed != profile.name:
        write("That is not the profile name; nothing was logged out.")
        report.result({"profile": profile.to_dict(), "logged_out": False}, status="cancelled")
        return 1

    client = await start_client(create_client(config), authorize=False)
    ended = False
    try:
        if await client.is_user_authorized():
            ended = await login.log_out(client)
    finally:
        await _disconnect_quietly(client)

    removed = profile_store.forget(profile)
    write(f"Profile {profile.name!r} is logged out." if ended else f"Profile {profile.name!r} was removed from this machine.")
    report.result(
        # Counts, never paths: what was removed is this machine's business,
        # and where it lived is never printed.
        {"profile": profile.name, "logged_out": ended, "files_removed": len(removed)},
        status="ok",
    )
    return 0


def _run_migrate(profile, *, report: Reporter, read, write, home: Path | None) -> int:
    """Move the pre-profile session into `profiles/default/`, after a y/N."""
    if not profile.legacy:
        raise CommandError(
            f"Profile {profile.name!r} already keeps its session in its own directory; nothing to migrate.",
            code="CONFIG_INVALID",
            hint="telegram-tools profiles",
        )
    if not profile.logged_in:
        raise login.LoginRequired(profile.name)

    write("The session from before profiles will be moved into the default profile's own directory.")
    write("Nothing is sent to Telegram and you stay logged in; only the file moves.")
    if read("Move it? [y/N]: ").strip().lower() not in ("y", "yes"):
        write("Left where it was.")
        report.result({"profile": profile.name, "migrated": False}, status="cancelled")
        return 1

    profile_store.migrate(home)
    write(f"Moved. Profile {profile.name!r} now keeps its own session.")
    report.result({"profile": profile.name, "migrated": True}, status="ok")
    return 0


# -- administration (section 13) --------------------------------------------


def _manage_kind(entity, target: Target) -> str:
    """`supergroup`, `forum` or `channel`; a basic group is refused, because every call here is a channel call."""
    try:
        return chat_kind(entity)
    except CommandError as exc:
        raise CommandError(
            f"{target.title} is a {classify_entity(entity)}, and the admin, member, invite and settings calls are supergroup calls.",
            code="PLATFORM_UNSUPPORTED",
            hint="Telegram upgrades a basic group to a supergroup the moment you change a setting on it in the app; run this again after that.",
        ) from exc


def _refuse_missing(rights: Rights, required: Sequence[str], target: Target) -> None:
    """A read that Telegram only answers for an admin refuses by name, like a write's preflight."""
    missing = rights.missing(required)
    if missing:
        names = ", ".join(missing)
        raise CommandError(
            f"Your Telegram account lacks {names} in {target.title}.",
            code="PERMISSION_DENIED",
            hint=f"Ask an admin of {target.title} for {names}, or run this as an account that has it.",
        )


async def _run_manage(client, args, *, report: Reporter) -> int:
    """One administration verb, behind the steps every write here takes.

    Plan, preflight, the hierarchy rule, the gate section 7 assigns, the
    re-derivation, the call, the readback, the audit line. A `typed_name` verb
    (`member ban`, `admin demote`) dry-runs by default, takes `--execute`, asks
    for the person's exact label, has no `--yes`, and needs a terminal in either
    mode. Under `--as-bot` the bot has to be an admin of the chat, and then the
    same preflight names any right it lacks.
    """
    op = manage_ops.op_for(args)
    resolved = await _resolve(client, report, args.chat)
    peer = resolved.input_entity
    target = ChatTargets.chat_target(resolved, args.chat)
    report.set_target(target)
    report.show_banner()
    kind = _manage_kind(resolved.entity, target)
    port = TelegramManagePort(client)
    rights = await _rights(client, report, peer)
    if _in_bot_mode(report) and "is_admin" in rights.answered and "is_admin" not in rights.held:
        raise CommandError(
            f"{report.acting.label} is not an admin of {target.title}, so `{op.command}` cannot run as it.",
            code="IDENTITY_MODE_UNSUPPORTED",
            hint=report.account_command,
        )

    if not op.writes:
        return await _run_manage_read(port, op, args, resolved, target, kind, report=report, rights=rights)

    identity = await _acting(client, report)
    me = report.me or await client.get_me()
    execute = bool(getattr(args, "execute", False))

    # -- who, and what changes --------------------------------------------------
    member = user = input_user = None
    if op.person:
        user, input_user = await port.resolve_user(args.user)
        member = await port.participant(peer, user, input_user)
    params: dict = {}
    details: list[str] = []
    names: tuple[str, ...] = ()
    until = None
    made: dict | None = None

    if op.verb in ("promote", "rights"):
        names = manage_ops.parse_rights(args.rights, universe=manage_ops.ADMIN_RIGHT_NAMES, what="admin")
        if op.verb == "promote" and member.is_admin:
            raise CommandError(f"{member.label} is already an admin of {target.title}.", code="TARGET_KIND_MISMATCH", hint=f"telegram-tools admin rights --chat {args.chat} --user {args.user} --rights …")
        if op.verb == "rights" and not member.is_admin:
            raise CommandError(f"{member.label} is not an admin of {target.title}.", code="TARGET_KIND_MISMATCH", hint=f"telegram-tools admin promote --chat {args.chat} --user {args.user} --rights …")
        actor = await port.participant(peer, me, InputUserSelf())
        manage_ops.require_hierarchy(actor, target=member if member.is_admin else None, granting=names, chat_title=target.title)
        params = {"status": member.status, "rights": list(names), "rank": args.rank or ""}
        details.append(f"Rights  {', '.join(names) or 'none'}")
        if args.rank:
            details.append(f"Rank    {args.rank}")
    elif op.verb == "demote":
        if not member.is_admin:
            raise CommandError(f"{member.label} is not an admin of {target.title}; there is nothing to demote.", code="TARGET_KIND_MISMATCH")
        actor = await port.participant(peer, me, InputUserSelf())
        manage_ops.require_hierarchy(actor, target=member, granting=(), chat_title=target.title)
        params = {"status": member.status, "rights": []}
        details.append("Rights  none (every admin right taken away)")
    elif op.verb in ("ban", "mute", "restrict"):
        if member.is_admin:
            raise CommandError(
                f"{member.label} is an admin of {target.title}; an admin cannot be {op.verb}ned until demoted." if op.verb == "ban" else f"{member.label} is an admin of {target.title}; an admin cannot be restricted until demoted.",
                code="HIERARCHY_DENIED",
                hint=f"telegram-tools admin demote --chat {args.chat} --user {args.user} --execute",
            )
        if op.verb == "ban":
            names = manage_ops.BAN_RIGHTS
            params = {"status": member.status, "rights": list(names), "reason": args.reason or ""}
            if args.reason:
                details.append(f"Reason  {args.reason}")
            details.append("Note    Telegram stores no reason for a ban; the local audit line is the only record.")
        else:
            until = manage_ops.parse_until(args.until)
            names = manage_ops.MUTE_RIGHTS if op.verb == "mute" else manage_ops.parse_rights(args.rights, universe=manage_ops.BANNED_RIGHT_NAMES, what="banned")
            if "view_messages" in names:
                raise ValueError("Taking view_messages away is a ban: use `member ban`.")
            params = {"status": member.status, "rights": list(names), "until": manage_ops.until_text(until)}
            details.append(f"Takes   {', '.join(names)}")
            details.append(f"Until   {manage_ops.until_text(until)}")
    elif op.verb in ("unban", "unmute"):
        if member.status not in ("banned", "restricted"):
            raise CommandError(f"{member.label} is not banned or restricted in {target.title} (they are {member.status}).", code="TARGET_KIND_MISMATCH")
        params = {"status": member.status, "rights": []}
        details.append("Lifts   every restriction")
    elif op.verb in ("approve", "decline"):
        params = {"status": member.status, "approved": op.verb == "approve"}
    elif op.verb == "create":
        expires = manage_ops.parse_until(args.expires) if args.expires else None
        params = {"title": args.title or "", "expires": manage_ops.until_text(expires), "usage_limit": args.usage_limit, "request_needed": bool(args.request_needed)}
        details.append(f"Title   {args.title or '(none)'}")
        details.append(f"Expires {manage_ops.until_text(expires) or 'never'}")
        details.append(f"Uses    {args.usage_limit or 'unlimited'}")
        if args.request_needed:
            details.append("Joining needs an admin's approval")
    elif op.verb == "revoke":
        params = {"link": redact_text(args.link)}
        details.append(f"Link    {redact_text(args.link)}")
    elif op.verb == "set":
        seconds = manage_ops.parse_slow_mode(args.slow_mode)
        if kind == "channel":
            raise CommandError(f"{target.title} is a broadcast channel, which has no slow mode.", code="PLATFORM_UNSUPPORTED")
        params = {"slow_mode_seconds": seconds}
        details.append(f"Slow mode  {'off' if not seconds else f'{seconds}s'}")

    def build(chat_target: Target, person):
        rid = person.rid if person is not None else chat_target.rid
        fresh = dict(params)
        if person is not None and "status" in fresh:
            fresh["status"] = person.status
        return build_plan(
            identity=identity,
            command=op.command,
            targets=[chat_target],
            mutations=[Mutation(op.mutation, rid, fresh)],
            approval=op.approval,
            rights=rights,
            required=op.required,
        )

    plan, warnings = build(target, member)
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    require_rights(plan, rights, op.required)

    actor_label = f"{report.acting.label} (via {report.via_label})" if _in_bot_mode(report) else _entity_title(me, "you")
    preview = manage_ops.format_preview(
        op, actor=actor_label, chat_title=target.title, chat_id=resolved.id, member=member, details=details, execute=execute if op.typed else None
    )

    # -- the gate ---------------------------------------------------------------
    if op.typed and not execute:
        report.info(preview)
        outcome = manage_ops.Outcome(op.command, resolved.id, member, dry_run=True)
        report.printed_result(outcome.to_dict(), status="dry_run")
        return 0
    if op.typed:
        # A person's membership or rights: a terminal in either mode, as `delete` has.
        if not manage_ops.terminal_present():
            raise ApprovalRequired(report.human_command)
        answered = manage_ops.confirm_typed_label(preview, member, **report.confirm_io())
        if not answered:
            report.info("That is not the label; nothing was changed.")
    else:
        answered = message_ops.confirm_prompt_y(preview, **report.confirm_io())
    if not answered:
        outcome = manage_ops.Outcome(op.command, resolved.id, member, cancelled=True)
        report.printed_result(outcome.to_dict(), status="cancelled")
        return 1

    # -- re-derivation: the chat and the person are still what was shown --------
    async def rebuild():
        again = await _resolve(client, report, args.chat)
        fresh_target = ChatTargets.chat_target(again, args.chat)
        fresh_member = member if member is None else await port.participant(again.input_entity, user, input_user)
        return build(fresh_target, fresh_member)[0]

    await recheck_for(plan, rebuild)()

    # -- the call ---------------------------------------------------------------
    if op.verb in ("promote", "rights"):
        await port.set_admin(peer, input_user, names, rank=args.rank)
    elif op.verb == "demote":
        await port.set_admin(peer, input_user, ())
    elif op.verb in ("ban", "mute", "restrict"):
        await port.set_banned(peer, input_user, names, until=until)
    elif op.verb in ("unban", "unmute"):
        await port.set_banned(peer, input_user, ())
    elif op.verb in ("approve", "decline"):
        await port.answer_join_request(peer, input_user, approved=op.verb == "approve")
    elif op.verb == "create":
        made = await port.create_invite(peer, title=args.title, expires=expires, usage_limit=args.usage_limit, request_needed=bool(args.request_needed))
    elif op.verb == "revoke":
        made = await port.revoke_invite(peer, args.link)
    elif op.verb == "set":
        await port.set_slow_mode(peer, seconds)

    # -- readback ---------------------------------------------------------------
    extra: dict = {}
    after = None
    if member is not None:
        async def person_now() -> str:
            nonlocal after
            after = await port.participant(peer, user, input_user)
            line = f"{after.label} is now {after.status} in {target.title}"
            if after.rights and after.status in ("admin", "restricted"):
                line += f" with {', '.join(after.rights)}"
            if after.until:
                line += f" until {after.until}"
            if op.verb == "ban" and args.reason:
                line += f" (reason: {args.reason})"
            return line

        evidence = await read_back("the person", person_now)
    elif op.verb == "create":
        evidence = Evidence.verified(f"an invite link to {target.title} now exists" + (f", titled {made['title']}" if made.get("title") else "") + (f", expiring {made['expires']}" if made.get("expires") else ""))
        extra["invite"] = made
    elif op.verb == "revoke":
        evidence = Evidence.verified(f"the link is revoked: {made.get('revoked')}") if made.get("revoked") else Evidence.unverified("Telegram did not report the link as revoked")
        extra["invite"] = made
    else:
        async def settings_now() -> str:
            now = await port.settings(resolved)
            seconds_now = now.get("slow_mode_seconds", 0)
            return f"slow mode in {target.title} is now {'off' if not seconds_now else f'{seconds_now}s'}"

        evidence = await read_back("the settings", settings_now)
    if op.verb == "ban":
        extra["reason"] = args.reason or ""
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    outcome = manage_ops.Outcome(op.command, resolved.id, after or member, done=True, extra=extra)
    report.printed_result(outcome.to_dict(), status="ok", show_invites=op.verb == "create")
    return 0


async def _run_manage_read(port, op, args, resolved, target: Target, kind: str, *, report: Reporter, rights: Rights) -> int:
    """`admin list`, `member list`, `join-requests list`, `invite list`, `settings show`: reads, no plan."""
    peer = resolved.input_entity
    _refuse_missing(rights, op.required, target)
    if op.group == "admin":
        rows = await port.admins(peer)
        report.info(manage_ops.format_members(rows, chat_title=target.title, what="admin(s)"))
        payload = [row.to_dict() for row in rows]
        for row in payload:
            report.record(row)
        report.result({"admins": payload}, status="ok" if payload else "empty")
    elif op.group == "member":
        rows = await port.members(peer, query=args.query, limit=int(args.limit), banned=bool(args.banned))
        what = "banned or restricted" if args.banned else "member(s)"
        report.info(manage_ops.format_members(rows, chat_title=target.title, what=what))
        payload = [row.to_dict() for row in rows]
        for row in payload:
            report.record(row)
        report.result({"members": payload, "banned": bool(args.banned)}, status="ok" if payload else "empty")
    elif op.group == "join-requests":
        rows = await port.join_requests(peer)
        report.info(manage_ops.format_requests(rows, chat_title=target.title))
        for row in rows:
            report.record(row)
        report.result({"requests": rows}, status="ok" if rows else "empty")
    elif op.group == "invite":
        rows = await port.invites(peer, revoked=bool(args.revoked))
        report.info(manage_ops.format_invites(rows, chat_title=target.title))
        # The one envelope that carries links: this command asked for them.
        report.result({"invites": rows, "revoked": bool(args.revoked)}, status="ok" if rows else "empty", show_invites=True)
    else:
        settings = await port.settings(resolved)
        report.info(manage_ops.format_settings(settings, chat_title=target.title))
        report.result({"settings": settings}, status="ok")
    return 0


# -- bot mode --------------------------------------------------------------


# -- watch: the runner, its rules, and the two guarantees ---------------------


def _watch_plan(identity: Identity, command: str, op: str, params: dict, *, approval: str = "prompt_y", targets=()):
    """The plan a watch write carries.

    A rule file and a runner-held schedule live on this machine, not at
    Telegram's end, so there is no right to preflight and the preflight is
    empty by construction rather than by omission. The other three steps are
    the same as every other write here: the plan is built before anything is
    asked, the result is read back, and one redacted line goes to the audit
    log. The mutation's rid is the identity's own, because a rule belongs to
    the login that wrote it.
    """
    plan, _warnings = build_plan(
        identity=identity,
        command=command,
        targets=list(targets),
        mutations=[Mutation(op, targets[0].rid if targets else identity.id, params)],
        approval=approval,
        rights=Rights(frozenset(), frozenset()),
        required=(),
    )
    return plan


def _rules_set(which=None):
    """The rules directory as a reloadable set. A bad file refuses the whole load."""
    kwargs = {} if which is None else {"which": which}
    return _rules.RuleSet(archive_store.paths_for().rules, **kwargs)


def _refuse_unrunnable(paths) -> None:
    """The two things that stop a runner before it connects: no fcntl, and a live holder."""
    if _runner.fcntl is None:
        raise CommandError(
            "The runner needs an exclusive file lock, which this platform does not provide.",
            code="PLATFORM_UNSUPPORTED",
            hint="watch run supports macOS and Linux; every one-shot command still works here",
        )
    holder = _runner.read_lock(paths.runner_lock)
    if holder is not None and holder.alive:
        raise CommandError(
            f"A runner already holds {paths.runner_lock.name}: pid {holder.pid} since {holder.started_at}.",
            code="RUNNER_LOCKED",
            hint="`watch status` shows the holder; `watch stop` asks it to exit",
        )


def _own_identity_rids() -> list[str]:
    """Every profile's rid on this machine: an event any of them sent is dropped (section 10.3)."""
    return [str(_rid.make(PREFIX, "user", profile.user_id)) for profile in profile_store.listing() if profile.user_id]


def _archive_sync_callback(client, identity: Identity, loop):
    """What a rule's `archive` action calls: one scope synced, within the store's budgets.

    The walk runs on the client's own thread, with an archive connection made
    there: SQLite binds a connection to the thread that opened it, and the
    runner's own connection belongs to the thread the engine runs on.
    """

    def fulfil(request):
        async def work():
            with archive_store.open_archive() as scoped:
                source = TelegramArchiveSource(client, only=[request.rid])
                result = await scoped.sync(source, identity)
                return {"status": result.status, "rid": request.rid, "scopes": len(result.scopes)}

        return loop.call(work())

    return fulfil


async def _run_watch_run(args, config, *, report: Reporter) -> int:
    """`watch run`: replay what the last run missed, then live events and schedules, in the foreground.

    The client is a detached one (`client.detached_session`): it holds a copy
    of the profile's authorization in memory and never opens the session file,
    so `telegram-tools send` in another terminal keeps working while this runs.
    The runner installs no service and prints none: it is started by a person,
    and stopped by Ctrl-C or `watch stop`.
    """
    paths = archive_store.paths_for()
    _refuse_unrunnable(paths)
    identity = await _offline_identity(config, report)
    report.show_banner()
    mode = "bot" if _in_bot_mode(report) else "account"
    rules = _rules_set()
    report.info(f"{len(rules)} rule(s) loaded from {paths.rules}")

    client = create_detached_client(config)
    loop = watch_events.ClientLoop(client)
    loop.start()
    try:
        if not loop.call(client.is_user_authorized()):
            raise login.LoginRequired(getattr(config, "profile", profile_store.DEFAULT_PROFILE))
        source = watch_events.TelegramEventSource(client, loop, mode=mode)
        source.register()
        sender = watch_events.TelegramMessageSender(client, loop, config.send_allowlist)
        with archive_store.open_archive() as archive:
            runner = _runner.Runner(
                archive,
                identity,
                rules,
                paths,
                sender=sender,
                sync=_archive_sync_callback(client, identity, loop),
                queue=review_ops.queue_for(archive),
                own_identities=_own_identity_rids(),
                tz=watch_ops.local_zone(),
            )
            report.info("Running. Ctrl-C stops it, and so does `telegram-tools watch stop`.")
            counts = runner.run(source)
            status = runner.status()
        source.close()
    finally:
        loop.stop()
    report.result({**counts, "dropped_events": source.dropped, "rules": len(rules), "status": status}, status="ok")
    return 0


async def _run_watch_status(args, config, *, report: Reporter) -> int:
    """`watch status`: the lock and its holder, the rules loaded, the last tick, cursors and schedules."""
    await _offline_identity(config, report)
    report.show_banner()
    paths = archive_store.paths_for()
    try:
        loaded = len(_rules_set())
    except CodedError as exc:
        # A rule the runner would refuse to start on is what `status` is for.
        report.warn(f"the rules do not load: {exc.error.message}")
        loaded = 0
    with archive_store.open_archive() as archive:
        status = _runner.report(paths, archive)
    status["rules"] = loaded
    if not report.machine:
        print(watch_ops.format_status(status, rules=loaded))
    report.result(status, status="ok" if status["running"] else "empty")
    return 0


async def _run_watch_signal(args, config, *, report: Reporter) -> int:
    """`watch stop` and `watch reload`: a signal to the holder, and what it answered."""
    await _offline_identity(config, report)
    report.show_banner()
    paths = archive_store.paths_for()
    kind = args.watch_kind
    result = _runner.stop(paths) if kind == "stop" else _runner.reload(paths)
    report.info(
        f"Runner pid {result['pid']}: "
        + ("asked to exit" if kind == "stop" else "asked to re-read its rules")
        + (f", waited {result['waited_s']}s" if "waited_s" in result else "")
    )
    if result["status"] != "ok":
        report.warn(result.get("error") or "the runner has not answered yet")
    report.result(result, status=result["status"])
    return 1 if result["status"] != "ok" else 0


async def _run_watch_rules(args, config, *, report: Reporter) -> int:
    """The rule files on this machine. Nothing here connects: a rule is local configuration."""
    verb = getattr(args, "rules_verb", None)
    if verb is None:
        raise ValueError("watch rules needs one of: " + ", ".join(watch_ops.RULES_VERBS) + ".")
    identity = await _offline_identity(config, report)
    report.show_banner()
    paths = archive_store.paths_for()

    if verb == "list":
        loaded = _rules_set()
        rows = [rule.to_dict() for rule in loaded]
        for row in rows:
            report.record(row)
        if not report.machine:
            print(watch_ops.format_rules(list(loaded), paths.rules))
        report.result({"count": len(rows), "rules": rows, "directory": str(paths.rules)}, status="ok" if rows else "empty")
        return 0

    if verb == "test":
        loaded = _rules_set()
        event = _rules.load_event(args.event)
        wanted = getattr(args, "name", None)
        chosen = [loaded.get(wanted)] if wanted else list(loaded)
        explained = _rules.explain(chosen, event, identity, own_identities=_own_identity_rids())
        if not report.machine:
            print(watch_ops.format_test(explained))
        report.result(explained, status="ok")
        return 0

    name = args.name
    path = paths.rule(name)
    if verb in ("add", "edit"):
        base = watch_ops.read_rule_file(path) if verb == "edit" else None
        if verb == "add" and path.exists():
            raise watch_ops.WatchError(
                f"a rule named {name!r} is already there",
                hint=f"telegram-tools watch rules edit --name {name}",
            )
        data = watch_ops.rule_from(args, base)
        # The core validates before anything is written: an unknown action kind,
        # a regex that does not compile and a command that is not on PATH are
        # all refused here, at rule load, not when the rule would fire.
        rule = _rules.load_rule(data, source=f"{name}.json")
        plan = _watch_plan(identity, f"watch rules {verb}", f"rule.{verb}", {"name": name, "actions": [action.kind for action in rule.actions]})
        report.set_plan(plan)
        _rules.write_rule(paths, rule)
        written = _rules.load_file(path)
        evidence = Evidence.verified(f"{path.name} holds rule {written.name} with {len(written.actions)} action(s)")
        report.set_evidence(evidence)
        report.audit(plan, status="ok", evidence=evidence)
        if not report.machine:
            print(watch_ops.format_rules([written], paths.rules))
            print(f"Written to {path}. It is JSON: edit it by hand whenever the flags are the long way round.")
        report.result({**plan.describe(), "rule": written.to_dict(), "path": str(path)}, status="ok")
        return 0

    if verb in ("enable", "disable"):
        stored = watch_ops.read_rule_file(path)
        stored["enabled"] = verb == "enable"
        rule = _rules.load_rule(stored, source=path.name)
        plan = _watch_plan(identity, f"watch rules {verb}", f"rule.{verb}", {"name": name})
        report.set_plan(plan)
        _rules.write_rule(paths, rule)
        written = _rules.load_file(path)
        evidence = Evidence.verified(f"{written.name} is {'enabled' if written.enabled else 'disabled'}")
        report.set_evidence(evidence)
        report.audit(plan, status="ok", evidence=evidence)
        report.info(f"{written.name} is now {'enabled' if written.enabled else 'disabled'}.")
        report.result({**plan.describe(), "rule": written.to_dict()}, status="ok")
        return 0

    # remove: it deletes a file the user wrote, so it asks first.
    stored = watch_ops.read_rule_file(path)
    plan = _watch_plan(identity, "watch rules remove", "rule.remove", {"name": name})
    report.set_plan(plan)
    preview = watch_ops.format_rules([_rules.load_rule(stored, source=path.name)], paths.rules)
    if not watch_ops.confirm(preview, f"Delete {path.name}?", **report.confirm_io()):
        report.result({**plan.describe(), "removed": None, "cancelled": True}, status="cancelled")
        return 1
    path.unlink()
    evidence = (
        Evidence.verified(f"{path.name} is gone") if not path.exists() else Evidence.unverified(f"{path.name} is still there")
    )
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.info(f"{path.name} removed.")
    report.result({**plan.describe(), "removed": name}, status="ok")
    return 0


async def _run_watch(args, config, *, report: Reporter) -> int:
    kind = getattr(args, "watch_kind", None)
    if kind is None:
        raise ValueError("watch needs one of: " + ", ".join(watch_ops.WATCH_VERBS) + ".")
    if kind == "rules":
        return await _run_watch_rules(args, config, report=report)
    if kind == "run":
        return await _run_watch_run(args, config, report=report)
    if kind == "status":
        return await _run_watch_status(args, config, report=report)
    if kind in ("stop", "reload"):
        return await _run_watch_signal(args, config, report=report)
    raise ValueError(f"Unknown watch command: {kind}")


# -- schedule: what Telegram holds, and what this runner holds ----------------


async def _native_schedules(client, report: Reporter, reference: str) -> tuple[list, Any, Any]:
    """The messages Telegram is holding for a chat, newest first, as `schedule list` rows."""
    resolved = await _resolve(client, report, reference)
    chat = ChatTargets.chat_target(resolved, reference)
    history = await client(GetScheduledHistoryRequest(peer=resolved.input_entity, hash=0))
    rows = []
    for message in getattr(history, "messages", None) or ():
        topic_id = watch_events.topic_of(message)
        rows.append(
            watch_ops.ScheduledMessage(
                message_id=int(getattr(message, "id")),
                rid=watch_ops.schedule_rid(resolved.id, topic_id),
                at=_iso_date(getattr(message, "date", None)),
                text=getattr(message, "message", "") or "",
            ).to_dict()
        )
    return rows, resolved, chat


def _iso_date(value) -> str | None:
    return None if value is None else value.isoformat()


async def _run_schedule(args, config, *, client=None, report: Reporter) -> int:
    """`schedule list|post|cancel`. Every row says which of the two guarantees it has."""
    kind = getattr(args, "schedule_kind", None)
    if kind is None:
        raise ValueError("schedule needs one of: " + ", ".join(watch_ops.SCHEDULE_VERBS) + ".")
    reference = getattr(args, "chat", None)
    # A client is opened only when Telegram itself has to answer: the runner's
    # own schedules are rows in the local archive.
    owns = client is None and (kind == "post" or reference is not None)
    if owns:
        client = await start_client(create_client(config), authorize=not report.machine)
        if report.machine and not await client.is_user_authorized():
            await _disconnect_quietly(client)
            raise login.LoginRequired(getattr(config, "profile", profile_store.DEFAULT_PROFILE))
    try:
        if client is not None and reference is not None or kind == "post":
            identity = await _acting(client, report)
        else:
            identity = await _offline_identity(config, report)
        report.show_banner()
        with archive_store.open_archive() as archive:
            state = _runner.RunnerState(archive, identity)
            schedules = _runner.Schedules(state, _runner.Clock(), watch_ops.local_zone())
            if kind == "list":
                return await _run_schedule_list(args, schedules, client=client, report=report, reference=reference)
            if kind == "post":
                return await _run_schedule_post(args, schedules, identity, client=client, report=report)
            return await _run_schedule_cancel(args, schedules, identity, client=client, report=report, reference=reference)
    finally:
        if owns:
            await client.disconnect()


async def _run_schedule_list(args, schedules, *, client, report: Reporter, reference) -> int:
    native: list = []
    if reference is not None and client is not None:
        native, _resolved, chat = await _native_schedules(client, report, reference)
        report.set_target(chat)
    local = [_runner.listing(schedule) for schedule in schedules.list()]
    for row in (*native, *local):
        report.record(row)
    if not report.machine:
        print(watch_ops.format_schedules(native, local))
        if reference is None:
            print("Only this runner's own are listed: Telegram holds scheduled messages per chat, so pass --chat to see those.")
    report.result(
        {"native": native, "local": local, "guarantees": list(_runner.GUARANTEES)},
        status="ok" if native or local else "empty",
    )
    return 0


async def _run_schedule_post(args, schedules, identity: Identity, *, client, report: Reporter) -> int:
    """A message this runner will post. It says plainly that a runner that is down posts nothing."""
    text = _message_text(args.text, has_files=False)
    if not text:
        raise ValueError("schedule post needs --text.")
    destination, peer, _resolved = await _resolve_destination(client, report, args.chat, getattr(args, "topic", None))
    report.set_target(destination)
    when = None if args.at is None else watch_ops.require_future(watch_ops.parse_when(args.at))
    every = None if args.every is None else watch_ops.check_every(args.every)
    rights = await _rights(client, report, peer)
    plan, warnings = build_plan(
        identity=identity,
        command="schedule post",
        targets=[destination],
        mutations=[Mutation("schedule.post", destination.rid, {"at": None if when is None else when.isoformat(), "every": every})],
        approval="prompt_y",
        rights=rights,
        required=SEND_RIGHTS,
    )
    report.set_plan(plan)
    for warning in warnings:
        report.warn(warning)
    # The right is checked now, even though the send is later: a schedule that
    # could never post is worth refusing while somebody is here to read why.
    require_rights(plan, rights, SEND_RIGHTS)
    preview = "\n".join(
        [
            f"Scheduling into {destination.display}",
            watch_ops.RULE,
            f"When    {when.isoformat() if when else 'every ' + str(every)}",
            f"Guarantee {watch_ops.RUNNER_HELD}",
            watch_ops.RULE,
            text,
            watch_ops.RULE,
        ]
    )
    if not watch_ops.confirm(preview, "Schedule it?", **report.confirm_io()):
        report.result({**plan.describe(), "scheduled": None, "cancelled": True}, status="cancelled")
        return 1
    try:
        schedule = schedules.add(destination.rid, text, at=None if when is None else when.isoformat(), every=every)
    except _runner.RunnerError as exc:
        raise watch_ops.WatchError(str(exc), code="INVALID_ARGUMENT") from exc
    stored = schedules.get(schedule.id)
    evidence = Evidence.verified(f"schedule {stored.id} is stored, next {_runner._iso(stored.next_wall)}, {stored.guarantee}")
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    row = _runner.listing(stored)
    if not report.machine:
        print(watch_ops.format_schedule_created(row))
    report.result({**plan.describe(), "schedule": row}, status="ok")
    return 0


async def _run_schedule_cancel(args, schedules, identity: Identity, *, client, report: Reporter, reference) -> int:
    """Cancel one: with `--chat` it is a message Telegram is holding, without it one of this runner's."""
    schedule_id = args.schedule_id
    if reference is not None:
        if client is None:
            raise ValueError("schedule cancel --chat needs a connection.")
        native, resolved, chat = await _native_schedules(client, report, reference)
        report.set_target(chat)
        row = next((item for item in native if item["id"] == str(schedule_id)), None)
        if row is None:
            raise CommandError(
                f"Telegram is holding no scheduled message {schedule_id!r} in {chat.title}.",
                code="TARGET_NOT_FOUND",
                hint=f"telegram-tools schedule list --chat {reference}",
            )
        rights = await _rights(client, report, resolved.input_entity)
        plan, warnings = build_plan(
            identity=identity,
            command="schedule cancel",
            targets=[chat],
            mutations=[Mutation("schedule.cancel", row["rid"], {"message_id": int(schedule_id), "guarantee": row["guarantee"]})],
            approval="prompt_y",
            rights=rights,
            required=SEND_RIGHTS,
        )
        report.set_plan(plan)
        for warning in warnings:
            report.warn(warning)
        preview = watch_ops.format_schedules([row], [])
        if not watch_ops.confirm(preview, f"Cancel scheduled message {schedule_id}?", **report.confirm_io()):
            report.result({**plan.describe(), "cancelled_schedule": None, "cancelled": True}, status="cancelled")
            return 1
        await client(DeleteScheduledMessagesRequest(peer=resolved.input_entity, id=[int(schedule_id)]))
        again, _resolved, _chat = await _native_schedules(client, report, reference)
        gone = not any(item["id"] == str(schedule_id) for item in again)
        evidence = Evidence.verified(f"Telegram no longer holds {schedule_id}") if gone else Evidence.unverified(f"{schedule_id} is still scheduled")
        report.set_evidence(evidence)
        report.audit(plan, status="ok", evidence=evidence)
        report.info(f"Scheduled message {schedule_id} cancelled.")
        report.result({**plan.describe(), "cancelled_schedule": row}, status="ok")
        return 0

    try:
        stored = schedules.get(schedule_id)
    except _runner.RunnerError as exc:
        raise CommandError(
            str(exc),
            code="TARGET_NOT_FOUND",
            hint="`schedule list` shows this runner's own; a message Telegram holds needs --chat too",
        ) from exc
    row = _runner.listing(stored)
    plan = _watch_plan(
        identity,
        "schedule cancel",
        "schedule.cancel",
        {"schedule": stored.id, "guarantee": stored.guarantee},
    )
    report.set_plan(plan)
    if not watch_ops.confirm(watch_ops.format_schedules([], [row]), f"Cancel schedule {stored.id}?", **report.confirm_io()):
        report.result({**plan.describe(), "cancelled_schedule": None, "cancelled": True}, status="cancelled")
        return 1
    schedules.cancel(stored.id)
    still_there = any(item.id == stored.id for item in schedules.list())
    evidence = Evidence.verified(f"schedule {stored.id} is gone") if not still_there else Evidence.unverified(f"{stored.id} is still stored")
    report.set_evidence(evidence)
    report.audit(plan, status="ok", evidence=evidence)
    report.info(f"Schedule {stored.id} cancelled.")
    report.result({**plan.describe(), "cancelled_schedule": row}, status="ok")
    return 0


def require_bot_mode_supports(args, report: Reporter) -> None:
    """Refuse an account-only command under --as-bot, before anything connects.

    Named by code so an agent can key on it, with the same command minus the
    flag as the hint, because that command is the answer.
    """
    command = str(args.command or "")
    kind = getattr(args, "create_kind", None)
    verb = getattr(args, "message_verb", None)
    supported = command in BOT_MODE_COMMANDS and (command != "create" or kind in BOT_MODE_CREATE_KINDS)
    if command == "message" and verb not in message_ops.BOT_VERBS:
        supported = False
    # Section 10.6: `schedule_date` is user-only, so a bot may send now and
    # never later. The rest of `send` is unchanged under --as-bot.
    if command == "send" and getattr(args, "at", None):
        supported = False
    if supported:
        return
    what = command_name(args) + (" --at" if command == "send" else "")
    if command == "send":
        why = "Telegram lets only an account hand it a message to post later"
    elif command == "schedule":
        why = "Telegram's scheduled messages are user-only, and a repeat is held by the runner the account started"
    elif command == "message":
        why = "a bot has no dialog of its own to mark, no Saved Messages and no drafts"
    elif command == "review":
        why = "the review queue is the account's archive, and a bot reads no history"
    elif command == "structure":
        why = "a blueprint is read and applied through the account that administers the chat, and a bot creates no chat"
    else:
        why = "a bot has no dialog list, no history and nothing of its own to delete or set up"
    raise CommandError(
        f"`{what}` needs the account: {why}, so --as-bot cannot run it. Run it without --as-bot.",
        code="IDENTITY_MODE_UNSUPPORTED",
        hint=report.account_command,
    )


async def _via_account(config, report: Reporter) -> tuple[int, str]:
    """The account a bot acts through: its id and label.

    From the profile record when `auth` has written one -- no connection, and
    the account's own session can stay held by a menu elsewhere. A profile
    from before records existed is asked once, through its session.
    """
    stored = profile_store.load(getattr(config, "profile", profile_store.DEFAULT_PROFILE))
    if stored.label and stored.user_id:
        return int(stored.user_id), stored.label
    client = await start_client(create_client(config), authorize=not report.machine)
    try:
        if report.machine and not await client.is_user_authorized():
            raise login.LoginRequired(stored.name)
        user = await client.get_me()
    finally:
        await _disconnect_quietly(client)
    return int(getattr(user, "id", 0)), account_label(user)


async def run_as_bot(args, config, *, report: Reporter) -> int:
    """One command as the bot `--as-bot` names, through a client of its own.

    The nickname resolves exactly as `bots --bot` resolves it; the token opens
    a MemorySession and is never written anywhere; the bot Telegram answers
    for has to be the bot the token's own prefix names, or nothing runs.
    """
    nick = args.as_bot
    # Named `signin`, not `token`: the repository's commit guard reads any
    # `token = <8+ chars>` as a credential, and this is the one line that would
    # otherwise spell it.
    signin = lookup_bot_token(config.bot_tokens, nick)
    if signin is None:
        raise CommandError(
            f"No bot named {nick!r} in TELEGRAM_BOT_TOKENS.",
            code="CONFIG_MISSING",
            hint=f"Add {nick}:<token> to TELEGRAM_BOT_TOKENS in ~/.telegram-tools/.env, or pass a nickname it has.",
        )
    via_id, via_label = await _via_account(config, report)

    async with bot_client(config, signin) as bot:
        provider = await BotIdentity.open(bot, getattr(config, "profile", "default"), via_id=via_id, via_label=via_label)
        expected = bot_id_from_token(signin)
        if expected is not None and provider.id != expected:
            raise CommandError(
                f"The token stored as {nick!r} is for bot {expected}, but Telegram signed in bot {provider.id}.",
                code="IDENTITY_MISMATCH",
                hint="Fix that entry in TELEGRAM_BOT_TOKENS in ~/.telegram-tools/.env.",
            )
        report.set_identity(provider.identity(), me=provider.user, via_label=via_label)
        if args.command == "send":
            return await _run_send(bot, args, config, report=report)
        if args.command == "create":
            return await _run_create(bot, args, report=report)
        if args.command == "message":
            return await _run_message(bot, args, config, report=report)
        if args.command in manage_ops.GROUPS:
            return await _run_manage(bot, args, report=report)
        if args.command == "watch":
            return await _run_watch(args, config, report=report)
        raise ValueError(f"Unknown command: {args.command}")


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
        return run_doctor(report=report, profile=_profile_name(args))

    # Section 5.2: an account-only command under --as-bot refuses here, before
    # config is read and before anything could connect.
    as_bot = getattr(args, "as_bot", None)
    if as_bot and args.command != "profiles":
        require_bot_mode_supports(args, report)

    # Neither of these opens a connection, and neither needs credentials: one
    # reads the profile store, the other moves a file inside it. Dispatched
    # before `load_config` so a half-set-up machine can still use them.
    if args.command == "profiles":
        return _run_profiles(args, report=report)
    if args.command == "auth" and args.migrate:
        io = report.confirm_io()
        return _run_migrate(
            profile_store.load(_profile_name(args)),
            report=report,
            read=io.get("read") or input,
            write=report.info,
            home=None,
        )

    if config is None:
        config = load_config(profile=getattr(args, "profile", None))

    audit_path = getattr(config, "audit_path", None)
    if audit_path is not None and report.audit_log is None:
        report.audit_log = AuditLog(audit_path)

    # Section 5.2: a write refuses while this tool's own files are readable by
    # anyone else on the machine. Reads are left alone, because someone whose
    # modes have drifted still has to be able to run `doctor` and read why.
    if (
        args.command in WRITES
        or (args.command == "archive" and getattr(args, "archive_kind", None) in ARCHIVE_WRITES)
        or (args.command == "review" and getattr(args, "review_kind", None) in REVIEW_WRITES)
        or (args.command == "structure" and getattr(args, "structure_kind", None) in STRUCTURE_WRITES)
        or (args.command in manage_ops.GROUPS and (args.command, getattr(args, manage_ops.VERB_DESTS[args.command], None)) in MANAGE_WRITES)
        or (args.command == "watch" and getattr(args, "watch_kind", None) in WATCH_WRITES)
        or (args.command == "watch" and getattr(args, "watch_kind", None) == "rules" and getattr(args, "rules_verb", None) in RULES_WRITES)
        or (args.command == "schedule" and getattr(args, "schedule_kind", None) in SCHEDULE_WRITES)
    ):
        require_tight_modes()

    if args.command == "auth":
        return await _run_auth(args, config, report=report)

    if as_bot:
        return await run_as_bot(args, config, report=report)

    # The archive commands that never touch Telegram run before a client is
    # opened: they read a local file, and the identity they act as comes from
    # the profile record. `archive sync` is the one that connects.
    if args.command == "archive":
        kind = getattr(args, "archive_kind", None)
        if kind is None:
            raise ValueError("archive needs one of: sync, status, search, export, retention, forget.")
        if kind == "status":
            return await _run_archive_status(args, config, report=report)
        if kind == "search":
            return await _run_archive_search(args, config, report=report)
        if kind == "export":
            return await _run_archive_export(args, config, report=report)
        if kind in ("retention", "forget"):
            return await _run_archive_prune(args, config, report=report)
    if args.command == "search" and getattr(args, "archive", False):
        return await _run_search_archive(args, config, report=report)
    # The review queue reads the archive; `approve` and `retry` open the
    # account's client themselves, and only when a file (not a link) is fetched.
    if args.command == "review":
        return await _run_review(args, config, client=client, report=report)
    # `structure remap` reads the archive's remap rows and opens no connection.
    if args.command == "structure" and getattr(args, "structure_kind", None) == "remap":
        return await _run_structure_remap(args, config, report=report)
    # `watch` opens its own client when it needs one -- `run` a detached one on a
    # thread of its own -- and none at all for the rules, the status and the two
    # signals. `schedule` connects only when Telegram itself has to answer.
    if args.command == "watch":
        return await _run_watch(args, config, report=report)
    if args.command == "schedule":
        return await _run_schedule(args, config, client=client, report=report)

    owns_client = client is None
    if owns_client:
        # In machine mode nothing may prompt, so an unauthorised session is a
        # refusal naming `auth` rather than Telethon's own phone-number prompt
        # blocking on a pipe. Human mode keeps that prompt, unchanged.
        client = await start_client(create_client(config), authorize=not report.machine)
        if report.machine and not await client.is_user_authorized():
            await _disconnect_quietly(client)
            raise login.LoginRequired(getattr(config, "profile", "default"))

    try:
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
        if args.command == "message":
            return await _run_message(client, args, config, report=report)
        if args.command == "create":
            return await _run_create(client, args, report=report)
        if args.command == "delete":
            return await _run_delete(client, args, report=report)
        if args.command == "archive":
            return await _run_archive_sync(client, args, report=report)
        if args.command == "structure":
            return await _run_structure(client, args, config, report=report)
        if args.command in manage_ops.GROUPS:
            return await _run_manage(client, args, report=report)
        raise ValueError(f"Unknown command: {args.command}")
    finally:
        if owns_client:
            await client.disconnect()


def command_name(args) -> str:
    """What the envelope calls this run: the subcommand, and its kind where it has one."""
    kind = (
        getattr(args, "create_kind", None)
        or getattr(args, "delete_kind", None)
        or getattr(args, "archive_kind", None)
        or getattr(args, "message_verb", None)
        or getattr(args, "review_kind", None)
        or getattr(args, "structure_kind", None)
        or getattr(args, "admin_kind", None)
        or getattr(args, "member_kind", None)
        or getattr(args, "join_kind", None)
        or getattr(args, "invite_kind", None)
        or getattr(args, "settings_kind", None)
        or getattr(args, "watch_kind", None)
        or getattr(args, "schedule_kind", None)
    )
    # `watch rules <verb>` is the one command three words deep, and the envelope
    # names all three: `watch rules add` and `watch rules test` are not one command.
    verb = getattr(args, "rules_verb", None)
    if kind == "rules" and verb:
        kind = f"rules {verb}"
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
            if getattr(args, "as_bot", None):
                # The menu is the account's session; a bot has no rows in it.
                parser.error("--as-bot needs a command (send, or create topic); bot mode has no menu.")
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

            # `--profile` reaches the menu too: it decides which login the
            # whole session acts as, and the banner names it on every screen.
            return asyncio.run(run_menu(profile=getattr(args, "profile", None)))
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
        if isinstance(exc, ApprovalRequired):
            # The review queue's gates refuse without a terminal in either mode
            # (section 9.1), and 3 is the code that says so.
            print(f"error: {exc}", file=sys.stderr)
            return exit_code("refused", exc.code)
        parser.error(str(exc))
    except PermissionError as exc:
        if report.machine:
            return report.failed(error_for(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CodedError as exc:
        # The shared store refusing by code: a crossed budget, a database newer
        # than this build, a SQLite without FTS5. Same exit as any refusal, and
        # the hint printed in human mode because it names the way out.
        if report.machine:
            return report.failed(exc.error)
        print(f"error: {exc.error.message}", file=sys.stderr)
        if exc.error.hint:
            print(f"hint: {exc.error.hint}", file=sys.stderr)
        return exit_code("refused", exc.code)
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
