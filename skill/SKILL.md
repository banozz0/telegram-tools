---
name: telegram-tools
description: "Use when you need the real numeric ID of a Telegram chat, channel, group or forum topic — 'what's the ID of that topic?', 'which chat is -100…?', 'where do I send this?' — when the user wants their own Telegram messages searched or exported to JSON/CSV, or when a message must be posted to a chat or topic the user has allowlisted."
version: 1.4.0
author: banozz0
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [telegram, chat-ids, topic-ids, forum, search, export, send, cli]
---

# telegram-tools

A local CLI that logs in as the user's own Telegram account and answers questions
about their chats: the exact `-100…` chat IDs, every forum topic ID, and the contents
of their message history. Installed from PyPI, on PATH:

```
telegram-tools <command>
```

Credentials and the login session live in `~/.telegram-tools/` and never leave the
machine. The installed build can lag PyPI — its own `--help` is the only reliable
statement of what it can do today.

## This machine

Install path, credential location and where automated output is delivered differ per
machine, so they are not in this file. If a `LOCAL.md` sits beside it, that file is
this machine's setup and it wins over anything general said here — read it before the
first run. With no `LOCAL.md`, `telegram-tools doctor` reports where the config lives
and whether the login works.

## When to Use

Reach for this whenever an answer needs a Telegram identifier the user cannot read off
their screen — a chat ID, a channel ID, a forum topic (thread) ID — or when they want
their own message history searched, filtered or exported. It is the fastest way to
settle "which thread does this go to?", which is the single most common cause of a
message being delivered into a topic nobody reads.

It can also **send** a message and **create** — or **delete** — a group, channel or topic. Those write
to Telegram as the user, so rules 2 and 3 below govern them — read those before
running either. It still does not run bots, download media, or delete topics.

## Hard rules

**1. Every run acts as the user's real Telegram account.** This is not a bot session —
it is their user account, the same one their friends message. Reads are read-only and
fine. Anything that writes is theirs to run, not yours.

**2. `send` only goes where the user already said it may.** `send --yes` posts with
no human in the loop, and the CLI refuses it for any destination not in the user's
`TELEGRAM_SEND_ALLOWLIST`. That refusal is the whole safety model — do not work
around it by dropping `--yes` (which would block on a `y/N` prompt no agent can
answer), by editing the user's `.env`, or by picking a different chat. A destination
that is not allowlisted is a destination the user has not approved: draft the message,
show it to them, and let them send it or add the entry.

**3. Never run `create` unprompted.** New groups, channels and topics are real,
visible objects in the user's Telegram — other people see them appear. Create one only
when the user asked for that specific thing in this conversation, and never invent a
title. `create` outside an explicit ask is theirs to run, not yours.

**4. Never run `delete`.** It removes the group, channel or forum topic itself,
for everyone in it, not just the messages inside. There is no `--yes`: the
destructive path needs `--execute` plus the target's exact title typed at a
prompt, which no agent can answer. That is deliberate, not an obstacle to route
around — do not drive it through the menu, a pty, or a piped answer. If the user
wants something gone, hand them the exact command and let them run it.

**5. Never run `clear-messages`.** It deletes real messages out of their forum topics
and Telegram does not undo that. Dry-run is its default and the destructive path
needs both `--execute` and a typed confirmation, so you will not trip it by accident
— but do not run it at all, in any form, even to preview. If the answer is "those
messages should go", say so and let the user run it.

**6. Never print the credentials.** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and the
`.session` file are secrets. Point at where they live; never read them out, copy
them, or paste them into a reply. `doctor` exists precisely so setup can be checked
without any of that reaching the screen.

**7. If the CLI errors, say so.** A login prompt, a flood-wait, an expired session —
that *is* the answer. Never guess a chat ID. A made-up `-100…` sends the user's next
alert into the void, and they will not find out until something they needed never
arrived.

## Machine-readable output

Put `--json` **before** the subcommand and the command prints exactly one object
on stdout and nothing else. Prefer it for every run: it is the difference between
parsing a table and reading a field.

