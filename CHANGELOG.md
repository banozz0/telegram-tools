# Changelog

All notable changes to this project will be documented here.

This project follows a practical changelog style: user-visible changes, safety changes, and release notes belong here; active task tracking belongs outside the repo.

## 3.15.0 - 2026-09-06

- **Admins, members, join requests, invite links and settings.** Five command groups for running a group or channel from the terminal, every write behind the same four steps as the rest of the tool. `admin list` shows the creator and every admin with their rights and ranks; `admin promote --user U --rights r,r [--rank]` makes someone an admin with exactly those rights, `admin rights` sets an existing admin's to exactly the ones named, and `admin demote` takes every right away. `member list` (`--query`, `--limit`, `--banned`), `member ban`, `member unban`, `member mute --until`, `member unmute` and `member restrict --rights --until`. `join-requests list`, `approve` and `decline` for a chat that needs approval to join. `invite list`, `invite create` (`--title`, `--expires`, `--usage-limit`, `--request-needed`) and `invite revoke --link`. `settings show` and `settings set --slow-mode SECONDS`.
- **Two of them are `delete`'s gate.** `member ban` and `admin demote` remove someone's membership or someone's rights, so they dry-run by default, take `--execute`, ask for the person's exact label (`@username`, or their name when they have none) at a terminal, refuse without one in either mode (`APPROVAL_REQUIRED`, exit 3), and have no `--yes`. Every other write asks `y/N` and has no `--yes` either: no allowlist exists for an admin action.
- **A missing right is named before anything is sent**, and so is the hierarchy: an admin can give only rights it holds and edit only an admin holding no more than it does; the creator can do anything. A grant the account cannot make, an edit of the creator, or an edit of an admin with more rights refuses with `HIERARCHY_DENIED` naming both rights sets, and banning an admin refuses the same way and names `admin demote`. The preflight vocabulary gains `add_admins`.
- **`--reason` on a ban is a local record.** Telegram stores no reason beside a ban, so the reason goes into the plan, the readback and the audit line in `~/.telegram-tools/audit.jsonl`, and nowhere on Telegram. Mutes and restrictions are bounded: `--until` takes a duration (`30m`, `2h`, `7d`, `1w`) or an ISO date or time, at least a minute ahead and at most a year, because Telegram reads anything longer as forever; without it the command refuses.
- **Invite links are shown where they were asked for and nowhere else.** `invite list` and `invite create` print them and carry them in `result`; every other screen, envelope field and audit line goes through the shared redaction, which blanks a link, including the one `invite revoke` was given.
- **Bot mode.** All five groups run under `--as-bot` where the bot is an admin of the chat; a bot that is not refuses with `IDENTITY_MODE_UNSUPPORTED` before any preview, and a bot admin without the specific right is refused by name like the account.
- **Menu.** Root row 6, `Manage (admins, members, invites, settings)`, is no longer reserved: it opens the five groups, each verb a form of its flags, with ban and demote dry-running first and the exact label typed at the CLI's own prompt. The root screen is unchanged, so no transcript is re-recorded.
- The tool's own directory is now made `0700` when a first write creates it; it was `0755`, and a second write on a fresh machine refused over it.

## 3.14.0 - 2026-09-06

