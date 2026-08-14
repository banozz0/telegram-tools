# Design: `bots` command — read and edit your Telegram bots

Date: 2026-08-13
Status: approved design, pending implementation plan
Target release: 3.1.0

## Goal

Answer two questions from the terminal instead of chatting with @BotFather:

1. *What bots do I own, and what are their numeric IDs?*
2. *Change this bot's name / bio / description / commands / photo / default admin rights.*

One new command, `telegram-tools bots`, running on the user session the rest of the
tool already uses.

## Non-goals

These have no API and stay a manual @BotFather job. The tool names them in help text
rather than pretending:

- Changing a bot's `@username`, or creating/deleting bots.
- Fetching, exporting, or revoking bot tokens. `bots.exportBotToken` does exist and would
  do all three, but it is never called, with or without its `revoke` flag. Note for any
  doc written from this spec: an API for revocation *does* exist — the tool declines to
  use it. Saying "no API exists" would be false.
- Transferring ownership, inline mode, privacy mode, payments, web app settings.
- Anything about bots the user does not own, beyond public read-only info.

## Auth model

Two rails, verified against Telethon 1.44 TL definitions on 2026-08-13.

**Rail 1 — the existing user session** (`~/.telegram-tools/*.session`). Covers all reads
and most writes, no new secret:

| Operation | TL method |
| --- | --- |
| List owned bots + IDs | `bots.getAdminedBots` |
| Read everything shown (bio, description, commands, current default admin rights, photo) | `users.getFullUser(bot)` → `UserFull.about`, `.bot_info`, `.bot_group_admin_rights`, `.bot_broadcast_admin_rights` — one request, and it also works for bots the user does not own. `bots.getBotInfo(lang_code, bot=)` covers the three text fields too but is not used, since it would cost a second round trip for a subset. |
| Write name / bio / description | `bots.setBotInfo(lang_code, bot=, name=, about=, description=)` |
| Set profile photo | `photos.uploadProfilePhoto(bot=, file=)` |

**Rail 2 — that bot's own token**, needed only because these three TL methods take no
`bot` parameter and must be called *as* the bot:

| Operation | TL method |
| --- | --- |
| Write commands | `bots.setBotCommands` / `bots.resetBotCommands` |
| Write default admin rights | `bots.setBotGroupDefaultAdminRights`, `bots.setBotBroadcastDefaultAdminRights` |
| Remove profile photo | `photos.deletePhotos` |

Rail 2 runs through a Telethon client started with `bot_token=`, reusing the configured
`api_id`/`api_hash` and a **`MemorySession`** — no second session file is written to
disk, and no HTTP client or new dependency enters the project.

### Token source

Optional env var, read only, never written by the tool:

```
TELEGRAM_BOT_TOKENS=harry:12345:AAExample,alerts:67890:BBExample
```

- Entries are comma-separated; each is `nickname:token`, split on the **first** colon so
  the token keeps its own `12345:AAE…` shape.
- Whitespace around entries is stripped; empty entries are ignored.
- A malformed entry raises `ConfigError` naming the entry's position, never its contents.
  Malformed means: no colon, an empty nickname, or a token half that does not start with a
  numeric bot id — the last case is what catches a bare token pasted in with no nickname,
  and it avoids inventing a rule about which characters a nickname may contain.
- Lookup for `--bot X` matches a nickname key or the bot id inside the token's own
  `12345:AA…` prefix; the id wins when a nickname disagrees with it, because a nickname is
  a label a human typed and the prefix is the bot the token actually opens. A `@username`
  matches only when that username was also used as the nickname — the token map holds no
  usernames, so there is nothing else to match it against. A matched token replaces the
  reference with its own bot id, which is what makes a nickname resolvable at all.
  `config.resolve_bot_token` owns that whole policy; `cli.py` does not re-derive it.
- After the bot resolves, the edit is refused unless the token's own bot id equals the
  resolved bot's id. Without that check, a token filed under a nickname that names a
  *different* bot could write to the bot the nickname named while the result JSON reported
  the intended one. The refusal compares ids only and never prints any part of a token.
