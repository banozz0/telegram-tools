# telegram-tools — domain context

The terms this codebase uses, and the boundaries they imply.

- **Seam** — `client.py` and the `telethon.tl.functions.*` calls the command
  modules make: the one boundary the SDK is touched at. Everything above it
  works with the plain dataclasses in `models.py`. Tests mock exactly the
  client object those calls are made on, never Telethon's internals.
- **Session** — the Telethon login a profile owns. One session file is one connection:
  a second client opening it raises SQLite's "database is locked", which
  `start_client` turns into a `SessionInUseError` that says a menu is open
  somewhere. That is why the menu passes its own started client into
  `cli.run`, and why the menu hands the file back (`MenuSession.release`)
  before `auth` opens its own client on it. Telethon appends `.session` to
  whatever path it is handed, so the store keeps `<dir>/session` and the file
  is `<dir>/session.session`; it also creates that file at the process umask
  in its constructor, which is why `create_client` tightens it afterwards
  rather than before.
- **Profile** — a named login: `~/.telegram-tools/profiles/<name>/`, holding
  the session (0600, in a 0700 directory) and a non-secret `profile.json`
  (label, account id, created, last login, proxy name). `--profile` before the
  subcommand picks one, `TELEGRAM_TOOLS_PROFILE` is the default, and the
  default is `default`. A profile may keep its own `.env`, read between the
  working directory's and the tool's.
- **Legacy session** — `~/.telegram-tools/telegram-tools.session`, the file
  every version before profiles wrote. It is the `default` profile *by
  reference*: `profiles.load` resolves to it when `default` has no session of
  its own, nothing moves it, and `TELEGRAM_TOOLS_SESSION` still wins over every
  profile. `auth --migrate` is the only path that moves it, behind a y/N.
- **Acting mode** — what an identity is doing: `account`, signed in as the
  person, or `bot`, signed in with one of that person's bot tokens under
  `--as-bot`. The mode is a field of the identity, printed in the banner and
  carried in every envelope, and the switch is always explicit: without the
  flag nothing is a bot.
- **Bot mode** — `--as-bot NICK` (`cli.run_as_bot`): the nickname resolves
  through `TELEGRAM_BOT_TOKENS` exactly as `bots --bot` does, the token opens a
  `MemorySession` (`bot_session.bot_client`, never on disk), and the run is
  handed that client with a `BotIdentity` already set. It narrows, never
  widens: `BOT_MODE_COMMANDS` is the whole list of what runs (`send`, `create
  topic`), everything else refuses with `IDENTITY_MODE_UNSUPPORTED` before
  config is read, because Telegram marks the dialog list, history, search and
  the rest user-only (Telethon raises `BotMethodInvalidError`). Targets resolve
  by id or `@username` only (`adapters/bot.py`, `resolve_chat_as_bot`), and the
  preflight is the bot's rights, with "not a member" a named refusal rather
  than an unknown. The menu has no bot mode: it is one account session.
- **Via** — the account a bot identity acts through: `identity.via` is its rid,
  and the banner names it after the mode, `bot (via Sven (@sven))`. Read from
  the profile record `auth` wrote (a label and an id) so no account session is
  opened; a profile with no record is asked once through its session.
- **Banner** — the `Acting as: <label> · <mode> · Target: <path> (<ids>)` line
  every screen opens with (`_core.identity.banner`). A command prints it once,
  through the reporter, so `--json` puts it on stderr and the envelope carries
  the same two as fields; a run whose output is a file prints none, because it
  has no screen. In the menu it is injected between a screen's title and its
  rule, and only once something has connected — the root of a fresh run has
  none, which is what keeps a bare `telegram-tools` from needing credentials.
- **Loose mode** — anything under `~/.telegram-tools` readable by group or
  others (`_core.paths.loose_modes`). `doctor` reports it and every command
  that writes refuses until it is fixed; reads are left alone, so `doctor` can
  always say why. Directories are made 0700 from the root down, because
  `mkdir(parents=True)` applies its mode to the leaf only.
- **Refuse, never skip** — the rule a proxy follows. Telethon warns and
  connects directly when `python-socks` is missing, so `proxy.py` checks the
  import itself and refuses: someone who asked to go through a proxy must never
  silently connect from their own address. The same shape governs `auth --qr`
  without the `qr` extra.
- **Gate** — the confirmation pattern on every path that writes. `send` =
  full-message preview + `y/N` (`--yes` instead requires the allowlist);
  `create` = preview + `y/N`; `clear-messages` = dry-run default + `--execute`
  + a typed `DELETE`; `delete` = dry-run default + `--execute` + the target's
  **own title** typed back, and no `--yes` at all; bot edits = a diff +
  confirm; `message delete` = `clear-messages`' gate on a selection, plus the
  count typed above 1000; every other message verb = `send`'s gate, with
  `--yes` bound to the chat the write *lands in*. The menu builds the same args the flags would and never sets
  `yes`/`execute` itself — it is never a shorter path past a gate.
