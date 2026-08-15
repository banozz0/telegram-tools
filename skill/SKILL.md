---
name: telegram-tools
description: "Use when you need the real numeric ID of a Telegram chat, channel, group or forum topic — 'what's the ID of that topic?', 'which chat is -100…?', 'where do I send this?' — or when the user wants their own Telegram messages searched or exported to JSON/CSV."
version: 1.0.0
author: banozz0
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [telegram, chat-ids, topic-ids, forum, search, export, cli]
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

It does not send messages, run bots, download media, or create and delete topics.
Anything that *posts* to Telegram belongs to a bot token and a different tool.

## Hard rules

**1. Every run acts as the user's real Telegram account.** This is not a bot session —
it is their user account, the same one their friends message. Reads are read-only and
fine. Anything that writes is theirs to run, not yours.

**2. Never run `clear-messages`.** It deletes real messages out of their forum topics
and Telegram does not undo that. Dry-run is its default and the destructive path
needs both `--execute` and a typed confirmation, so you will not trip it by accident
— but do not run it at all, in any form, even to preview. If the answer is "those
messages should go", say so and let the user run it.

**3. Never print the credentials.** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and the
`.session` file are secrets. Point at where they live; never read them out, copy
them, or paste them into a reply. `doctor` exists precisely so setup can be checked
without any of that reaching the screen.

**4. If the CLI errors, say so.** A login prompt, a flood-wait, an expired session —
that *is* the answer. Never guess a chat ID. A made-up `-100…` sends the user's next
alert into the void, and they will not find out until something they needed never
arrived.

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
| "is telegram-tools set up?" | `telegram-tools doctor` |

- **`discover` defaults to admin/managed chats only** — the ones the user runs. Add
  `--all` only when the chat you want is one they merely belong to; it is a much
  longer walk through their dialog list.
- **`--json` on `discover` takes a path, not a flag.** It writes the file; it does
  not print JSON to the terminal.
- **`search` requires `--chat`.** Accepts a username, a link, or the numeric ID.
  Narrow with `--topic`, `--keyword`, `--from-user` (a username, an ID, or `me`),
  `--since` / `--until` (ISO dates), and `--limit`. With no `--output` it prints a
  readable table, which is usually what you want to summarise from.
- **Check the tool's own help before using a flag** that is not in this table. The
  CLI's `--help` is current; this file is a snapshot.
- **The menu is for the human at the keyboard.** `telegram-tools` with no arguments
  opens a looping menu with pick-lists. Every action it offers is a flag combination
  this CLI already has — nothing in the menu is a capability the flags lack.

## Never run these

- **`clear-messages`** — irreversible deletion of the user's messages. Rule 2 above.
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
