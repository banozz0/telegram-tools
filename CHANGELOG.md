# Changelog

All notable changes to this project will be documented here.

This project follows a practical changelog style: user-visible changes, safety changes, and release notes belong here; active task tracking belongs outside the repo.

## 3.5.0 - 2026-08-31

The menu release: every flag reachable, back that stops forgetting, and a look.

- Every screen below the root carries a breadcrumb trail (`Main › Clear › Hermes › Dry-run done`), and the menu is in colour when it is talking to a terminal: an accent on the numbers and the current screen, dim hints and back rows, a red `error:` line. It is plain text in a pipe, under `NO_COLOR`, or with `TERM=dumb`, and the colour is applied at the one place the menu prints, so prompts still hand back plain strings.
- After a job the menu offers its own next step instead of only a way back to the root: *Tweak it* back to the filled-in search or send form, *Create another*, *Clear more topics*, *Edit more* — plus *Main menu*. *Run it again* appears where a re-run makes sense (chats & topics, search, send); create, clear and bot edits get their own next-step row instead, because re-running those would make a second identical object, clear topics already empty, or re-apply a diff that is now empty. Enter is still the menu and `0` still exits; `doctor` keeps the plain prompt, since running it twice tells you nothing new.
- Backing out of a form with something typed in it — a composed message, staged search filters, staged bot edits — now asks first (`Keep editing` / `Discard it and go back`) instead of dropping it silently.
- Two dead ends step back one screen instead of bouncing to the root: a forum group with no topics on the clear screen returns to the picker, and the bot list with no bots of your own still offers the lookup row. Backing out of a bot's screen returns to the bot list, not the root.
- Long pick-lists page on the letters `n` and `p`, and an item keeps its number on every page — typing a number you saw on the previous page picks it without paging back. The rows after a list (Filter, Select all, Continue) keep their numbers too.
- Clear: an explicit *All topics* row is the `--all-topics` flag (ticking every topic by hand still means the same). A *Batch size* row on the dry-run screen is `--batch-size`, default 100. The ticks are remembered per chat, so backing out to the picker and coming back does not mean ticking again, and Continue with the same ticks goes straight to the dry-run screen instead of scanning every topic a second time. The dry-run-first gate and the typed `DELETE` are unchanged.
- Bots: *Save the bot list to a JSON file* is `bots --json` for the whole list. *Type a bot @username, ID or nickname* resolves exactly as `bots --bot` does — a `TELEGRAM_BOT_TOKENS` nickname included — and routes a bot you do not own to the read-only view the flags already had: its profile shows, the edit row does not. A typo there prints the error and returns to the bot list.
- `discover --admin-only` is gone. It was declared but never read: admin-only has been the default since `--all` arrived, so the flag did nothing and was hidden from `--help`. Nothing that used to work stops working; a script still passing it now gets argparse's usual unknown-flag error, which is the honest answer. The bundled skill never documented it, so `skill/SKILL.md` is unchanged.

## 3.4.1 - 2026-08-25

- `search`'s printed table marks messages that carry a photo or file with `[media]`. It always recorded `has_media` in the JSON and CSV exports and only the table dropped it, so a photo sent with no caption printed as a blank row and read as "nothing was sent" — which is exactly how it was found, minutes after `send --file` shipped. No export format changed.

## 3.4.0 - 2026-08-25

- `send` takes attachments: `--file PATH`, repeatable. Several files go as one album, `--text` becomes the caption, and a file with no text is a valid send. Every path is checked before the confirmation, so a typo in the fourth one cannot surface after the first three have already gone. The preview lists each file with its size read off disk — naming the wrong file is exactly what a preview is for.
- The menu's message box takes several lines, ended by a `.` on its own line. This closes a real hazard rather than adding a nicety: pasting a three-line message into the old one-line prompt fed lines two and three to the menu as if they were menu choices.
- Two clients on one login session now say so — "Another telegram-tools is already using the login session. Close the other one - a menu open in another terminal counts" — instead of a raw `sqlite3.OperationalError: database is locked` traceback. The menu prints it and stays open.
- The menu's send screen gains a Files row; Send it moves down one on that screen.

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
