# Design: the looping menu

Date: 2026-08-14
Status: approved design, pending implementation plan
Target release: 3.2.0

## Goal

`telegram-tools` with no arguments becomes a menu you walk forward and back through,
and only type into when a list cannot carry the answer. Nobody should need to know the
flags to use the tool.

The current menu is one screen deep: it asks for a chat as free text it could have
listed, runs one action, and exits to the shell.

## The bar

Seven rules, from `Workflows/CLI Menu Standard.md` (Obsidian vault). Every one is an
acceptance criterion, not a suggestion:

1. **Bare invocation opens the menu.** `telegram-tools <command> --flags` stays complete
   and untouched. The menu is a front end that builds the same calls — one code path.
2. **Numbered lists, 0 = back** at every level; at the root, 0 = exit. Roughly seven
   items per screen; nest rather than cram.
3. **Pickers over typing.** Anything the tool can enumerate — chats, topics, bots,
   admin-right names, formats — is a live pick-list. Free text only at true leaves:
   a search keyword, a name, a bio, a file path, a date.
4. **Inputs show the current value in brackets**, Enter keeps it, and fields where
   clearing is legal offer keep / change / clear as a list, so blank never means two
   things.
5. **After every action: result, then Enter = menu, 0 = exit.** The tool loops.
6. **Safety gates survive verbatim.** The menu is never a shorter path to a destructive
   action than the flags are.
7. **Ctrl-C anywhere exits clean** — no traceback, no partial write.

## Non-goals

- No new commands and no new capability. This is the same tool with a front door.
- No change to any flag, flag name, default, or exit code on the command path.
- No colour, no curses, no TUI framework, no new dependency. Plain numbered prompts on
  stdout, exactly like the confirmations already in the tool.
- No shell completion, no config file for menu preferences, no remembering last choices
  between runs.

## Shape of the menu

```
telegram-tools
--------------------------------------------
1. Chats & topics (find IDs)
2. Search / export messages
3. Clear topic messages
4. My bots
5. Check setup
0. Exit
```

Five entries, one per command. `search` and `export` are one flow with two endings,
because they are one command (`search`, with or without `--output`).

### 1. Chats & topics

Scope screen (1 = chats I manage, 2 = every chat), then output screen (1 = print here,
2 = write a JSON file → path input). Runs `discover`, whose own `--all` / `--json`
flags this maps onto directly.

This is the only flow that pays for the full `discover` walk (a permissions call per
chat, a topics call per forum). That is the command's inherent cost, not the menu's.

### 2. Search / export messages

Chat picker, then a staging screen:

```
Search in Hermes (-1001234567890)
--------------------------------------------
1. Topic          [all topics]
2. Contains       [(anything)]
3. From           [(anyone)]
4. Since          [(any date)]
5. Until          [(any date)]
6. Limit          [(no limit)]
7. Run it (print here)
8. Export to a file
0. Back
```

- **Topic** — picker built from that chat's forum topics, plus "all topics". Non-forum
  chats hide the row entirely rather than offering a field that cannot apply.
- **Contains** — free text, clearable.
- **From** — list: anyone / me / someone else (→ free text), mapping to `--from-user`.
- **Since**, **Until** — free text ISO date, clearable. Validated by `search` as today.
- **Limit** — free text integer, clearable. Rejects non-positive input on the spot.
- **Export** asks for a path, then a format picker (json / csv). Since the flow always
  has a path, the `--format csv` without `--output` error can never be reached here.

### 3. Clear topic messages

Chat picker restricted to forum groups — they are the only chats with topics — then a
toggle list:

```
Topics in Hermes — tick what to clear
--------------------------------------------
1. [x] 141   Deploys
2. [ ] 217   Support
3. [ ] 16    General
4. Select all
5. Continue (1 selected)
0. Back
```

Numbers toggle. "Continue" with nothing selected is refused, not silently ignored.
Then the flow **always runs the dry-run first** (`execute=False`), prints its result,
and offers:

