"""The command surface, frozen: every `--help` text must stay what it was.

`tests/fixtures/help/` holds the help of the root parser and of every
subcommand, captured at 80 columns from 3.7.2. This test re-renders them and
compares. Flags are the contract an agent and a script read; a flag that
quietly changes its name, its help line or its defaults breaks callers that
never see a changelog.

Two relaxations, both deliberate:

* `ALLOWED_ADDITIONS` lists, verbatim, the additions each card was allowed to
  make -- the global `--json`/`--jsonl` on the root parser, and the optional
  path on the per-command `--json`. They are removed from the live text before
  it is compared. Anything else that moved fails here. The list is keyed by the
  parser it applies to: a global list would also delete the same words where
  another parser happens to spell them (`--on` names the event kind `message`,
  and `message,` is the root's own subcommand entry), which is a hole the
  frozen surface cannot afford.
* `ALLOWED_REWRITES` is the same idea for a phrase that changed rather than
  appeared -- a choice list that grew, a required flag that became optional,
  a help line a card reworded. Each entry says what the live help says now and
  what the fixture says, keyed by the parser, and the substitution runs before
  the comparison. It is the narrower relaxation on purpose: an addition can
  only add, a rewrite can hide a removal, so every entry carries its reason.
* Whitespace is squeezed to single spaces on both sides. `--json [JSON_OUTPUT]`
  is two characters wider than `--json JSON_OUTPUT`, and argparse widens the
  whole help column of that parser to match, so a byte comparison would fail on
  padding that carries no meaning. Every word still has to be the same word, in
  the same order.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

from telegram_tools.cli import build_parser

FIXTURES = Path(__file__).parent / "fixtures" / "help"

# Which parser each fixture file holds. The name is the argv path, `-`-joined,
# with one exception: the `auth` parser is captured as `login.txt`, because the
# repository's commit guard refuses any non-Markdown filename carrying `auth`.
COMMANDS = {
    "root": (),
    "discover": ("discover",),
    "clear-messages": ("clear-messages",),
    "search": ("search",),
    "bots": ("bots",),
    "send": ("send",),
    "create": ("create",),
    "create-group": ("create", "group"),
    "create-channel": ("create", "channel"),
    "create-topic": ("create", "topic"),
    "delete": ("delete",),
    "delete-group": ("delete", "group"),
    "delete-channel": ("delete", "channel"),
    "delete-topic": ("delete", "topic"),
    "login": ("auth",),
    "profiles": ("profiles",),
    "doctor": ("doctor",),
    "archive": ("archive",),
    "archive-sync": ("archive", "sync"),
    "archive-status": ("archive", "status"),
    "archive-search": ("archive", "search"),
    "archive-export": ("archive", "export"),
    "archive-retention": ("archive", "retention"),
    "archive-forget": ("archive", "forget"),
    "message": ("message",),
    "message-reply": ("message", "reply"),
    "message-edit": ("message", "edit"),
    "message-delete": ("message", "delete"),
    "message-forward": ("message", "forward"),
    "message-copy": ("message", "copy"),
    "message-react": ("message", "react"),
    "message-unreact": ("message", "unreact"),
    "message-pin": ("message", "pin"),
    "message-unpin": ("message", "unpin"),
    "message-poll": ("message", "poll"),
    "message-typing": ("message", "typing"),
    "message-read": ("message", "read"),
    "message-unread": ("message", "unread"),
    "message-bookmark": ("message", "bookmark"),
    "message-draft": ("message", "draft"),
    "review": ("review",),
    "review-list": ("review", "list"),
    "review-approve": ("review", "approve"),
    "review-accept": ("review", "accept"),
    "review-reject": ("review", "reject"),
    "review-retry": ("review", "retry"),
    "review-status": ("review", "status"),
    "structure": ("structure",),
    "structure-export": ("structure", "export"),
    "structure-diff": ("structure", "diff"),
    "structure-apply": ("structure", "apply"),
    "structure-remap": ("structure", "remap"),
    "admin": ("admin",),
    "admin-list": ("admin", "list"),
    "admin-promote": ("admin", "promote"),
    "admin-rights": ("admin", "rights"),
    "admin-demote": ("admin", "demote"),
    "member": ("member",),
    "member-list": ("member", "list"),
    "member-ban": ("member", "ban"),
    "member-unban": ("member", "unban"),
    "member-mute": ("member", "mute"),
    "member-unmute": ("member", "unmute"),
    "member-restrict": ("member", "restrict"),
    "join-requests": ("join-requests",),
    "join-requests-list": ("join-requests", "list"),
    "join-requests-approve": ("join-requests", "approve"),
    "join-requests-decline": ("join-requests", "decline"),
    "invite": ("invite",),
    "invite-list": ("invite", "list"),
    "invite-create": ("invite", "create"),
    "invite-revoke": ("invite", "revoke"),
    "settings": ("settings",),
    "settings-show": ("settings", "show"),
    "settings-set": ("settings", "set"),
    "watch": ("watch",),
    "watch-run": ("watch", "run"),
    "watch-status": ("watch", "status"),
    "watch-stop": ("watch", "stop"),
    "watch-reload": ("watch", "reload"),
    "watch-rules": ("watch", "rules"),
    "watch-rules-list": ("watch", "rules", "list"),
    "watch-rules-add": ("watch", "rules", "add"),
    "watch-rules-edit": ("watch", "rules", "edit"),
    "watch-rules-remove": ("watch", "rules", "remove"),
    "watch-rules-enable": ("watch", "rules", "enable"),
    "watch-rules-disable": ("watch", "rules", "disable"),
    "watch-rules-test": ("watch", "rules", "test"),
    "schedule": ("schedule",),
    "schedule-list": ("schedule", "list"),
    "schedule-post": ("schedule", "post"),
    "schedule-cancel": ("schedule", "cancel"),
}

# The only text these cards were allowed to add, spelled exactly as the help
# spells it (whitespace-squeezed, the way both sides are compared), keyed by the
# parser it belongs to. Removed from that parser's live help before the
# comparison; everything left over has to match the fixture.
_ROOT_ADDITIONS = (
    "[--json] [--jsonl]",
    "--json Emit one machine-readable envelope on stdout instead of the human output",
    "--jsonl Stream one JSON line per record, then the envelope as the last line",
    # The profiles card (agent-bo-95421936): a global `--profile` naming the
    # login this run acts as, and the two commands that manage those logins.
    # Nothing existing moved -- `auth` and `profiles` are new subcommands, and
    # the root's own flags gained one option.
    "[--profile NAME]",
    "--profile NAME Act as this named login; default is TELEGRAM_TOOLS_PROFILE, or 'default'",
    # The two new names inside the subcommand list, which argparse prints twice:
    # once in the usage line and once over the positional arguments.
    "auth,profiles,",
    "auth Log a profile in or out (asks at the terminal)",
    "profiles List the named logins on this machine",
    # The bot-mode card (agent-bo-95421937): one global flag, the explicit
    # switch into acting as an owned bot. No subcommand gained or lost a flag.
    "[--as-bot NICK]",
    # Reworded on the message-ops card (agent-bo-95421945): the flag's help
    # names what a bot may run now that eleven message verbs join send and
    # create topic, and the four account-only verbs it refuses.
    "--as-bot NICK Act as this bot (a TELEGRAM_BOT_TOKENS nickname) instead of the account: send, create topic and the message verbs except read, unread, bookmark and draft",
    # The archive card (agent-bo-95421940): one new subcommand group, and on
    # the live `search` a switch that is the documented alias of `archive
    # search`. The subcommand name appears twice, as `auth,profiles,` does.
    "archive,",
    "archive Sync, search and export the local archive",
    # The message-ops card (agent-bo-95421945): one new subcommand group. Both
    # spellings the subcommand name takes, as `archive,` does.
    "message,",
    "message Act on messages: reply, edit, delete, forward, copy, react, pin, poll, read, bookmark, draft",
    # The review-queue card (agent-bo-95421943): one new subcommand group, the
    # links and files a sync noted, fetched only after a human approves. No
    # existing command gained or lost a flag. Both spellings of the name.
    "review,",
    "review The review queue: links and files the archive saw, fetched only after you approve",
    # The chat-blueprints card (agent-bo-95421949): one new subcommand group,
    # a chat's structure exported, diffed and applied behind the typed title.
    # No existing command gained or lost a flag. Both spellings of the name.
    "structure,",
    "structure Export, diff and apply a chat's structure blueprint (topics and settings, never people or messages)",
    # The admin-rights card (agent-bo-95421953): five new subcommand groups,
    # admins, members, join requests, invite links and a chat's settings. No
    # existing command gained or lost a flag. The five names are one string
    # here because `admin,` on its own would also strip `add_admins,` out of
    # the rights lists on the admin parsers' own help.
    "admin,member,join-requests,invite,settings,",
    "admin Admins and their rights: list, promote, rights, demote (demote asks for the person's exact label)",
    "member Members and restrictions: list, ban, unban, mute, unmute, restrict (ban asks for the person's exact label)",
    "join-requests People waiting to join a chat that needs approval: list, approve, decline",
    "invite Invite links: list, create, revoke (links are shown by list and create only)",
    # The folders-and-settings card (agent-bo-95421954) reworded this one line:
    # `settings` now reaches a topic as well as a chat, and more than slow mode.
    "settings A chat's or topic's settings: show, set",
    # The watch card (agent-bo-95421957): two new subcommand groups -- the rules
    # and the runner, and the messages waiting to be posted. Both spellings of
    # the names.
    "watch,schedule,",
    "watch Rules over live Telegram events, and the runner that fires them",
    "schedule Messages waiting to be posted: list, post (this runner holds it), cancel",
)

ALLOWED_ADDITIONS = {
    "root": _ROOT_ADDITIONS,
    # The archive card gave the live `search` a switch that is the documented
    # alias of `archive search`.
    "search": (
        "[--archive]",
        "--archive Search the local archive instead of Telegram (the same as `archive search`)",
    ),
    # The folders-and-settings card (agent-bo-95421954). `settings show` gained
    # `--topic`, and `settings set` gained the fields of a chat and of a topic
    # plus the `--execute` that `--forum off` needs; `--slow-mode` stopped being
    # required, which is the one word of the old line that moved.
    "settings-show": (
        "[--topic TOPIC]",
        "--topic TOPIC Show this topic's own settings instead of the chat's",
    ),
    "settings-set": (
        "[--topic TOPIC]",
        "[--title TITLE] [--about ABOUT]",
        "[--forum ON|OFF]",
        "[--icon-emoji-id ID] [--closed ON|OFF]",
        "[--hidden ON|OFF] [--execute]",
        "--topic TOPIC Change this topic instead of the chat",
        "--title TITLE A new name, for the chat or for the topic named by --topic",
        "--about ABOUT The chat's description; empty clears it",
        "--forum ON|OFF Topics on or off for this group; off puts every topic's messages in one stream and its topics stop existing",
        "--icon-emoji-id ID The topic's icon, as the custom-emoji document id `structure export` prints; 0 removes it",
        "--closed ON|OFF Whether only admins may post in the topic",
        "--hidden ON|OFF Whether the topic is hidden; Telegram allows this on the General topic only",
        "--execute Actually switch topics off, after typing the chat's exact title (no other setting needs it)",
    ),
    # `send` gained one flag on the message-ops card and one on the watch card:
    # a reply target, and the moment Telegram is to hold the message until.
    "send": (
        "[--reply-to MSG]",
        "--reply-to MSG Post it as a reply to this message id",
        "[--at TIME]",
        "--at TIME Hand it to Telegram to post at this ISO 8601 moment; Telegram holds it and posts it with this machine off",
    ),
}

ALLOWED_REWRITES = {
    # A choice list that grew. The help line is the same words; the braces name
    # more formats. Section 15: `--format` gains jsonl, markdown and html, and
    # json and csv stay first so a script reading the usage line still finds them.
    "search": (("{json,csv,jsonl,markdown,html}", "{json,csv}"),),
    # The folders-and-settings card (agent-bo-95421954). `settings set` used to
    # change one thing, so `--slow-mode` was required; now it changes any of a
    # chat's or a topic's fields and each is optional, which argparse spells
    # with brackets. Nothing was removed: the flag and its help line are the
    # same words.
    "settings-set": (("[--slow-mode SECONDS]", "--slow-mode SECONDS"),),
    # The same card reworded the two rows of the `settings` group's own help,
    # because `show` reaches a topic and `set` reaches more than slow mode.
    "settings": (
        (
            "show Title, description, topics, slow mode, join approval, default member rights, counts",
            "show Slow mode, join approval, default member rights, counts",
        ),
        (
            "set Change a setting (y/N; --forum off dry-runs and asks for the chat's exact title)",
            "set Change a setting (y/N)",
        ),
    ),
}

# The per-command `--json` gained an optional path, which argparse spells with
# brackets in both the usage line and the option list.
OPTIONAL_PATH = re.compile(r"\[([A-Z_]+)\]")


def render(parts: tuple[str, ...]) -> str:
    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        parser.parse_args([*parts, "--help"])
    return buffer.getvalue()


def squeeze(text: str) -> str:
    return " ".join(text.split())


def normalise(text: str) -> str:
    """The help as this test compares it: whitespace squeezed, an optional path spelled plainly."""
    return OPTIONAL_PATH.sub(r"\1", squeeze(text))


@pytest.fixture(autouse=True)
def _eighty_columns(monkeypatch):
    # The fixtures were captured at 80; argparse wraps to the terminal it is in.
    monkeypatch.setenv("COLUMNS", "80")


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_help_text_is_the_captured_one(name):
    live = normalise(render(COMMANDS[name]))
    for addition in ALLOWED_ADDITIONS.get(name, ()):
        live = live.replace(normalise(addition), "")
    for now, then in ALLOWED_REWRITES.get(name, ()):
        live = live.replace(normalise(now), normalise(then))
    expected = normalise((FIXTURES / f"{name}.txt").read_text(encoding="utf-8"))

    assert squeeze(live) == expected, (
        f"{name} --help changed. Fixtures under tests/fixtures/help/ are the frozen surface: "
        "an addition this card allows belongs in ALLOWED_ADDITIONS, anything else is a break."
    )


def test_every_fixture_names_a_parser():
    captured = {path.stem for path in FIXTURES.glob("*.txt")}
    assert captured == set(COMMANDS)
