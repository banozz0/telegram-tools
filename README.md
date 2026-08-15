# telegram-tools

A local CLI for operating your own Telegram chats: find the real IDs of your groups, channels, and forum topics, search and export messages, and clear all messages out of forum topics without destroying the topics themselves.

Built on [Telethon](https://github.com/LonamiWebs/Telethon). Everything runs on your machine with your own Telegram API credentials — no server, no third party, nothing leaves your computer except the Telegram API calls you asked for.

## What it does

- **`discover`** — lists your chats, channels, and forum groups with their exact numeric IDs and every forum topic ID. The fastest way to answer "what is this chat's `-100…` ID and what are its topic IDs?"
- **`search`** — searches messages by text, sender, date range, or topic, and prints a table or exports JSON/CSV.
- **`clear-messages`** — deletes all messages inside selected forum topic(s) while preserving the topics and their IDs. Dry-run by default; deleting requires both `--execute` *and* typing `DELETE` at a prompt.
- **`bots`** — lists the bots you own with their numeric IDs, and edits what @BotFather edits: display name, bio, description, commands, profile photo, and default admin rights.
- **`doctor`** — checks your local setup without printing any secrets.

## What it doesn't do (on purpose)

- No deleting or creating forum topics — topic IDs never change.
- No media downloads.
- No sending messages and no automation loops. The `bots` command edits bot *settings*; it never runs a bot.
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

# Which bots do I own, and what are their IDs?
telegram-tools bots

# Show one bot's full profile
telegram-tools bots --bot @mybot

# Rename it and update the text people see before pressing Start
telegram-tools bots --bot @mybot --name "My Bot" --description "Does the thing"

# Set a new profile photo (no token needed) — removing one does need the bot's token
telegram-tools bots --bot @mybot --photo avatar.png
```

## The menu

Run `telegram-tools` with no arguments and you get a menu instead of flags:

```text
telegram-tools
--------------------------------------------
1. Chats & topics (find IDs)
2. Search / export messages
3. Clear topic messages
4. My bots
5. Check setup
0. Exit
```

`0` always goes back — one step at a time inside a picker, straight to this menu from
anywhere else — and exits at the root. Chats, topics, bots, and admin rights come from
live pick-lists rather than prompts asking you to type an ID. Bot fields show their
current value and offer keep / change / clear. After every job it returns to the menu.

The safety gates are the same as the flags', not looser: clearing topic messages
dry-runs first and still asks you to type `DELETE`, and bot edits still print a diff
and ask before writing. With no terminal attached it prints this help instead.

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
141   Deploys
217   Support
16    General
```

## Safety model

| Command | Destructive? |
| --- | --- |
| `discover`, `search`, `doctor` | No — read-only |
| `bots` | No — changes settings on bots you own, after a diff and a `y/N` unless you pass `--yes`; reversible if you still have the old values, but `--remove-photo` and `--clear-commands` discard data Telegram will not hand back |
| `clear-messages` | Yes — but only with `--execute` **and** a typed `DELETE`, only messages, never topics |

`clear-messages` also verifies you actually hold the delete-messages permission in the chat before doing anything, skips topic starter messages, and handles Telegram flood-wait limits automatically.

`bots` refuses to edit a bot you do not own, and it never fetches or exports a bot token from Telegram — the three token-only edits simply fail with a message naming the fields they need one for.

## Status

Stable for its four jobs; used regularly by its author. This is a solo project whose code was written by AI agents under review — issues are welcome, fixes are best-effort, and there is no support promise.

## License

MIT. See [LICENSE](LICENSE).
