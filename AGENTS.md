# telegram-tools — agent brief
Local Telethon CLI that operates the user's own Telegram account (read, export, archive, write, administer, watch).
Public repo: never commit phone numbers, API hashes, tokens, sessions or exported chat data (CONTRIBUTING.md binds).

## Commands
- Setup: `python3.11 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"`.
- Iterate: `.venv/bin/python -m pytest -q tests/test_<area>.py` or `-k <name>`. No network, no scanner binary needed.
- Full check once before landing: `.venv/bin/python -m pytest -q`, `.venv/bin/python -m compileall -q src`, the `--help` smoke in CONTRIBUTING.md.

## Where things are

README.md's file map shows every file with its own first line; the agents repo's `automation/bin/file-map .` rewrites it after you add, move or rename files.

- `adapters/<area>.py` is the Telethon port; `<area>.py` holds the rules, plan and screens; `cli._run_<area>` is the handler.
- Telethon API notes: vault `Projects/CLI Tools Site/Telethon API notes.md`; the early superpowers specs and plans are archived in the vault (`Archive/Repo docs/telegram-tools/`).
- CONTEXT.md defines the domain terms (seam, profile, rid, cursor). Bare invocation is the human menu; agents pass a subcommand.
- `_core/` is a vendored copy at the tag in `_core/VERSION`: never edit it (`test_core_copy.py`). Fix upstream, then `scripts/sync-core.sh <tag>`.
- `skill/SKILL.md` is the bundled agent skill's source (the wheel force-includes it); a CLI-surface change updates it in the same commit.

## Rules
- Every write: plan, preflight rights, re-derive the target after its gate, read back, append one redacted audit line. All five, menu too.
- A call the platform refused is an executed write (one `failed` audit line); a refusal the tool made before any call leaves nothing.
- Keep every gate in README → Safety model, and keep the menu from ever being a shorter path past one.
- `--yes` on a send, message verb or alert needs the landing chat in `TELEGRAM_SEND_ALLOWLIST`; the sender's check is the gate, never weakened.
- `--as-bot` narrows, never widens: `BOT_MODE_COMMANDS` in `cli.py` plus `messages.BOT_VERBS` is the whole list.
- An optional extra (`proxy`, `qr`) that is absent refuses its command, never degrades it. The base install stays Telethon + dotenv.
- Parity: `delete` removes exactly what `create` makes; a basic group and the General topic are refused.
- `~/.telegram-tools/` is 0600 files in 0700 dirs; every write refuses while anything there is group- or world-readable.
- `test_help_surface.py` freezes every `--help` in `tests/fixtures/help/`. A new phrase needs an `ALLOWED_ADDITIONS` entry, a changed
  one an `ALLOWED_REWRITES` entry, each keyed by parser with its reason. Telethon's rights lists compare as markers, not text.
- `test_seams.py`: `src/`, README, AGENTS, CONTEXT and `skill/SKILL.md` never name the sibling platform or tool.
- `test_menu.py`: every flag has a menu row or a `NO_ROW` reason; the menu never builds a skipped confirm.
- `test_hint_commands.py`: every hinted or documented command carrying `--profile` must parse; the flag goes before the subcommand.
- `test_columns.py` holds measured terminal widths for emoji; change the width rule only with a new measurement, never a heuristic.
- CI runs 3.11–3.14 on fresh deps. Red there, green here: diff `pip freeze` with CI's install log; version-gate fixtures, never re-freeze them.
- Secret scanners refuse non-Markdown filenames carrying `auth`/`token`/`secret` and `token = "…"` strings: hence `login.py`; name samples `TOKEN_SAMPLE`.
- An editable install's `importlib.metadata.version()` is frozen at install time; read `telegram_tools.__version__` instead.

## Working here
- Root menu rows are a promise: regrouping needs the maintainer's sign-off on a transcript (`tests/transcripts/record.py`, real pty, invented account).
- The README picture is `assets/menu.png`. When `menu.ROOT_ITEMS` changes, recapture it from the site and fix its alt text (`test_readme_menu_picture.py`).
- A user-visible change carries its CHANGELOG entry and version bump in one commit (`test_version.py`); only a no-behaviour change waits under `## Unreleased`.
- Releasing (maintainer only): version in `pyproject.toml` + `__init__.py`, no git tags; rebuild `dist/`, test the sdist in a fresh `[dev]` venv.
  PyPI account `banozz` (not the GitHub handle); the full upload recipe is in the maintainer's private runbook.