```
1. Clear them for real (asks you to type DELETE)
0. Back to the topic list
```

Choosing 1 re-runs with `execute=True`, which reaches the existing warning banner and
typed-`DELETE` prompt in `delete.py`, unchanged. Two deliberate consequences:

- The menu is **stricter** than the flags: you cannot reach the destructive pass without
  having seen a dry-run count first. Rule 6 sets a floor, not a ceiling.
- It costs a second message scan. Accepted — this is the one irreversible action in the
  tool.

Ticking every topic maps to `--all-topics`; a partial selection maps to repeated
`--topic`. "Select all" ticks everything, so it lands on the same branch — the mapping
follows what is ticked, not which row did the ticking.

### 4. My bots

`list_bots` (one API call) → numbered bots → picking one fetches that bot's full
profile and prints it with the same `format_bot_profile` the command uses. This is the
one read the menu performs itself rather than through `run()`: the edit screen needs
those current values anyway (rule 4), and going through the command path would fetch
the identical profile a second time to print the identical text. Every *edit* still
goes through `run()`. Then:

```
1. Edit this bot
2. Save this profile to a JSON file
0. Back
```

Edit is a staging screen with per-field keep / change / clear:

```
Editing @harrybot (12345)
--------------------------------------------
1. Name            [Harry]
2. Bio             [(not set)]
3. Description     [Runs the agency]
4. Commands        [/start, /help]
5. Profile photo   [set]
6. Group rights    [post_messages, edit_messages]
7. Channel rights  [(none)]
8. Review & apply
0. Back (discards)
```

| Field | Change | Clear | Rail |
| --- | --- | --- | --- |
| Name | free text | not offered — Telegram bots must have a name | user session |
| Bio | free text | yes → `--bio ""` | user session |
| Description | free text | yes → `--description ""` | user session |
| Commands | JSON file path | yes → `--clear-commands` | bot token |
| Profile photo | image file path | yes → `--remove-photo` | bot token |
| Group rights | multi-select toggle over `right_names()` minus the implicit `other` | yes → `--group-rights none` | bot token |
| Channel rights | same | yes → `--channel-rights none` | bot token |

**Token-gated fields are marked, not sprung.** When `lookup_bot_token(config.bot_tokens,
bot.id)` returns nothing, the three bot-rail rows render as
`4. Commands  [/start, /help]  (needs this bot's token)` and selecting one explains that
and returns, instead of staging an edit that is guaranteed to fail at apply time.

"Review & apply" builds one `bots` namespace carrying only the staged fields, with
`yes=False` — so `_run_bots` prints `Editing @harrybot (12345)`, the old → new diff, and
the `y/N` prompt exactly as the flag path does. **The menu never passes `--yes`.**

`0. Back` from the staging screen discards staged edits without touching Telegram, and
says so.

### 5. Check setup

Runs `doctor`. Needs no credentials and no connection, which is the point of it.

## Architecture

### New modules

Two, not one. `cli.py` is 385 lines and the menu is larger than the whole of what it
replaces; leaving it there would make the file the tool's biggest by a wide margin.
`cli.py` keeps `build_parser`, `run`, `main`.

- **`src/telegram_tools/prompts.py`** — the screen primitives. No Telegram, no domain
  types, no I/O beyond the injected `read`/`write`. Tested on its own, in a file that
  never imports the rest of the tool.
- **`src/telegram_tools/menu.py`** — `MenuSession`, the five flows, `run_menu`. Knows
  the domain; knows nothing about terminal mechanics beyond calling `prompts`.

The chat picker (`_pick_chat`) lives in `menu.py`, not `prompts.py`: it knows what a
`ChatChoice` is and which kinds group together, which is domain knowledge. Only the
paging, filtering, and toggling underneath it are primitives.

`run_interactive_menu` and its `_read_execute` helper are deleted, not deprecated —
nothing outside `tests/test_menu.py` calls them.

### One connection for the whole session

`run()` gains two optional keyword arguments:

