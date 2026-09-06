# telegram-tools

[![Site: cli-tools-site.vercel.app](https://img.shields.io/badge/site-cli--tools--site.vercel.app-00afff?style=flat-square&labelColor=09090b)](https://cli-tools-site.vercel.app/)

A local CLI for your own Telegram chats: find the real IDs of your groups, channels and forum topics, search and export messages, keep a local archive of them you can search offline, send a message into a chat or topic, and create and delete groups, channels and topics.

It also empties a forum topic without destroying the topic itself — the one job the app will not do.

Built on [Telethon](https://github.com/LonamiWebs/Telethon). Everything runs on your machine with your own Telegram API credentials — no server, no third party, nothing leaves your computer except the Telegram API calls you asked for.

## What it does

- **`discover`** — lists your chats, channels, and forum groups with their exact numeric IDs and every forum topic ID. The fastest way to answer "what is this chat's `-100…` ID and what are its topic IDs?"
- **`search`** — searches messages by text, sender, date range, or topic, and prints a table or exports JSON, CSV, JSON lines, Markdown or HTML. Messages carrying a photo or file are marked `[media]`. `search --archive` answers the same flags from the local archive, offline.
- **`archive`** — a local, searchable copy of everything your account can read: `archive sync` fills it and resumes where it stopped, `archive search` is full-text search over it with no connection, `archive export` writes one search in five formats, `archive status` says what it holds, and `archive retention` / `archive forget` prune it behind the same typed-title gate `delete` has. See [The local archive](#the-local-archive).
- **`clear-messages`** — deletes all messages inside selected forum topic(s) while preserving the topics and their IDs. Dry-run by default; deleting requires both `--execute` *and* typing `DELETE` at a prompt.
- **`send`** — posts a message, a file, or both to a chat or into one forum topic. Shows you the whole message and its destination, then asks `y/N`.
- **`create`** — makes a supergroup (optionally with topics already on), a broadcast channel, or a topic inside a forum group, and prints the new ID.
- **`delete`** — removes a supergroup, a broadcast channel, or a forum topic: the thing itself, not just its messages. Dry-run by default; deleting requires `--execute` *and* typing the target's exact title at a prompt. It deletes exactly what `create` can make, so nothing this tool removes is beyond making again.
- **`bots`** — lists the bots you own with their numeric IDs, and edits what @BotFather edits: display name, bio, description, commands, profile photo, and default admin rights.
- **`doctor`** — checks your local setup without printing any secrets.
- **`--as-bot NICK`** — runs `send` or `create topic` as one of your own bots instead of as you, naming both on every screen. See [Acting as a bot](#acting-as-a-bot).
- **`--json`** — any command, machine-readable: one object on stdout carrying the result, the target, the gate and the error code. For agents and scripts; see [For scripts and agents](#for-scripts-and-agents).

## What it doesn't do (on purpose)

- No deleting forum topics, and no renaming them — `clear-messages` leaves topic IDs untouched.
- No media downloads. `send` can attach files; nothing downloads them back — the archive keeps text and a `has_media` mark, never the files.
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

Two optional extras, each for one capability that needs a library and refuses rather
than degrade without it — a proxy (`telegram-tools[proxy]`) and the QR block that
`auth --qr` draws (`telegram-tools[qr]`):

```bash
pipx install 'telegram-tools[proxy,qr]'
```

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

Treat the api_hash like a password.

### Logging in

```bash
telegram-tools auth          # phone number, then the code Telegram sends
telegram-tools auth --qr     # or scan a code from a phone that is already signed in
```

`auth` asks at the terminal and cannot be run unattended — it has no `--yes`, and there
is nothing here for a script to drive. If the account has two-step verification, the
password is asked for at the prompt and stored nowhere. `auth --qr` needs
`pip install 'telegram-tools[qr]'` to draw the block; open Telegram on the signed-in
phone, *Settings → Devices → Link Desktop Device*, and scan it.

You can also just run a command: without a session, Telethon's own login prompt still
appears exactly as it always has.

```bash
telegram-tools profiles              # what this machine is logged in as
telegram-tools auth --logout         # end a session, after typing the profile's name
```

Logging out also removes the local session; the session shows under Telegram's
*Settings → Devices* either way.

### More than one account

```bash
telegram-tools auth --profile work           # log a second account in
telegram-tools --profile work discover       # act as it (the flag goes before the command)
export TELEGRAM_TOOLS_PROFILE=work           # or make it this shell's default
```

Each profile keeps its own session in `~/.telegram-tools/profiles/<name>/`, written
`0600` inside a `0700` directory, beside a `profile.json` that holds a label, the account
id, when it was created and last used, and the name of any proxy — never a phone number,
never a token, never a password. A profile may keep its own `.env` beside it when it
needs a second application id or its own proxy.

If you were using telegram-tools before profiles existed, nothing moved: your
`~/.telegram-tools/telegram-tools.session` *is* the `default` profile, read where it has
always been, and `TELEGRAM_TOOLS_SESSION` still wins over everything. `doctor` mentions
that it could move into the profile directory, and `auth --migrate` does it after a y/N —
the only thing in this tool that moves a session file.

### Who am I acting as?

Every command and every menu screen below the root opens with the account it is about to
use, and the thing it is about to use it on:

```
Acting as: Sven (@sven) · account · Target: Agency › 💻 Deploys (-1001234567890)
```

The same identity and target are fields in the `--json` envelope. An account with no
username is named by its first name and the last two digits of its number — `-- (…23)` —
because a display name is whatever its owner typed and need not tell two accounts apart.
Two digits, never more: the number itself is never part of a label, and a session path is
never printed anywhere.

### Acting as a bot

```bash
telegram-tools --as-bot alerts send --chat -1001234567890 --topic 141 --text "deploy is green"
```

`--as-bot NICK` goes before the subcommand, like `--profile`, and switches the run from
your account to a bot whose token is stored under that nickname in `TELEGRAM_BOT_TOKENS`
(the same nicknames `bots --bot` uses). The token opens an in-memory session and is never
written to disk. The banner then names both identities, because the question you are
answering is "which of my things is about to post this":

```
Acting as: @alertsbot · bot (via Sven (@sven)) · Target: Agency › 💻 Deploys (-1001234567890:141)
```

Bot mode narrows what a run can do; it never widens it. A Telegram bot has no dialog
list, no history and no search, so `discover`, `search`, `bots`, `clear-messages`,
`delete`, `create group`, `create channel` and `auth` refuse under `--as-bot` with
`IDENTITY_MODE_UNSUPPORTED`, before anything connects, and the hint is the same command
without the flag. What runs is what a bot is for: `send`, and `create topic` in a forum
group it administers. Targets are resolved by numeric id or `@username` only — a bot
cannot look up a link — and the bot has to be a member of the chat, which is checked
before the preview. `--yes` needs the destination in `TELEGRAM_SEND_ALLOWLIST`, exactly
as for an account send. There is no bot-mode menu: a bare `telegram-tools --as-bot NICK`
refuses.

The account named after `via` comes from the profile record `auth` wrote, so a bot-mode
run opens no account session at all.

### Optional: a proxy

```bash
# in ~/.telegram-tools/.env, or a profile's own .env
TELEGRAM_PROXY=socks5://127.0.0.1:1080
```

`socks5://`, `socks4://` and `http://` are understood, with optional `user:password@`.
This needs `pip install 'telegram-tools[proxy]'`. Without that library the command
**refuses** — Telethon's own behaviour there is a warning and a direct connection from
your own address, which is exactly the outcome someone asking for a proxy must not get.
`doctor` tells you before you run anything.

### Optional: bot tokens

Most of `bots` runs on your normal login. Three edits — commands, profile photo removal, and default admin rights — must be sent by the bot itself, so they need that bot's token:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_BOT_TOKENS=mybot:12345:AAExampleToken,alerts:67890:BBExampleToken
```

Nicknames are yours to choose and can be used as `--bot mybot`, or as `--as-bot mybot` to [act as that bot](#acting-as-a-bot). The tool only ever reads this variable — it never writes a token anywhere, and never prints one.

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
# Log in, and see which accounts this machine knows
telegram-tools auth
telegram-tools profiles

# What are my chats and their IDs?
telegram-tools discover            # admin/managed chats only
telegram-tools discover --all      # everything

# Act as a second account
telegram-tools --profile work discover

# Search a group
telegram-tools search --chat @mygroup --contains deploy

# Export a topic to JSON (or --format csv|jsonl|markdown|html)
telegram-tools search --chat @mygroup --topic 141 --output topic-141.json

# Keep a local copy of everything, then search it without touching Telegram
telegram-tools archive sync
telegram-tools archive search --query "deploy AND green" --context 2
telegram-tools archive export --query deploy --format html --output deploys.html
telegram-tools search --archive --chat @mygroup --keyword deploy   # the live flags, answered offline

# Clear a topic (dry-run first — this is the default)
telegram-tools clear-messages --chat @mygroup --topic 141
# Actually delete: needs --execute AND typing DELETE at the prompt
telegram-tools clear-messages --chat @mygroup --topic 141 --execute

# Send a message into a topic (shows it, then asks y/N)
telegram-tools send --chat -1001234567890 --topic 141 --text "deploy is green"

# Multi-line body, straight from a file or a pipe
cat notes.txt | telegram-tools send --chat -1001234567890 --text -

# The same, posted by one of your bots instead of by you
telegram-tools --as-bot alerts send --chat -1001234567890 --topic 141 --text "deploy is green"

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

## The local archive

`archive sync` copies what your account can read into `~/.telegram-tools/archive.sqlite`:
every chat, and every topic of every forum group (a forum's messages all live in topics,
so the topic is the unit, never the group). It walks each scope from the newest message
down and commits in batches, so a sync you kill halfway restarts from the last batch
that landed and never writes a row twice; a second run fetches only what arrived since.
It prints one line per scope and ends with a coverage table naming every scope it could
not read and why. Flood waits are slept and counted, and a wait longer than ten minutes
marks that scope failed and `rate_limited` in coverage rather than holding the run.
`--since` bounds a walk and leaves it open, so a later plain sync continues below the
floor; `--full` walks everything again, and is how a deletion is found — Telegram's
history never says what was removed, so a full walk marks every archived row it no
longer sees as deleted, keeping the row and its text out of searches unless asked for.

```bash
telegram-tools archive sync                                   # everything, resuming
telegram-tools archive sync --scope tg:topic:-1001234567890:141 --since 2026-06-01
telegram-tools archive status
telegram-tools archive search --query "deploy AND green" --regex '3\.\d+' --context 2
telegram-tools archive export --query deploy --format markdown --output deploys.md
telegram-tools archive retention --scope tg:chat:-1001234567890 --keep 90d   # dry-run
telegram-tools archive forget --scope tg:chat:-1001234567890 --execute        # asks for the title
```

`archive search` is FTS5 full-text search — words, `"a phrase"`, `AND`, `OR`, `NOT`,
`prefix*` — ranked by relevance and marked `«like this»` on screen, and it never
connects. `archive export` writes the same rows the search printed in `json`, `csv`,
`jsonl`, `markdown` or `html` (one self-contained page, no scripts); a bare `--output`
name lands in `~/.telegram-tools/exports/`, an absolute path is honoured. `search
--archive` takes the live command's flags and answers them from the archive.

The archive is per profile's account, not per profile: every row records which
identity synced it, and `--identity tg:user:ID` narrows a search or a status to one.
It lives under a 2 GiB budget (`archive_max_bytes` in `~/.telegram-tools/config.json`,
written with the defaults on first use); a sync that would cross it stops before
writing with `DISK_BUDGET` and names the retention command that frees space. The
archive needs a Python whose SQLite has FTS5 — `doctor` says whether yours does.

## The menu

Run `telegram-tools` with no arguments and you get a menu instead of flags:

```text
telegram-tools
Acting as: Sven (@sven) · account
--------------------------------------------
1. Find IDs (chats, topics)
2. Read (search live, archive, export)
3. Write (send)
4. Build (create, delete)
5. Clear messages
6. Manage (admins, members, invites, settings)
7. Watch (rules, runner, review queue)
8. Identity (profiles, my bots)
9. Check setup
0. Exit
```

Rows 6 and 7 name what a later version brings and say so when you pick them; they hold
their numbers now so nothing above or below them has to move again. The `Acting as:`
line appears once something has connected — a bare `telegram-tools` opens without
needing credentials, and `Check setup` never needs any.

`0` always steps back one screen — inside a picker or on a flow's own screen alike —
and exits once you're back at the root; on a text prompt a blank line does the same.
Every screen below the root carries its trail (`Main › Read › Search › Hermes › From`), so
you always know where you are. Chats, topics, bots, and admin rights come from live
pick-lists rather than prompts asking you to type an ID; long lists page on `n` and
`p`, and an item keeps its number on every page. Bot fields show their current value
and offer keep / change / clear.

After a job the menu offers its own next step — *Tweak it* back to the filled-in search
or send form, *Create another*, *Clear more topics*, *Edit more* — plus *Main menu*, and
*Run it again* where a re-run makes sense (chats & topics, search, send). Enter is still
the main menu (the root, not the group screen you came through), `0` still exits, and `doctor` keeps the plain Enter/`0` prompt. Backing out of
a form with something typed in it — a message, search filters, bot edits — asks first.
Every flag has a row:
the clear screen offers *All topics* and a batch size, the bots screen can save the
whole bot list to JSON and look up a bot you do not own, read-only, and *Read* opens
a screen of six — the live search, then sync, status, search or export, prune and
forget for the archive. Sync picks its scope from your live chats and topics, the same
picker Search uses; search, prune and forget pick from what the archive already holds.

The menu is in colour when it is talking to a terminal, and plain text in a pipe, under
`NO_COLOR`, or with `TERM=dumb`.

The message box takes several lines — end it with a `.` on its own line — so pasting
a multi-line message works instead of feeding its later lines to the menu as answers.

The safety gates are the same as the flags', not looser: clearing topic messages
dry-runs first and still asks you to type `DELETE`, deleting a group, channel or topic
dry-runs first and still asks you to type its exact title, pruning or forgetting part
of the archive dry-runs first and asks for the scope's title too, sending shows the whole
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
  `SESSION_IN_USE`, `CONFIG_MISSING`, `CONFIG_INVALID`, `LOGIN_REQUIRED`,
  `RATE_LIMITED` and others. `error.hint` is the exact command or edit that
  would fix it — worth relaying verbatim.
- **`identity` names the account every run acted as**, with the profile it came
  from, and `target` what it acted on. Under `--as-bot` its `mode` is `bot`, its
  `id` is `tg:bot:…` and `via` is the account's `tg:user:…`. Neither ever carries
  a phone number, a token or a session path.
- **`meta.waited_ms` is the flood-wait time an `archive sync` slept**; every other
  command reports 0 there, and `meta.api_calls` is not measured yet by any.
- **The archive commands are offline** except `archive sync`: `status`, `search`,
  `export`, `retention` and `forget` read `~/.telegram-tools/archive.sqlite` and
  open no connection, so they never wait on Telegram and never see a flood-wait.
  `archive search` is the cheap way to answer a history question; `search --archive`
  is the same answer behind the live command's flags. A `--json archive sync` carries
  the coverage table in `result.scopes` and `result.skipped`, one entry per scope.

Exit codes, unchanged apart from one addition:

| Code | Meaning |
| --- | --- |
| 0 | done — `ok`, `empty`, `dry_run` |
| 1 | not done — cancelled at a gate, a declined confirm, `partial` (`doctor` with a failed check) |
| 2 | refused — usage, config, permission, a platform error |
| 3 | **new:** the command asks for confirmation and there is no terminal to ask on. Only under `--json`; `error.hint` is the same command for a human to run |
| 130 | interrupted |

`discover --json out.json` and `bots --json out.json` still write those files
exactly as before; a bare `--json` on either means the envelope. A run whose
output goes to a file prints no `Acting as:` banner, so its stdout stays what it
has always been: empty.

`--as-bot NICK` goes before the subcommand too and runs `send` or `create topic`
as that bot; any other command under it refuses with `IDENTITY_MODE_UNSUPPORTED`
and the same command minus the flag as the hint. An agent should pass it only
when the user named the bot.

`--profile NAME` goes before the subcommand and picks the login;
`TELEGRAM_TOOLS_PROFILE` is the default. Under `--json` a profile with no session
refuses with `LOGIN_REQUIRED` and the `auth` command that fixes it, rather than
blocking on a prompt. **`auth` is not for an agent to drive** — it asks a human
for a code or a password. Relay the refusal and let the person run it.

## Safety model

| Command | Destructive? |
| --- | --- |
| `discover`, `search`, `doctor`, `profiles`, `archive status`, `archive search`, `archive export` | No — read-only |
| `archive sync` | Read-only against Telegram — writes only the local archive, resuming without duplicating; refuses under `--as-bot` |
| `archive retention`, `archive forget` | Local only — prune or remove rows of the local archive, never anything on Telegram. Dry-run by default; executing needs `--execute` **and** the scope's exact title typed back; there is no `--yes` |
| `auth` | Local only — writes or removes this machine's login. `--logout` needs the profile's name typed back, `--migrate` a `y/N`; there is no `--yes`, and it cannot run unattended. Nothing it asks for is stored: a two-step-verification password goes straight into the sign-in call |
| `create` | No — makes new things, changes nothing existing, after a `y/N` unless you pass `--yes` |
| `send` | Outward-facing — posts publicly as you (text, files, or both), after showing the whole message and asking `y/N`. `--yes` skips the prompt only for destinations in `TELEGRAM_SEND_ALLOWLIST`. Under `--as-bot` it posts as that bot, only into chats the bot is in, behind the same preview and the same allowlist |
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

Sessions are written `0600` inside `0700` directories, and every command that
writes something refuses while anything under `~/.telegram-tools` is readable by
group or others — `doctor` names the files and the `chmod` that fixes them.
Reads still work, so `doctor` can always tell you why.

## Status

Stable for its six jobs; used regularly by its author. This is a solo project whose code was written by AI agents under review — issues are welcome, fixes are best-effort, and there is no support promise.

## License

MIT. See [LICENSE](LICENSE).
