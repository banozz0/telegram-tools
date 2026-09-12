---
name: telegram-tools
description: "Use when you need the real numeric ID of a Telegram chat, channel, group or forum topic — 'what's the ID of that topic?', 'which chat is -100…?', 'where do I send this?' — when the user wants their own Telegram messages searched or exported (JSON, CSV, JSONL, Markdown, HTML), when a history question can be answered from the local archive instead of a fresh fetch, when a message must be posted to a chat or topic the user has allowlisted, when a message the user named should get a reply, a reaction, a pin, or be forwarded, copied or bookmarked, when the user asks who the admins of a chat are, who is waiting to join, which invite links exist, what a chat's or a topic's settings are, or which chat folders they have, or when they want a message posted at a set time or a rule that alerts them when something happens in a chat."
version: 1.17.0
author: banozz0
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [telegram, chat-ids, topic-ids, forum, search, archive, export, send, reply, react, message, review, structure, blueprint, admin, member, invite, settings, folders, watch, rules, schedule, cli]
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

The user may have more than one account logged in. `--profile NAME` goes **before**
the subcommand and picks which one a run acts as; `telegram-tools profiles` lists
them. With no flag it is `TELEGRAM_TOOLS_PROFILE`, or `default`.

A run can also act as one of the user's own bots: `--as-bot NICK`, also before the
subcommand, where NICK is a nickname from their `TELEGRAM_BOT_TOKENS`. Rule 9 below
says when you may pass it; the short version is **only when the user named the bot**.

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

It also keeps a **local archive**: `archive sync` copies what the account can read
into a searchable file on the user's machine, and `archive search` answers a history
question from it with no connection, no flood-wait and full-text ranking. When the
question is about the past — "when did we decide X?", "find every mention of Y" —
the archive is the cheap way to answer it; the live `search` is for what happened
since the last sync, or a chat the archive does not hold yet.

It can also **send** a message, act on one that exists (**`message`**: reply, react,
pin, forward, copy, bookmark and more), and **create** — or **delete** — a group,
channel or topic. Those write to Telegram as the user, so rules 2, 3 and 10 below
govern them — read those before running any. It still does not run bots.

It can also run a chat: **`admin`**, **`member`**, **`join-requests`**, **`invite`**
and **`settings`** list and change who administers a group, who is in it, who is
waiting at its door, which links open it, and what the chat or one of its topics is
called and how it behaves. The reads are yours; rule 13 below says which of the writes
are, and which four never are.

It also knows the user's own **`folders`** — the shelves above their chat list. Reading
them is yours (`folders list`); making, changing or deleting one is theirs, and rule 15
says why. A folder belongs to an account, so `--as-bot folders` refuses.

It can **watch** live events and **schedule** a message for later. `watch rules`
writes the rules and `watch run` is the process that fires them; `send --at` hands a
message to Telegram to post later, and `schedule post` stores one this machine's
runner posts. Rule 14 below is your part: read the rules and the schedules, write
neither, and always quote the guarantee a schedule carries, because it decides
whether the message survives the machine being off.

Downloads exist, and only one way: the **review queue**. A sync notes every link and
file it sees; `review list` shows them; a person approves, and later accepts, each one
at a terminal. Rule 11 below is the whole of your part in that: read the queue, never
answer its gates.

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
wants something gone, hand them the exact command and let them run it. **`leave
--execute` is the same rule**: it deletes nothing, but it takes the user's seat in
a group or channel, and a chat they created keeps running without them and cannot
be re-entered as its creator. Its dry-run (no `--execute`) is safe to run and says
whether they are the creator; the real thing is theirs, behind the same typed title.

**5. Never run `clear-messages`.** It deletes real messages out of their forum topics
and Telegram does not undo that. Dry-run is its default and the destructive path
needs both `--execute` and a typed confirmation, so you will not trip it by accident
— but do not run it at all, in any form, even to preview. If the answer is "those
messages should go", say so and let the user run it.

**6. Never run `auth`.** It is the login: a phone number, a code Telegram sends to
the user's device, sometimes a two-step-verification password. None of those are
yours to ask for, type or hold, and `--logout` and `--migrate` have gates no agent
can answer. There is no `--yes`. If a command refuses with `LOGIN_REQUIRED`, relay
the `auth` command in `error.hint` and let the user run it at their own terminal.
Do not drive it through the menu, a pty, or a piped answer. `profiles remove` is
the same rule: it deletes a login from this machine behind the profile's exact name
at a terminal, with no `--yes` — hand it to the user, never type the name.