```python
async def run(args, *, client=None, config=None) -> int
```

Given a live client it uses it and does **not** disconnect it; given none it behaves
exactly as today (load config, create client, start, disconnect in `finally`). Config
loads lazily either way.

This is required, not tidiness: Telethon's session is a SQLite file and a second client
opening it while the first holds it is a lock error waiting to happen. One client also
means one login, one connect, for a menu session that may run a dozen actions.

### Menu session state

```python
class MenuSession:
    async def client(self)                      # lazily loads config, connects once
    async def chats(self) -> list[ChatChoice]   # cached after the first fetch
    async def topics(self, chat) -> list[TopicInfo]
    async def bots(self) -> list[BotInfo]       # cached after the first fetch
    async def close(self)
```

Caches live for the run and are never refreshed — restarting the tool is the refresh.
A "reload the list" item was considered and dropped as unearned complexity.

### The light chat picker

New in `discovery.py`:

```python
async def list_dialog_choices(client) -> list[ChatChoice]
```

One page-through of `iter_dialogs` — no permissions call per chat, no topics call per
forum. Two or three API calls instead of one per chat. It cannot report `is_admin`, and
so the picker does not claim to: it lists every chat, grouped by kind.

`ChatChoice` is a new frozen dataclass in `models.py` — `id`, `title`, `username`,
`type` — reusing `classify_entity` for the type. It is deliberately not `ChatInfo`,
which carries an `is_admin` this path cannot know and a `topics` list it does not fetch.

Topics are fetched only for the chat the user picked, through the existing
`get_forum_topics`.

The picker passes `str(chat.id)` as `--chat`. `resolve_chat` already resolves a numeric
ID against the dialog list, so this is the same code path a typed ID takes.

### Entry point

```python
async def run_menu(*, read=input, write=print, session=None, runner=None) -> int
```

`session` defaults to a real `MenuSession`, `runner` to `cli.run`. Both exist as
parameters so tests can supply fakes without monkeypatching or a network. `menu.py`
imports `cli`; `cli.main` imports `menu` inside the function, which is what keeps the
two out of an import cycle.

**The menu's exit code is the session's, not the action's.** A normal exit returns 0
even if some action inside it returned 1 (a cancelled clear, a declined bot edit); an
interrupt returns 130. One session can run a dozen actions and there is no honest way to
fold those into one number. The command path's exit codes are untouched.

### Screen primitives

All pure-ish helpers taking `read`/`write`, none of them touching the network, all
unit-testable without stdin:

| Helper | Job |
| --- | --- |
| `choose(labels, *, title, read, write)` | numbered list → index, or `BACK` |
| `pick(items, *, label, extras, ...)` | paged list → an item, an extra's key, or `BACK` |
| `pick_many(items, *, preselected, ...)` | the toggle list used for topics and admin rights |
| `ask_text` / `ask_int` | free text and positive integers; blank cancels |
| `edit_field(title, current, *, ask, allow_clear)` | keep / change / clear |
| `after_action(read, write) -> bool` | "Enter = menu, 0 = exit" |

There is no `staging_screen` helper. The two staging screens (search filters, bot
fields) each build their own rows from their own field list and call `choose` — the
shapes rhyme but their rows, gates, and terminal actions differ enough that one
parameterised helper would take more arguments than it saves.

Paged list layout (page size 9, extras numbered after the page):

```
Pick a chat  >  Forum Groups
--------------------------------------------
1. Hermes            -1001234567890
...
9. Old project       -1002222222222
10. Next page (12 more)
11. Previous page          [only when there is one]
12. Filter by name
13. Type an ID or @username
0. Back
```

Groups: Forum Groups, Channels, Groups, Direct chats. Empty groups are hidden.
"Filter by name" is a case-insensitive substring match inside the current group — the
one place typing beats a list, at 200 chats. The filtered list replaces the pages until
`0` takes it back to the unfiltered group; a filter matching nothing says so and asks
again rather than showing an empty screen.

