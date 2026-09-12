# telegram-tools

[![Site: cli-tools-site.vercel.app](https://img.shields.io/badge/site-cli--tools--site.vercel.app-00afff?style=flat-square&labelColor=09090b)](https://cli-tools-site.vercel.app/)

A local CLI for your own Telegram chats: find the real IDs of your groups, channels and forum topics, search and export messages, keep a local archive of them you can search offline, send a message into a chat or topic, and create and delete groups, channels and topics.

It also empties a forum topic without destroying the topic itself — the one job the app will not do.

Built on [Telethon](https://github.com/LonamiWebs/Telethon). Everything runs on your machine with your own Telegram API credentials — no server, no third party, nothing leaves your computer except the Telegram API calls you asked for.

## What it does

- **`discover`** — lists your chats, channels, and forum groups with their exact numeric IDs and every forum topic ID. The fastest way to answer "what is this chat's `-100…` ID and what are its topic IDs?"
- **`search`** — searches messages by text, sender, date range, or topic, and prints a table or exports JSON, CSV, JSON lines, Markdown or HTML. Messages carrying a photo or file are marked `[media]`. `search --archive` answers the same flags from the local archive, offline.
- **`archive`** — a local, searchable copy of everything your account can read: `archive sync` fills it and resumes where it stopped, `archive search` is full-text search over it with no connection, `archive export` writes one search in five formats, `archive status` says what it holds, and `archive retention` / `archive forget` prune it behind the same typed-title gate `delete` has. See [The local archive](#the-local-archive).
- **`review`** — the links and files the archive has seen, in one queue, and the only way anything is ever downloaded: `review list` shows them (asking nothing of any host), `review approve` fetches the ones you pick into quarantine after a `y/N`, the built-in checks and an optional local ClamAV give each a verdict, and `review accept` moves a file into `~/.telegram-tools/media/` after showing that verdict, behind a second `y/N`. Neither gate has a `--yes`. See [The review queue](#the-review-queue).
- **`clear-messages`** — deletes all messages inside selected forum topic(s) while preserving the topics and their IDs. Dry-run by default; deleting requires both `--execute` *and* typing `DELETE` at a prompt.
- **`send`** — posts a message, a file, or both to a chat or into one forum topic, optionally as a reply (`--reply-to`). Shows you the whole message, its destination and every `@mention` in it, then asks `y/N`.
- **`message`** — what you do to a message once it exists: `reply`, `edit`, `delete`, `forward`, `copy`, `react`, `unreact`, `pin`, `unpin`, `poll`, `typing`, `read`, `unread`, `bookmark`, `draft`. Each shows the chat and the message it is about to act on, then asks. Deleting is dry-run by default, bounded, and needs `--execute` plus a typed `DELETE`. See [Message tools](#message-tools).
- **`create`** — makes a supergroup (optionally with topics already on), a broadcast channel, or a topic inside a forum group, and prints the new ID.
- **`delete`** — removes a supergroup, a broadcast channel, or a forum topic: the thing itself, not just its messages. Dry-run by default; deleting requires `--execute` *and* typing the target's exact title at a prompt. It deletes exactly what `create` can make, so nothing this tool removes is beyond making again.
- **`leave`** — takes this account out of a group or channel. Nothing in it is deleted and everyone else stays; what goes is your seat. Dry-run by default, and the dry-run says when you created the chat, because leaving one you own does not hand it to anyone and you cannot come back as its creator. Leaving requires `--execute` *and* typing the chat's exact title at a terminal.
- **`structure`** — a chat's shape as a file: `structure export` writes a blueprint (kind, title, description, topics, default rights, slow mode, join approval — never members, admins, messages, history or invite links), `structure diff` says what another chat would need to match it, `structure apply` makes the missing topics and settings behind the same typed-title gate `delete` has and never deletes anything on the target, and `structure remap` prints the id table an apply wrote. See [Structure blueprints](#structure-blueprints).
- **`admin`, `member`, `join-requests`, `invite`, `settings`** — running a group or channel: list, promote, change and demote admins; list, ban, kick, unban, mute, unmute and restrict members; approve or decline join requests; list, create and revoke invite links; show and change a chat's title, description, topics flag and slow mode, or one topic's title, icon, closed and hidden. Every write names the right it needs before it starts, and banning, kicking or demoting someone — or switching a group's topics off — asks for their exact label or the chat's exact title at a terminal. See [Admins and members](#admins-and-members).
- **`folders`** — your own chat folders, the shelves Telegram draws above your chat list: `folders list` shows them with their chats and categories, `folders create` and `folders edit` build one out of named chats and whole categories (`groups`, `bots`, `contacts`, …), and `folders delete` removes one behind the same typed-title gate `delete` has. A folder belongs to an account, so `--as-bot folders` refuses. See [Folders](#folders).
- **`watch`** — rules over live Telegram events and the runner that fires them: `watch rules add` writes a rule (what to watch, what to alert about, what to tag, bookmark, archive or queue for review), `watch run` keeps one process up that receives updates, replays what it missed and fires those rules, and `watch status`, `stop` and `reload` operate it. A rule can never download or change anything. See [Watching and scheduling](#watching-and-scheduling).
- **`schedule`** and **`send --at`** — messages posted later, and the tool says plainly which of them survives this machine being off: `send --at` hands the message to Telegram (**server-held**), `schedule post` stores one this runner posts (**runner-held**). `schedule list` shows both with their guarantee, `schedule cancel` cancels either. See [Watching and scheduling](#watching-and-scheduling).
- **`bots`** — lists the bots you own with their numeric IDs, and edits what @BotFather edits: display name, bio, description, commands, profile photo, and default admin rights.
- **`doctor`** — checks your local setup without printing any secrets.
- **`--as-bot NICK`** — runs `send`, `create topic`, a message verb or an administration command as one of your own bots instead of as you, naming both on every screen. Folders are not on that list: a bot has no chat list to shelve. See [Acting as a bot](#acting-as-a-bot).
- **`--json`** — any command, machine-readable: one object on stdout carrying the result, the target, the gate and the error code. For agents and scripts; see [For scripts and agents](#for-scripts-and-agents).

## What it doesn't do (on purpose)

- No deleting a topic by renaming it — `settings set --topic` changes a topic's title, icon, closed and hidden flags and nothing else; `clear-messages` leaves topic IDs untouched, and `delete topic` is still the only way a topic goes.
- No reordering. `folders edit` never touches the chats you pinned inside a folder, and a blueprint lists topics by title because Telegram gives them no order a client can set.
- No unattended downloads, and no download at all outside the review queue. `archive sync` notes every link and file it walks past and fetches none of them; `message copy` links to an attachment rather than fetching it. A byte reaches your disk only after you approved that candidate at a terminal, it sat in quarantine through every check, and you accepted it with the verdict in front of you. No rule, schedule or sync can approve, and no `--yes` exists for either step.
- No cloud scanning. The one scanner is a local ClamAV, if you have one; without it every verdict is `UNSCANNED`, said plainly, never "clean".
- No perfect clones. A blueprint carries a chat's structure — kind, title, description, topics, default rights, slow mode, join approval — and nothing that belongs to people or to time: no members, no admins, no messages, no history, no invite links, no linked discussion group. `structure export` says so every time it runs, and the file lists it too.
- No automation loops. The `bots` command edits bot *settings*; it never runs a bot.
- No background service. `watch run` runs in the foreground, started by you and stopped by you or `watch stop`; the tool neither installs nor prints a launchd or systemd unit. A schedule this runner holds fires only while it is up, and every listing says so.
- No rule that downloads or changes anything. A rule may alert, tag, bookmark, record what the platform sent, sync a scope into the archive, or put a link or file in the review queue — that is the whole list, and it is checked when the rule is written. There is no `download` and no `send` beyond the alert's fixed template, so a rule can put something in front of you but never act for you.
- No changing a bot's `@username`, creating or deleting bots, or reading/revoking bot tokens — those stay with @BotFather.
- No cloud anything — credentials and session files stay in `~/.telegram-tools/`.

## Install

```bash
pipx install telegram-tools
# or
uv tool install telegram-tools
```

Or from source: `pipx install git+https://github.com/banozz0/telegram-tools.git`

Two optional extras, each for one capability that needs a library and refuses rather
than degrade without it — a proxy (`telegram-tools[proxy]`) and the QR block that
`auth --qr` draws (`telegram-tools[qr]`):

```bash
pipx install 'telegram-tools[proxy,qr]'
```

Requires Python 3.11+.

## Setup: your Telegram API credentials

The tool logs in as *you* (a user account, not a bot), so it needs a Telegram API key. One-time, about two minutes:

1. Open <https://my.telegram.org/apps> and log in with your Telegram phone number.
2. Fill in the short "Create new application" form (any name/short name works; platform "Desktop").
3. Copy the **App api_id** (a number) and **App api_hash** (a hex string).
4. Store them where the tool can find them:

```bash
mkdir -p ~/.telegram-tools
cat > ~/.telegram-tools/.env <<'EOF'
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your-api-hash-here
EOF
```

Shell environment variables and a `.env` in the current directory also work, and win over `~/.telegram-tools/.env`.

Treat the api_hash like a password.

### Logging in

```bash
telegram-tools auth          # phone number, then the code Telegram sends
telegram-tools auth --qr     # or scan a code from a phone that is already signed in
```

`auth` asks at the terminal and cannot be run unattended — it has no `--yes`, and there
is nothing here for a script to drive. If the account has two-step verification, the
password is asked for at the prompt and stored nowhere. `auth --qr` needs
`pip install 'telegram-tools[qr]'` to draw the block; open Telegram on the signed-in
phone, *Settings → Devices → Link Desktop Device*, and scan it.

You can also just run a command: without a session, Telethon's own login prompt still
appears exactly as it always has.

```bash
telegram-tools profiles              # what this machine is logged in as
telegram-tools auth --logout         # end a session, after typing the profile's name
```

Logging out also removes the local session; the session shows under Telegram's
*Settings → Devices* either way.

### More than one account

```bash
telegram-tools auth --profile work           # log a second account in
telegram-tools --profile work discover       # act as it (the flag goes before the command)
export TELEGRAM_TOOLS_PROFILE=work           # or make it this shell's default
```

Each profile keeps its own session in `~/.telegram-tools/profiles/<name>/`, written
`0600` inside a `0700` directory, beside a `profile.json` that holds a label, the account
id, when it was created and last used, and the name of any proxy — never a phone number,
never a token, never a password. A profile may keep its own `.env` beside it when it
needs a second application id or its own proxy.

If you were using telegram-tools before profiles existed, nothing moved: your
`~/.telegram-tools/telegram-tools.session` *is* the `default` profile, read where it has
always been, and `TELEGRAM_TOOLS_SESSION` still wins over everything. `doctor` mentions
that it could move into the profile directory, and `auth --migrate` does it after a y/N —
the only thing in this tool that moves a session file.

### Who am I acting as?

Every command and every menu screen below the root opens with the account it is about to
use, and the thing it is about to use it on:

```
Acting as: Sven (@sven) · account · Target: Agency › 💻 Deploys (-1001234567890)
```

The same identity and target are fields in the `--json` envelope. An account with no
username is named by its first name and the last two digits of its number — `-- (…23)` —
because a display name is whatever its owner typed and need not tell two accounts apart.
Two digits, never more: the number itself is never part of a label, and a session path is
never printed anywhere.

### Acting as a bot

```bash
telegram-tools --as-bot alerts send --chat -1001234567890 --topic 141 --text "deploy is green"
```

`--as-bot NICK` goes before the subcommand, like `--profile`, and switches the run from
your account to a bot whose token is stored under that nickname in `TELEGRAM_BOT_TOKENS`
(the same nicknames `bots --bot` uses). The token opens an in-memory session and is never
written to disk. The banner then names both identities, because the question you are
answering is "which of my things is about to post this":

```
Acting as: @alertsbot · bot (via Sven (@sven)) · Target: Agency › 💻 Deploys (-1001234567890:141)
```

Bot mode narrows what a run can do; it never widens it. A Telegram bot has no dialog
list, no history and no search, so `discover`, `search`, `bots`, `clear-messages`,
`delete`, `create group`, `create channel` and `auth` refuse under `--as-bot` with
`IDENTITY_MODE_UNSUPPORTED`, before anything connects, and the hint is the same command
without the flag. What runs is what a bot is for: `send`, and `create topic` in a forum
group it administers. Targets are resolved by numeric id or `@username` only — a bot
cannot look up a link — and the bot has to be a member of the chat, which is checked
before the preview. `--yes` needs the destination in `TELEGRAM_SEND_ALLOWLIST`, exactly
as for an account send. There is no bot-mode menu: a bare `telegram-tools --as-bot NICK`
refuses.

The account named after `via` comes from the profile record `auth` wrote, so a bot-mode
run opens no account session at all.

### Optional: a proxy

```bash
# in ~/.telegram-tools/.env, or a profile's own .env
TELEGRAM_PROXY=socks5://127.0.0.1:1080
```

`socks5://`, `socks4://` and `http://` are understood, with optional `user:password@`.
This needs `pip install 'telegram-tools[proxy]'`. Without that library the command
**refuses** — Telethon's own behaviour there is a warning and a direct connection from
your own address, which is exactly the outcome someone asking for a proxy must not get.
`doctor` tells you before you run anything.

### Optional: bot tokens

Most of `bots` runs on your normal login. Three edits — commands, profile photo removal, and default admin rights — must be sent by the bot itself, so they need that bot's token:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_BOT_TOKENS=mybot:12345:AAExampleToken,alerts:67890:BBExampleToken
```

Nicknames are yours to choose and can be used as `--bot mybot`, or as `--as-bot mybot` to [act as that bot](#acting-as-a-bot). The tool only ever reads this variable — it never writes a token anywhere, and never prints one.

### Optional: the send allowlist

`send` asks you to confirm every message. `--yes` skips that prompt, and because a
skipped prompt means nobody saw where the message was going, it only works for
destinations you have named in advance:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_SEND_ALLOWLIST=-1001234567890:141,-1009876543210,@myalerts
```

Each entry is a chat ID or `@username`, optionally `:topic-id` to allow just one topic
in it. Unset means every `--yes` send is refused — `send` without `--yes` still works
and still asks. `doctor` reports how many destinations are listed, never which.

## 30 seconds of usage

```bash
# Log in, and see which accounts this machine knows
telegram-tools auth
telegram-tools profiles

# What are my chats and their IDs?
telegram-tools discover            # admin/managed chats only
telegram-tools discover --all      # everything

# Act as a second account
telegram-tools --profile work discover

# Search a group
telegram-tools search --chat @mygroup --contains deploy

# Export a topic to JSON (or --format csv|jsonl|markdown|html)
telegram-tools search --chat @mygroup --topic 141 --output topic-141.json

# Keep a local copy of everything, then search it without touching Telegram
telegram-tools archive sync
telegram-tools archive search --query "deploy AND green" --context 2
telegram-tools archive export --query deploy --format html --output deploys.html
telegram-tools search --archive --chat @mygroup --keyword deploy   # the live flags, answered offline

# Clear a topic (dry-run first — this is the default)
telegram-tools clear-messages --chat @mygroup --topic 141
# Actually delete: needs --execute AND typing DELETE at the prompt
telegram-tools clear-messages --chat @mygroup --topic 141 --execute

# Send a message into a topic (shows it, then asks y/N)
telegram-tools send --chat -1001234567890 --topic 141 --text "deploy is green"

# Multi-line body, straight from a file or a pipe
cat notes.txt | telegram-tools send --chat -1001234567890 --text -

# The same, posted by one of your bots instead of by you
telegram-tools --as-bot alerts send --chat -1001234567890 --topic 141 --text "deploy is green"

# Reply to a message, react to one, pin one (each shows the message first, then asks)
telegram-tools message reply --chat -1001234567890 --to 4812 --text "on it"
telegram-tools message react --chat -1001234567890 --id 4812 --emoji 🔥
telegram-tools message pin --chat -1001234567890 --id 4812

# Delete messages: dry-run first, then --execute and type DELETE
telegram-tools message delete --chat -1001234567890 --ids 4812,4813
telegram-tools message delete --chat -1001234567890 --from-search "deploy AND red" --execute

# Forward with the header, or copy the text (an attachment becomes a link)
telegram-tools message forward --chat -1001234567890 --ids 4812 --to @releases
telegram-tools message copy --chat -1001234567890 --ids 4812 --to @releases --to-topic 7

# Attach files (repeatable; several go as one album, --text is the caption)
telegram-tools send --chat -1001234567890 --file shot.png --file notes.pdf --text "the numbers"

# Make a group with topics already switched on, then a topic in it
telegram-tools create group --title "Agency" --forum
telegram-tools create topic --chat -1001234567890 --title "Deploys"

# Take one back. Both dry-run first; --execute then asks you to type the title.
telegram-tools delete topic --chat -1001234567890 --topic 141
telegram-tools delete group --chat -1001234567890 --execute

# Leave a group or channel you are in (nothing is deleted): dry-run, then --execute and its exact title
telegram-tools leave --chat -1001234567890
telegram-tools leave --chat -1001234567890 --execute

# Which bots do I own, and what are their IDs?
telegram-tools bots

# Show one bot's full profile
telegram-tools bots --bot @mybot

# Rename it and update the text people see before pressing Start
telegram-tools bots --bot @mybot --name "My Bot" --description "Does the thing"

# Set a new profile photo (no token needed) — removing one does need the bot's token
telegram-tools bots --bot @mybot --photo avatar.png
```

`discover` output looks like:

```text
Forum Groups
============
Example Forum
Chat ID: -1001234567890
Type: Forum Group
Admin: yes

Topics
--------------------------------------------
141   💻 Deploys
217   🔎 Support
16    General
```

## Message tools

`telegram-tools message <verb> --chat CHAT …` does to a message what the app's long-press
menu does, from a terminal. The verbs:

| Verb | Does | Flags |
| --- | --- | --- |
| `reply` | posts a reply | `--to MSG --text` |
| `edit` | changes the text of your own message, or anyone's with the edit right | `--id MSG --text` |
| `delete` | deletes messages, dry-run by default | `--ids 1,2` or `--from-search QUERY`, `--limit`, `--i-know`, `--execute` |
| `forward` | forwards, header and all | `--ids` / `--from-search`, `--to CHAT`, `--to-topic` |
| `copy` | re-posts the text as you; an attachment becomes a link to the original, never the bytes | the same as `forward` |
| `react` / `unreact` | puts your reaction on, takes it off | `--id MSG`, `--emoji 🔥` (`unreact` without `--emoji` removes every reaction of yours) |
| `pin` / `unpin` | needs the pin right | `--id MSG` |
| `poll` | posts a poll | `--question`, `--option` (2 to 10), `--multiple`, `--topic` |
| `typing` | shows *typing…* | `--seconds` |
| `read` / `unread` | marks the chat read, or unread | — |
| `bookmark` | forwards to Saved Messages and writes a `bookmarks` row in the archive | `--id MSG`, `--label` |
| `draft` | saves a draft in the chat, or a topic in it | `--text`, `--topic` |

Every verb resolves the chat, fetches the message it is about to act on, and shows both
— the chat, and the message's id, date, sender and first line — before asking. A
message id that is not there refuses with `TARGET_NOT_FOUND` before any prompt.

**`delete` is `clear-messages`' gate on a selection.** It lists every id it would
remove and stops; `--execute` asks you to type `DELETE`, and there is no `--yes`.
`--from-search` is an archive query (`archive search` syntax), so the archive has to
hold the chat; the ids it selects are shown in full. More than `--limit` messages
(200 by default) refuses with `BULK_LIMIT` rather than cutting the selection, and
above 1000 you also need `--i-know` and to type the exact count at the prompt.
Someone else's message needs the delete-messages right; your own needs none.

**Everything else is `send`'s gate.** A preview and a `y/N`; `--yes` skips it only when
the chat the write lands in — `--to` for `forward` and `copy`, `--chat` otherwise — is
in `TELEGRAM_SEND_ALLOWLIST`. `read`, `unread`, `bookmark` and `draft` are the
account's own and refuse under `--as-bot`; the rest run as the bot where it is a member
and holds the right.

## The local archive

`archive sync` copies what your account can read into `~/.telegram-tools/archive.sqlite`:
every chat, and every topic of every forum group (a forum's messages all live in topics,
so the topic is the unit, never the group). It walks each scope from the newest message
down and commits in batches, so a sync you kill halfway restarts from the last batch
that landed and never writes a row twice; a second run fetches only what arrived since.
It prints one line per scope and ends with a coverage table naming every scope it could
not read and why. Flood waits are slept and counted, and a wait longer than ten minutes
marks that scope failed and `rate_limited` in coverage rather than holding the run.
`--since` bounds a walk and leaves it open, so a later plain sync continues below the
floor; `--full` walks everything again, and is how a deletion is found — Telegram's
history never says what was removed, so a full walk marks every archived row it no
longer sees as deleted, keeping the row and its text out of searches unless asked for.

```bash
telegram-tools archive sync                                   # everything, resuming
telegram-tools archive sync --scope tg:topic:-1001234567890:141 --since 2026-06-01
telegram-tools archive status
telegram-tools archive search --query "deploy AND green" --regex '3\.\d+' --context 2
telegram-tools archive export --query deploy --format markdown --output deploys.md
telegram-tools archive retention --scope tg:chat:-1001234567890 --keep 90d   # dry-run
telegram-tools archive forget --scope tg:chat:-1001234567890 --execute        # asks for the title
```

`archive search` is FTS5 full-text search — words, `"a phrase"`, `AND`, `OR`, `NOT`,
`prefix*` — ranked by relevance and marked `«like this»` on screen, and it never
connects. `archive export` writes the same rows the search printed in `json`, `csv`,
`jsonl`, `markdown` or `html` (one self-contained page, no scripts); a bare `--output`
name lands in `~/.telegram-tools/exports/`, an absolute path is honoured. `search
--archive` takes the live command's flags and answers them from the archive.

The archive is per profile's account, not per profile: every row records which
identity synced it, and `--identity tg:user:ID` narrows a search or a status to one.
It lives under a 2 GiB budget (`archive_max_bytes` in `~/.telegram-tools/config.json`,
written with the defaults on first use); a sync that would cross it stops before
writing with `DISK_BUDGET` and names the retention command that frees space. The
archive needs a Python whose SQLite has FTS5 — `doctor` says whether yours does.

## The review queue

Every `archive sync` notes the links and files it walks past — a link as the message
wrote it, a file by Telegram's own key with the type, size and name Telegram claimed —
as candidates in one queue. Nothing is fetched by the sync, or by anything else that
runs on its own: a candidate waits until a person approves it, at a terminal.

```bash
telegram-tools review list                      # what is waiting; asks nothing of any host
telegram-tools review list --kind link --state queued
telegram-tools review approve                   # pick at the terminal, y/N, then the fetch runs
telegram-tools review approve --ids 883cabdff25821d2,1f0e2d3c4b5a6978
telegram-tools review status                    # counts, quarantine, media, the scanner, every fetched row
telegram-tools review accept --ids 883cabdff25821d2   # shows the verdict, asks y/N, moves it into media/
telegram-tools review reject --ids 1f0e2d3c4b5a6978   # asks y/N, deletes the quarantined bytes
telegram-tools review retry --ids 1f0e2d3c4b5a6978    # a failed download, from where it stopped
```

`review approve` shows every candidate the answer covers — the URL exactly as written,
the claimed type and size, which message it came from — asks `y/N`, and then fetches:
a link through a fetcher that walks its redirects one `HEAD` at a time (at most five,
each hop checked before it is followed, and none of them touched before you said yes),
a file through your own login from the message it came from. Bytes land in
`~/.telegram-tools/quarantine/<download-id>/` (0700, the payload 0600) beside a
`manifest.json` saying what the fetch learned. A download killed halfway is `failed`
with its bytes kept; `review retry` continues from that byte, needs no new approval,
and the sha256 is taken over the whole file, so the result is the same file an
uninterrupted run would have made.

Every fetch runs the same eleven checks in the same order, and the first failure is
the verdict `BLOCKED` with the check named: scheme (`https` and `http` only; a file
never goes through a URL a message supplied), redirects, private network (a hostname
that resolves to loopback, link-local, a private range or a cloud metadata address is
refused, and the connection is pinned to the address that was checked so a second DNS
answer cannot move it), path (the filename comes from the manifest id, never from the
URL or the sender), size (`download_max_bytes`, 256 MiB, and the quarantine budget),
time (ten minutes, or sixty seconds without a byte), archive expansion (zip, tar,
gzip, bzip2 and xz are inspected without extraction and refused when their entries,
declared size or ratio cross the caps; 7z, rar and anything the tool cannot look
inside are refused by name), MIME versus extension versus magic bytes, a checksum the
source supplied, a duplicate already in `media/`, and the scanner. The scanner is
ClamAV if `clamdscan` or `clamscan` is on your PATH — `doctor` says which was found
or that neither was — and its word is `CLEAN` or `INFECTED`. **No scanner means
`UNSCANNED`**, printed as such; nothing here calls a file clean because nobody looked.

`review accept` shows that verdict and asks again. A `BLOCKED` or `INFECTED` file
cannot be accepted (`UNSAFE_BLOCKED`, with the reject command as the hint); an
`UNSCANNED` one can, with the word in front of you. Accepted files are renamed into
`~/.telegram-tools/media/<sha2>/<sha256>` under the `media_max_bytes` budget (5 GiB);
`review reject` deletes the quarantine bytes, and a rejected candidate stays rejected —
the same link in the same message is the same row on every later sync. Both gates
refuse without a terminal, in either mode, with `APPROVAL_REQUIRED` and exit 3: a `y`
piped into stdin is not a person. Every approve, accept, reject and retry leaves its
line in the audit log. Nothing is uploaded anywhere, ever: there is no reputation
lookup, no sandbox, no cloud scan in any code path.

## Structure blueprints

A forum with the right topics, the right default rights and slow mode is this tool's home turf, and a blueprint makes that repeatable:

```bash
telegram-tools structure export --chat @teamhermes --output hermes.json
telegram-tools structure diff --blueprint hermes.json --chat @teamhermes2
telegram-tools structure apply --blueprint hermes.json --chat @teamhermes2            # dry-run: every step, nothing done
telegram-tools structure apply --blueprint hermes.json --chat @teamhermes2 --execute  # asks for the chat's exact title
telegram-tools structure apply --blueprint hermes.json --create --execute             # a new forum with that title first
telegram-tools structure remap --apply-id 3f9c2a1b7d4e6f80                            # the id table, offline
```

Every export opens with the same four lines, because a blueprint is a floor plan and not a copy:

```text
This is a blueprint of the chat's structure, not a copy of the chat.
It carries: kind, title, description, topics (title and icon), default rights, slow mode, join approval.
It never carries: members, admins, messages, history, invite links, the linked discussion group.
Applying it makes topics and settings on the target and never deletes anything there.
```

The file is `cli-tools/blueprint/telegram/1`: sorted keys, ids replaced by handles like `topic:deploys` with the source id recorded beside them, topics listed by title (Telegram gives them no order a client can set), and a `never_transferred` list generated from the same allowlist the exporter filters through — a field outside that list cannot reach the file. Two forums with the same shape and different ids diff empty, and exporting a chat, applying the file to a new one and exporting that again gives the same bytes once the source ids are stripped.

`apply` reads the target first and plans only the difference: topics to make, topics and settings to change, in blueprint order, the chat's own settings last. It never plans a delete — a topic or setting the target has beyond the blueprint is reported as *left alone*. The dry-run prints every step; `--execute` asks you to type the target's exact title (with `--create`, the title the new chat will have), re-resolves the chat after you answer and refuses with `PLAN_DRIFT` if it changed, then makes one step at a time. A step Telegram refuses stops the apply there, with everything already made kept and recorded, so a rerun `diff` shows what remains. Afterwards the chat is read back and compared: anything still pending is `partial` (exit 1), and the readback says which. Each accepted step appends its own line to `~/.telegram-tools/audit.jsonl`; the remap rows live in the archive under the apply id, and `structure remap` prints them without connecting.

The kinds have to match — a forum blueprint applies to a forum, a channel's to a channel — and a basic group has no blueprint at all, for the reason `delete` refuses one: `create` makes supergroups, so it could never be applied back. Admin rights are held by people on Telegram, so they are on the never-transferred list rather than in the file; the administration commands set them on a person you name.

## Admins and members

Running a forum from a terminal means the people in it as much as the topics, and these five groups are the app's admin screens as commands:

```bash
telegram-tools admin list --chat @teamhermes
telegram-tools admin promote --chat @teamhermes --user @harry --rights pin_messages,manage_topics --rank ops
telegram-tools admin rights --chat @teamhermes --user @harry --rights pin_messages
telegram-tools admin demote --chat @teamhermes --user @harry --execute          # asks for their exact label
telegram-tools member list --chat @teamhermes --query har                       # --banned lists the banned and restricted
telegram-tools member ban --chat @teamhermes --user @troll --reason spam --execute
telegram-tools member kick --chat @teamhermes --user @troll --reason spam --execute  # out now, may rejoin
telegram-tools member mute --chat @teamhermes --user @harry --until 2h
telegram-tools member restrict --chat @teamhermes --user @harry --rights send_media,send_stickers --until 7d
telegram-tools member unmute --chat @teamhermes --user @harry                   # unban lifts a ban the same way
telegram-tools join-requests list --chat @teamhermes
telegram-tools join-requests approve --chat @teamhermes --user @newbie          # or decline
telegram-tools invite list --chat @teamhermes                                   # the links, in full
telegram-tools invite create --chat @teamhermes --title night --expires 7d --usage-limit 5 --request-needed
telegram-tools invite revoke --chat @teamhermes --link https://t.me/+…
telegram-tools settings show --chat @teamhermes                                 # --topic 217 shows that topic's own
telegram-tools settings set --chat @teamhermes --slow-mode 60                   # 0, 10, 30, 60, 300, 900 or 3600
telegram-tools settings set --chat @teamhermes --title "Team Hermes" --about "the agency's room"
telegram-tools settings set --chat @teamhermes --topic 217 --title Help --closed on
telegram-tools settings set --chat @agency --forum on                           # topics on for a group that has none
telegram-tools settings set --chat @agency --forum off --execute                # asks for the chat's exact title
```

Every write shows who acts, on whom, in which chat, and what changes, then asks. Three of them are `delete`'s gate, because they take something away from a person rather than adding to a chat: `member ban`, `member kick` and `admin demote` dry-run by default, take `--execute`, ask you to type the person's exact label (`@harry`, or their name when they have no username), refuse without a terminal in either mode, and have no `--yes`. Everything else asks `y/N`, and on all of it but `settings set` `--yes` answers the question: the preview still prints, nothing else changes, and no allowlist applies — a `--yes` here is a script or an agent saying the user already asked for exactly this.

Before anything is sent, the tool asks Telegram what rights your account holds in the chat and refuses by name when the one it needs is missing (`ban_users` for the member verbs, `add_admins` for the admin ones, `invite_users` for join requests and links, `change_info` for settings). The hierarchy is checked the same way: an admin can give only rights it holds and edit only an admin who holds no more than it does, the creator can do anything, and a grant or edit outside that refuses with `HIERARCHY_DENIED` naming both rights sets before the call. Banning or kicking an admin is refused too and names `admin demote`. After you answer the gate, the chat and the person are looked up a second time — someone promoted in that window refuses with `PLAN_DRIFT` — and afterwards the person is read back and their new status reported.

Telegram has no kick of its own: `member kick` is what Telegram means by one, a ban followed at once by an unban, so the person is out, may rejoin, and no ban row remains — the dry-run says so. It refuses someone who is not in the chat, because kicking a banned person would lift their ban; that is `member unban`. Telegram stores no reason beside a ban or a kick. `--reason` is kept in the plan, the readback and the line `member ban` or `member kick` appends to `~/.telegram-tools/audit.jsonl`, and that local line is the only record of why. Mutes and restrictions are bounded: `--until` is required, takes a duration (`30m`, `2h`, `7d`, `1w`) or an ISO date or time, and has to be at least a minute ahead and at most a year, because Telegram treats anything longer as forever and a restriction with no end is a ban under another name.

Invite links are credentials to a chat, so they appear only where you asked for them: `invite list` and `invite create` print them and carry them in `result`. Every other screen, envelope field and audit line goes through the same redaction pass as a token, which blanks a link — including the one you handed to `invite revoke`.

`settings` reaches a chat and, with `--topic`, one topic in it. A chat takes `--title`, `--about`, `--forum` and `--slow-mode` and needs `change_info`; a topic takes `--title`, `--icon-emoji-id`, `--closed` and `--hidden` and needs `manage_topics`. Both are named before anything is sent, a flag from the wrong scope is a usage error naming it rather than a call Telegram refuses, and the General topic takes only the title and `hidden` Telegram lets it take. A flag you leave out leaves its field alone, and every `set` reads the fields back afterwards and reports what actually moved, so a change the platform accepted and did not apply reads as `no field changed` rather than as a success.

`--forum off` is the one setting behind `delete`'s gate. Switching topics off puts every topic's messages into one stream and its topics stop existing, so it dry-runs by default, needs `--execute` plus the chat's exact title typed at a terminal, refuses without one in either mode, and has no `--yes`. `--forum on` is one `y/N`, like every other setting.

Under `--as-bot` all five groups run as the bot, where the bot is an admin of the chat; a bot that is a plain member refuses with `IDENTITY_MODE_UNSUPPORTED` before any preview, and a bot admin without the specific right is refused by name like the account would be. A basic group is refused (`PLATFORM_UNSUPPORTED`): every call here is a supergroup call, and Telegram itself upgrades a basic group the moment you change a setting on it in the app.

## Folders

A folder is Telegram's own shelf over your chat list — the tabs above it in the app — and it belongs to your account rather than to any chat:

```bash
telegram-tools folders list
telegram-tools folders create --title Ops --emoji 🛠 --include @teamhermes --include @agency --types groups
telegram-tools folders edit --id 2 --title "Ops and alerts" --include @teamhermes --include @agencyalerts
telegram-tools folders edit --id 2 --types none                                 # keeps its chats, drops the categories
telegram-tools folders delete --id 2 --execute                                  # asks for the folder's exact title
```

A folder holds the chats you name (`--include`, repeatable) and whole categories Telegram matches for you (`--types`, from `bots`, `broadcasts`, `contacts`, `groups`, `non_contacts`, plus `exclude_archived`, `exclude_muted` and `exclude_read`), minus the chats you leave out (`--exclude`). Both chat lists **replace**: `--include a --include b` is what the folder holds afterwards, and `--include none` empties it. A field no flag names is left alone, and that includes the chats you pinned inside the folder — this tool never reorders them. A folder with no chats and no matching category would match nothing, which Telegram refuses, so this refuses it first and says which flag to add.

`create` and `edit` show what changes and ask `y/N`, and `--yes` answers it with the preview still printed. `delete` is `delete`'s gate: dry-run by default, `--execute` plus the folder's exact title typed at a terminal, no `--yes` either. Deleting a folder removes the shelf and none of the chats on it.

Two folders you will see and not be able to change here. The **All chats** row Telegram always sends is not a folder anyone made, so it is not listed. A folder that arrived through a **chatlist invite** is listed with `shared`, and `edit` and `delete` refuse it by name (`PLATFORM_UNSUPPORTED`): Telegram gives a shared folder no exclude list and no categories, so the folder this tool would write back is not the folder that is there. Change or leave those in the app.

Folders are account-only. `--as-bot folders …` exits 2 with `IDENTITY_MODE_UNSUPPORTED` before anything connects, because a bot has no chat list to shelve.

## Watching and scheduling

Two things live here: rules over what happens in your chats, and messages posted later.

### Rules and the runner

A rule is a JSON file in `~/.telegram-tools/rules/`, `0600`, one per file, and the flags are a convenience — the file stays yours to edit by hand.

```bash
telegram-tools watch rules add --name deploys \
  --on message --on link --scope tg:topic:-1001234567890:141 \
  --domain github.com --keyword deploy \
  --alert-to tg:chat:-1009876543210 --queue-review --cooldown 300
telegram-tools watch rules list
telegram-tools watch rules test --event recorded.json      # says what would fire; fires nothing
telegram-tools watch rules disable --name deploys          # enable, remove (asks first)
telegram-tools watch run                                   # foreground; Ctrl-C or `watch stop`
telegram-tools watch status
```

`--on` takes `message`, `edit`, `reaction`, `member_join`, `member_leave`, `link` and `media`. One message can be three events: it is always a `message`, and *also* a `link` when it carries URLs and a `media` when it carries a file — so a rule watching links does not have to watch every message in the chat. In a forum the scope is the topic (`tg:topic:CHAT:TOPIC`), not the group.

What a rule may do is a closed list: `--alert-to` a chat or topic, `--alert-command` something on your PATH, `--tag`, `--bookmark`, `--capture-metadata` (record what the platform delivered — never a fetch), `--archive-scope`, `--queue-review`. Anything else is refused when the rule is written. An alert to a chat goes out through this tool's own send under the same `TELEGRAM_SEND_ALLOWLIST` an unattended `send --yes` answers to: a destination outside the list is reported `NOT_ALLOWLISTED` rather than posted. An alert to a command runs that command with the alert text on stdin; a command that is not on PATH is `COMMAND_MISSING` when the rule loads, not when it would have fired. Every alert ends with an origin marker line, and the runner drops an event that carries one and was sent by a bot — which is what stops two runners alerting each other forever.

`watch run` is one process per machine. It takes an exclusive lock, so a second one exits 2 with `RUNNER_LOCKED` naming the holder, before it connects. It does not hold your login's session file: it copies the authorization into memory at start, so `telegram-tools send` in another terminal keeps working while it runs. On start it replays each scope from where it stopped, with duplicate suppression on, so an event it saw before its last exit fires nothing and one the exit swallowed fires once. It needs a file lock, which macOS and Linux have and Windows does not — there `watch run` exits 2 with `PLATFORM_UNSUPPORTED` and every one-shot command still works. `doctor` reports the holder, the last thing the runner logged, and whether your rules load.

### The two guarantees

```bash
telegram-tools send --chat @teamhermes --text "standup" --at 2026-09-09T09:00     # server-held
telegram-tools schedule post --chat @teamhermes --text "standup" --every "0 9 * * mon"
telegram-tools schedule list --chat @teamhermes
telegram-tools schedule cancel --id 12ab34cd56ef                                 # --chat too, for one Telegram holds
```

`send --at` hands the message to Telegram, which holds it and posts it with this machine off, your laptop shut and the tool uninstalled: **server-held**. A time with no offset is this machine's local time, and the tool echoes it back with the offset applied so there is no doubt which moment was meant. Telegram has no repeat, so `--every` is always the other kind: `schedule post` stores a row **this runner** posts, and every listing spells it out — `runner-held: fires only while watch run is up on this machine`. `--every` takes an interval (`15m`, `2h`, `1d`) or a five-field cron expression. Both kinds are checked against the right to post before they are stored, and `schedule list --chat C` shows them together, each row carrying its own guarantee. `schedule cancel --id N --chat C` cancels one Telegram is holding; without `--chat` it cancels one of this runner's. Scheduling is account-only: Telegram gives a bot no way to hand it a message for later, so `--as-bot` refuses `send --at` and `schedule` by name. Watching is not — a bot receives updates for the chats it is in, and `--as-bot watch run` works.

## The menu

Run `telegram-tools` with no arguments and you get a menu instead of flags:

```text
telegram-tools
Acting as: Sven (@sven) · account
--------------------------------------------
1. Find IDs (chats, topics)
2. Read (search live, archive, export)
3. Write (send, reply, message tools)
4. Build (create, delete, structure)
5. Clear messages
6. Manage (admins, members, invites, settings)
7. Watch (rules, runner, review queue)
8. Identity (profiles, my bots)
9. Check setup
0. Exit
```

Every row now opens something; the nine numbers are settled and nothing above or below
them has to move again. The `Acting as:`
line appears once something has connected — a bare `telegram-tools` opens without
needing credentials, and `Check setup` never needs any.

`0` always steps back one screen — inside a picker or on a flow's own screen alike —
and exits once you're back at the root; on a text prompt a blank line does the same.
Every screen below the root carries its trail (`Main › Read › Search › Hermes › From`), so
you always know where you are. Chats, topics, bots, and admin rights come from live
pick-lists rather than prompts asking you to type an ID; long lists page on `n` and
`p`, and an item keeps its number on every page. Bot fields show their current value
and offer keep / change / clear.

After a job the menu offers its own next step — *Tweak it* back to the filled-in search
or send form, *Create another*, *Clear more topics*, *Edit more* — plus *Main menu*, and
*Run it again* where a re-run makes sense (chats & topics, search, send). Enter is still
the main menu (the root, not the group screen you came through), `0` still exits, and `doctor` keeps the plain Enter/`0` prompt. Backing out of
a form with something typed in it — a message, search filters, bot edits — asks first.
Every flag has a row:
the clear screen offers *All topics* and a batch size, the bots screen can save the
whole bot list to JSON and look up a bot you do not own, read-only, and *Read* opens
a screen of six — the live search, then sync, status, search or export, prune and
forget for the archive. Sync picks its scope from your live chats and topics, the same
picker Search uses; search, prune and forget pick from what the archive already holds.
*Write* opens a screen of sixteen — Send, then one row per message verb — each picking
the chat from your live chats and staging the verb's fields; Send's form has a *Reply to*
row, and Delete's *Delete for real* toggle is its `--execute`. *Build* opens six rows: create,
delete, then export a blueprint, diff one against a chat, apply one — the dry-run runs first,
and the exact title is typed at the CLI's own prompt — and show an apply's remap table.
*Manage* opens six screens: the five administration groups, a row per verb — the settings
form stages every field of a chat and of a topic, and its `--forum off` row runs the
dry-run first and asks for the chat's exact title at the CLI's own prompt — and *Folders*,
which is the one Manage screen that picks no chat, because a folder belongs to the account. *Watch* opens four screens:
*Rules* (list, add, edit, enable, disable, remove, test — the add form has a row for every
flag the command takes, and the file it writes stays editable by hand), *Runner* (run it
here in the foreground, its status, stop, reload), *Scheduled* (what is scheduled with its
guarantee, schedule one this runner posts, cancel either kind) and *Review queue*, whose
six rows are unchanged.

The menu is in colour when it is talking to a terminal, and plain text in a pipe, under
`NO_COLOR`, or with `TERM=dumb`.

The message box takes several lines — end it with a `.` on its own line — so pasting
a multi-line message works instead of feeding its later lines to the menu as answers.

The safety gates are the same as the flags', not looser: clearing topic messages
dry-runs first and still asks you to type `DELETE`, deleting a group, channel or topic
dry-runs first and still asks you to type its exact title, pruning or forgetting part
of the archive dry-runs first and asks for the scope's title too, deleting messages
dry-runs first and still asks for `DELETE`, deleting a folder or switching a group's topics
off dry-runs first and asks for its exact title, sending or any other message verb shows
the whole thing and asks `y/N`, scheduling a message shows it with its guarantee and asks,
removing a rule asks, and bot edits still print a diff and ask before writing. The
menu has no equivalent of `--yes` at all. With no terminal attached it prints this help instead.

## For scripts and agents

Put `--json` before the subcommand and the command prints exactly one object on
stdout, and nothing else:

```bash
telegram-tools --json discover
telegram-tools --json send --chat -1001234567890 --topic 141 --text "deploy is green" --yes
```

```json
{
  "schema": "cli-tools/envelope/1",
  "tool": "telegram-tools", "version": "3.8.0",
  "command": "send", "args": {"chat": "-1001234567890", "topic": 141, "yes": true},
  "identity": {"platform": "telegram", "mode": "account", "label": "Sven (@sven)", "id": "tg:user:12345678", "profile": "default", "via": null},
  "target": {"rid": "tg:topic:-1001234567890:141", "kind": "topic", "title": "Deploys", "path": ["Agency", "Deploys"]},
  "status": "ok",
  "result": {"chat_id": -1001234567890, "topic_id": 141, "message_id": 9001, "files": 0, "sent": true, "cancelled": false},
  "plan": {"plan_id": "b7f1e2d3c4b5a697", "approval": "yes_allowlist", "preflight": {"required": ["send_messages"], "held": ["send_messages"], "missing": []}},
  "evidence": {"readback": "message 9001 is in Agency › Deploys", "fetched_at": "2026-09-04T09:31:04Z"},
  "warnings": [], "error": null,
  "meta": {"started": "2026-09-04T09:30:58Z", "duration_ms": 621, "api_calls": 0, "waited_ms": 0}
}
```

- **`result` is the command's own payload**, with every key it printed before — so
  a reader of the old `--json PATH` files reads the same keys one level in.
- **`status`** is one of `ok`, `empty`, `partial`, `dry_run`, `cancelled`,
  `refused`, `failed`.
- **`--jsonl`** streams one JSON line per record first (a chat, a message) and
  closes with the same envelope marked `"kind": "envelope"`.
- **Everything a person would read moves to stderr** under either flag — tables,
  previews, progress, prompts — so stdout stays parseable.
- **`error.code` is stable.** `NOT_ALLOWLISTED`, `TARGET_NOT_FOUND`,
  `TARGET_KIND_MISMATCH`, `PERMISSION_DENIED`, `HIERARCHY_DENIED`, `PLAN_DRIFT`, `APPROVAL_REQUIRED`, `BULK_LIMIT`,
  `SESSION_IN_USE`, `CONFIG_MISSING`, `CONFIG_INVALID`, `LOGIN_REQUIRED`,
  `RULE_INVALID`, `COMMAND_MISSING`, `RUNNER_LOCKED`, `RUNNER_NOT_RUNNING`,
  `RATE_LIMITED` and others. `error.hint` is the exact command or edit that
  would fix it — worth relaying verbatim.
- **`identity` names the account every run acted as**, with the profile it came
  from, and `target` what it acted on. Under `--as-bot` its `mode` is `bot`, its
  `id` is `tg:bot:…` and `via` is the account's `tg:user:…`. Neither ever carries
  a phone number, a token or a session path.
- **`meta.waited_ms` is the flood-wait time an `archive sync` slept**; every other
  command reports 0 there, and `meta.api_calls` is not measured yet by any.
- **The archive commands are offline** except `archive sync`: `status`, `search`,
  `export`, `retention` and `forget` read `~/.telegram-tools/archive.sqlite` and
  open no connection, so they never wait on Telegram and never see a flood-wait.
  `archive search` is the cheap way to answer a history question; `search --archive`
  is the same answer behind the live command's flags. A `--json archive sync` carries
  the coverage table in `result.scopes` and `result.skipped`, one entry per scope,
  and `result.manifests` counts the links and files it noted for review.
- **The administration reads are safe to run** (`admin list`, `member list`,
  `join-requests list`, `invite list`, `settings show`): they read a chat and change
  nothing; `result.admins[]`, `result.members[]` and `result.requests[]` carry ids,
  labels, statuses and rights, and `invite list` carries the links, which no other
  envelope does. `member ban`, `member kick` and `admin demote` are not for an agent to drive: each
  asks a person for the exact label at a terminal, refuses without one
  (`APPROVAL_REQUIRED`, exit 3, in either mode), and has no `--yes`.
- **The watch reads are offline** (`watch rules list`, `watch rules test`, `watch status`,
  and `schedule list` without `--chat`): they read local files and the local archive, and
  `rules test` says what a recorded event *would* do without firing any of it. Writing a
  rule is a user's decision, not an agent's: relay the `watch rules add` line you would run
  and let the person run it, and leave `watch run` to them too — it is a process they start
  and stop. Every schedule in `result` carries `guarantee`, either `server-held` or
  `runner-held: fires only while watch run is up on this machine`; quote it, because the
  difference is whether the message survives this machine being off.
- **`review list` and `review status` are offline too**, and the answer is safe to
  read: `result.candidates[].url` is the link exactly as the message wrote it, never
  resolved. `review approve` and `review accept` are not for an agent to drive: each
  asks a person at a terminal, refuses without one (`APPROVAL_REQUIRED`, exit 3, in
  either mode), and has no `--yes`. Relay the candidate ids and let the person decide.

Exit codes, unchanged apart from one addition:

| Code | Meaning |
| --- | --- |
| 0 | done — `ok`, `empty`, `dry_run` |
| 1 | not done — cancelled at a gate, a declined confirm, `partial` (`doctor` with a failed check) |
| 2 | refused — usage, config, permission, a platform error |
| 3 | **new:** the command asks for confirmation and there is no terminal to ask on. Under `--json`, and for `review approve`, `review accept`, `review reject`, `structure apply --execute`, `member ban --execute`, `member kick --execute` and `admin demote --execute` in either mode; `error.hint` is the same command for a human to run |
| 130 | interrupted |

`discover --json out.json` and `bots --json out.json` still write those files
exactly as before; a bare `--json` on either means the envelope. A run whose
output goes to a file prints no `Acting as:` banner, so its stdout stays what it
has always been: empty.

`--as-bot NICK` goes before the subcommand too and runs `send`, `create topic`, a
message verb (all but `read`, `unread`, `bookmark` and `draft`) or an administration
command (where the bot is an admin of the chat) as that bot; any other
command under it refuses with `IDENTITY_MODE_UNSUPPORTED`
and the same command minus the flag as the hint. An agent should pass it only
when the user named the bot.

`--profile NAME` goes before the subcommand and picks the login;
`TELEGRAM_TOOLS_PROFILE` is the default. Under `--json` a profile with no session
refuses with `LOGIN_REQUIRED` and the `auth` command that fixes it, rather than
blocking on a prompt. **`auth` is not for an agent to drive** — it asks a human
for a code or a password. Relay the refusal and let the person run it.

## Safety model

| Command | Destructive? |
| --- | --- |
| `discover`, `search`, `doctor`, `profiles`, `archive status`, `archive search`, `archive export` | No — read-only |
| `archive sync` | Read-only against Telegram — writes only the local archive, resuming without duplicating; refuses under `--as-bot`. Notes every link and file it sees as a review candidate and fetches none |
| `review list`, `review status` | No — read the archive and two local directories; no host is contacted and no redirect is resolved |
| `review approve`, `review retry` | Outward-facing — after a `y/N` at a terminal, contacts the link's host (redirects walked one `HEAD` at a time) or reads the file through your login, into quarantine, through eleven checks and the scanner. No `--yes`; refuses without a terminal (`APPROVAL_REQUIRED`, exit 3) |
| `review accept`, `review reject` | Local only — move a quarantined file into `media/` after showing its verdict, or delete the quarantined bytes; each after a `y/N`, no `--yes`. `BLOCKED` and `INFECTED` can never be accepted |
| `watch rules list`, `watch rules test`, `watch status`, `schedule list` (without `--chat`) | No — read local files and the local archive; `test` fires nothing and contacts nobody |
| `watch rules add/edit/enable/disable/remove` | Local only — write or delete a rule file in `~/.telegram-tools/rules/`; `remove` asks `y/N`, or takes `--yes`. A rule can never download or mutate anything: the action list is closed and checked when the file is written |
| `watch run` | Outward-facing only through its rules — receives updates, and posts an alert through the ordinary send path under `yes_allowlist`, so a destination outside `TELEGRAM_SEND_ALLOWLIST` is refused `NOT_ALLOWLISTED`. Holds an exclusive lock (`RUNNER_LOCKED` for a second one), holds no session file, installs no service |
| `watch stop`, `watch reload` | Local only — a signal to the runner this machine is running; `RUNNER_NOT_RUNNING` when there is none |
| `send --at`, `schedule post` | Outward-facing, later — `--at` hands the message to Telegram (`server-held`), `schedule post` stores one this runner posts (`runner-held`, and it says so). Both behind the same preview and `y/N` as `send`, both preflighted for the right to post, and both account-only |
| `schedule cancel` | Visible to nobody until it would have posted — cancels a message Telegram is holding (`--chat`) or one of this runner's, after a `y/N` or with `--yes` |
| `structure export`, `structure diff`, `structure remap` | No — export and diff read one chat and write a local file or a screen; remap reads the local archive and never connects |
| `admin list`, `member list`, `join-requests list`, `invite list`, `settings show`, `folders list` | No — read the chat's people, requests, links or settings, one topic's own settings, or this account's folders; `invite list` shows the links you asked for |
| `admin promote`, `admin rights`, `member unban`, `member mute`, `member unmute`, `member restrict`, `join-requests approve/decline`, `invite create`, `invite revoke`, `settings set` (except `--forum off`) | Visible to the chat — each shows who acts, on whom and what changes, then asks `y/N`; all but `settings set` take `--yes` with the same preview printed. A missing right, or a right the account cannot grant, refuses by name before the call; mutes and restrictions need `--until`, a minute to a year. `settings set` needs `change_info` on a chat and `manage_topics` on a topic, and reads the fields back as a diff |
| `folders create`, `folders edit` | Visible only to you — reshape this account's own chat folders; each shows what changes and asks `y/N`, or takes `--yes`. No chat is touched: a folder holds references to chats, never the chats themselves |
| `member ban`, `member kick`, `admin demote` | Yes — a person's membership or a person's rights, for everyone. Dry-run by default; only with `--execute` **and** the person's exact label typed at a terminal, in either mode; there is no `--yes`. A kick is a ban then an unban (the person may rejoin); a ban's or a kick's `--reason` is kept in the local audit line, because Telegram stores none |
| `settings set --forum off` | Yes — every topic in the group stops existing and its messages become one stream, for everyone. Dry-run by default; only with `--execute` **and** the chat's exact title typed at a terminal, in either mode; there is no `--yes`. `--forum on` is an ordinary `y/N` |
| `folders delete` | Yes, for this account's chat list only — the shelf goes, none of the chats on it do. Dry-run by default; only with `--execute` **and** the folder's exact title typed at a terminal, in either mode; there is no `--yes`. A shared folder from a chatlist invite is refused by name |
| `structure apply` | Additive on the target — makes topics and sets the chat's settings, never deletes a topic, a setting or the chat. Dry-run by default; executing needs `--execute` **and** the target's exact title typed at a terminal, in either mode; there is no `--yes`. `--create` makes a new chat of the blueprint's kind first |
| `archive retention`, `archive forget` | Local only — prune or remove rows of the local archive, never anything on Telegram. Dry-run by default; executing needs `--execute` **and** the scope's exact title typed back; there is no `--yes` |
| `auth` | Local only — writes or removes this machine's login. `--logout` needs the profile's name typed back, `--migrate` a `y/N`; there is no `--yes`, and it cannot run unattended. Nothing it asks for is stored: a two-step-verification password goes straight into the sign-in call |
| `create` | No — makes new things, changes nothing existing, after a `y/N` unless you pass `--yes` |
| `send` | Outward-facing — posts publicly as you (text, files, or both), after showing the whole message and asking `y/N`. `--yes` skips the prompt only for destinations in `TELEGRAM_SEND_ALLOWLIST`. Under `--as-bot` it posts as that bot, only into chats the bot is in, behind the same preview and the same allowlist |
| `message reply/edit/forward/copy/react/unreact/pin/unpin/poll/typing/read/unread/bookmark/draft` | Outward-facing where it posts, visible to the chat where it reacts or pins — each shows the chat and the message it acts on, then asks `y/N`; `--yes` only for a landing chat in `TELEGRAM_SEND_ALLOWLIST`. `edit` of someone else's message needs the edit right, `pin` the pin right |
| `message delete` | Yes — messages, for everyone. Dry-run by default and lists every id; only with `--execute` **and** a typed `DELETE`, never more than `--limit` (200) without raising it, never more than 1000 without `--i-know` and the count typed too; there is no `--yes`. Someone else's message needs the delete-messages right |
| `bots` | No — changes settings on bots you own, after a diff and a `y/N` unless you pass `--yes`; reversible if you still have the old values, but `--remove-photo` and `--clear-commands` discard data Telegram will not hand back |
| `clear-messages` | Yes — but only with `--execute` **and** a typed `DELETE`, only messages, never topics |
| `delete` | Yes, and further than `clear-messages` goes — the group, channel or topic itself, for everyone in it. Only with `--execute` **and** the target's exact title typed back; there is no `--yes`, so it never runs unattended. It removes only what `create` can make: a basic group is refused, because this tool cannot make one back |
| `leave` | No — nothing is deleted and the chat stays for everyone else; this account's seat in it goes, and a chat you created keeps running without you and cannot be re-entered as its creator, which the dry-run says. Only with `--execute` **and** the chat's exact title typed at a terminal, in either mode; there is no `--yes`. A bot may leave a chat it was added to |

`clear-messages` also verifies you actually hold the delete-messages permission in the chat before doing anything, skips topic starter messages, and handles Telegram flood-wait limits automatically.

`bots` refuses to edit a bot you do not own, and it never fetches or exports a bot token from Telegram — the three token-only edits simply fail with a message naming the fields they need one for.

Every write — sending, a message verb, creating, clearing, deleting, leaving, applying a blueprint, an admin, member, setting or folder change, a rule file, a schedule, editing a bot — now also
asks Telegram what rights your account actually holds in that chat before it
does anything, and refuses by name when one it needs is missing. Once you have
answered the gate, the target is resolved a second time and compared with the
one you were shown: a chat renamed or replaced in that window refuses rather
than acting on whatever now holds the name. Afterwards the result is read back
and reported, and one redacted line per executed write is appended to
`~/.telegram-tools/audit.jsonl` (from the menu exactly as from a flag). No
token, phone number, API hash or session path can reach that file — the same
redaction pass covers it, every envelope and every error message.

Sessions are written `0600` inside `0700` directories, and every command that
writes something refuses while anything under `~/.telegram-tools` is readable by
group or others — `doctor` names the files and the `chmod` that fixes them.
Reads still work, so `doctor` can always tell you why. `exports/` is the one
exception: what lands there is meant to be shared, nothing secret is ever written
there, and its mode is yours to choose.

## Status

Stable for its six jobs; used regularly by its author. This is a solo project whose code was written by AI agents under review — issues are welcome, fixes are best-effort, and there is no support promise.

## License

MIT. See [LICENSE](LICENSE).