**7. Never print the credentials.** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and the
`.session` file are secrets. Point at where they live; never read them out, copy
them, or paste them into a reply. `doctor` exists precisely so setup can be checked
without any of that reaching the screen.

**8. If the CLI errors, say so.** A login prompt, a flood-wait, an expired session —
that *is* the answer. Never guess a chat ID. A made-up `-100…` sends the user's next
alert into the void, and they will not find out until something they needed never
arrived.

**9. `--as-bot` only when the user named the bot.** Without it every run is the
user's own account (rule 1). With it, `send` and `create topic` run as that bot —
a different identity, with different reach and different consequences — and the
switch is theirs to make: pass it only when the user said, in this conversation,
which bot should post ("send that from the alerts bot"). Never pick a bot because
its token happens to be configured, never fall back to it when the account is
refused, and never use it to reach a chat the account is not in. Every other
command refuses under it with `IDENTITY_MODE_UNSUPPORTED` and names the command
to run without the flag — relay that, do not retry without it on your own
initiative if the user asked for the bot. The allowlist (rule 2) binds a bot send
exactly as it binds an account send.

**10. The message verbs are `send`'s rule, and the bulk ones are `delete`'s.**
`message reply`, `react`, `unreact`, `pin`, `unpin`, `poll`, `typing`, `read`,
`unread`, `bookmark` and `draft` post or change something visible as the user; run
one only for a message the user named in this conversation, and only with `--yes`
into a chat their allowlist names (rule 2, unchanged — the refusal is
`NOT_ALLOWLISTED`, relay it). **Never run `message delete`**, in any form: it removes
real messages for everyone, has no `--yes`, and its `--execute` needs `DELETE` typed
at a prompt — hand the user the command. **Never run `message edit` on a message the
user did not write**, and never `forward` or `copy` a selection the user did not
name: `--from-search` selects every archived match, and a wide query moves a wide
selection. A refusal of `BULK_LIMIT` means the selection was bigger than the tool
acts on at once; narrow it, never add `--i-know` yourself.

**11. Never run `review approve`, `review accept`, `review reject` or `review retry`.**
The review queue is the only path by which a byte from a link or an attachment reaches
the user's disk, and it is built around two decisions a person makes at a terminal:
approve (which starts the fetch) and accept (which keeps the file, with the safety
verdict in front of them). Each refuses without a terminal in either mode
(`APPROVAL_REQUIRED`, exit 3) and has no `--yes` — do not drive one through the menu,
a pty, or a piped answer, and do not retry after the refusal. `review list` and
`review status` are yours to run: they read the archive, contact no host, and show
the URL exactly as the message wrote it. When the user wants a file, hand them the
candidate id and `telegram-tools review approve --ids <id>`; when a verdict says
`UNSCANNED`, tell them no scanner was found rather than calling the file clean.

**12. Never run `structure apply --execute`.** A blueprint apply makes topics and
changes a chat's settings for everyone in it, and its gate is `delete`'s: `--execute`
plus the chat's exact title typed at a terminal, refused without one in either mode
(`APPROVAL_REQUIRED`, exit 3), and no `--yes`. Do not drive it through the menu, a pty,
or a piped answer. `structure export`, `structure diff`, `structure remap` and the
dry-run of `apply` (no `--execute`) are yours to run: they read a chat or the archive
and change nothing. When the user wants a chat to match a blueprint, hand them the
`--execute` command and relay the dry-run's step list. A blueprint is a floor plan and
not a copy: never describe it as copying members, admins, messages or history, because
`never_transferred` in the file says it does not, and `structure export` prints the same.

