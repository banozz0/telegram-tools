# telegram-tools

Private CLI tools for managing your own Telegram chats, groups, channels, forum topics, and bot tokens.

## Project Overview

`telegram-tools` is a local Telethon-based command line toolkit. It is built for your authenticated Telegram account and stores session data locally.

This project distinguishes these operations deliberately:

| Operation | Destructive? | What it does |
| --- | --- | --- |
| Discovery | No | Lists chats/channels/groups and forum topics. |
| Search | No | Finds matching messages and prints metadata/text. |
| Export | No | Writes message metadata/text to JSON or CSV. |
| Clear Messages | Yes | Permanently removes messages inside selected forum topic(s). Topics remain. Topic IDs remain. |
| Future Topic Deletion | Not implemented | This toolkit does not delete forum topics. |

The clear-message workflow never deletes forum topics. It only clears/deletes messages inside topics.

## Installation

Create a Telegram API app at <https://my.telegram.org/apps>, then install locally:

```bash
cd /Users/sven/code/telegram-tools
/opt/homebrew/bin/python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Configure credentials with either `.env` or exported shell variables.

`.env`:

```bash
cp .env.example .env
$EDITOR .env
```

Shell:

```bash
export TELEGRAM_API_ID=123456
export TELEGRAM_API_HASH=your_api_hash
```

Exported shell variables take precedence over `.env`.

## First Login

The first command that uses your Telegram user account starts Telethon's interactive login. The local session is stored in `.telegram-tools/`, which is gitignored because it contains reusable auth material.

Example:

```bash
telegram-tools discover
```

## Discovery

Read-only.

Use discovery when you need chat IDs, usernames, chat types, admin status, or forum topic IDs.

Required inputs: none.

Examples:

```bash
telegram-tools discover
telegram-tools discover --admin-only
telegram-tools discover --json exports/chats.json
```

Example output:

```text
Chat
--------------------------------------------
Hermes
Chat ID: -1003930298580
Type: Forum Group
Admin: yes

