# telegram-tools — agent brief

Local Telethon CLI operating the user's own Telegram account: `discover` chat/topic IDs, `search`/export messages, `send` a message, `create` group/channel/topic, `delete` the same three, `clear-messages`, `bots` (BotFather settings), `doctor`. A global `--json`/`--jsonl` puts one shared-schema envelope on stdout for agents; human output is untouched by it. Public repo — contributor-facing; never commit phone numbers, API hashes, tokens, or exported chat data (CONTRIBUTING.md binds).

## Working here
- Test: `.venv/bin/python -m pytest -q` → all pass (552 as of v3.8.0), no network. CI adds `compileall` + per-subcommand `--help` smoke; there is no lint step.
- `src/telegram_tools/_core/` is **not this repo's code**: a copy of a shared tree at the tag in `_core/VERSION`. Never edit it — `tests/test_core_copy.py` fails on any local change. A fix goes to the source, then `CLI_TOOLS_CORE_PATH=<checkout> scripts/sync-core.sh <tag>`.
- Two tests are laws, not conveniences. `tests/test_help_surface.py` freezes every `--help` against `tests/fixtures/help/` — an added flag needs a named entry in `ALLOWED_ADDITIONS` carrying its reason. `tests/test_seams.py` fails if `src/`, README, AGENTS, CONTEXT or `skill/SKILL.md` names the sibling platform or the sibling tool.
- Every write builds a plan, preflights the rights it needs, re-derives the target after its gate, reads back, and appends one redacted line to `~/.telegram-tools/audit.jsonl` — from the menu as well. A new write does all five or it is not finished.
- Bare invocation is the human menu; agents pass a subcommand. Session + exports live in `~/.telegram-tools/`, outside the repo.
- `skill/SKILL.md` (independently versioned) is the bundled agent skill's source of truth: a CLI-surface change updates it **in the same commit** — convention, nothing enforces it.
- A user-visible fix also gets its CHANGELOG entry and version bump in the same change; unreleased fixes silently stacking on the last shipped version is the failure mode to avoid.

## Destructive commands
`clear-messages` deletes real messages: dry-run is the default, execution needs `--execute` plus typed `DELETE`. `bots` mutates live bot settings behind a diff + confirm (`--yes` skips). `send` posts publicly as the user behind a full-message preview + `y/N`; its `--yes` additionally requires the destination in `TELEGRAM_SEND_ALLOWLIST` (unset = every `--yes` send refused). `create` confirms before making a real, visible object. `delete` removes the chat or topic itself: dry-run default, execution needs `--execute` plus the target's **exact title** typed back — for a container the mistake worth catching is the wrong target, not the absent intent — and it has no `--yes`, so deletion is never unattended. Keep those gates, and keep the menu from ever being the shorter path past one.

Parity rule: `delete` removes exactly what `create` makes. Supergroups, broadcast channels and forum topics, yes; a basic group is refused, because `create` makes supergroups and cannot make one back. Telegram has no delete-topic method — a topic is removed by deleting all its messages including the one that opened it (`messages.deleteTopicHistory`), which is what `delete topic` does; the General topic has no such message and is refused.

## Releasing (maintainer only)
PyPI account is **banozz** (not the GitHub handle). Full recipe with the zsh traps (the `!` negation, the glob no-op, stale-sdist check, twine output redaction) lives in the maintainer's private runbook. Rebuild `dist/` after any source edit; `repo ≠ sdist` — `docs/superpowers/` is tracked here but excluded from the package.
