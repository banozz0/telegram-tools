# Changelog

All notable changes to this project will be documented here.

This project follows a practical changelog style: user-visible changes, safety changes, and release notes belong here; active task tracking belongs outside the repo.

## 3.3.0 - 2026-08-25

- Add `send`: post a message to a chat, or into one forum topic with `--topic`. It prints the destination and the whole message body, then asks `y/N`. `--text -` reads the body from stdin, which is how a multi-line message gets in without fighting your shell.
- Add `create`: `create group` (a supergroup, with `--forum` to switch topics on in the same call), `create channel` (a broadcast channel), and `create topic` (a topic inside a forum group). Each asks before it acts and prints the new ID, so a new chat can be piped straight into `send`.
- Both are in the menu, at 3 and 4. The other entries moved down: clearing topic messages is now 5, bots 6, check setup 7. The menu has no `--yes` equivalent — every send it makes shows the message and asks.
- New optional `TELEGRAM_SEND_ALLOWLIST` (`chat[:topic]`, comma separated). It gates one thing only: `send --yes`, the unattended path where nobody sees the preview. Unset means every `--yes` send is refused; `send` without `--yes` is unaffected. `doctor` reports how many destinations are listed and never which.
- The menu's staged message shows in the prompt when you go back to it (`Message [hiiiii]`), so keeping it does not mean typing it again. Long or multi-line bodies are flattened and cut for that one line only.
- No attachments on `send` — text only for now.

## 3.2.0 - 2026-08-15

- `telegram-tools` with no arguments now opens a menu you walk forward and back through instead of a single screen that exits after one action: numbered lists all the way down, `0` always backs out one screen at a time — inside a picker or a flow's own screen alike — and a return to the menu after every job.
- Chats, forum topics, bots, and admin-right names are picked from live lists. Typing is left for the things a list cannot carry — a search phrase, a name, a date, a file path.
- Bot editing shows each field's current value and offers keep / change / clear, so a field can finally be emptied from the menu; blank no longer has to mean "keep".
- Clearing topic messages from the menu always runs a dry-run first and only then offers the real pass, which still asks you to type `DELETE`. Bot edits still print an old → new diff and ask; the menu never skips it.
- Ctrl-C anywhere exits cleanly with no traceback. With no arguments and no terminal — a script, a cron job, an agent — it prints help instead of waiting forever for a human.
- No flag changed. The menu builds the same commands the flags do.

## 3.1.0 - 2026-08-14

- Add `bots`: list the bots you own with their numeric IDs, show one bot's full profile, and edit its display name, bio, description, commands, profile photo, and default group/channel admin rights.
- Bot editing runs on your existing login. Commands, photo removal, and default admin rights are sent by the bot itself and need that bot's token in the new optional `TELEGRAM_BOT_TOKENS` variable (`nickname:token`, comma separated). Tokens are read only — never written to disk, printed, or exported. This is not a return of the 3.0.0 `bots.json` store.
- Edits print an old → new diff and ask for confirmation; `--yes` skips the prompt. Every edit run names the bot first (`Editing @yourbot (12345)`), including under `--yes`, so a mistyped nickname can never act on a different bot unseen. A blank answer at the prompt says so rather than looking like it ignored you.
- Default admin rights ignore Telegram's implicit `other` flag, which it adds to any non-empty rights set. Without that, re-running the same `--group-rights` command showed a change that was not one and re-sent the write.
- `doctor` reports how many bot tokens are loaded, and nothing else about them.
- Changing a bot's `@username` and creating or deleting bots have no API at all and stay with @BotFather. Revoking a token does have one (`bots.exportBotToken`), but this tool never calls it — fetching or exporting a bot token is deliberately out of scope.

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