```
telegram-tools --json discover
telegram-tools --json send --chat <id> --topic <id> --text "..." --yes
```

The object always has the same keys. The ones worth reading:

- **`status`** — `ok`, `empty`, `partial`, `dry_run`, `cancelled`, `refused`, `failed`.
- **`result`** — the command's own payload, with the same keys the old
  `--json PATH` files wrote (`chats`, `matched`, `sent`, `created`, `cleared`, …).
- **`target`** — what it acted on: `rid` (`tg:chat:-100…`, `tg:topic:-100…:141`),
  `title`, `path`. Use `rid` as the key when you need to name a chat or topic
  across runs.
- **`error.code`** and **`error.hint`** when something was refused. The code is
  stable and safe to branch on; the hint is the exact command or edit that fixes
  it — relay it to the user verbatim rather than retrying.
- **`evidence.readback`** — what the tool read back after a write. A value that
  starts with `unverified:` means the write went out but could not be confirmed;
  say so rather than reporting success.

`--jsonl` streams one line per record (a chat, a message) and closes with the
same object marked `"kind": "envelope"` — use it when the answer could be long.

Exit codes: **0** done, **1** not done (cancelled, or `doctor` with a failed
check), **2** refused, **3** a gate needs a human and there is no terminal,
**130** interrupted. Exit 3 is the one to recognise: it means the command wanted
a confirmation you cannot give, and `error.hint` is the command to hand the user.

Codes you will actually meet: `NOT_ALLOWLISTED` (a `--yes` send outside
`TELEGRAM_SEND_ALLOWLIST` — rule 2, relay it), `APPROVAL_REQUIRED` (rule 4 or 5
territory: hand it over), `TARGET_NOT_FOUND` and `TARGET_KIND_MISMATCH` (the
chat reference is wrong — run `discover`, never guess), `PERMISSION_DENIED` (the
account lacks the right, named), `SESSION_IN_USE` (the user has the menu open —
say so, do not retry), `RATE_LIMITED` (Telegram asked for a wait; ask a narrower
question), `PLAN_DRIFT` (the chat changed mid-run — re-run and re-read).

## Commands

| The ask | Run |
|---|---|
| "what's the ID of that chat/group/channel?" | `telegram-tools discover` |
| "what are the topic IDs in that group?" | `telegram-tools discover` (topics are listed under their forum) |
| "include the chats I'm just a member of" | `telegram-tools discover --all` |
| "give me that as a file" | `telegram-tools discover --json /path/out.json` |
| "find where X was discussed" | `telegram-tools search --chat <id> --keyword "X"` |
| "everything in that topic since Monday" | `telegram-tools search --chat <id> --topic <topic-id> --since 2026-08-10` |
| "export it" | `telegram-tools search --chat <id> --format csv --output /path/out.csv` |
| "post this to that topic" (allowlisted) | `telegram-tools send --chat <id> --topic <topic-id> --text "..." --yes` |
| a long or multi-line message | pipe it: `... \| telegram-tools send --chat <id> --text - --yes` |
| "send them that file" (allowlisted) | `telegram-tools send --chat <id> --file /path/to/file --text "caption" --yes` |
| "make me a group with topics" (they asked) | `telegram-tools create group --title "..." --forum --yes` |
| "add a topic to that group" (they asked) | `telegram-tools create topic --chat <id> --title "..." --yes` |
| "delete that topic/group" | hand them `telegram-tools delete topic --chat <id> --topic <id> --execute` — rule 4, they run it |
| "is telegram-tools set up?" | `telegram-tools doctor` |

- **`discover` defaults to admin/managed chats only** — the ones the user runs. Add
  `--all` only when the chat you want is one they merely belong to; it is a much
  longer walk through their dialog list.
- **`--json` after a subcommand still takes a path.** `discover --json out.json`
  writes that file and prints nothing. The envelope is the *global* flag, before
  the subcommand: `telegram-tools --json discover`. A bare `discover --json` with
  no path means the envelope too.
