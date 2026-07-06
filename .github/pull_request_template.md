## Summary

- Summary of changes.

## Safety

- [ ] I did not include Telegram API hashes, bot tokens, phone numbers, `.env`, `bots.json`, session files, or private exports.
- [ ] I did not make destructive Telegram operations easier to run accidentally.
- [ ] If I touched `clear-messages`, dry-run, `--execute`, typed `DELETE`, topic preservation, and topic ID preservation are covered by tests.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m compileall -q src`
- [ ] `telegram-tools --help`
- [ ] `telegram-tools doctor` if local Telegram config is available
- [ ] `for f in scripts/*.command; do bash -n "$f"; done`
