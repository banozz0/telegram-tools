# telegram-tools

[![Site: cli-tools-site.vercel.app](https://img.shields.io/badge/site-cli--tools--site.vercel.app-00afff?style=flat-square&labelColor=09090b)](https://cli-tools-site.vercel.app/)

A local CLI for your own Telegram chats: find the real IDs of your groups, channels and forum topics, search and export messages, send a message into a chat or topic, and create and delete groups, channels and topics.

It also empties a forum topic without destroying the topic itself — the one job the app will not do.

Built on [Telethon](https://github.com/LonamiWebs/Telethon). Everything runs on your machine with your own Telegram API credentials — no server, no third party, nothing leaves your computer except the Telegram API calls you asked for.

## What it does

- **`discover`** — lists your chats, channels, and forum groups with their exact numeric IDs and every forum topic ID. The fastest way to answer "what is this chat's `-100…` ID and what are its topic IDs?"
- **`search`** — searches messages by text, sender, date range, or topic, and prints a table or exports JSON/CSV. Messages carrying a photo or file are marked `[media]`.
- **`clear-messages`** — deletes all messages inside selected forum topic(s) while preserving the topics and their IDs. Dry-run by default; deleting requires both `--execute` *and* typing `DELETE` at a prompt.
- **`send`** — posts a message, a file, or both to a chat or into one forum topic. Shows you the whole message and its destination, then asks `y/N`.
- **`create`** — makes a supergroup (optionally with topics already on), a broadcast channel, or a topic inside a forum group, and prints the new ID.
- **`delete`** — removes a supergroup, a broadcast channel, or a forum topic: the thing itself, not just its messages. Dry-run by default; deleting requires `--execute` *and* typing the target's exact title at a prompt. It deletes exactly what `create` can make, so nothing this tool removes is beyond making again.
- **`bots`** — lists the bots you own with their numeric IDs, and edits what @BotFather edits: display name, bio, description, commands, profile photo, and default admin rights.
- **`doctor`** — checks your local setup without printing any secrets.
- **`--json`** — any command, machine-readable: one object on stdout carrying the result, the target, the gate and the error code. For agents and scripts; see [For scripts and agents](#for-scripts-and-agents).

## What it doesn't do (on purpose)

- No deleting forum topics, and no renaming them — `clear-messages` leaves topic IDs untouched.
- No media downloads. `send` can attach files; nothing downloads them back.
- No automation loops. The `bots` command edits bot *settings*; it never runs a bot.
- No changing a bot's `@username`, creating or deleting bots, or reading/revoking bot tokens — those stay with @BotFather.
- No cloud anything — credentials and session files stay in `~/.telegram-tools/`.

## Install

```bash
pipx install telegram-tools
# or
uv tool install telegram-tools
```

Or from source: `pipx install git+https://github.com/banozz0/telegram-tools.git`

Requires Python 3.11+.

## Setup: your Telegram API credentials

The tool logs in as *you* (a user account, not a bot), so it needs a Telegram API key. One-time, about two minutes:

1. Open <https://my.telegram.org/apps> and log in with your Telegram phone number.
2. Fill in the short "Create new application" form (any name/short name works; platform "Desktop").
3. Copy the **App api_id** (a number) and **App api_hash** (a hex string).
4. Store them where the tool can find them:

```bash
mkdir -p ~/.telegram-tools
cat > ~/.telegram-tools/.env <<'EOF'
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your-api-hash-here
EOF
```

Shell environment variables and a `.env` in the current directory also work, and win over `~/.telegram-tools/.env`.

Treat the api_hash like a password. The first command you run starts Telethon's interactive login (phone number + code from Telegram); the resulting session file is stored in `~/.telegram-tools/` and reused afterwards. Log out anytime by deleting the session file in that directory (your `.env` can stay) — the session also shows under Telegram's *Settings → Devices*.

### Optional: bot tokens

Most of `bots` runs on your normal login. Three edits — commands, profile photo removal, and default admin rights — must be sent by the bot itself, so they need that bot's token:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_BOT_TOKENS=mybot:12345:AAExampleToken,alerts:67890:BBExampleToken
```

Nicknames are yours to choose and can be used as `--bot mybot`. The tool only ever reads this variable — it never writes a token anywhere, and never prints one.

### Optional: the send allowlist

`send` asks you to confirm every message. `--yes` skips that prompt, and because a
skipped prompt means nobody saw where the message was going, it only works for
destinations you have named in advance:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_SEND_ALLOWLIST=-1001234567890:141,-1009876543210,@myalerts
```

Each entry is a chat ID or `@username`, optionally `:topic-id` to allow just one topic
in it. Unset means every `--yes` send is refused — `send` without `--yes` still works
and still asks. `doctor` reports how many destinations are listed, never which.

## 30 seconds of usage

```bash
# What are my chats and their IDs?
telegram-tools discover            # admin/managed chats only
telegram-tools discover --all      # everything

# Search a group
telegram-tools search --chat @mygroup --contains deploy

# Export a topic to JSON
telegram-tools search --chat @mygroup --topic 141 --output topic-141.json

# Clear a topic (dry-run first — this is the default)
telegram-tools clear-messages --chat @mygroup --topic 141
# Actually delete: needs --execute AND typing DELETE at the prompt
telegram-tools clear-messages --chat @mygroup --topic 141 --execute

# Send a message into a topic (shows it, then asks y/N)
telegram-tools send --chat -1001234567890 --topic 141 --text "deploy is green"

# Multi-line body, straight from a file or a pipe
cat notes.txt | telegram-tools send --chat -1001234567890 --text -

# Attach files (repeatable; several go as one album, --text is the caption)
telegram-tools send --chat -1001234567890 --file shot.png --file notes.pdf --text "the numbers"

# Make a group with topics already switched on, then a topic in it
telegram-tools create group --title "Agency" --forum
telegram-tools create topic --chat -1001234567890 --title "Deploys"

# Take one back. Both dry-run first; --execute then asks you to type the title.
telegram-tools delete topic --chat -1001234567890 --topic 141
telegram-tools delete group --chat -1001234567890 --execute

# Which bots do I own, and what are their IDs?
telegram-tools bots

# Show one bot's full profile
telegram-tools bots --bot @mybot

# Rename it and update the text people see before pressing Start
telegram-tools bots --bot @mybot --name "My Bot" --description "Does the thing"

# Set a new profile photo (no token needed) — removing one does need the bot's token
telegram-tools bots --bot @mybot --photo avatar.png
```

`discover` output looks like:

```text
Forum Groups
============
Example Forum
Chat ID: -1001234567890
Type: Forum Group
Admin: yes

Topics
--------------------------------------------
141   💻 Deploys
217   🔎 Support
16    General
```

## The menu

Run `telegram-tools` with no arguments and you get a menu instead of flags:

```text
telegram-tools
--------------------------------------------
1. Chats & topics (find IDs)
2. Search / export messages
3. Send a message
4. Create a group, channel, or topic
5. Clear topic messages
6. My bots
7. Check setup
0. Exit
```

`0` always steps back one screen — inside a picker or on a flow's own screen alike —
and exits once you're back at the root; on a text prompt a blank line does the same.
Every screen below the root carries its trail (`Main › Search › Hermes › From`), so
you always know where you are. Chats, topics, bots, and admin rights come from live
pick-lists rather than prompts asking you to type an ID; long lists page on `n` and
`p`, and an item keeps its number on every page. Bot fields show their current value
and offer keep / change / clear.

After a job the menu offers its own next step — *Tweak it* back to the filled-in search
or send form, *Create another*, *Clear more topics*, *Edit more* — plus *Main menu*, and
*Run it again* where a re-run makes sense (chats & topics, search, send). Enter is still
the menu, `0` still exits, and `doctor` keeps the plain Enter/`0` prompt. Backing out of
a form with something typed in it — a message, search filters, bot edits — asks first.
Every flag has a row:
the clear screen offers *All topics* and a batch size, the bots screen can save the
whole bot list to JSON and look up a bot you do not own, read-only.

The menu is in colour when it is talking to a terminal, and plain text in a pipe, under
`NO_COLOR`, or with `TERM=dumb`.

The message box takes several lines — end it with a `.` on its own line — so pasting
a multi-line message works instead of feeding its later lines to the menu as answers.

The safety gates are the same as the flags', not looser: clearing topic messages
dry-runs first and still asks you to type `DELETE`, deleting a group, channel or topic
dry-runs first and still asks you to type its exact title, sending shows the whole
message and asks `y/N`, and bot edits still print a diff and ask before writing. The
menu has no equivalent of `--yes` at all. With no terminal attached it prints this help instead.

## For scripts and agents

Put `--json` before the subcommand and the command prints exactly one object on
stdout, and nothing else:

```bash
telegram-tools --json discover
telegram-tools --json send --chat -1001234567890 --topic 141 --text "deploy is green" --yes
```

```json
{
  "schema": "cli-tools/envelope/1",
  "tool": "telegram-tools", "version": "3.8.0",
  "command": "send", "args": {"chat": "-1001234567890", "topic": 141, "yes": true},
  "identity": {"platform": "telegram", "mode": "account", "label": "Sven (@sven)", "id": "tg:user:12345678", "profile": "default", "via": null},
  "target": {"rid": "tg:topic:-1001234567890:141", "kind": "topic", "title": "Deploys", "path": ["Agency", "Deploys"]},
  "status": "ok",
  "result": {"chat_id": -1001234567890, "topic_id": 141, "message_id": 9001, "files": 0, "sent": true, "cancelled": false},
  "plan": {"plan_id": "b7f1e2d3c4b5a697", "approval": "yes_allowlist", "preflight": {"required": ["send_messages"], "held": ["send_messages"], "missing": []}},
  "evidence": {"readback": "message 9001 is in Agency › Deploys", "fetched_at": "2026-09-04T09:31:04Z"},
  "warnings": [], "error": null,
  "meta": {"started": "2026-09-04T09:30:58Z", "duration_ms": 621, "api_calls": 0, "waited_ms": 0}
}
```

- **`result` is the command's own payload**, with every key it printed before — so
  a reader of the old `--json PATH` files reads the same keys one level in.
- **`status`** is one of `ok`, `empty`, `partial`, `dry_run`, `cancelled`,
  `refused`, `failed`.
- **`--jsonl`** streams one JSON line per record first (a chat, a message) and
  closes with the same envelope marked `"kind": "envelope"`.
- **Everything a person would read moves to stderr** under either flag — tables,
  previews, progress, prompts — so stdout stays parseable.
- **`error.code` is stable.** `NOT_ALLOWLISTED`, `TARGET_NOT_FOUND`,
  `TARGET_KIND_MISMATCH`, `PERMISSION_DENIED`, `PLAN_DRIFT`, `APPROVAL_REQUIRED`,
  `SESSION_IN_USE`, `CONFIG_MISSING`, `RATE_LIMITED` and others. `error.hint` is
  the exact command or edit that would fix it — worth relaying verbatim.
- **`meta.api_calls` and `meta.waited_ms` are not measured yet** and report 0.
  They are in the schema because both tools share it; a later release fills them.

Exit codes, unchanged apart from one addition:

| Code | Meaning |
| --- | --- |
| 0 | done — `ok`, `empty`, `dry_run` |
| 1 | not done — cancelled at a gate, a declined confirm, `partial` (`doctor` with a failed check) |
| 2 | refused — usage, config, permission, a platform error |
| 3 | **new:** the command asks for confirmation and there is no terminal to ask on. Only under `--json`; `error.hint` is the same command for a human to run |
| 130 | interrupted |

`discover --json out.json` and `bots --json out.json` still write those files
exactly as before; a bare `--json` on either means the envelope.

## Safety model

| Command | Destructive? |
| --- | --- |
| `discover`, `search`, `doctor` | No — read-only |
| `create` | No — makes new things, changes nothing existing, after a `y/N` unless you pass `--yes` |
| `send` | Outward-facing — posts publicly as you (text, files, or both), after showing the whole message and asking `y/N`. `--yes` skips the prompt only for destinations in `TELEGRAM_SEND_ALLOWLIST` |
| `bots` | No — changes settings on bots you own, after a diff and a `y/N` unless you pass `--yes`; reversible if you still have the old values, but `--remove-photo` and `--clear-commands` discard data Telegram will not hand back |
| `clear-messages` | Yes — but only with `--execute` **and** a typed `DELETE`, only messages, never topics |
| `delete` | Yes, and further than `clear-messages` goes — the group, channel or topic itself, for everyone in it. Only with `--execute` **and** the target's exact title typed back; there is no `--yes`, so it never runs unattended. It removes only what `create` can make: a basic group is refused, because this tool cannot make one back |

`clear-messages` also verifies you actually hold the delete-messages permission in the chat before doing anything, skips topic starter messages, and handles Telegram flood-wait limits automatically.

`bots` refuses to edit a bot you do not own, and it never fetches or exports a bot token from Telegram — the three token-only edits simply fail with a message naming the fields they need one for.

Every write — sending, creating, clearing, deleting, editing a bot — now also
asks Telegram what rights your account actually holds in that chat before it
does anything, and refuses by name when one it needs is missing. Once you have
answered the gate, the target is resolved a second time and compared with the
one you were shown: a chat renamed or replaced in that window refuses rather
than acting on whatever now holds the name. Afterwards the result is read back
and reported, and one redacted line per executed write is appended to
`~/.telegram-tools/audit.jsonl` (from the menu exactly as from a flag). No
token, phone number, API hash or session path can reach that file — the same
redaction pass covers it, every envelope and every error message.

## Status

Stable for its six jobs; used regularly by its author. This is a solo project whose code was written by AI agents under review — issues are welcome, fixes are best-effort, and there is no support promise.

## License

MIT. See [LICENSE](LICENSE).