- **Message verb** — one of the fifteen things `message` does to a message
  (`messages.VERBS`). Each is an `Op`: its approval kind, the rights its plan
  states, the mutation op the plan records (one per message), and the heading
  its preview opens with. `read`, `unread`, `bookmark` and `draft` are the
  account's own and refuse under bot mode; the rest are `BOT_VERBS`.
- **Selection** — the messages a bulk verb (`delete`, `forward`, `copy`) acts
  on: `--ids` as typed, or `--from-search`, an archive query answered from the
  archive's scopes for that chat. Always resolved to ids before anything is
  fetched, and every id is in the plan and the preview.
- **Bulk bound** — section 7's rule, `messages.bound_selection`: more than
  `--limit` (200) refuses with `BULK_LIMIT` rather than cutting the selection,
  and more than 1000 needs `--i-know` *and* the count typed after `DELETE`.
  Refused by count, before a single message is fetched.
- **Brief** — one message as a preview shows it: id, date, sender, first line,
  a `[media]` mark, and whether it is the identity's own (`out`, else the
  sender id). Own-ness decides whether `edit` and `delete` add a right.
- **Copy** — `message copy` re-posts a message's text as the identity and, for
  an attachment, a link to the original (`messages.message_link`). Never the
  bytes: downloads belong to the review queue and its quarantine.
- **Mentions line** — `Mentions @harry, @all (everyone in the chat)`, above the
  body of every posting preview (`mentions.py`). Telegram has no mass-mention
  control beyond the text, so naming them is the whole control.
- **Allowlist** — `TELEGRAM_SEND_ALLOWLIST`: the `chat[:topic]` destinations an
  unattended (`--yes`) send may reach. Unset refuses every one of them; only
  the unattended path consults it, because a human who saw the preview has
  already made the decision the list exists to make for them.
- **Archive** — `~/.telegram-tools/archive.sqlite`, the shared store
  (`_core/archive.py`, schema `cli-tools/archive/1`) holding what this
  account can read, opened through `archive.open_archive`. The file is created
  0600 *before* SQLite sees it, because SQLite makes a database at the umask
  and hands the `-wal` and `-shm` files the database's mode. Every row records
  the identity that synced it. `archive sync` is the one archive command that
  connects; status, search, export, retention and forget read the file, and
  the identity they act as comes from the profile record.
- **Scope** — one syncable unit, named by its rid: a chat (`tg:chat:ID`) or a
  forum topic (`tg:topic:ID:TOPIC`). A forum group is its topics and never
  itself, because every message in a forum belongs to one topic and a
  chat-level walk would archive each row twice under two rids. The listing is
  `adapters/archive.py`, this tool's `ArchiveSource`.
- **Cursor** — what the store hands back to resume a scope. Telegram serves
  history newest first, so this tool's cursor is a state, `top:low:open|done`:
  every id in `[low, top]` is archived, and `done` means everything below `low`
  is too. A run first asks for what arrived above `top` (`min_id`), then
  continues below `low` (`offset_id`) when the walk never finished; one record
  of lookahead is what lets the last record say `done`; a walk cut by a
  `--since` floor stays `open`, so a later plain sync finishes it. A cursor
  this adapter did not write reads as none, which is a full walk. The source
  also keeps `seen`, the ids each scope served, because Telegram's history
  never says what was deleted: `archive sync --full` marks what a full walk
  did not see (`archive.mark_missing_deleted`).
- **Coverage** — the row per scope saying what the identity could see of it,
  with a named reason when it could not: `no_access` (a `--scope` the account
  cannot resolve, or a forum whose topics it cannot list), `unsupported_kind`
  (a rid that is not a place messages live), `rate_limited` and the rest of
  the shared vocabulary, and `bot_live_only` for a bot identity, which lists
  what its runner will fill later and reads no history now.
- **Exports directory** — `~/.telegram-tools/exports/`, 0700, where a relative
  `archive export --output` name lands (`_core/export.py` resolves it). The
  live `search --output` keeps its old behaviour, relative to the working
  directory, because scripts already pass it paths.
- **Typed name** — the gate `archive retention` and `archive forget` share
  with `delete`: a dry-run by default, and with `--execute` the scope's exact
  title typed back, no `--yes`. The re-derivation after the gate compares the
  scope row's title, not the plan id, because a retention plan's cutoff moves
  with the clock.