- **`[media]` in a `search` row means a photo or file is attached.** A media-only
  message has no text at all, so without that marker the row looks empty and reads
  as "nothing is there". `--format json` carries the same fact as `has_media`.
- **`search` requires `--chat`.** Accepts a username, a link, or the numeric ID.
  Narrow with `--topic`, `--keyword`, `--from-user` (a username, an ID, or `me`),
  `--since` / `--until` (ISO dates), and `--limit`. With no `--output` it prints a
  readable table, which is usually what you want to summarise from.
- **`send` needs `--yes` from an agent session, and `--yes` needs the allowlist.**
  Without `--yes` it prints the message and waits for a `y/N` nobody is there to
  type. With `--yes` it refuses anything outside `TELEGRAM_SEND_ALLOWLIST` and the
  error names the destination to add — relay that to the user verbatim rather than
  retrying. `doctor` says how many destinations are listed, never which.
- **`send --topic` is the difference between delivered and lost.** Omitting it posts
  to the chat itself, not the thread. Confirm the topic ID with `discover` first;
  never guess one.
- **`--file` is repeatable and needs a path that exists.** Several files arrive as
  one album and `--text` becomes their caption; a file with no `--text` is a valid
  send. Attaching sends the user's file to other people — the allowlist governs it
  exactly as it governs text, and rule 2 applies unchanged.
- **A held session is not a bug to retry.** "Another telegram-tools is already using
  the login session" means the user has the menu open somewhere. Say so; a retry
  loop will not free it.
- **A write leaves a local record.** Every executed send, create, clear, delete
  or bot edit appends one line to `~/.telegram-tools/audit.jsonl`. It is the
  user's log, it holds no secrets, and you never need to read it — but do not
  suggest deleting it either.
- **Check the tool's own help before using a flag** that is not in this table. The
  CLI's `--help` is current; this file is a snapshot.
- **The menu is for the human at the keyboard.** `telegram-tools` with no arguments
  opens a looping menu with pick-lists. Every action it offers is a flag combination
  this CLI already has — nothing in the menu is a capability the flags lack.

## Never run these

- **`delete`** — it removes the group, channel or topic itself, for everyone in
  it. Rule 4 above. It refuses to run unattended by construction; hand the user
  the command instead.
- **`clear-messages`** — irreversible deletion of the user's messages. Rule 5 above.
- **`create` on your own initiative** — rule 3. If a new group or topic looks like
  the right answer, propose it and let the user say yes; do not create it and report
  back.
- **`bots`** — it edits a live bot's name, bio, description, commands, profile photo
  and default admin rights. Those are the user's public-facing bots; the edits are
  theirs to make. Check `--help` for whether the installed build has it at all.
- **A bare `telegram-tools`** — no subcommand opens the interactive menu, which waits
  for a human; from an agent session with a terminal it will block. Always pass a
  command. With no terminal attached it prints help instead, so it will not hang in a
  pipe, but it answers nothing either.

## Delivering the answer

- **Asked in conversation** → answer in that conversation, with the ID verbatim.
  Never round, never abbreviate, never drop the leading `-100`.
- **Scheduled or automated** → to the destination `LOCAL.md` names, with the thread id
  passed explicitly. Never pick a delivery target yourself; with no `LOCAL.md`, ask.

## Honest status

The login is a real Telegram session and it can expire or be revoked from the user's
*Settings → Devices*; when that happens the CLI asks for a phone number and a code,
which only they can supply. Do not attempt that flow — stop and tell them.

Telegram rate-limits aggressively. A wide `discover --all` or a large export can earn
a flood-wait measured in minutes; that is the API pushing back, not a bug, and the
fix is to ask a narrower question rather than retry.

If `doctor` reports missing config, the api_id/api_hash simply have not been placed in
`~/.telegram-tools/.env` yet — say so. Never go looking for a key, and never write one
yourself.

## The repo is the truth

This file lives in the tool's own repo at `skill/SKILL.md` and that copy is the source
of truth; every installed copy is a derivative. When the CLI gains a command, this file
changes in the same commit.