`pick_many` pages by the same rule, with its toggles preserved across pages.

**On rule 2's "roughly seven items".** A full page here is nine chats plus up to four
navigation rows. The rule's intent is that a screen never becomes a wall to scroll —
nine items and a fixed set of navigation rows in the same place every time reads as a
page, not a wall, and the alternative (five per page) doubles the paging for no gain.
Every other screen in the tool sits at or under eight items.

### Errors do not end the session

Every action the menu runs is wrapped:

```python
except (ConfigError, EntityResolutionError, ValueError, PermissionError, OSError, RPCError) as exc:
    write(f"error: {exc}")
```

then the usual "Enter = menu, 0 = exit". A flood-wait, an unresolvable chat, a missing
photo file, or a missing token is a message and a return to the menu — never a stack
trace and never an exit. Unnamed exception types still propagate; a bug should be loud.

On the command path, `main()` keeps `parser.error` and today's exit codes untouched.

### Interruption

`main()` catches `KeyboardInterrupt` and `EOFError` around `asyncio.run`, prints a
newline, and returns **130** — for both the menu and the command path. No traceback.

`main()` also prints help instead of opening the menu when stdin is not a TTY. A menu
needs a human; a bare `telegram-tools` from a script or an agent would otherwise block
on `input()` until something times it out. Two lines, and it removes the one way this
change could hang an automated caller.

## Testing

`tests/test_menu.py` grows from 4 tests to roughly 25, keeping the existing pattern:
injected `read`/`write` fakes, a fake `MenuSession` carrying canned `ChatChoice` /
`TopicInfo` / `BotInfo` lists, and an injected runner standing in for `cli.run`. No real
stdin, no network, no session file, no credentials.

Coverage that matters:

- Every root entry builds the namespace its flag equivalent would.
- `0` goes back one level from every screen, and exits from the root.
- The loop keeps looping: two actions in one session, then exit.
- Chat picker: grouping, paging both ways, name filter, manual entry.
- Topic toggle: select, deselect, select all, refusal to continue with nothing ticked.
- Bot edit: keep leaves a field out of the namespace; clear puts the clearing value in;
  a token-less bot refuses the three bot-rail fields; the namespace always has
  `yes=False`.
- Clear-messages: the dry-run runs first and the execute pass is a second call.
- An action raising `ValueError` prints the message and returns to the menu.
- `EOFError` and `KeyboardInterrupt` exit 130 without a traceback.

`tests/test_cli.py` gains the `run(args, client=...)` contract: a passed client is used
and left connected; no client means create-and-disconnect as before.

## Docs and release

All in the same change, per the repo's own rule that docs follow the code:

- **CHANGELOG** — 3.2.0 entry describing the menu, its safety behaviour, and that the
  flags are unchanged.
- **README** — replace the one-line mention at line 98 with a short menu section
  showing the root screen and naming the loop, the pickers, and the two safety gates.
- **`skill/SKILL.md`** — the menu is a hazard for agents, and the file must say so:
  never run bare `telegram-tools` from an agent session, because it opens an interactive
  menu and waits for a human. Also update the version line to whatever is actually
  installed at commit time — the file's current "3.0.0 from PyPI" is a claim to verify,
  not to copy forward.
- **`pyproject.toml`** — version 3.2.0.

## Risks and unverified claims

- **Clearing a bio or description sends an empty string.** That is the same call
  `--bio ""` already makes today, so the menu adds no new code path — but nobody has
  watched Telegram accept it. Needs one live check on a real bot after the build, and
  the changelog should not promise it works until that check passes.
- **Big accounts.** The light picker is two or three calls, but `iter_dialogs` on a
  large account still takes seconds, and it happens on the first screen that needs a
  chat. Acceptable, and much better than the alternative.
- **`str(chat.id)` for channels** resolves via the dialog walk, which is how a typed
  `-100…` already resolves. No new failure mode, but it does mean the picker's chat is
  looked up a second time inside `run()`.
