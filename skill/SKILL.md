---
name: telegram-tools
description: "Use when Sven needs the real numeric ID of a Telegram chat, channel, group or forum topic — 'what's the ID of that topic?', 'which chat is -100…?', 'where do I send this?' — or when he wants his own Telegram messages searched or exported to JSON/CSV."
version: 1.0.0
author: banozz0
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [telegram, chat-ids, topic-ids, forum, search, export, cli]
---

# telegram-tools

A local CLI that logs in as Sven's own Telegram account and answers questions about
his chats: the exact `-100…` chat IDs, every forum topic ID, and the contents of his
message history. Installed on the sven account, on PATH:

```
telegram-tools <command>
```

It is `telegram-tools` 3.0.0 from PyPI, installed with pipx at
`/Users/sven/.local/bin/telegram-tools`. Credentials and the login session live in
`/Users/sven/.telegram-tools/` and never leave the machine.

## When to Use

Reach for this whenever an answer needs a Telegram identifier Sven cannot read off
his screen — a chat ID, a channel ID, a forum topic (thread) ID — or when he wants
his own message history searched, filtered or exported. It is the fastest way to
settle "which thread does this go to?", which is the single most common cause of a
message being delivered into a topic nobody reads.

It does not send messages, run bots, download media, or create and delete topics.
Anything that *posts* to Telegram belongs to a bot token and a different tool.

## Hard rules

**1. Every run acts as Sven's real Telegram account.** This is not a bot session —
it is his user account, the same one his friends message. Reads are read-only and
fine. Anything that writes is his to run, not yours.

**2. Never run `clear-messages`.** It deletes real messages out of his forum topics
and Telegram does not undo that. Dry-run is its default and the destructive path
needs both `--execute` and a typed confirmation, so you will not trip it by accident
— but do not run it at all, in any form, even to preview. If the answer is "those
messages should go", say so and let Sven run it.

**3. Never print the credentials.** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and the
`.session` file are secrets. Point at where they live; never read them out, copy
them, or paste them into a reply. `doctor` exists precisely so setup can be checked
without any of that reaching the screen.

**4. If the CLI errors, say so.** A login prompt, a flood-wait, an expired session —
that *is* the answer. Never guess a chat ID. A made-up `-100…` sends Sven's next
alert into the void, and he will not find out until something he needed never
arrived.

## Commands

| Sven asks | Run |
|---|---|
| "what's the ID of that chat/group/channel?" | `telegram-tools discover` |
| "what are the topic IDs in the Hermes group?" | `telegram-tools discover` (topics are listed under their forum) |
| "include the chats I'm just a member of" | `telegram-tools discover --all` |
| "give me that as a file" | `telegram-tools discover --json /path/out.json` |
| "find where X was discussed" | `telegram-tools search --chat <id> --keyword "X"` |
| "everything in that topic since Monday" | `telegram-tools search --chat <id> --topic <topic-id> --since 2026-08-10` |
| "export it" | `telegram-tools search --chat <id> --format csv --output /path/out.csv` |
| "is telegram-tools set up?" | `telegram-tools doctor` |

- **`discover` defaults to admin/managed chats only** — the ones Sven runs. Add
  `--all` only when the chat you want is one he merely belongs to; it is a much
  longer walk through his dialog list.
- **`--json` on `discover` takes a path, not a flag.** It writes the file; it does
  not print JSON to the terminal.
- **`search` requires `--chat`.** Accepts a username, a link, or the numeric ID.
  Narrow with `--topic`, `--keyword`, `--from-user` (a username, an ID, or `me`),
  `--since` / `--until` (ISO dates), and `--limit`. With no `--output` it prints a
  readable table, which is usually what you want to summarise from.
- **Check the tool's own help before using a flag** that is not in this table. The
  CLI's `--help` is current; this file is a snapshot.

## Never run these

- **`clear-messages`** — irreversible deletion of Sven's messages. Rule 2 above.
- **`bots`** — lands in 3.1.0 (in the repo, not in the installed 3.0.0). It edits a
  live bot's name, bio, description, commands, profile photo and default admin
  rights. Those are Sven's public-facing bots; the edits are his to make.

## Delivering the answer

- **Asked in conversation** → answer in that conversation, with the ID verbatim.
  Never round, never abbreviate, never drop the leading `-100`.
- **Scheduled or automated** → the Alerts topic, thread `5698`, with the thread id
  passed explicitly. Agents never hold a conversation in Alerts; it is delivery only.

## Honest status

The login is a real Telegram session and it can expire or be revoked from Sven's
*Settings → Devices*; when that happens the CLI asks for a phone number and a code,
which only he can supply. Do not attempt that flow — stop and tell him.

Telegram rate-limits aggressively. A wide `discover --all` or a large export can earn
a flood-wait measured in minutes; that is the API pushing back, not a bug, and the
fix is to ask a narrower question rather than retry.

If `doctor` reports missing config, the api_id/api_hash simply have not been placed
in `/Users/sven/.telegram-tools/.env` yet — tell Sven. Never go looking for a key,
and never write one yourself.

## The repo is the truth

This file lives in the tool's own repo at `skill/SKILL.md` and installs with
`agent-config/scripts/install-tool-skill.sh ~/code/telegram-tools`. When the CLI
gains a command, this file changes in the same commit. Standard:
`Workflows/Tool Skill Standard.md` in the Obsidian vault.