- **Chat blueprints.** `telegram-tools structure export --chat C --output hermes.json` writes a chat's structure as one JSON file: its kind (supergroup, forum or channel), title, description, topics (title and icon), default rights, slow mode and whether joining needs approval — and prints, every time, what a blueprint is not: no members, no admins, no messages, no history, no invite links, no linked discussion group. Those are listed in the file's own `never_transferred`, generated from the same allowlist the exporter uses, so a field outside it cannot reach a blueprint. Topics are listed by title, because Telegram gives them no order a client can set. `structure diff --blueprint F --chat C` says what the chat would need to match the file and changes nothing. `structure apply --blueprint F --chat C` dry-runs by default, listing every step in order; `--execute` asks for the chat's exact title (there is no `--yes`, and no terminal means `APPROVAL_REQUIRED`, exit 3, in either mode), then makes the missing topics and settings one step at a time, never deleting a topic or a setting the chat has beyond the blueprint — those are reported as left alone. `--create` instead makes a new chat of the blueprint's kind and title first, through the same call `create` uses, and applies the rest to it. A step Telegram refuses stops the apply with what was made kept, so a rerun `diff` shows the remainder; afterwards the chat is read back and compared, and anything still pending is `partial`. Every accepted step leaves its own audit line, and `structure remap --apply-id ID` prints the source-to-target id table the apply wrote into the archive, offline. A basic group has no blueprint, for the reason `delete` refuses one: `create` makes supergroups.
- **Menu.** Root row 4 now reads `Build (create, delete, structure)` and opens six rows: create, delete, then export a blueprint, diff one against a chat, apply one (the dry-run runs first, and the exact title is typed at the CLI's own prompt) and show an apply's remap table. The root label changed, so the transcript is re-recorded.
- The preflight vocabulary gains `manage_topics`, the right an apply needs to make or edit a topic; `change_info` covers the chat's own settings.

## 3.13.0 - 2026-09-06

- **The review queue.** `archive sync` now notes every link and every photo or document it walks past as a candidate — the URL exactly as the message wrote it, a file by Telegram's own key with the type, size and name Telegram claimed — and fetches none of them; it says how many it noted, and `result.manifests` carries the count. One command group, `telegram-tools review <verb>`, is the only place anything is ever downloaded: `list` (`--kind`, `--state`) and `status` read the queue and ask nothing of any host; `approve` (`--ids`, or a pick at the terminal) shows the candidates, asks `y/N`, then fetches into `~/.telegram-tools/quarantine/<download-id>/` through the eleven built-in checks and a local ClamAV when one is on PATH; `accept` shows the verdict, asks `y/N`, and moves the file into `~/.telegram-tools/media/<sha2>/<sha256>`; `reject` asks `y/N` and deletes the quarantined bytes; `retry` continues a failed download from the byte it stopped at, with no new approval, to the same sha256 an uninterrupted run gives. Neither gate has a `--yes`, and both refuse without a terminal in either mode with `APPROVAL_REQUIRED` and exit 3: a `y` piped into stdin is not a person. `BLOCKED` and `INFECTED` can never be accepted (`UNSAFE_BLOCKED`); no scanner means `UNSCANNED`, printed as such. A link's redirects are walked one `HEAD` at a time, after the `y/N` and never before; a file is read through your own login from the message it came from, never through a URL. Every approve, accept, reject and retry leaves an audit line, and every one refuses under `--as-bot`.
- **`doctor`** reports the scanner binary it found or the two it looked for (`clamdscan`, `clamscan`), and what quarantine holds against its budget (`quarantine_max_bytes`, 1 GiB). `config.json` gains `download_max_bytes` (256 MiB) and the archive-expansion caps beside the budgets.
- **Menu.** Root row 7, Watch, opens a group of six: the queue with kind and state filters, approve (the pick and the `y/N` happen in the CLI on the same terminal), accept, reject and retry over the queue's own rows, and status. The root screen is unchanged, so no transcript is re-recorded. The menu never answers a gate for you.
- The shared copy under `src/telegram_tools/_core/` moves to workshop tag v0.8 (the queue, the download pipeline and the scanner adapter, plus the blueprint, rules and runner modules later versions use). The archive migrates forward on first open.

## 3.12.0 - 2026-09-06

- **Message tools.** One command group, `telegram-tools message <verb>`, for what you do to a message once it exists: `reply --to MSG --text`, `edit --id --text`, `delete`, `forward --to CHAT`, `copy --to CHAT`, `react --id --emoji`, `unreact`, `pin`, `unpin`, `poll --question --option …`, `typing --seconds`, `read`, `unread`, `bookmark --id` and `draft --text`. Every one takes `--chat`, resolves it, fetches the message it is about to act on, and shows both — the chat, and the message's id, date, sender and first line — before asking. Nothing here is a capability the app lacks; it is the app's message menu from a terminal, and from an agent.

- **Deleting messages is `clear-messages`' gate, on a selection.** `message delete --chat C --ids 10,11` (repeatable, comma-separated) or `--from-search "deploy AND red"` — an archive query, so the archive has to hold the chat — lists every id it would remove and stops. `--execute` then asks you to type `DELETE`; there is no `--yes`. A selection larger than `--limit` (200) is refused with `BULK_LIMIT` rather than cut, and above 1000 messages `--limit` alone will not do: `--i-know` is needed, and the exact count is typed at the prompt after `DELETE`. Deleting someone else's message needs the delete-messages right, named before the preview; your own needs none.

- **`forward` keeps the header, `copy` re-posts the text.** `copy` posts a message's text into `--to` as you, and for an attachment appends a link to the original message instead — it never fetches the bytes, because downloads are a later version's job and would need its quarantine. Both take `--to-topic` to land in a topic, and both are bounded like `delete`.

- **Every other verb is `send`'s gate.** A preview and a `y/N`; `--yes` skips it only when the chat the write lands in — `--to` for `forward` and `copy`, `--chat` otherwise — is in `TELEGRAM_SEND_ALLOWLIST`. Each executed verb reads back what it did (the reaction on the message, the pinned state, the new text, the unread count, the draft) and leaves one line in `~/.telegram-tools/audit.jsonl`; `typing` alone reads back `unverified`, because a typing status leaves nothing to fetch.

- **`bookmark` forwards to Saved Messages and notes it in the archive** — a `bookmarks` row naming the chat, the message and an optional `--label`, so a later search can find what you kept. `read`, `unread`, `bookmark` and `draft` are the account's own and refuse under `--as-bot` with `IDENTITY_MODE_UNSUPPORTED`; the other eleven run as the bot where it is a member and holds the right.

- **`send --reply-to MSG`** posts as a reply, and the preview says so. The preview — `send`'s and every posting verb's — now also names every `@mention` in the text on its own `Mentions` line, and marks `@all`, `@everyone`, `@channel` and `@here` as everyone in the chat, because Telegram has no other mass-mention control.

- **The menu's Write row** opens a screen of sixteen: Send, then one row per verb. Each picks the chat from your live chats, stages the verb's fields, and runs behind the same prompt the flags show; Delete's *Delete for real* toggle is the `--execute`, and `DELETE` is still typed at the CLI's own prompt. Send's form gained a *Reply to* row. The root's nine rows keep their numbers; row 3 now reads `Write (send, reply, message tools)`.

## 3.11.0 - 2026-09-06

- **A local archive of your chats.** `telegram-tools archive sync` copies what your account can read into `~/.telegram-tools/archive.sqlite` — every chat, and every topic of every forum group — and resumes where it stopped: a sync killed halfway restarts from the last committed batch and never writes a row twice, and a second run fetches only what arrived since. One progress line per scope, then a coverage table naming every scope it could not read and why (`no_access`, `unsupported_kind`, and `rate_limited` for a scope Telegram held behind a flood wait longer than ten minutes, which is reported failed and marked in coverage rather than waited out). `--scope tg:chat:ID` or `tg:topic:ID:TOPIC` narrows it, `--since` bounds the walk and leaves it open for a later plain sync to finish, and `--full` walks everything from the top again — which is also how a deletion is found: Telegram's history never says what was removed, so a full walk marks every archived row it no longer sees as `deleted_at`, keeping the row and its text. It is read-only against Telegram; flood waits are slept and counted in the envelope's `meta.waited_ms`.

- **Search it offline.** `archive search --query "deploy AND green"` is full-text search over everything synced, ranked by relevance, with the match marked `«like this»` — no connection, no flood wait. `--regex` post-filters, `--scope`, `--identity`, `--from`, `--since` and `--until` narrow it, `--context N` shows the messages around each hit. `archive export --format json|csv|jsonl|markdown|html --output NAME` writes the same rows the search printed, in the same order, all five formats agreeing; a bare name lands in `~/.telegram-tools/exports/`, an absolute path is honoured. The HTML is one self-contained page with no scripts.

- **`search --archive` answers the live command's flags from the archive.** `--chat` (a numeric id or `@username` the archive has synced), `--topic`, `--keyword`, `--from-user`, `--since`, `--until`, `--limit`, `--format` and `--output` all mean what they always did, and nothing connects. It is the documented alias of `archive search` for a script that already speaks `search`.

- **`search --format` gains `jsonl`, `markdown` and `html`.** `json` and `csv` are written byte for byte as before. The two human formats go through the same writers as the archive export, so a live export and an archive export read the same.

- **`archive status`** prints scopes, rows, oldest and newest, size against the 2 GiB `archive_max_bytes` budget in `~/.telegram-tools/config.json` (created with the defaults on first use) and the coverage summary. `doctor` now also says whether your Python's SQLite has FTS5 — the archive is built on it and refuses with `ARCHIVE_UNAVAILABLE` without it — and how full the archive is against each of the three budgets.

- **`archive retention --scope RID --keep 90d|N` and `archive forget --scope RID | --identity ID`** prune and remove, dry-run by default, and execute only with `--execute` plus the scope's exact title typed back — the same gate `delete` has, for the same reason, and with no `--yes` either. Each executed one leaves a line in the audit log.

- **The menu's Read row** now opens a screen of six: the live search, sync, status, search or export, prune and forget. Sync's scope is picked from your live chats and topics (a whole forum group, or one topic in it); the other archive rows pick from what the archive holds. The root's nine rows and their numbers do not move. `--as-bot` refuses every archive command with `IDENTITY_MODE_UNSUPPORTED`: a bot cannot read history.

- **"Main menu" after a job now means the main menu.** From a row under Read, Build or Identity it used to land on that group's screen, one step short; it goes to the root now, and `0` still exits outright.

- **A sync or prune refuses while the tool's own files are readable by others**, like every other write here; the archive and its config are created 0600. `exports/` no longer counts: it is where a file you mean to share lands, nothing secret is ever written there, and a machine that keeps it at 0755 was being refused a sync over it. The menu's date prompts take `DD/MM/YYYY` as well as ISO and show what it became.

## 3.10.0 - 2026-09-06

- **Act as one of your bots, explicitly.** `telegram-tools --as-bot alerts send --chat -100… --text "…"` posts as the bot whose token is stored under the nickname `alerts` in `TELEGRAM_BOT_TOKENS` — the same nicknames `bots --bot` has always used. The token opens an in-memory session and is never written to disk. Every screen names both: `Acting as: @alertsbot · bot (via Sven (@sven)) · Target: …`, and the `--json` envelope's `identity` carries `mode: "bot"`, `id: "tg:bot:…"` and `via: "tg:user:…"` — the account it belongs to. The send preview says `Sending as @alertsbot (via Sven (@sven))`. Without `--as-bot` nothing differs from before.

- **Bot mode narrows; it never widens.** A Telegram bot has no dialog list, no message history, no search and nothing of its own to delete or set up, so `--as-bot` on `discover`, `search`, `bots`, `clear-messages`, `delete`, `create group`, `create channel` or `auth` refuses with `IDENTITY_MODE_UNSUPPORTED` before anything connects, and the hint is the same command without the flag. What runs under it is what a bot is for: `send`, and `create topic` in a group it administers. A bare `telegram-tools --as-bot NICK` gets no menu — the menu is the account's session.

- **A bot only reaches chats it is in.** Targets under bot mode resolve by numeric id or `@username` only (a link is refused, because a bot cannot look one up), and the preflight asks Telegram what rights *the bot* holds there. A bot that is not a member of the chat is refused by name before the preview, with "add the bot to the chat" as the hint. `--yes` is gated by the same `TELEGRAM_SEND_ALLOWLIST` as an account send.

- **The account behind the bot is read from the profile record** that `auth` writes (`profile.json`: a label and an id, nothing secret), so a bot-mode run opens no account session and works while the menu holds that session elsewhere. A profile from before records existed is asked once, through its session, and under `--json` an unauthorised one refuses with `LOGIN_REQUIRED` as any command would.

- **A nickname that names no token refuses with `CONFIG_MISSING`**, and a token whose bot is not the bot Telegram signs in refuses with `IDENTITY_MISMATCH`; neither prints any part of a token.

## 3.9.0 - 2026-09-06

- **Named logins.** `telegram-tools auth` walks you into a login instead of leaving it to whatever the first command happened to trigger: a phone number and the code Telegram sends, or `auth --qr` showing a QR block you scan from a phone that is already signed in (Settings → Devices → Link Desktop Device). Two-step verification is asked for at the terminal and stored nowhere. `auth --logout` ends a session once you type the profile's name back; `telegram-tools profiles` lists what this machine has.

- **More than one account, kept apart.** `--profile NAME` goes before the subcommand — `telegram-tools --profile work discover` — and `TELEGRAM_TOOLS_PROFILE` sets the default. Each profile keeps its own session under `~/.telegram-tools/profiles/<name>/`, 0600 in a 0700 directory, beside a `profile.json` holding a label, the account id, when it was made and last used, and the name of the proxy it goes through. Nothing secret goes in that file, and it never records a phone number.

- **Your existing login keeps working, untouched.** `~/.telegram-tools/telegram-tools.session` is now the `default` profile *by reference*: nothing is moved, renamed or re-created, and `TELEGRAM_TOOLS_SESSION` still wins over every profile. `doctor` mentions that it could move into the profile directory; `auth --migrate` does it, after a y/N, and is the only thing here that moves a session file.

- **Every screen says which account it is about to act as.** `Acting as: Sven (@sven) · account · Target: Agency › 💻 Deploys (-1001234567890)` is the first line of every command and every menu screen below the root, and the same identity and target are fields in the `--json` envelope. An account with no username prints its first name and the last two digits of its number — `-- (…23)` — because a display name is chosen by its owner and is not required to distinguish anything: it can be blank, punctuation, or the same on two accounts. Two digits, never more; the number itself never appears in a label. A run whose output goes to a file — `discover --json out.json`, `search --output`— prints no banner, so its stdout is what it always was.

- **The root menu is regrouped, once.** Nine rows: Find IDs, Read, Write, Build, Clear messages, Manage, Watch, Identity, Check setup. Create and Delete now live under Build, My bots under Identity beside the new profile rows. Rows 6 and 7 name what a later version brings and say so when picked — they hold their numbers now so nothing above or below them has to move again. Every flag still has a row, and the menu is still never a shorter path past a gate.

- **Proxy support.** `TELEGRAM_PROXY=socks5://host:port` (also `socks4://`, `http://`, with optional credentials) is passed to Telethon, per profile through its own `~/.telegram-tools/profiles/<name>/.env` or machine-wide. It needs `pip install 'telegram-tools[proxy]'`; without that library **the command refuses** rather than quietly connecting from your own address, which is what would otherwise happen. `doctor` says so before you run anything.

- **`auth --qr` needs `pip install 'telegram-tools[qr]'`** to draw the block. Without it the command refuses and names the install. Both extras are optional: a plain `pip install telegram-tools` is still Telethon and python-dotenv.

- **The tool is stricter about its own files.** Sessions are written 0600 inside 0700 directories, and every command that writes something refuses while anything under `~/.telegram-tools` is readable by group or others — `doctor` names the files and the `chmod` that fixes them. Reads still work, so you can always run `doctor` and read why.

- **An agent that is not logged in gets an answer, not a hang.** Under `--json`, an unauthorised session refuses with `LOGIN_REQUIRED` and the exact `auth` command in the hint, instead of blocking on a phone-number prompt nobody can answer. Human mode still offers that prompt exactly as before.

## 3.8.0 - 2026-09-04

- **Machine-readable output, on every command.** `telegram-tools --json <command>` prints exactly one object on stdout — an *envelope* — and nothing else. It carries the command, the flags it was given, who the run acted as, what it acted on, a status, the command's own payload under `result`, any warnings, an error when there is one, and how long it took. Every key the old `--json PATH` files wrote is still there, inside `result`, under the same name. `--jsonl` streams one line per record first — a chat from `discover`, a message from `search` — and closes with the same envelope marked `"kind": "envelope"`. The flags go before the subcommand, where a global flag belongs: `telegram-tools --json discover`, not `discover --json`.

- **Human output did not change.** Not one table, preview, prompt, warning banner or exit code. Without the flag this is the tool it was; with it, everything a person would read moves to stderr so stdout stays parseable, and a prompt reads from the terminal rather than from the pipe.

- **Errors an agent can key on.** A refusal now carries a stable code — `NOT_ALLOWLISTED`, `TARGET_NOT_FOUND`, `TARGET_KIND_MISMATCH`, `PERMISSION_DENIED`, `PLAN_DRIFT`, `SESSION_IN_USE`, `CONFIG_MISSING`, `RATE_LIMITED` and the rest — plus a `hint` that is the exact command or edit that would fix it. Scripts that read those never have to match on English again.

- **Exit codes mean what they meant.** 0 done, 1 not done, 2 refused, 130 interrupted — unchanged, `doctor`'s 1 for a failed check included. One code is new: **3**, "this asks for confirmation and there is no terminal to ask on", reachable only under `--json`, with the human command in the hint. An agent that hits a gate now gets an answer instead of a hang.

- **Every write says what it is about to do, checks it may, and reads back what it did.** Before anything is sent, created, cleared or deleted, the tool builds a *plan* — the account, the resolved target, the change, the gate it needs — and asks Telegram what rights the account actually holds in that chat. A right Telegram says is absent refuses the write by name before a single call goes out. A right Telegram will not answer for — a private chat has no permissions to report — is named as unconfirmed and the write proceeds, because that is what it has always done.

- **The window between the preview and the deletion is closed.** After the gate is answered and before the call goes out, the target is resolved again and compared with the one that was shown. A group renamed, replaced or gone in between refuses with `PLAN_DRIFT` rather than deleting whatever now holds that name.

- **A local record of every executed write.** One redacted JSON line per write to `~/.telegram-tools/audit.jsonl` (0600, append-only, rotated at 10 MiB): when, as whom, which command, which target, which plan, which gate, and the readback. Menu runs are recorded exactly like flag runs. Nothing leaves the machine, and no token, phone number, api hash or session path can reach the file — one redaction pass covers the envelope, the audit line and every error before either is written.

- **`--json` on `discover` and `bots` now takes the path *or* nothing.** `discover --json out.json` writes the same file it always wrote; a bare `discover --json` means the envelope. No other flag, choice or help line moved, and a test now holds every `--help` text against a captured copy so none can.

- **`--version` said 3.6.0 for two releases.** The package's `__version__` had drifted from `pyproject.toml` and nothing read it, so nothing noticed. It is 3.8.0 in both now, and a test keeps them equal — the envelope, the plan id and every audit line carry that number, so it has to be the one that shipped.

- Internally, the envelope, the error codes, the exit table, the redaction rules and the column measuring now live in `src/telegram_tools/_core/`, a copy of shared code maintained outside this repository at a recorded tag. It ships inside the package like any other module — there is no new dependency, and `pip install telegram-tools` still pulls Telethon and python-dotenv and nothing else.

## 3.7.2 - 2026-09-01

- JSON output prints the emoji instead of escaping it. `json.dumps` escapes non-ASCII by default, so a topic named `💻 Dobby` came back as `"\ud83d\udcbb Dobby"` while every other line of the same output — the pickers, the discover table, the CSV export — drew the emoji. Same title, two spellings, one terminal. Both are valid JSON and any parser read the old form fine; only one of them is readable by a person, which is who reads the menu. Found in the sibling's delete try-it and fixed the same way here.

- The setting lives in one place rather than on six calls: a `json_text()` helper in `exporters.py` is now the only way this tool emits JSON, so the next command added cannot quietly reintroduce the escaping.

- JSON and CSV exports are written as UTF-8 explicitly. Raw non-ASCII through Python's default would use the machine's locale encoding and could fail where the old ASCII-only output never could — and the CSV path already wrote emoji raw at locale encoding, so that latent case is closed too. This matters more here than in the sibling: topic titles carry their icon emoji as of 3.6.0, so nearly every `discover --json` on a forum was full of escapes.


- `delete` printed its warning banner twice in one menu flow — once for the dry-run, once to confirm — which is exactly how a person learns to skim it. Found in the sibling's try-it and fixed the same way here: the dry-run prints a compact line plus the same GONE/OK consequences, and the banner belongs to the confirm alone, the screen that can still be stopped. A topic's dry-run also names the group it is in.

- The row before the point of no return read `Delete it for real (asks you to type Hermes)`, and a tester typed the title at that screen, where only a row number is an answer. It now reads `Delete it for real - the next screen asks for its exact title`, which says when.


- `delete` removes the group, channel or topic itself, not just the messages inside it. The tool could make all three and take none of them back, so a forum scaffolded with telegram-tools could only be unscaffolded in the Telegram app. `telegram-tools delete group|channel|topic` closes that with a gate one notch tighter than `clear-messages`: dry-run by default, and the real run wants `--execute` **and** the target's own title typed back, not the word `DELETE`. For a whole chat the mistake worth catching is deleting the *wrong* one, and only the title catches that. There is deliberately no `--yes` anywhere on the path: an agent can search, send and clear unattended, and can never delete a chat.

- Naming the kind is a second lock, checked against what Telegram says rather than what you typed. `delete group` pointed at a broadcast channel is refused before anything is asked, naming the real type. The preview says how far the deletion reaches and does not soften it: a group or channel goes for **every** member, not just for you, along with every message, its invite links and its ID, and only its creator can do it at all.

- `delete` removes exactly what `create` makes, so no cleanup this tool performs is a one-way door. Supergroups (with or without topics), broadcast channels and forum topics are all deletable and all creatable. A **basic group** is deliberately refused with that reason stated: `create` makes supergroups and cannot make a basic group back, so `delete` will not take one away — Telegram itself still can.

- Deleting a topic is honest about the mechanism. Telegram has no delete-topic method; a client removes a topic by deleting every message in it, the service message that opened it included, after which the topic is gone from the list (`messages.deleteTopicHistory`). That is what `delete topic` does, and the preview says so. The **General** topic has no such opening message and cannot be deleted at all — it is refused with a pointer to `clear-messages`, which empties it instead.

- The menu carries the same flow, and never as a shorter path past a gate. *Delete a group, channel, or topic* is a new root row: it works out the kind from what you picked rather than asking you to name it, dry-runs first, and only then offers a row that tells you which title it is about to ask for. A typed ID or `@username` is the one case it asks, because nothing has looked it up and guessing is not a gate.

- One new root menu row shifts the numbering: *Delete* is 5, *Clear topic messages* moves to 6, *My bots* to 7, *Check setup* to 8.

## 3.6.0 - 2026-08-31

- Topic listings show the emoji Telegram shows: `💻 Dobby`, `🔎 Researcher`, `‼️ Alerts`. A forum topic's title is plain text and the emoji in front of it is a separate custom-emoji document ID, so a tool that reads only the title prints titles that look like the emoji has gone missing — which is exactly how this was found. Every topic ID on a page is now resolved in one `messages.GetCustomEmojiDocuments` call, never one per topic, and the character goes in front of the title everywhere a topic is drawn: `discover`'s table, the menu's search, send and clear pickers, `send`'s confirmation preview, and the line `clear-messages` prints as it scans a topic. A topic with no icon, an ID Telegram will not resolve, or a failed call all print the bare title rather than an error. The `title` field itself is untouched — `--topic` matching and both export formats key on it — and `discover --json` gains an additive `icon_emoji` alongside it, so an existing reader keeps working.

- The package points at a home page now: PyPI's Homepage link is https://cli-tools-site.vercel.app/, the shared page for this tool and its Discord sibling, which shows the menu running and documents every command. `Repository`, `Issues` and `Changelog` still go to GitHub, and the README carries the same link as a badge.

- The README and the PyPI summary lead with your chats instead of the library. "Local Telethon CLI for Telegram chat/topic ID discovery…" put Telethon in the first two words of the line PyPI shows under the package name, where nobody is searching for it; the summary now opens with "your own Telegram chats and forum topics" and names Telethon at the end, where it answers "what is this built on" rather than "what is this for". The README's opening sentence was one 45-word run of seven commas and is now two: the everyday jobs, then the forum-topic clearing that the Telegram app itself will not do. Wording only — no command, flag or behaviour changed, and `telethon` stays in the package keywords so search still finds it.

## 3.5.1 - 2026-08-31

- The menu's chat picker lines its ID column up after an emoji title. Padding counted codepoints while the terminal draws columns, so `📚 Vaults` (8 characters, 9 columns) and `⚠️ Alerts` (9 characters, 8 columns) had their IDs two columns apart, and a picker with a few emoji titles read as ragged. A new `columns` helper measures a title the way a terminal actually draws it and cuts a long one on a column boundary rather than through an emoji. The rules are measured, not assumed: an emoji is two columns from one codepoint, a variation selector such as U+FE0F draws nothing and does not widen what it follows, a zero-width joiner draws nothing, and each half of a flag draws two. Fourteen shapes -- plain text, CJK, a combining mark, emoji with and without U+FE0F, a flag, a skin-tone modifier and two ZWJ sequences -- were checked by printing each one and asking the terminal where the cursor landed. Nothing else in the repo pads a name: every other column pads a numeric ID or puts the name last.

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