Topics
--------------------------------------------
141   Harry
217   Dobby
16    Professor
4596  Bangis
5027  Sentinel
```

Safety notes:

- Discovery does not modify Telegram.
- Default output is human-readable.
- Use `--json` when another tool needs structured output.

## Search

Read-only.

Use search when you want to inspect matching messages without writing a file.

Required inputs:

- `--chat`: numeric chat ID, `@username`, or `t.me` link.

Optional filters:

- `--topic`: forum topic ID.
- `--keyword`: case-insensitive text search.
- `--from-user`: sender username, ID, or `me`.
- `--since` / `--until`: ISO date or datetime.
- `--limit`: maximum messages.

Examples:

```bash
telegram-tools search --chat @my_group --keyword deploy
telegram-tools search --chat -1003930298580 --topic 141 --keyword deploy --limit 50
telegram-tools search --chat https://t.me/example_group --from-user @alice
```

Example output is JSON message metadata and text:

```json
[
  {
    "id": 1234,
    "chat_id": -1003930298580,
    "topic_id": 141,
    "date": "2026-07-06T12:30:00+00:00",
    "sender_id": 1001,
    "sender_username": "alice",
    "has_media": false,
    "text": "deploy finished"
  }
]
```

Safety notes:

- Search does not modify Telegram.
- Topic-scoped search iterates that topic and filters locally because Telegram's API does not support every search filter inside replies/topics.
- Media is not downloaded in v1.

## Export

Read-only.

Use export when you want JSON or CSV files containing metadata and text.

Required inputs:

- `--chat`: numeric chat ID, `@username`, or `t.me` link.
- `--output`: output path for CSV, recommended for JSON.

Examples:

```bash
telegram-tools search --chat @my_group --keyword deploy --format json --output exports/deploy.json
telegram-tools search --chat @my_group --topic 141 --format csv --output exports/topic-141.csv
```

Example CSV columns:

```text
id,chat_id,topic_id,date,sender_id,sender_username,reply_to_msg_id,reply_to_top_id,has_media,text
```

Safety notes:

- Export does not modify Telegram.
- Existing output files are overwritten.
- No media is downloaded in v1.

## Clear Topic Messages

Destructive. Preserves forum topics and topic IDs.

Use this when you want to clear messages from one forum topic.

Required inputs:

- `--chat`: numeric chat ID, `@username`, or `t.me` link.
- `--topic`: topic ID.

Dry-run example:

```bash
telegram-tools clear-messages --chat @my_group --topic 141
```

Execute example:

```bash
telegram-tools clear-messages --chat @my_group --topic 141 --execute
```

Example dry-run output:

```text
Scanning topic 141 (Harry)
Dry-run: 348 topic messages would be cleared
{
  "matched": 348,
  "cleared": 0,
  "dry_run": true,
  "cancelled": false
}
```

Safety notes:

- Dry-run is the default.
- `--execute` is required before any Telegram delete API call.
- Even with `--execute`, you must type `DELETE` at the safety prompt.
- Forum topics are not deleted.
- Topic IDs do not change.
- Topic starter messages are skipped.

## Clear Multiple Topics

Destructive. Preserves forum topics and topic IDs.

Use repeated `--topic` flags when you want to clear messages from selected forum topics.

Example:

```bash
telegram-tools clear-messages --chat @my_group --topic 141 --topic 217 --topic 16
```

Execute example:

```bash
telegram-tools clear-messages --chat @my_group --topic 141 --topic 217 --topic 16 --execute
```

Safety notes:

- Same safeguards as single-topic clearing.
- Each topic is scanned and only messages collected from that topic are cleared.
- This does not delete topics.

## Clear All Topic Messages

Destructive. Preserves forum topics and topic IDs.

Use this when every forum topic in a group should have its messages cleared.

Dry-run example:

```bash
telegram-tools clear-messages --chat @my_group --all-topics
```

Execute example:

```bash
telegram-tools clear-messages --chat @my_group --all-topics --execute
```

Safety notes:

- This can match many messages. Run dry-run first.
- The command lists forum topics through Telegram, then clears messages inside them.
- Forum topics are not deleted.
- Topic IDs do not change.

## Bot Inventory

Read-only with respect to your Telegram account. It calls Telegram Bot API `getMe` for supplied bot tokens.

Use this when you want to verify which bot tokens are valid without printing full tokens.

Token sources:

- `.env`
- Local `bots.json`, which is gitignored

`.env` examples:

```bash
TELEGRAM_BOT_TOKEN=<bot-token>
TELEGRAM_BOT_TOKENS=<bot-token>,<another-bot-token>
TELEGRAM_BOT_TOKEN_HERMES=<bot-token>
```

`bots.json` example:

```json
[
  {"name": "hermes", "token": "<bot-token>"}
]
```

Commands:

```bash
telegram-tools bot-inventory
telegram-tools bot-inventory --bots-json bots.json
```

Example output:

```text
ok	id	username	display_name	token	error
yes	123456789	hermes_bot	Hermes Bot	123456789:***
```

Safety notes:

- Full bot tokens are never printed.
- `bots.json` is gitignored.
- Never commit real bot tokens.

## Launcher Scripts

Clickable macOS `.command` launchers live in `scripts/`.

All launchers:

- `cd /Users/sven/code/telegram-tools`
- activate `.venv`
- prompt for required values
- keep Terminal open with `Press Enter to close`
- avoid hardcoded chat IDs, topic IDs, and secrets

### `scripts/discover.command`

Read-only.

What it does: runs human-readable discovery in Terminal. It optionally writes JSON to `exports/discovery.json`.

When to use it: when you want to click once and inspect chats/topics.

Inputs: optional JSON export confirmation.

Example:

```bash
open scripts/discover.command
```

### `scripts/search.command`

Read-only.

What it does: prompts for chat, keyword, and optional topic ID, then prints JSON search results.

When to use it: quick Terminal search.

Required inputs: chat and keyword.

Example:

```bash
open scripts/search.command
```

### `scripts/export-topic.command`

Read-only.

What it does: prompts for chat, topic ID, output path, and format, then exports topic messages.

When to use it: saving topic messages to JSON or CSV.

Required inputs: chat and topic ID.

Example:

```bash
open scripts/export-topic.command
```

### `scripts/clear-topic-messages.command`

Destructive only if you explicitly opt into execution.

What it does: prompts for chat and one topic ID, then runs `clear-messages`.

When to use it: clearing messages inside one topic.

Required inputs: chat and topic ID.

Safety notes:

- Defaults to dry-run.
- To pass `--execute`, type `DELETE` when the launcher asks.
- The CLI then shows the full clear-message safety prompt and asks for `DELETE` again.
- Topics and topic IDs are preserved.

### `scripts/clear-multiple-topics.command`

Destructive only if you explicitly opt into execution.

What it does: prompts for chat and multiple topic IDs, then runs `clear-messages`.

When to use it: clearing messages inside selected topics.

Required inputs: chat and topic IDs.

Safety notes: same as `clear-topic-messages.command`.

### `scripts/clear-all-topic-messages.command`

Destructive only if you explicitly opt into execution.

What it does: prompts for chat and runs `clear-messages --all-topics`.

When to use it: clearing messages inside every forum topic in a group.

Required inputs: chat.

Safety notes:

- Defaults to dry-run.
- This can match many messages.
- Topics and topic IDs are preserved.

### `scripts/bot-inventory.command`

Read-only.

What it does: validates bot tokens from `.env` or `bots.json`.

When to use it: checking which configured bot tokens are valid.

Required inputs: `.env` or `bots.json` with bot token values.

Safety notes:

- Full tokens are never printed.
- Do not commit `.env` or `bots.json`.

## Safety Features

- `.env`, `bots.json`, `.telegram-tools/`, session files, and exports are gitignored.
- Discovery, search, export, and bot inventory are read-only.
- Clear-message commands default to dry-run.
- `--execute` is required for destructive message clearing.
- The execution prompt explicitly says forum topics will not be deleted and topic IDs will not change.
- FloodWait responses are slept through before retrying a clear-message batch.
- The toolkit does not implement topic deletion.

## Project Structure

```text
src/telegram_tools/
  bot_inventory.py  Bot token loading, masking, and Bot API validation
  cli.py            argparse CLI entry point
  client.py         Telethon client creation
  config.py         .env and environment config loading
  delete.py         clear-message implementation and safety prompt
  discovery.py      chat/topic discovery and table formatting
  exporters.py      JSON/CSV writing
  resolver.py       shared chat/entity resolver
  search.py         message search/export collection
  topics.py         forum topic API helpers
scripts/
  *.command         clickable macOS launchers
tests/
  test_*.py         unit tests
```

## Troubleshooting

### `Cannot find any entity corresponding to "-100..."`

Run discovery first:

```bash
telegram-tools discover
```

Numeric IDs are resolved from the authenticated account's dialogs before falling back to Telethon entity lookup.

### First login asks for phone/code

That is Telethon creating the local session. The session is stored under `.telegram-tools/`.

### CSV export without output path fails

CSV needs an output file:

```bash
telegram-tools search --chat @my_group --topic 141 --format csv --output exports/topic.csv
```

### Clear-message command matched too many messages

Do not pass `--execute`. Inspect the dry-run result and narrow the chat/topic selection.

### Bot inventory shows `Unauthorized`

The token is invalid, revoked, malformed, or belongs to a deleted bot. The output masks the token so it is safe to read in Terminal.

## Validation

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src
.venv/bin/telegram-tools --help
for f in scripts/*.command; do bash -n "$f"; done
```
