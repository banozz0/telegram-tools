# Changelog

All notable changes to this project will be documented here.

This project follows a practical changelog style: user-visible changes, safety changes, and release notes belong here; active task tracking belongs outside the repo.

## 3.0.0 - 2026-08-12

First PyPI release. Curated to three tools: discovery, search/export, and clear-messages.

- **Breaking:** remove `bot-inventory` and `bot-add` (and `bots.json` support).
- **Breaking:** remove the macOS `.command` launchers — install with `pipx install telegram-tools` instead.
- **Breaking:** session files and config now default to `~/.telegram-tools/` instead of the current directory. Migrate an existing login with `mv .telegram-tools ~/.telegram-tools` (and move your `.env` values into `~/.telegram-tools/.env` if you want them global). `TELEGRAM_TOOLS_SESSION` still overrides.
- `.env` is now also read from `~/.telegram-tools/.env` (current directory still wins).
- Rewrite README for the PyPI audience, including an api_id/api_hash setup walkthrough.

## 2.0.1 - 2026-07-06

- Add public-readiness documentation: license, contributing guide, security policy, code of conduct, issue templates, and pull request template.
- Add GitHub Actions test workflow.
- Add `telegram-tools doctor` for local setup checks that do not print secrets, token values, or session paths.
- Replace machine-specific launcher paths with repository-relative path resolution.
- Expand package metadata for public packaging.

## 0.1.0 - 2026-07-06

- Add interactive menu.
- Add chat/topic discovery.
- Add message search and JSON/CSV export.
- Add dry-run-first clear-message workflow that preserves forum topics and topic IDs.
- Add bot inventory and bot-add commands with masked token output.
- Add clickable macOS `.command` launchers.