**13. Never run `member ban --execute`, `member kick --execute` or `admin demote
--execute`.** They take a person's membership or a person's rights away, for everyone in the chat, and their
gate is `delete`'s: `--execute` plus the person's exact label typed at a terminal,
refused without one in either mode (`APPROVAL_REQUIRED`, exit 3), and no `--yes`. Do
not drive either through the menu, a pty, or a piped answer; hand the user the command
and relay the dry-run (no `--execute`), which is safe to run and shows who would be
affected. The other administration writes — `admin promote`, `admin rights`, `member
unban`, `mute`, `unmute`, `restrict`, `join-requests approve` and `decline`, `invite
create` and `revoke`, `settings set` (except `--forum off`) — ask `y/N`; all but
`settings set` take `--yes`, which answers it with the same preview printed and no
allowlist, and `settings set` stays the user's to answer. Pass `--yes` only when the
user asked for that exact change of that exact person or link in this conversation;
otherwise hand them the command without it. Never add `--reason` on the user's behalf, never pick an `--until`
the user did not give, and never guess a person: `member list` and `admin list` show
ids and usernames. The reads — `admin list`, `member list`, `join-requests list`,
`invite list`, `settings show` (with or without `--topic`) — are yours to run. `invite list` prints real invite
links: they are credentials to the chat, so quote one back only to the user who asked
for it and never paste one anywhere else. `HIERARCHY_DENIED` means the account holds
the right but cannot use it on that person (it lacks a right it would grant, or the
person holds more than it does); relay both rights sets from the message and stop.
`settings set` takes a chat's `--title`, `--about`, `--forum` and `--slow-mode`, or one
topic's `--title`, `--icon-emoji-id`, `--closed` and `--hidden` behind `--topic ID`; a
flag of the wrong scope is a usage error, so read `settings show` first and hand over
the exact command. **Never run `settings set --forum off --execute`**: switching a
group's topics off makes every topic stop existing and puts its messages in one stream,
and its gate is `delete`'s — `--execute` plus the chat's exact title typed at a
terminal, refused without one in either mode, no `--yes`. Its dry-run (no `--execute`)
is safe and shows what would go.

**15. Folders are the user's chat list, and reshaping it is theirs.** `folders list` is
yours to run and answers "which folders do I have, and what's in each"; the rids it
prints (`tg:folder:2`) are what `--id` takes. `folders create`, `edit` and `delete`
change what the user sees every time they open Telegram: `create` and `edit` take
`--yes` for a folder the user described in this conversation, `delete` has none and
blocks — propose the exact command and hand it over. Two things to say accurately rather than guess: `--include`, `--exclude` and
`--types` **replace** the folder's lists rather than adding to them, so an edit that
names one chat leaves the folder holding that one chat; and a folder listed as `shared`
came from a chatlist invite and refuses `edit` and `delete` by name — that one is
changed in the app, not here. Never invent a folder's contents: read it first.

