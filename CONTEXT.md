# telegram-tools — domain context

The terms this codebase uses, and the boundaries they imply.

- **Seam** — `client.py` and the `telethon.tl.functions.*` calls the command
  modules make: the one boundary the SDK is touched at. Everything above it
  works with the plain dataclasses in `models.py`. Tests mock exactly the
  client object those calls are made on, never Telethon's internals.
- **Session** — the Telethon login at `~/.telegram-tools/telegram-tools.session`
  (`TELEGRAM_TOOLS_SESSION` overrides it). One session file is one connection:
  a second client opening it raises SQLite's "database is locked", which
  `start_client` turns into a `SessionInUseError` that says a menu is open
  somewhere. That is why the menu passes its own started client into
  `cli.run`.
- **Gate** — the confirmation pattern on every path that writes. `send` =
  full-message preview + `y/N` (`--yes` instead requires the allowlist);
  `create` = preview + `y/N`; `clear-messages` = dry-run default + `--execute`
  + a typed `DELETE`; `delete` = dry-run default + `--execute` + the target's
  **own title** typed back, and no `--yes` at all; bot edits = a diff +
  confirm. The menu builds the same args the flags would and never sets
  `yes`/`execute` itself — it is never a shorter path past a gate.
- **Allowlist** — `TELEGRAM_SEND_ALLOWLIST`: the `chat[:topic]` destinations an
  unattended (`--yes`) send may reach. Unset refuses every one of them; only
  the unattended path consults it, because a human who saw the preview has
  already made the decision the list exists to make for them.
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
  the whole list, so a row never changes number when the page does. `ui.paint`
  recognises exactly that shape and is the menu's only colour boundary — every
  prompt still returns plain strings, and an injected read/write (every test)
  never sees an escape code.
- **Column** — a name padded to a fixed width so the ID beside it lines up
  (`_core/columns.py`). Measured in terminal columns, never in codepoints: an
  emoji draws two, a variation selector draws none and does not widen what it
  follows, each half of a flag draws two. Those numbers came off a real
  terminal; `tests/test_columns.py` carries the fourteen measured shapes.
  `cell` cuts to fit (a picker row must stay one line), `pad` never cuts (a
  tree's reader came for the name).
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
- **Identity** — who a run acts as: platform, mode (`account` today), a label
  screens print, a rid and the profile it came from. Never a credential; the
  label is redacted on the way out, not checked and refused.
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
