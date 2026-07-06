# telegram-tools

Private Telegram tooling for your own chats, forum topics, exports, message clearing, and bot-token inventory.

## Project Overview

`telegram-tools` is a local Python + Telethon CLI. It is designed to be usable from:

- `telegram-tools`, which opens an interactive menu
- explicit argparse subcommands
- clickable macOS `.command` launchers in `scripts/`

The toolkit deliberately separates safe read-only actions from destructive message clearing:

| Area | Destructive? | Summary |
| --- | --- | --- |
| Discovery | No | Lists managed chats, forum groups, channels, and topic IDs. |
| Search | No | Searches message metadata/text and prints a readable table. |
| Export | No | Writes message metadata/text to JSON or CSV. |
| Clear Messages | Yes | Deletes messages inside selected topic(s), preserving topics and topic IDs. |
| Bot Inventory | No | Validates bot tokens via Bot API `getMe` and masks tokens. |
| Bot Add | Writes local file | Validates a token and stores it in gitignored `bots.json`. |
| Topic Deletion | Not implemented | This toolkit does not delete forum topics. |

Clear-message workflows delete messages only. Forum topics are not deleted. Topic IDs do not change.

## Installation

```bash
cd /Users/sven/code/telegram-tools
/opt/homebrew/bin/python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Create a Telegram API app at <https://my.telegram.org/apps>, then configure either `.env` or shell variables:

```bash
cp .env.example .env
$EDITOR .env
```

```bash
export TELEGRAM_API_ID=123456
export TELEGRAM_API_HASH=your_api_hash
```

Shell variables win over `.env`.

## First Login

The first command that uses your Telegram user account starts Telethon's interactive login:

```bash
telegram-tools discover
```

The session is stored under `.telegram-tools/`, which is gitignored.

## Interactive Menu

Run without a subcommand:

```bash
telegram-tools
```

Menu:

```text
1. Discover chats/topics
2. Search messages
3. Export messages
4. Clear topic messages
5. Clear multiple topics
6. Clear all topic messages
7. Bot inventory
8. Add bot
0. Exit
```

Use the menu when you want prompts instead of remembering flags. Existing subcommands still work.

## Discovery

Read-only.

Default behavior shows admin/managed chats only.

```bash
telegram-tools discover
```

Show every chat:

```bash
telegram-tools discover --all
```

Write JSON while keeping the same default filter:

```bash
telegram-tools discover --json exports/chats.json
telegram-tools discover --all --json exports/all-chats.json
```

Example output:

```text
Forum Groups
============
Example Forum
Chat ID: -1001112223333
Type: Forum Group
Admin: yes

Topics
--------------------------------------------
141   Harry
217   Dobby
16    Professor
```

Use discovery before clearing messages so you can copy exact chat IDs and topic IDs.

## Search

Read-only.

Search messages and print a readable table:

```bash
telegram-tools search --chat @my_group --contains deploy
telegram-tools search --chat -1001112223333 --topic 141 --contains deploy
```

`--contains` and `--keyword` are aliases.

Optional filters:

- `--topic`
- `--contains` / `--keyword`
- `--from-user`
- `--since`
- `--until`
- `--limit`

Example output:

```text
Messages
--------------------------------------------
1234  2026-07-06T12:30:00+00:00  topic=141  sender=alice  deploy finished
```

No media is downloaded.

## Export

Read-only.

Exports use the `search` command with `--output`.

JSON:

```bash
telegram-tools search --chat @my_group --topic 141 --format json --output exports/topic-141.json
```

CSV:

```bash
telegram-tools search --chat @my_group --topic 141 --format csv --output exports/topic-141.csv
```

Example JSON record:

```json
{
  "id": 1234,
  "chat_id": -1001112223333,
  "topic_id": 141,
  "date": "2026-07-06T12:30:00+00:00",
  "sender_username": "alice",
  "has_media": false,
  "text": "deploy finished"
}
```

Existing output files are overwritten.

## Clear Topic Messages

Destructive. Deletes messages permanently. Preserves forum topics and topic IDs.

Dry-run is default:

```bash
telegram-tools clear-messages --chat @my_group --topic 141
```

Execute:

```bash
telegram-tools clear-messages --chat @my_group --topic 141 --execute
```

Execution still requires typing `DELETE` at the safety prompt:

```text
====================================================
WARNING: CLEAR TOPIC MESSAGES

This will permanently delete ALL MESSAGES from the selected topic(s).