**14. A rule and a runner are the user's to set up, and a schedule's guarantee is
never yours to paraphrase.** `watch rules add` and `edit` write a file that will act
on the user's account without them present, so propose the exact command and let them
run it; the same for `watch run`, which is a process they start and stop, and which
blocks until they do. The reads are yours: `watch rules list`, `watch status`,
`schedule list`, and `watch rules test --event FILE`, which says what a recorded event
*would* do and fires none of it. `watch rules remove` asks `y/N` or takes `--yes`;
pass it only for a rule the user named. Every schedule in `result` carries
`guarantee`: `server-held` means Telegram holds the message and will post it with the
user's machine off; `runner-held: fires only while watch run is up on this machine`
means it will not post unless their runner is running. Quote the field, do not
summarise it as "scheduled". `send --at` is a `send`: rule 2's allowlist and preview
apply unchanged, and it is account-only, as is every `schedule` verb. A rule can never
download or change anything — `alert`, `tag`, `bookmark`, `capture_metadata`,
`archive` and `queue_review` are the whole list — so if the user wants a file fetched
automatically, say plainly that the tool will not, and that `queue_review` is the
nearest thing: it puts the file in front of them to approve.

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
- **`identity`** — who the run acted as: `label` (`Sven (@sven)`, or `@alertsbot`
  under `--as-bot`), `mode` (`account` or `bot`), `profile`, and under bot mode
  `via`, the account the bot belongs to. Worth naming in your answer when the
  user has more than one profile or the run was a bot, so they can see it was
  the right one. It never carries a phone number, a token or a session path, and
  neither should your reply.

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
question), `PLAN_DRIFT` (the chat changed mid-run — re-run and re-read),
`LOGIN_REQUIRED` (that profile has no session — rule 6, hand over the `auth`
command in the hint), `IDENTITY_MODE_UNSUPPORTED` (that command needs the
account, not a bot — rule 9; the hint is the same command without `--as-bot`),
`BULK_LIMIT` (a `message delete`, `forward` or `copy`
selection above `--limit` — narrow it; rule 10), `HIERARCHY_DENIED` (an admin action
the account's own rights do not reach — rule 13, relay both rights sets),
`RULE_INVALID` (the rule file will not load — the message names the file and the rule
it broke), `COMMAND_MISSING` (a rule alerts a command that is not on PATH; it is
caught when the rule loads, not when it would fire), `RUNNER_LOCKED` (a runner is
already up — the message names its pid), `RUNNER_NOT_RUNNING` (`watch stop` or
`reload` with nothing to signal), `CONFIG_INVALID` (often the file-mode refusal: something
under `~/.telegram-tools` is readable by others and every write refuses until it
is not; the hint carries the `chmod`, and `telegram-tools doctor`
names the files).

## Commands

| The ask | Run |
|---|---|
| "what's the ID of that chat/group/channel?" | `telegram-tools discover` |
| "what are the topic IDs in that group?" | `telegram-tools discover` (topics are listed under their forum) |
| "include the chats I'm just a member of" | `telegram-tools discover --all` |
| "give me that as a file" | `telegram-tools discover --json /path/out.json` |
| "find where X was discussed" | `telegram-tools search --chat <id> --keyword "X"` |
| "everything in that topic since Monday" | `telegram-tools search --chat <id> --topic <topic-id> --since 2026-08-10` |
| "export it" | `telegram-tools search --chat <id> --format csv --output /path/out.csv` (also `jsonl`, `markdown`, `html`) |
| "when did we talk about X?" / anything about the past | `telegram-tools --json archive search --query "X" --context 2` — offline, ranked, marked `«…»` |
| "is the archive up to date?" / "what's in it?" | `telegram-tools --json archive status` — offline |
| "archive my chats" / the archive is stale or empty | `telegram-tools --json archive sync` — read-only against Telegram, resumes, may take a while; `--scope tg:chat:<id>` for one chat |
| "give me everything about X as a file" | `telegram-tools --json archive export --query "X" --format markdown --output /path/out.md` |
| the live `search` flags, but from the archive | `telegram-tools --json search --archive --chat <id> --keyword "X"` |
| "prune / forget the archive" | hand them `telegram-tools archive retention --scope <rid> --keep 90d --execute` or `archive forget --scope <rid> --execute` — they type the title, you do not |
| "what links / files are waiting?" / "did anything get downloaded?" | `telegram-tools --json review list` (`--kind link\|media`, `--state queued\|quarantined\|…`), `telegram-tools --json review status` — offline, nothing fetched |
| "download that file" / "fetch that link" | hand them `telegram-tools review approve --ids <id>` (the id from `review list`), then `review accept --ids <id>` once it is quarantined — rule 11, they answer both |
| "post this to that topic" (allowlisted) | `telegram-tools send --chat <id> --topic <topic-id> --text "..." --yes` |
| a long or multi-line message | pipe it: `... \| telegram-tools send --chat <id> --text - --yes` |
| "send them that file" (allowlisted) | `telegram-tools send --chat <id> --file /path/to/file --text "caption" --yes` |
| "reply to that message" (they named it, allowlisted) | `telegram-tools --json message reply --chat <id> --to <msg-id> --text "..." --yes` |
| "react to it with 🔥" / "pin it" (they named it, allowlisted) | `telegram-tools --json message react --chat <id> --id <msg-id> --emoji 🔥 --yes`, `message pin --chat <id> --id <msg-id> --yes` |
| "forward that to the releases channel" (both named, allowlisted) | `telegram-tools --json message forward --chat <id> --ids <msg-id> --to <chat> --yes` (`copy` re-posts the text; an attachment becomes a link) |
| "save that message" / "bookmark it" | `telegram-tools --json message bookmark --chat <id> --id <msg-id> --label "..." --yes` — Saved Messages plus an archive row |
| "mark it read" / "draft this for me" | `telegram-tools --json message read --chat <id> --yes`, `message draft --chat <id> --text "..." --yes` |
| "delete those messages" | hand them `telegram-tools message delete --chat <id> --ids <a>,<b> --execute` — rule 10, they type DELETE |
| "save this forum's layout" / "what does that chat's setup look like?" | `telegram-tools --json structure export --chat <id> --output /path/hermes.json` — reads only; the file holds no people or messages |
| "how does that chat differ from the blueprint?" | `telegram-tools --json structure diff --blueprint /path/hermes.json --chat <id>` — reads only |
| "set that chat up like the blueprint" / "make a forum from it" | run the dry-run `telegram-tools --json structure apply --blueprint /path/hermes.json --chat <id>` and relay the steps; hand them the same command with `--execute` (or `--create --execute`) — rule 12, they type the title |
| "which new topic is which?" after an apply | `telegram-tools --json structure remap --apply-id <id>` — offline |
| "who are the admins of that chat?" / "what can X do there?" | `telegram-tools --json admin list --chat <id>` — reads only |
| "who is in that chat?" / "is X banned?" | `telegram-tools --json member list --chat <id> --query <name>` (`--banned` for the banned and restricted) — reads only |
| "who is waiting to join?" | `telegram-tools --json join-requests list --chat <id>` — reads only |
| "what invite links does that chat have?" | `telegram-tools --json invite list --chat <id>` — reads only; the links are real, quote them to the user only |
| "what's the slow mode / do people need approval to join?" | `telegram-tools --json settings show --chat <id>` — reads only |
| "what is that topic called / is it closed?" | `telegram-tools --json settings show --chat <id> --topic <topic-id>` — reads only |
| "which folders do I have?" / "what's in that folder?" | `telegram-tools --json folders list` — reads only |
| "make X an admin" / "let X pin things" (they named the person and the rights) | `telegram-tools --json admin promote --chat <id> --user <@x> --rights pin_messages --yes` — rule 13; without `--yes` it asks y/N |
| "ban X" / "remove X as admin" | hand them `telegram-tools member ban --chat <id> --user <@x> --execute` or `admin demote … --execute` — rule 13, they type the label; the dry-run without `--execute` is yours to show |
| "kick X" / "throw X out but let them come back" | hand them `telegram-tools member kick --chat <id> --user <@x> --execute` — rule 13, they type the label. A kick is a ban then an unban: they may rejoin and no ban row remains; say so. The dry-run without `--execute` is yours to show |
| "mute X for an hour" / "let X back in" | `telegram-tools --json member mute --chat <id> --user <@x> --until 1h --yes` / `member unmute … --yes` — rule 13, the `--until` is theirs |
| "let X in" / "turn X down" (a join request) | `telegram-tools --json join-requests approve --chat <id> --user <@x> --yes` or `… decline … --yes` — rule 13 |
| "make an invite link" / "kill that link" | `telegram-tools --json invite create --chat <id> --expires 7d --yes` or `invite revoke --chat <id> --link <link> --yes` — rule 13; the link is in `result` once |
| "set slow mode to a minute" | hand them `telegram-tools settings set --chat <id> --slow-mode 60` — rule 13 |
| "rename that topic / close it" | hand them `telegram-tools settings set --chat <id> --topic <topic-id> --title "..." --closed on` — rule 13 |
| "turn topics off for that group" | hand them `telegram-tools settings set --chat <id> --forum off --execute` — they type the chat's title; every topic goes. Rule 13 |
| "make me a folder for X" / "change that folder" | `telegram-tools --json folders create --title X --include <id> --types groups --yes` or `folders edit --id <n> … --yes` — rule 15; the lists replace |
| "delete that folder" | hand them `telegram-tools folders delete --id <n> --execute` — they type its exact title, and the chats on it stay. Rule 15 |
| "what am I watching for?" / "what rules do I have?" | `telegram-tools --json watch rules list` — offline |
| "is the watcher running?" | `telegram-tools --json watch status` — offline; reports the holder, the last tick and the rules loaded |
| "would this have fired?" | `telegram-tools --json watch rules test --event /path/event.json` — fires nothing, contacts nobody |
| "alert me when X is posted in Y" | hand them `telegram-tools watch rules add --name <n> --on message --scope <rid> --keyword X --alert-to <rid>` then `telegram-tools watch run` — rule 14, they run both |
| "what's scheduled?" | `telegram-tools --json schedule list --chat <id>` — quote each row's `guarantee` verbatim |
| "send this tomorrow at 9" (allowlisted) | `telegram-tools --json send --chat <id> --text "..." --at 2026-09-09T09:00 --yes` — Telegram holds it (`server-held`); account only |
| "post this every Monday at 9" | hand them `telegram-tools schedule post --chat <id> --text "..." --every "0 9 * * mon"` — rule 14; it is `runner-held`, so say it fires only while `watch run` is up |
| "cancel that scheduled message" | `telegram-tools --json schedule cancel --id <id> --yes` (add `--chat <id>` for one Telegram holds) — rule 14 |
| "make me a group with topics" (they asked) | `telegram-tools create group --title "..." --forum --yes` |
| "add a topic to that group" (they asked) | `telegram-tools create topic --chat <id> --title "..." --yes` |
| "delete that topic/group" | hand them `telegram-tools delete topic --chat <id> --topic <id> --execute` — rule 4, they run it |
| "leave that group/channel" | `telegram-tools --json leave --chat <id>` shows what leaving costs and whether they created it; then hand them `telegram-tools leave --chat <id> --execute` — rule 4, they type the title |
| "which account is this acting as?" | `telegram-tools profiles`, or read `identity` off any `--json` run |
| "use my other account" | `telegram-tools --profile work <command>` |
| "post that from the alerts bot" (they named it, allowlisted) | `telegram-tools --as-bot alerts send --chat <id> --topic <topic-id> --text "..." --yes` — rule 9 |
| "log me in" / "log me out" | hand them `telegram-tools auth` or `telegram-tools auth --logout` — rule 6, they run it |
| "get rid of that old profile" | hand them `telegram-tools profiles remove --name <name>` — rule 6, they type the name. It deletes the local session and record only; the one they are acting as refuses until `auth --logout` |
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
- **`archive search` is offline and `--query` is FTS5 syntax**: words, `"a phrase"`,
  `AND`, `OR`, `NOT`, `prefix*`. Punctuation inside a bare word (a hyphen, a dot) is
  syntax to FTS5, so quote it: `--query '"v3.4.1"'`. `--scope <rid>`, `--from
  tg:user:<id>`, `--since`/`--until`, `--regex` and `--context N` narrow or widen it;
  `result.messages[].highlight` carries the match marked `«…»`, `context_before` and
  `context_after` the neighbours. An empty answer on a fresh machine usually means
  no sync has run: `archive status` says, and `result.coverage` on a sync names every
  scope it could not read and why.
- **`archive sync` is the one archive command that connects.** It reads history and
  writes only the local file; it never posts, edits or deletes anything on Telegram,
  so it is fine to run when the user asked for the archive or a history question
  needs it. It also notes every link and file it sees as a review candidate and
  fetches none of them — `result.manifests` counts them. It can take minutes on a large account and honours flood-waits by
  sleeping — `meta.waited_ms` says how long. `status` is `partial` and the exit code
  1 when a scope failed; the rest still landed. Under `--as-bot` every archive
  command refuses with `IDENTITY_MODE_UNSUPPORTED`: a bot cannot read history.
- **`search` requires `--chat`.** Accepts a username, a link, or the numeric ID.
  Narrow with `--topic`, `--keyword`, `--from-user` (a username, an ID, or `me`),
  `--since` / `--until` (ISO dates), and `--limit`. With no `--output` it prints a
  readable table, which is usually what you want to summarise from.
- **`send` needs `--yes` from an agent session, and `--yes` needs the allowlist.**
  Without `--yes` it prints the message and waits for a `y/N` nobody is there to
  type. With `--yes` it refuses anything outside `TELEGRAM_SEND_ALLOWLIST` and the
  error names the destination to add — relay that to the user verbatim rather than
  retrying. `doctor` says how many destinations are listed, never which.
- **A message verb shows the message it acts on**, and refuses with `TARGET_NOT_FOUND`
  when the id is not in that chat — so a wrong id costs nothing. The id comes from
  `search`, `archive search` (`result.messages[].message_id`) or the user; never
  guess one. `send --reply-to <msg-id>` posts a reply from the send command itself.
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
- **A write leaves a local record.** Every executed send, message verb, create, clear, delete,
  blueprint step, admin or member change or bot edit appends one line to `~/.telegram-tools/audit.jsonl`. It is the
  user's log, it holds no secrets, and you never need to read it — but do not
  suggest deleting it either.
- **Check the tool's own help before using a flag** that is not in this table. The
  CLI's `--help` is current; this file is a snapshot.
- **Every run names the account it used.** Human output opens with
  `Acting as: <label> · account · Target: <path> (<ids>)`, and `--json` carries the
  same two as fields. When the user has several profiles, say which one answered.
  A bot-mode run reads `Acting as: @alertsbot · bot (via <account>) · Target: …`.
- **A bot only reaches chats it is in.** Under `--as-bot`, `--chat` takes a numeric
  id or an `@username` (never a link), and a chat the bot is not a member of is
  refused with `PERMISSION_DENIED` before the preview. That is not a reason to
  drop the flag and post as the account instead — tell the user the bot is not in
  that chat.
- **`doctor` is the setup answer, and it needs no login.** It reports the profiles,
  whether the current one has a session, whether the local files are private
  enough to write, and whether a configured proxy is usable — all without printing
  a path, a number or a token.
- **The menu is for the human at the keyboard.** `telegram-tools` with no arguments
  opens a looping menu with pick-lists. Every action it offers is a flag combination
  this CLI already has — nothing in the menu is a capability the flags lack.

## Never run these

- **`delete`** — it removes the group, channel or topic itself, for everyone in
  it. Rule 4 above. It refuses to run unattended by construction; hand the user
  the command instead.
- **`leave --execute`** — the user's seat in a group or channel, behind the chat's
  exact title typed at a terminal, with no `--yes`. Rule 4. The dry-run (no
  `--execute`) is safe to run and says whether they created the chat.
- **`clear-messages`** — irreversible deletion of the user's messages. Rule 5 above.
- **`message delete`** — the same, on a selection. Rule 10. It has no `--yes`; the
  dry-run (no `--execute`) is safe to run to show the user what would go.
- **`message edit` of someone else's message, and any `--from-search` selection the
  user did not name** — rule 10.
- **`create` on your own initiative** — rule 3. If a new group or topic looks like
  the right answer, propose it and let the user say yes; do not create it and report
  back.
- **`auth`** — the login itself, in every form. Rule 6 above. `profiles` (the list)
  is the read-only half and is fine to run; `profiles remove` is rule 6 too.
- **`review approve`, `review accept`, `review reject`, `review retry`** — the two
  human decisions that download and keep a file, and the two that undo or redo one.
  Rule 11. `approve`, `accept` and `reject` refuse without a terminal; `retry` asks
  nothing because its yes was given at approve, and it still starts a fetch. `review
  list` and `review status` are the read-only half and are fine to run.
- **`structure apply --execute`** — it makes topics and changes settings on a real chat,
  behind the chat's exact title typed at a terminal, with no `--yes`. Rule 12. The
  dry-run (no `--execute`), `export`, `diff` and `remap` are the read-only half and are
  fine to run.
- **`member ban --execute`, `member kick --execute` and `admin demote --execute`** — a
  person's membership or rights, for everyone, behind their exact label typed at a terminal, with no `--yes`.
  Rule 13. The dry-run (no `--execute`) and the five reads are fine to run; the other
  administration writes ask `y/N`, or take `--yes` when the user asked for exactly that.
- **`settings set --forum off --execute`** — every topic in the group stops existing
  and its messages become one stream, behind the chat's exact title typed at a
  terminal, with no `--yes`. Rule 13. Its dry-run (no `--execute`) is fine to run;
  `--forum on` and every other `settings set` asks `y/N` and is the user's to answer.
- **`folders delete`** — it reshapes the chat list the user sees every time they open
  Telegram, behind the folder's exact title typed at a terminal, with no `--yes`. Rule
  15. `folders create` and `edit` take `--yes` for a folder the user described;
  `folders list` is the read-only half and is fine to run; a folder marked `shared`
  refuses the two writes by name.
- **`watch rules add`, `edit`, `enable`, `disable`, `remove`, and `watch run`** — the
  first five write a file that acts on the user's account without them present, and
  `watch run` is a long-running process they start and stop (it blocks until they do).
  Rule 14. `watch rules list`, `watch status`, `schedule list` and `watch rules test`
  are the read-only half and are fine to run.
- **`schedule post`** — it asks `y/N` and has no `--yes`, so from an agent session it
  blocks. Rule 14; hand the user the command. `schedule cancel --yes` cancels one the
  user named. `send --at` is a `send` and follows rule 2: allowlisted destination,
  `--yes`, and the user asked.
- **`archive retention` and `archive forget`** — they remove rows from the user's
  local archive. Dry-run is the default and executing needs `--execute` plus the
  scope's exact title typed at a prompt, with no `--yes`; hand the user the command.
  The dry-run (no `--execute`) is safe to run to show them what would go.
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
