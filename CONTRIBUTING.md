# Contributing

Thanks for helping improve `telegram-tools`.

## Safety First

Do not include any real Telegram credentials or private chat data in issues, pull requests, commits, tests, screenshots, fixtures, or logs.

Never commit:

- `.env`
- `bots.json`
- `.telegram-tools/`
- `*.session*`
- phone numbers
- Telegram API hashes
- bot tokens
- exported private chat data

## Development Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Validation

Run the full local check before opening a pull request:

```bash
python -m pytest
python -m compileall -q src
telegram-tools --help
telegram-tools discover --help
telegram-tools clear-messages --help
telegram-tools search --help
telegram-tools bot-inventory --help
telegram-tools bot-add --help
telegram-tools doctor --help
for f in scripts/*.command; do bash -n "$f"; done
```

If you have local Telegram config and session storage, also run:

```bash
telegram-tools doctor
```

## Clear-Message Changes

Be especially conservative around `clear-messages`.

The required behavior is:

- dry-run by default.
- `--execute` is required before Telegram deletion APIs are called.
- a typed `DELETE` prompt is required during execution.
- forum topics are not deleted.
- topic IDs do not change.
- only messages collected from the selected chat/topic are deleted.

Add or update tests for any change touching that flow.