- **Chat reference** — what `--chat` accepts: a numeric ID, a `@username` or a
  link. `resolve_chat` (`resolver.py`) is the one place it becomes an entity;
  a numeric reference is looked for in the dialog list first, because Telegram
  will not resolve an ID the account has no access hash for.
- **Topic** — a forum thread. Its ID is the ID of the service message that
  opened it, which is why Telegram has no delete-topic method: a client removes
  a topic by deleting every message in it, that one included
  (`messages.deleteTopicHistory`). The **General** topic has no such message
  and so cannot be deleted at all — `clear-messages` empties it instead.
- **Topic title vs display title** — `title` is the plain text Telegram stores
  and everything keys on it: `--topic` matching, both export formats,
  `discover --json`. The emoji in front of it is a separate custom-emoji
  document resolved one page at a time (`resolve_icon_emoji`), and it lives on
  `display_title`, which is screens only.
- **Parity rule** — `delete` removes exactly what `create` makes: supergroups
  (with or without topics), broadcast channels and forum topics. A **basic
  group** is refused with that reason stated, because `create` makes
  supergroups and cannot make a basic group back. The kind you name is checked
  against what Telegram says the chat is before anything is asked.
- **Record** — the plain dict a message becomes (`records.py`): what `search`
  prints and what both export formats write. `has_media` keeps an
  attachment-only message from reading as empty.