- Loaded from the same places as the API credentials: shell env, `./.env`,
  `~/.telegram-tools/.env`.
- Absent or non-matching token is not an error until a Rail 2 field is actually
  requested; then the tool exits 2 with: which field needs it, and the env var format.

## Field naming

Telegram's three text fields have names that mislead. The CLI flags and help text use
plain descriptions, and `--help` states the mapping:

| Flag | MTProto field | Bot API equivalent | Where the user sees it |
| --- | --- | --- | --- |
| `--name` | `name` | `setMyName` | The bot's display name, in chat lists and headers |
| `--bio` | `about` | `setMyShortDescription` | The line under the bot's profile |
| `--description` | `description` | `setMyDescription` | The "what can this bot do?" screen before Start |

## CLI surface

```
telegram-tools bots                                  # list owned bots: ID, @username, name
telegram-tools bots --json bots.json                 # same, as JSON
telegram-tools bots --bot @harrybot                  # one bot, full profile
telegram-tools bots --bot @harrybot --name "Harry" --bio "..." --description "..."
telegram-tools bots --bot @harrybot --commands cmds.json
telegram-tools bots --bot @harrybot --clear-commands
telegram-tools bots --bot @harrybot --photo face.png
telegram-tools bots --bot @harrybot --remove-photo
telegram-tools bots --bot @harrybot --group-rights delete_messages,ban_users
telegram-tools bots --bot @harrybot --channel-rights none
telegram-tools bots --bot @harrybot --name "Harry" --yes    # skip the confirm prompt
```

- `--bot` accepts a nickname from `TELEGRAM_BOT_TOKENS`, `@username`, or a numeric ID.
  Resolution checks the `getAdminedBots` list first; a match there proves ownership and
  supplies the `InputUser` directly. No match falls back to `resolver.resolve_chat`, and
  the bot is then shown read-only with an explicit "not owned by you" note.
- No `--bot` and no edit flags → the list. `--bot` with no edit flags → show. `--bot`
  plus any edit flag → edit. Edit flags without `--bot` is an argparse error.
- `--json PATH` applies to list, show, and the edit result, matching `discover --json`. A
  flag that takes a path always writes that path; it is never silently ignored.
- Rights values are comma-separated `ChatAdminRights` field names, taken from the
  installed Telethon's constructor signature rather than a hardcoded list, so the set
  cannot drift (`change_info`, `post_messages`, `edit_messages`, `delete_messages`,
  `ban_users`, `invite_users`, `pin_messages`, `add_admins`, `anonymous`, `manage_call`,
  `manage_topics`, `post_stories`, `edit_stories`, `delete_stories`,
  `manage_direct_messages`, `manage_ranks`, `other` in 1.44). `none` clears every right.
  An unknown name is a `ValueError` listing the valid names.
- Telegram sets `other` on any non-empty admin-rights set by itself — confirmed live, see
  "Live verification" below — so it is not a right a user chooses. `parse_rights` still
  accepts it as an input name, matching every other name `right_names()` lists, but
  `rights_to_names` drops it before it reaches either the profile display or the
  edit-plan comparison. Without that, re-requesting rights Telegram had already applied
  read back as a change (`… → …, other`) and sent a pointless write.
- Commands file is JSON: `[{"command": "start", "description": "Start the bot"}, …]`.
  Validation before any network call: list of objects, both keys present and non-empty,
  command lowercase `[a-z0-9_]{1,32}`, description 1–256 chars, no duplicates, max 100
  entries. Commands are set for `BotCommandScopeDefault` with `lang_code=""`.

## Edit behaviour

Edits are reversible, so they get a diff and a `y/N` prompt — not the `--execute` plus
typed `DELETE` ceremony `clear-messages` uses for destruction.

1. Read the current profile.
2. Drop no-op edits (new value equals current) and say so.
3. Print one line naming the bot — `Editing @harrybot (12345)`, or `Editing bot 12345`
   when it has no username — on every run with a non-empty plan, `--yes` included.
   `--yes` is the one mode with no diff to catch a mistyped token nickname pointed at the
   wrong bot, so it is also the one mode that must not skip the name.
