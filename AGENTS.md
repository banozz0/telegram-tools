# telegram-tools — agent brief

Local Telethon CLI operating the user's own Telegram account: `discover` chat/topic IDs, `search`/export messages, `send` a message, `create` group/channel/topic, `clear-messages`, `bots` (BotFather settings), `doctor`. Public repo — contributor-facing; never commit phone numbers, API hashes, tokens, or exported chat data (CONTRIBUTING.md binds).

## Working here
- Test: `.venv/bin/python -m pytest -q` → all pass (300 as of v3.3.0), no network. CI adds `compileall` + per-subcommand `--help` smoke; there is no lint step.
- Bare invocation is the human menu; agents pass a subcommand. Session + exports live in `~/.telegram-tools/`, outside the repo.
- `skill/SKILL.md` (independently versioned) is the bundled agent skill's source of truth: a CLI-surface change updates it **in the same commit** — convention, nothing enforces it.
- A user-visible fix also gets its CHANGELOG entry and version bump in the same change; unreleased fixes silently stacking on the last shipped version is the failure mode to avoid.

## Destructive commands
`clear-messages` deletes real messages: dry-run is the default, execution needs `--execute` plus typed `DELETE`. `bots` mutates live bot settings behind a diff + confirm (`--yes` skips). `send` posts publicly as the user behind a full-message preview + `y/N`; its `--yes` additionally requires the destination in `TELEGRAM_SEND_ALLOWLIST` (unset = every `--yes` send refused). `create` confirms before making a real, visible object. Keep those gates, and keep the menu from ever being the shorter path past one.

## Releasing (maintainer only)
PyPI account is **banozz** (not the GitHub handle). Full recipe with the zsh traps (the `!` negation, the glob no-op, stale-sdist check, twine output redaction) lives in the maintainer's private runbook. Rebuild `dist/` after any source edit; `repo ≠ sdist` — `docs/superpowers/` is tracked here but excluded from the package.