- **Owned bot** — a bot the account created. `bots` edits name, bio and
  description through the account (BotFather's own API), and commands, photo
  and default admin rights through **that bot's** token from
  `TELEGRAM_BOT_TOKENS`. A nickname is a label a human typed and can name the
  wrong bot, so a token is only used once its own bot ID matches the resolved
  profile.
- **Screen** — what `prompts._screen` renders: a title over a rule, numbered
  rows, an optional `n`/`p` paging line, then `0`. Items are numbered across
  the whole list, so a row never changes number when the page does. That
  closing `0` is also how a screen is told apart from a command's own output
  when the banner is injected. `ui.paint` recognises exactly that shape — the
  banner line included — and is the menu's only colour boundary: every
  prompt still returns plain strings, and an injected read/write (every test)
  never sees an escape code.
- **Column** — a name padded to a fixed width so the ID beside it lines up
  (`_core/columns.py`). Measured in terminal columns, never in codepoints: an
  emoji draws two, a variation selector draws none and does not widen what it
  follows, each half of a flag draws two. Those numbers came off a real
  terminal; `tests/test_columns.py` carries the fourteen measured shapes.
  `cell` cuts to fit (a picker row must stay one line), `pad` never cuts (a
  tree's reader came for the name).
- **Reserved row** — a root-menu number that names a capability a later
  version brings (`Manage`). It is on the menu now so that every row above and
  below it keeps its number when that version lands; picking it says which
  version fills it and comes straight back. `Watch` held its number the same
  way until the review queue filled it; rules and the runner join it there.
- **Candidate** — one link or one file a sync saw, as the review queue holds
  it: a `manifests` row of kind `link` (the URL exactly as the message wrote
  it) or `media` (Telegram's own file key, `document:ID` or `photo:ID`, as the
  locator, with the type, size and filename Telegram claimed as display
  metadata). Noted by `adapters/media.candidates_of` while the archive source
  walks, enqueued by `archive sync` after the store commits, never fetched by
  either. The same link in the same message is the same row on every sync.
- **Review queue** — `review <verb>` over the shared queue (`_core/review.py`):
  the seven states and the two human moves, `queued → approved` and
  `quarantined → accepted`, each behind a `prompt_y` answered at a terminal.
  `review.py` is this tool's side: the pipeline with this tool's fetchers, the
  screens, the plans. `list` and `status` are queries; `approve` and `retry`
  are the only commands here that contact a host, and only after the answer.
- **Terminal gate** — the review queue's stricter `prompt_y`: both human moves
  refuse without a tty in *either* mode (`cli._review_io`, `APPROVAL_REQUIRED`,
  exit 3), where every other gate checks the tty only under `--json`. A `y`
  piped into stdin is not a person, and `Approval.interactive` carries the
  tty check rather than the answer.
- **Media fetcher** — `adapters/media.TelegramMediaFetcher`, this tool's
  `MediaFetcher`: re-reads the source message through the login, refuses one
  that no longer carries the manifest's file key, and streams `iter_download`
  from the byte offset the pipeline hands it. The pipeline writes the chunks,
  counts them, runs the checks and takes the sha256 over the whole file; the
  fetcher never touches the disk and never decides a verdict. A file never
  goes through a URL a message supplied.
- **Quarantine** — `~/.telegram-tools/quarantine/<download-id>/` (0700):
  `payload` (0600) and `manifest.json`, held until a person accepts (renamed
  into `media/<sha2>/<sha256>`) or rejects (deleted). Both directories are
  tightened before a fetch, because `mkdir(parents=True)` makes the parent at
  the umask.
- **Verdict** — what a quarantined file carries: `BLOCKED` (a built-in check
  failed, named), `CLEAN` or `INFECTED` (the scanner said so), or `UNSCANNED`
  (no scanner gave a word, and the reason says which binaries were looked for).
  `accept` shows it before asking and refuses `BLOCKED` and `INFECTED` with
  `UNSAFE_BLOCKED`; `doctor` names the scanner it found or looked for.
- **Trail** — the breadcrumb a screen's title carries (`Main › Clear › Ops`),
  built by `ui.crumb`. A flow passes its own trail down; a screen never invents
  one.
- **After-run row** — the next step a flow owns once an action has run
  (`menu.py`): `AGAIN` re-runs inside `_act`, `STAY` is handed back for the
  flow to answer (Tweak it, Create another, Edit more), `MENU`/`EXIT` leave it.
- **Runner contract** — the menu calls `cli.run(args, client=..., config=...)`
  with namespaces shaped exactly like parsed flags; a passed-in client is owned
  by the caller and never closed by `run`. Its exit code is what titles the
  after-run screen: 0 is Done, 1 (a declined confirm) is Not done, and a caught
  error is Failed.
- **Envelope** — the one object `--json` puts on stdout (`cli-tools/envelope/1`,
  built in `_core/contract.py`). Schema, tool, version, command, echoed args,
  identity, target, status, result, plan, evidence, warnings, error, meta —
  always those keys, always in that order. `result` is the command's own
  payload and keeps every key it printed before, so the schema is additive
  rather than a second output format.
- **Reporter** — `envelope.py`: where one run's words, payload, target, plan,
  evidence and audit line go. Human mode prints and returns; machine mode
  collects and emits once. Commands talk to it instead of to `print`, so the
  mode is decided once in `main` and the menu — which passes none — keeps the
  human default.
- **rid** — the stable string key for a Telegram object: `tg:chat:-100…`,
  `tg:topic:-100…:141`, `tg:user:…`, `tg:bot:…`. Two segments for a thing that
  lives inside a container. Everything machine-readable names a target by rid.
- **Identity** — who a run acts as: platform, mode (`account` or `bot`), a label
  screens print, a rid and the profile it came from, plus `via` when a bot acts
  through an account. Never a credential; the
  label is redacted on the way out, not checked and refused. A `@username` is
  the label whenever there is one; with none, section 5.1 asks for the name plus
  the **last two digits** of the account's number, because a display name is
  chosen by its owner and need not distinguish anything — Sven's own reads `--`,
  which named nothing until the digits were added. Two digits, never more, and
  the full number does not leave `phone_tail`.
- **Plan** — what a write is about to do, built before anything is asked: the
  identity, the resolved targets, the mutations, the approval kind and the
  preflight, hashed into a `plan_id`. A dry-run prints it and the real run
  re-derives it.
- **Approval kind** — which gate a write needs, one of four: `prompt_y`,
  `typed_delete` (messages inside a container that survives), `typed_name` (the
  container itself), `yes_allowlist` (the unattended path, where an allowlist
  exists — only `send` has one).
- **Preflight** — the rights a plan needs against the rights the account holds.
  The distinction that matters is between a right Telegram reports as absent,
  which refuses the write by name, and one it will not answer for at all — a
  private chat has no participant permissions — which is named as unconfirmed
  and lets the write through, because that is what has always happened.
- **Drift** — the re-derivation between the answered gate and the call. The
  target is resolved again and compared with the one that was shown; a
  difference is `PLAN_DRIFT` and nothing is sent or deleted.
- **Readback** — the state fetched after a write and reported as `evidence`.
  One that cannot be fetched reads `unverified: <reason>` and is never
  presented as verified.
- **Audit line** — one redacted JSON line per *executed* write in
  `~/.telegram-tools/audit.jsonl`, from the menu exactly as from a flag. Dry
  runs and cancellations leave nothing.
- **Redaction** — the single pass every envelope, audit line and error message
  goes through before it is written. Shapes, not vendors: bot tokens, the API
  hash, phone numbers, session paths.
- **Shared copy** — `src/telegram_tools/_core/`: a byte-identical copy of a
  tree maintained outside this repository, at the tag recorded in
  `_core/VERSION`. It is never edited here — `tests/test_core_copy.py`
  recomputes its hash and fails on any local change, and
  `scripts/sync-core.sh` is how a new tag arrives. It names no platform, so it
  reads the same under any package that carries it.