4. Unless `--yes`: print `field: old → new` per remaining change (long text truncated for
   display, full value still sent), then prompt `Apply these changes? [y/N]`. A `y`
   applies. Anything else cancels and exits 1 with `cancelled`; an empty answer first
   prints `No answer read - cancelled.`, so a stray newline left in the terminal buffer
   cannot read as a typed `y` that got silently ignored. `--yes` skips this whole step.
5. Apply — Rail 1 first, then Rail 2 if a token is needed and present.
6. Print a JSON result: `{"bot_id":…, "username":…, "applied":[…], "skipped":[…],
   "cancelled": false}`.

Paths are checked before anything is sent: a missing `--commands` or `--photo` file exits
2 with a usage error rather than a traceback, and never after a partial write.

If a later write fails after an earlier one succeeded, the tool reports which fields
applied before the error and exits non-zero. No rollback is attempted; the diff plus the
`applied` list is enough to redo it by hand.

For that report to be true, the `applied` list must be **owned by the caller and appended
to as each field lands** — not returned from the apply functions. A returned list is lost
with the frame when the call raises, which would print `applied: []` after edits had
already reached Telegram. A report that understates what changed is worse than no report,
because its only purpose is recovery after a failure.

## Modules

New:

- `src/telegram_tools/bots.py` — Rail 1. `list_admined_bots(client)`,
  `get_bot_profile(client, input_user)`, `apply_owner_edits(client, input_user, edits)`,
  plus pure helpers `parse_rights`, `parse_commands_file`, `build_edit_plan`,
  `format_bot_table`, `format_bot_profile`. Follows `discovery.py`: `getattr` defaults
  over TL type checks, so tests can use `SimpleNamespace`. Telethon's own
  `telethon.tl.functions.bots` is imported under an alias inside this file so the two
  names never read as the same thing.
- `src/telegram_tools/bot_session.py` — Rail 2. `bot_client(config, token)` async context
  manager over `TelegramClient(MemorySession(), …).start(bot_token=…)`, plus
  `set_commands`, `clear_commands`, `set_default_rights`, `remove_photo`.

Changed:

- `models.py` — `BotInfo` (id, username, name, bio, description, is_owned, commands,
  group_rights, channel_rights, has_photo) and `BotCommandInfo` (command, description),
  each with `to_dict()`, matching `ChatInfo`'s shape.
- `config.py` — `parse_bot_tokens(raw)` pure function plus a `bot_tokens: dict[str, str]`
  field on `Config`, and `lookup_bot_token` / `resolve_bot_token`, which own the entire
  nickname-or-bot-id lookup so no caller reimplements it. `bot_tokens` and `api_hash` are
  `field(repr=False)`, so no generated `repr` can carry them. Never logged.
- `cli.py` — parser, `_run_bots`, dispatch, and interactive menu entries "7. List my
  bots" and "8. Edit a bot".
- `doctor.py` — a line reporting how many bot tokens loaded, and nothing else about them.

## Secrets handling

- Tokens are never written to disk, never printed, never included in `--json` output,
  error messages, or the confirm diff.
- `Config` hides `api_hash` and `bot_tokens` from its generated `repr`; any token-adjacent
  error message refers to a nickname or a bot id only.
- A test asserts that a formatted error for a bad `TELEGRAM_BOT_TOKENS` value contains
  no substring of the token.

## Testing

Mirrors the existing suite: `SimpleNamespace` fakes, no network, no session files.
`tests/test_bots.py` and `tests/test_bot_config.py` cover:

- `parse_bot_tokens`: single entry, several entries, whitespace, token's internal colon
  preserved, malformed entry raises without echoing the token, absent var → `{}`.
- `parse_rights`: names → `ChatAdminRights` flags, `none` clears, unknown name raises,
  `other` accepted like any other name.
- `rights_to_names`: drops `other` while keeping the rights beside it; a `build_edit_plan`
  regression pins the case that mattered live — a current/requested pair that disagrees
  only on `other` produces an empty plan.