OK: Forum topics will NOT be deleted.
OK: Topic IDs will NOT change.
OK: Only messages will be removed.
====================================================
Type DELETE to continue:
```

Safety notes:

- `--execute` is required before Telegram deletion APIs are called.
- The prompt says exactly what is preserved.
- Topic starter messages are skipped.
- FloodWait is handled automatically.

## Clear Multiple Topics

Destructive only with `--execute`.

```bash
telegram-tools clear-messages --chat @my_group --topic 141 --topic 217 --topic 16
telegram-tools clear-messages --chat @my_group --topic 141 --topic 217 --topic 16 --execute
```

Each topic is scanned and only messages collected from that topic are cleared.

## Clear All Topic Messages

Destructive only with `--execute`.

Preferred flag:

```bash
telegram-tools clear-messages --chat @my_group --all-topics-in-chat
```

Backward-compatible alias:

```bash
telegram-tools clear-messages --chat @my_group --all-topics
```

Execute:

```bash
telegram-tools clear-messages --chat @my_group --all-topics-in-chat --execute
```

This clears messages inside every forum topic in the chat. It does not delete topics.

## Bot Inventory

Read-only with respect to Telegram chats.

Tokens can live in `.env`:

```bash
TELEGRAM_BOT_TOKEN=<bot-token>
TELEGRAM_BOT_TOKENS=<bot-token>,<another-bot-token>
TELEGRAM_BOT_TOKEN_HERMES=<bot-token>
```

Or local gitignored `bots.json`:

```json
[
  {"name": "example", "token": "<bot-token>"}
]
```

Run:

```bash
telegram-tools bot-inventory
```

Example output:

```text
name	bot ID	username	masked token	status
Example Bot	123456789	example_bot	123456789:***	ok
```

Full tokens are never printed.

## Bot Add

Writes local gitignored `bots.json`.

Use this when you receive a new bot token and want to validate/store it locally:

```bash
telegram-tools bot-add
```

The command prompts for the token, validates it through Bot API `getMe`, stores it in `bots.json`, and prints only a masked token.

## Launcher Scripts

Clickable macOS launchers live in `scripts/`.

All launchers:

- `cd /Users/sven/code/telegram-tools`
- activate `.venv`
- run `telegram-tools`
- prompt for required values
- keep Terminal open afterwards
- do not hardcode Example Forum/chat/topic IDs

| Launcher | Mode | What it does |
| --- | --- | --- |
| `telegram-tools.command` | mixed | Opens the interactive menu. |
| `discover.command` | read-only | Runs discovery; optionally writes JSON. |
| `search.command` | read-only | Prompts for chat/keyword/topic and searches. |
| `export-topic.command` | read-only | Prompts for chat/topic/output and exports. |
| `clear-topic-messages.command` | destructive only with opt-in | Clears messages in one topic. |
| `clear-multiple-topics.command` | destructive only with opt-in | Clears messages in selected topics. |
| `clear-all-topic-messages.command` | destructive only with opt-in | Clears messages in all topics in a chat. |
| `bot-inventory.command` | read-only | Validates configured bot tokens. |
| `bot-add.command` | local write | Prompts for and stores a validated bot token. |

Examples:

```bash
open scripts/telegram-tools.command
open scripts/discover.command
open scripts/clear-topic-messages.command
open scripts/bot-add.command
```

Clear launchers default to dry-run. To pass `--execute`, type `DELETE` when the launcher asks; the CLI then asks for `DELETE` again at the safety prompt.

## Example Workflows

### Find a topic ID and export it

```bash
telegram-tools discover
telegram-tools search --chat -1001112223333 --topic 141 --format json --output exports/topic-141.json
```

### Search before clearing

```bash
telegram-tools search --chat @my_group --topic 141 --contains "old notice"
telegram-tools clear-messages --chat @my_group --topic 141
```

Only add `--execute` after the dry-run count is what you expect.

### Add and inventory a bot

```bash
telegram-tools bot-add
telegram-tools bot-inventory
```

## Safety Features

- `.env`, `bots.json`, `.telegram-tools/`, session files, exports, and `.DS_Store` are gitignored.
- `discover`, `search`, export, and `bot-inventory` are read-only.
- `bot-add` writes only local `bots.json`.
- `clear-messages` is destructive, but only for messages.
- Forum topics are not deleted.
- Topic IDs do not change.
- `--execute` and a typed `DELETE` prompt are both required for message clearing.
- No media download in v1.1.

## Project Structure

```text
src/telegram_tools/
  bot_inventory.py  Bot token loading, masking, validation, and local storage
  cli.py            argparse CLI and interactive menu
  client.py         Telethon client creation
  config.py         .env and environment config loading
  delete.py         clear-message implementation and safety prompt
  discovery.py      chat/topic discovery and grouped table formatting
  exporters.py      JSON/CSV writing
  resolver.py       shared chat/entity resolver
  search.py         message search and readable formatting
  topics.py         forum topic API helpers
scripts/
  *.command         clickable macOS launchers
tests/
  test_*.py         unit tests
```

## Troubleshooting

### Running `telegram-tools` opens a menu

That is the v1.1 default. Use explicit subcommands for scripts:

```bash
telegram-tools discover
telegram-tools search --chat @my_group --contains deploy
```

### `Cannot find any entity corresponding to "-100..."`

Run discovery first:

```bash
telegram-tools discover --all
```

Numeric IDs are resolved from authenticated dialogs before falling back to Telethon entity lookup.

### CSV export without output path fails

CSV needs a file:

```bash
telegram-tools search --chat @my_group --topic 141 --format csv --output exports/topic.csv
```

### Clear-message dry-run matches too many messages

Do not pass `--execute`. Narrow the chat/topic selection and rerun dry-run.

### Bot inventory says `Unauthorized`

The token is invalid, revoked, malformed, or belongs to a deleted bot. The printed token is masked.

## Validation

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src
.venv/bin/telegram-tools --help
.venv/bin/telegram-tools discover --help
.venv/bin/telegram-tools clear-messages --help
.venv/bin/telegram-tools search --help
.venv/bin/telegram-tools bot-inventory --help
.venv/bin/telegram-tools bot-add --help
for f in scripts/*.command; do bash -n "$f"; done
```
