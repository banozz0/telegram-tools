# telegram-tools

Private CLI tools for managing your own Telegram chats, groups, channels, and forum topics with Telethon.

## Setup

1. Create a Telegram API app at <https://my.telegram.org/apps>.
2. Create a local virtual environment with Python 3.11+ and install the project:

```bash
/opt/homebrew/bin/python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

3. Export your credentials:

```bash
export TELEGRAM_API_ID=123456
export TELEGRAM_API_HASH=your_api_hash
```

The first CLI run starts Telethon's interactive login flow. The local session is stored under `.telegram-tools/` and is intentionally gitignored.

## Commands

Discover dialogs and forum topics:

```bash
telegram-tools discover --json exports/chats.json
```

Dry-run deletion for one topic:

```bash
telegram-tools delete --chat @my_group --topic 12345
```

Dry-run deletion for every topic in a forum group:

```bash
telegram-tools delete --chat @my_group --all-topics
```

Actually delete matched topic messages:

```bash
telegram-tools delete --chat @my_group --topic 12345 --execute
```

The delete command defaults to dry-run. With `--execute`, it still prompts for exactly `DELETE` before calling Telegram deletion APIs.

Search and export messages:

```bash
telegram-tools search --chat @my_group --keyword "deploy" --format json --output exports/deploy.json
telegram-tools search --chat @my_group --topic 12345 --since 2026-07-01 --until 2026-07-06 --format csv --output exports/topic.csv
telegram-tools search --chat @my_group --from-user @alice --limit 500 --format json --output exports/alice.json
```

No media is downloaded in v1. Exports contain message metadata and text only.

## Safety Notes

- Session files and `.env` are ignored because they contain secrets or reusable login material.
- `delete` never executes unless both `--execute` and the `DELETE` prompt are satisfied.
- Topic deletion skips topic starter IDs so existing topic IDs are preserved.
- FloodWait responses are slept through automatically before retrying batches.
- This toolkit is for managing your own groups/channels. Do not use it to spam or violate Telegram's terms.

## Validation

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall src
```