- `parse_commands_file`: valid file, bad shape, bad command pattern, duplicate command,
  over-length description, over-100 entries.
- `build_edit_plan`: no-op edits dropped, mixed rails split correctly, Rail 2 fields
  without a token produce the explanatory error.
- `format_bot_table` / `format_bot_profile`: expected columns and the "not owned by you"
  marker.
- Confirm flow: the heading prints on every edit run, `--yes` included, and does not print
  the confirm prompt when `--yes` is set; when it does run, `n` cancels and applies
  nothing, an empty answer also cancels and additionally writes
  `No answer read - cancelled.`, and the heading prints before the diff.
- `resolve_bot_token`: nickname hit, numeric-bot-id hit, and a `@username` that is not a
  nickname falling through to no token. At the CLI: a token whose bot id is not the
  resolved bot's is refused, and a token nicknamed after another bot's username never
  reaches the bot rail.
- Failure reporting: an owner edit that landed before the bot rail raised still shows up
  in `applied` on the way out.
- CLI resolution: edit flags without `--bot` errors; `--bot` alone shows.

## Docs to update in the same change

- `README.md` — the "No sending messages, no bots, no automation loops" bullet is now
  false; rewrite it (the tool still never sends messages or runs loops). Add `bots` to
  the feature list, the usage section, and note that `@username`, token revocation, and
  bot creation remain @BotFather-only.
- `CHANGELOG.md` — 3.1.0 entry.
- `pyproject.toml` — version 3.1.0.
- `docs/telethon-api-notes.md` — the TL methods in the Auth model table, including which
  ones lack a `bot` parameter and why that forces a bot session.
- `.env.example` — commented `TELEGRAM_BOT_TOKENS` line with the format.

## Live verification

Checked against a real account and a real owned bot on 2026-08-14. Confirmed:

1. `bots.getAdminedBots` returns a list of `User` objects for owned bots.
2. `bots.setBotInfo` accepts `bot=` for a bot the session owns, for all three fields.
3. `bots.setBotCommands` / `bots.resetBotCommands` and `bots.setBotGroupDefaultAdminRights`
   apply from a bot-token session opened with `MemorySession`.
4. `lang_code=""` is the correct default for `setBotInfo` / `setBotCommands` — the edits
   showed up in a real Telegram client, not just in the read-back.
5. Telegram sets the `other` admin-rights flag implicitly on any non-empty rights set —
   not a user's choice, and not something any fake had modeled, so offline tests could
   not have caught it. This is what the "Rights values" and "Edit behaviour" sections
   above now describe, and what the three post-live fixes below responded to.

Skipped on purpose — testing either would cost the bot's current photo:

6. `photos.uploadProfilePhoto(bot=…)` sets a bot's photo from the owner session.
7. `photos.deletePhotos` from the bot session removes it, using the photo reference read
   via `users.getFullUser`.

### Post-live fixes (2026-08-14)

Three behaviours this run surfaced, none reachable from a fake:

1. **The `other` right.** `--group-rights delete_messages,ban_users` applied correctly,
   but the read-back showed `delete_messages, ban_users, other`, and re-running the exact
   same command then showed a diff and re-sent it. Fixed by dropping `other` in
   `rights_to_names` (a named constant, `bots.IMPLICIT_OTHER_RIGHT`, not a bare literal);
   `right_names()` and `parse_rights` are unchanged; `other` is still a valid input name.
2. **The confirm heading skipped under `--yes`.** It printed only on the interactive path,
   so `--yes` — the one run with no diff to catch a mistyped token nickname pointed at the
   wrong bot — was also the one run that never named the bot. Now prints on every edit run
   regardless of `--yes`; the diff and the prompt itself stay behind the `--yes` check.
3. **A blank answer read as a silent cancel.** A stray newline left in the terminal buffer
   produced an empty read, which cancelled with no output — indistinguishable from a typed
   `y` being ignored. `confirm_bot_edits` now prints `No answer read - cancelled.` first
   when the answer is empty; a deliberate `n` still cancels with no extra line.
