# `bots` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `telegram-tools bots` — list the bots you own with their numeric IDs, show one bot's full profile, and edit its name, bio, description, commands, profile photo, and default admin rights from the terminal instead of chatting with @BotFather.

**Architecture:** Two rails. Everything possible runs on the existing Telethon **user session** (`bots.py`): `bots.getAdminedBots` for the list, `users.getFullUser` for reads, `bots.setBotInfo` and `photos.uploadProfilePhoto(bot=)` for writes. Three writes — commands, default admin rights, photo removal — have TL methods with no `bot` parameter, so they must be called *as* the bot; those run through a short-lived Telethon client started with `bot_token=` on a `MemorySession` (`bot_session.py`), so no token ever reaches disk. Pure functions (parsers, plan builder, formatters) are separated from network calls so tests never touch Telegram.

**Tech Stack:** Python 3.11+, Telethon 1.44 (raw TL layer), python-dotenv, pytest, argparse. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-13-bots-command-design.md`

## Global Constraints

- **Work on a branch:** `git checkout -b feat/bots-command` before Task 1. This repo is public and a scheduled auto-sync commits and pushes uncommitted work on `main`.
- **No new dependencies.** Rail 2 uses Telethon with a `MemorySession`, not an HTTP client.
- **Bot tokens are never written to disk, printed, logged, included in `--json` output, error messages, or the confirm diff.** Errors refer to a nickname or a field name only.
- **Never call `bots.exportBotToken`,** with or without its `revoke` flag. Never call bot deletion or username-change methods.
- **Style matches the existing modules:** `from __future__ import annotations`, `getattr(obj, "field", default)` over TL `isinstance` checks, frozen dataclasses with `to_dict()`, pure formatters returning strings, and `SimpleNamespace` fakes in tests. No network, no session files, and no real credentials in any test.
- **Python:** 3.11+. Run tests with `.venv/bin/pytest` from the repo root.
- **Field naming is fixed** — `--name` = display name (`name`), `--bio` = the line under the profile (`about`), `--description` = the "what can this bot do?" text (`description`). Do not rename these flags.
- **Version for this work:** 3.1.0.

---

### Task 1: Bot token configuration and doctor check

**Files:**
- Modify: `src/telegram_tools/config.py`
- Modify: `src/telegram_tools/doctor.py`
- Test: `tests/test_bot_config.py` (create)

**Interfaces:**
- Consumes: `ConfigError`, `Config`, `load_config`, `config_dir` from `config.py`; `DoctorCheck` from `doctor.py`.
- Produces:
  - `parse_bot_tokens(raw: str | None) -> dict[str, str]` — nickname (lowercased) → token.
  - `lookup_bot_token(tokens: Mapping[str, str], *references: Any) -> str | None`
  - `bot_id_from_token(token: str) -> int | None`
  - `Config.bot_tokens: dict[str, str]`
  - `check_bot_tokens(root: Path, env: Mapping[str, str], home: Path | None = None) -> DoctorCheck`

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/bots-command
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_bot_config.py`:

```python
from pathlib import Path

import pytest

from telegram_tools.config import ConfigError, bot_id_from_token, load_config, lookup_bot_token, parse_bot_tokens
from telegram_tools.doctor import check_bot_tokens


def test_parse_bot_tokens_returns_empty_mapping_when_unset():
    assert parse_bot_tokens(None) == {}
    assert parse_bot_tokens("") == {}


def test_parse_bot_tokens_keeps_the_colon_inside_the_token():
    tokens = parse_bot_tokens("harry:12345:AAExampleToken")

    assert tokens == {"harry": "12345:AAExampleToken"}


def test_parse_bot_tokens_reads_several_entries_and_trims_whitespace():
    tokens = parse_bot_tokens(" harry:12345:AAOne , alerts:67890:BBTwo ,")

    assert tokens == {"harry": "12345:AAOne", "alerts": "67890:BBTwo"}


def test_parse_bot_tokens_lowercases_nicknames():
    assert parse_bot_tokens("Harry:12345:AAOne") == {"harry": "12345:AAOne"}


def test_parse_bot_tokens_rejects_an_entry_without_a_nickname():
    with pytest.raises(ConfigError, match="entry 2"):
        parse_bot_tokens("harry:12345:AAOne,12345:AAOne")


def test_parse_bot_tokens_accepts_a_numeric_nickname():
    assert parse_bot_tokens("007:12345:AAOne") == {"007": "12345:AAOne"}


def test_parse_bot_tokens_error_never_echoes_the_token():
    with pytest.raises(ConfigError) as excinfo:
        parse_bot_tokens("harry:12345:AAOne,broken-entry")

    assert "broken-entry" not in str(excinfo.value)
    assert "AAOne" not in str(excinfo.value)


def test_lookup_bot_token_matches_nickname_username_or_id():
    tokens = {"harry": "12345:AAOne"}

    assert lookup_bot_token(tokens, "@Harry") == "12345:AAOne"
    assert lookup_bot_token(tokens, None, "harry") == "12345:AAOne"
    assert lookup_bot_token(tokens, "alerts") is None


def test_bot_id_from_token_reads_the_leading_digits():
    assert bot_id_from_token("12345:AAExampleToken") == 12345
    assert bot_id_from_token("not-a-token") is None


def test_load_config_exposes_bot_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "harry:12345:AAOne")

    config = load_config(home=tmp_path / "home")

    assert config.bot_tokens == {"harry": "12345:AAOne"}


def test_load_config_defaults_bot_tokens_to_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.delenv("TELEGRAM_BOT_TOKENS", raising=False)

    assert load_config(home=tmp_path / "home").bot_tokens == {}


def test_check_bot_tokens_counts_without_printing_tokens(tmp_path):
    check = check_bot_tokens(tmp_path, {"TELEGRAM_BOT_TOKENS": "harry:12345:AAOne,alerts:67890:BBTwo"}, home=tmp_path / "home")

    assert check.status == "OK"
    assert "2 bot token" in check.message
    assert "AAOne" not in check.message


def test_check_bot_tokens_warns_when_none_are_set(tmp_path):
    check = check_bot_tokens(tmp_path, {}, home=tmp_path / "home")

    assert check.status == "WARN"
    assert check.failed is False


def test_check_bot_tokens_fails_on_malformed_value(tmp_path):
    check = check_bot_tokens(tmp_path, {"TELEGRAM_BOT_TOKENS": "broken"}, home=tmp_path / "home")

    assert check.status == "FAIL"


def test_check_bot_tokens_reads_the_home_dotenv(tmp_path):
    home = tmp_path / "home"
    home.joinpath(".telegram-tools").mkdir(parents=True)
    home.joinpath(".telegram-tools", ".env").write_text("TELEGRAM_BOT_TOKENS=harry:12345:AAOne\n")

    check = check_bot_tokens(tmp_path, {}, home=home)

    assert check.status == "OK"
    assert "1 bot token" in check.message
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bot_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_bot_tokens'`

- [ ] **Step 4: Implement the config side**

In `src/telegram_tools/config.py`, change the imports and `Config`, and add the three functions:

```python
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_path: Path
    bot_tokens: dict[str, str] = field(default_factory=dict)


def parse_bot_tokens(raw: str | None) -> dict[str, str]:
    tokens: dict[str, str] = {}
    if not raw:
        return tokens

    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        nickname, separator, token = entry.partition(":")
        nickname = nickname.strip().lower()
        token = token.strip()
        if not separator or not nickname or bot_id_from_token(token) is None:
            raise ConfigError(f"TELEGRAM_BOT_TOKENS entry {position} must look like nickname:token.")
        tokens[nickname] = token
    return tokens


def lookup_bot_token(tokens: Mapping[str, str], *references: Any) -> str | None:
    for reference in references:
        if reference is None:
            continue
        key = str(reference).strip().lstrip("@").lower()
        if key in tokens:
            return tokens[key]
    return None


def bot_id_from_token(token: str) -> int | None:
    prefix, _, _ = token.partition(":")
    return int(prefix) if prefix.isdigit() else None
```

`parse_bot_tokens` calls `bot_id_from_token`, which is defined below it in the same module — the name resolves at call time, so the order above is fine. Validating the token half (it must start with a numeric bot id) is what rejects a bare `12345:AAOne` entry that has no nickname, without inventing a rule about which characters a nickname may contain.

At the end of `load_config`, pass the tokens through:

```python
    session_path = Path(env.get("TELEGRAM_TOOLS_SESSION", config_dir(home) / "telegram-tools"))
    return Config(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        bot_tokens=parse_bot_tokens(env.get("TELEGRAM_BOT_TOKENS")),
    )
```

- [ ] **Step 5: Implement the doctor check**

In `src/telegram_tools/doctor.py`, add the import and the check. `run_doctor` returns before `load_config` runs, so `os.environ` has no `.env` values yet — read them without mutating the environment:

```python
from dotenv import dotenv_values

from telegram_tools.config import ConfigError, config_dir, parse_bot_tokens


def _effective_env(root: Path, env: Mapping[str, str], home: Path | None = None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (config_dir(home) / ".env", root / ".env"):
        if path.exists():
            merged.update({key: value for key, value in dotenv_values(path).items() if value is not None})
    merged.update(env)
    return merged


def check_bot_tokens(root: Path, env: Mapping[str, str], home: Path | None = None) -> DoctorCheck:
    try:
        tokens = parse_bot_tokens(_effective_env(root, env, home).get("TELEGRAM_BOT_TOKENS"))
    except ConfigError:
        return DoctorCheck("FAIL", "TELEGRAM_BOT_TOKENS is malformed (expected nickname:token, comma separated)")
    if not tokens:
        return DoctorCheck("WARN", "No bot tokens loaded (only needed to edit bot commands, photo, or admin rights)")
    return DoctorCheck("OK", f"{len(tokens)} bot token(s) loaded")
```

Add it to the checks list in `run_doctor`:

```python
    checks = [
        check_python_version(version_info),
        check_config_presence(root, env, home),
        check_session_storage(env, home),
        check_bot_tokens(root, env, home),
    ]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bot_config.py tests/test_config.py tests/test_doctor.py -v`
Expected: PASS — all tests, including the pre-existing config and doctor tests.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_tools/config.py src/telegram_tools/doctor.py tests/test_bot_config.py
git commit -m "feat: read optional bot tokens from TELEGRAM_BOT_TOKENS"
```

---

### Task 2: Bot models and pure parsers

**Files:**
- Modify: `src/telegram_tools/models.py`
- Create: `src/telegram_tools/bots.py`
- Test: `tests/test_bots.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `BotCommandInfo(command: str, description: str)` with `to_dict()`
  - `BotInfo(id, username, name, bio, description, is_owned, has_photo, commands, group_rights, channel_rights)` with `to_dict()`
  - `right_names() -> list[str]`
  - `parse_rights(raw: str) -> ChatAdminRights`
  - `rights_to_names(rights: Any) -> list[str]`
  - `parse_commands_file(path: str | Path) -> list[BotCommandInfo]`
  - `DEFAULT_LANG_CODE = ""`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bots.py`:

```python
import json

import pytest

from telegram_tools.bots import parse_commands_file, parse_rights, right_names, rights_to_names
from telegram_tools.models import BotCommandInfo, BotInfo


def test_right_names_come_from_telethon_and_include_the_common_flags():
    names = right_names()

    assert "delete_messages" in names
    assert "ban_users" in names
    assert "self" not in names


def test_parse_rights_enables_only_the_named_rights():
    rights = parse_rights("delete_messages, ban_users")

    assert rights.delete_messages is True
    assert rights.ban_users is True
    assert rights.add_admins is False


def test_parse_rights_none_clears_everything():
    rights = parse_rights("none")

    assert all(getattr(rights, name) is False for name in right_names())


def test_parse_rights_rejects_an_unknown_right():
    with pytest.raises(ValueError, match="not_a_right"):
        parse_rights("delete_messages,not_a_right")


def test_rights_to_names_lists_only_enabled_rights():
    assert rights_to_names(parse_rights("ban_users")) == ["ban_users"]
    assert rights_to_names(None) == []


def test_parse_commands_file_reads_a_valid_file(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "/Start", "description": "Start the bot"}]))

    assert parse_commands_file(path) == [BotCommandInfo(command="start", description="Start the bot")]


def test_parse_commands_file_rejects_a_non_list(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps({"command": "start"}))

    with pytest.raises(ValueError, match="JSON list"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_a_bad_command_name(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "not a command", "description": "nope"}]))

    with pytest.raises(ValueError, match="a-z"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_an_empty_description(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": "start", "description": ""}]))

    with pytest.raises(ValueError, match="description"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_duplicates(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([
        {"command": "start", "description": "one"},
        {"command": "start", "description": "two"},
    ]))

    with pytest.raises(ValueError, match="more than once"):
        parse_commands_file(path)


def test_parse_commands_file_rejects_more_than_100_entries(tmp_path):
    path = tmp_path / "cmds.json"
    path.write_text(json.dumps([{"command": f"cmd{index}", "description": "x"} for index in range(101)]))

    with pytest.raises(ValueError, match="at most 100"):
        parse_commands_file(path)


def test_bot_info_to_dict_round_trips_nested_commands():
    info = BotInfo(
        id=12345,
        username="harrybot",
        name="Harry",
        bio="Assistant",
        description="Does things",
        is_owned=True,
        has_photo=True,
        commands=[BotCommandInfo(command="start", description="Start the bot")],
        group_rights=["delete_messages"],
        channel_rights=[],
    )

    assert info.to_dict() == {
        "id": 12345,
        "username": "harrybot",
        "name": "Harry",
        "bio": "Assistant",
        "description": "Does things",
        "is_owned": True,
        "has_photo": True,
        "commands": [{"command": "start", "description": "Start the bot"}],
        "group_rights": ["delete_messages"],
        "channel_rights": [],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telegram_tools.bots'`

- [ ] **Step 3: Add the models**

Append to `src/telegram_tools/models.py`:

```python
@dataclass(frozen=True)
class BotCommandInfo:
    command: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "description": self.description,
        }


@dataclass(frozen=True)
class BotInfo:
    id: int
    username: str | None
    name: str
    bio: str | None
    description: str | None
    is_owned: bool
    has_photo: bool = False
    commands: list[BotCommandInfo] = field(default_factory=list)
    group_rights: list[str] = field(default_factory=list)
    channel_rights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "bio": self.bio,
            "description": self.description,
            "is_owned": self.is_owned,
            "has_photo": self.has_photo,
            "commands": [command.to_dict() for command in self.commands],
            "group_rights": list(self.group_rights),
            "channel_rights": list(self.channel_rights),
        }
```

`bio` and `description` are `None` when they were never fetched (list view) or are unset; the profile formatter prints `(not set)` for both cases.

- [ ] **Step 4: Write the parsers**

Create `src/telegram_tools/bots.py`:

```python
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

from telethon.tl import types

from telegram_tools.models import BotCommandInfo, BotInfo

DEFAULT_LANG_CODE = ""
MAX_COMMANDS = 100
COMMAND_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


def right_names() -> list[str]:
    parameters = inspect.signature(types.ChatAdminRights.__init__).parameters
    return [name for name in parameters if name != "self"]


def parse_rights(raw: str) -> types.ChatAdminRights:
    valid = right_names()
    value = raw.strip().lower()
    if value in {"", "none"}:
        return types.ChatAdminRights(**{name: False for name in valid})

    selected = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in selected if item not in valid]
    if unknown:
        raise ValueError(f"Unknown admin right(s): {', '.join(unknown)}. Valid names: {', '.join(valid)}, or none.")
    return types.ChatAdminRights(**{name: name in selected for name in valid})


def rights_to_names(rights: Any) -> list[str]:
    if rights is None:
        return []
    return [name for name in right_names() if getattr(rights, name, False)]


def parse_commands_file(path: str | Path) -> list[BotCommandInfo]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError("Commands file must contain a JSON list of {command, description} objects.")
    if len(raw) > MAX_COMMANDS:
        raise ValueError(f"Commands file has {len(raw)} entries; Telegram allows at most {MAX_COMMANDS}.")

    commands: list[BotCommandInfo] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Command {index} must be an object with 'command' and 'description'.")
        command = str(entry.get("command", "")).strip().lstrip("/").lower()
        description = str(entry.get("description", "")).strip()
        if not COMMAND_PATTERN.match(command):
            raise ValueError(f"Command {index} ({command!r}) must be 1-32 characters of a-z, 0-9 or _.")
        if not 1 <= len(description) <= 256:
            raise ValueError(f"Command {index} (/{command}) needs a description of 1-256 characters.")
        if command in seen:
            raise ValueError(f"Command /{command} is listed more than once.")
        seen.add(command)
        commands.append(BotCommandInfo(command=command, description=description))
    return commands
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bots.py -v`
Expected: PASS — 12 tests.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_tools/models.py src/telegram_tools/bots.py tests/test_bots.py
git commit -m "feat: add bot models and admin-rights/commands parsers"
```

---

### Task 3: Read the bots you own

**Files:**
- Modify: `src/telegram_tools/bots.py`
- Test: `tests/test_bots.py`

**Interfaces:**
- Consumes: `BotInfo`, `BotCommandInfo`, `rights_to_names` (Task 2); `resolve_chat`, `EntityResolutionError` from `resolver.py`.
- Produces:
  - `ResolvedBot(user: Any, input_user: Any, is_owned: bool)`
  - `list_admined_bots(client) -> list[Any]` (raw Telethon users)
  - `list_bots(client) -> list[BotInfo]`
  - `resolve_bot(client, reference: str | int) -> ResolvedBot`
  - `get_bot_profile(client, resolved: ResolvedBot) -> BotInfo`
  - `format_bot_table(bots: list[BotInfo]) -> str`
  - `format_bot_profile(bot: BotInfo) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bots.py`, moving the four new `import` lines up to the existing
import block at the top of the file:

```python
import asyncio
from types import SimpleNamespace

from telegram_tools.bots import ResolvedBot, format_bot_profile, format_bot_table, get_bot_profile, list_bots, resolve_bot
from telegram_tools.resolver import EntityResolutionError


def fake_bot_user(bot_id=12345, username="harrybot", first_name="Harry"):
    return SimpleNamespace(id=bot_id, username=username, first_name=first_name, bot=True, photo=None, usernames=None)


class FakeClient:
    """Stands in for TelegramClient: records TL requests and replays canned answers."""

    def __init__(self, *, admined=None, full=None):
        self.admined = admined if admined is not None else []
        self.full = full
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        name = type(request).__name__
        if name == "GetAdminedBotsRequest":
            return self.admined  # returned as-is: one test passes a boxed SimpleNamespace(users=[...])
        if name == "GetFullUserRequest":
            return self.full
        raise AssertionError(f"unexpected request {name}")

    async def get_input_entity(self, entity):
        return SimpleNamespace(user_id=entity.id)


def fake_full_user(*, about="Assistant", description="Does things", commands=(), group_rights=None, channel_rights=None, photo=None):
    bot_info = SimpleNamespace(
        description=description,
        commands=[SimpleNamespace(command=command, description=text) for command, text in commands],
    )
    return SimpleNamespace(
        full_user=SimpleNamespace(
            about=about,
            bot_info=bot_info,
            bot_group_admin_rights=group_rights,
            bot_broadcast_admin_rights=channel_rights,
            profile_photo=photo,
        )
    )


def test_list_bots_maps_admined_bots_to_bot_info():
    client = FakeClient(admined=[fake_bot_user()])

    bots = asyncio.run(list_bots(client))

    assert [bot.id for bot in bots] == [12345]
    assert bots[0].username == "harrybot"
    assert bots[0].name == "Harry"
    assert bots[0].is_owned is True


def test_list_bots_accepts_a_boxed_users_result():
    client = FakeClient(admined=SimpleNamespace(users=[fake_bot_user()]))

    assert [bot.id for bot in asyncio.run(list_bots(client))] == [12345]


def test_resolve_bot_matches_an_owned_bot_by_username_case_insensitively():
    client = FakeClient(admined=[fake_bot_user()])

    resolved = asyncio.run(resolve_bot(client, "@HarryBot"))

    assert resolved.is_owned is True
    assert resolved.user.id == 12345


def test_resolve_bot_matches_an_owned_bot_by_numeric_id():
    client = FakeClient(admined=[fake_bot_user()])

    assert asyncio.run(resolve_bot(client, 12345)).is_owned is True


def test_resolve_bot_falls_back_to_entity_lookup_for_a_bot_you_do_not_own(monkeypatch):
    client = FakeClient(admined=[])
    other = SimpleNamespace(id=999, username="otherbot", first_name="Other", bot=True)

    async def fake_resolve_chat(_client, _reference):
        return SimpleNamespace(id=999, entity=other, input_entity=SimpleNamespace(user_id=999))

    monkeypatch.setattr("telegram_tools.bots.resolve_chat", fake_resolve_chat)
    resolved = asyncio.run(resolve_bot(client, "@otherbot"))

    assert resolved.is_owned is False
    assert resolved.user.id == 999


def test_resolve_bot_rejects_a_reference_that_is_not_a_bot(monkeypatch):
    client = FakeClient(admined=[])
    human = SimpleNamespace(id=42, username="sven", first_name="Sven", bot=False)

    async def fake_resolve_chat(_client, _reference):
        return SimpleNamespace(id=42, entity=human, input_entity=SimpleNamespace(user_id=42))

    monkeypatch.setattr("telegram_tools.bots.resolve_chat", fake_resolve_chat)

    with pytest.raises(EntityResolutionError, match="not a bot"):
        asyncio.run(resolve_bot(client, "@sven"))


def test_get_bot_profile_reads_bio_description_commands_and_rights():
    from telegram_tools.bots import parse_rights

    user = fake_bot_user()
    client = FakeClient(
        full=fake_full_user(
            commands=[("start", "Start the bot")],
            group_rights=parse_rights("delete_messages"),
            photo=SimpleNamespace(id=1),
        )
    )
    resolved = ResolvedBot(user=user, input_user=SimpleNamespace(user_id=12345), is_owned=True)

    profile = asyncio.run(get_bot_profile(client, resolved))

    assert profile.bio == "Assistant"
    assert profile.description == "Does things"
    assert profile.commands == [BotCommandInfo(command="start", description="Start the bot")]
    assert profile.group_rights == ["delete_messages"]
    assert profile.channel_rights == []
    assert profile.has_photo is True


def test_format_bot_table_lists_ids_and_usernames():
    bots = [BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

    output = format_bot_table(bots)

    assert "12345" in output
    assert "@harrybot" in output
    assert "Harry" in output


def test_format_bot_table_handles_no_bots():
    assert format_bot_table([]) == "No bots found."


def test_format_bot_profile_marks_a_bot_you_do_not_own():
    bot = BotInfo(id=999, username="otherbot", name="Other", bio=None, description=None, is_owned=False)

    output = format_bot_profile(bot)

    assert "not owned by you" in output
    assert "(not set)" in output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bots.py -v`
Expected: FAIL — `ImportError: cannot import name 'ResolvedBot'`

- [ ] **Step 3: Implement the reads**

Append to `src/telegram_tools/bots.py` (and add `from dataclasses import dataclass`, `from telethon.tl import functions, types`, `from telegram_tools.resolver import EntityResolutionError, resolve_chat` to the imports):

```python
@dataclass(frozen=True)
class ResolvedBot:
    user: Any
    input_user: Any
    is_owned: bool


def _users_from_result(result: Any) -> list[Any]:
    if isinstance(result, list):
        return list(result)
    return list(getattr(result, "users", []) or [])


def _bot_keys(user: Any) -> set[str]:
    keys = {str(int(getattr(user, "id")))}
    username = getattr(user, "username", None)
    if username:
        keys.add(str(username).lower())
    for extra in getattr(user, "usernames", None) or []:
        name = getattr(extra, "username", None)
        if name:
            keys.add(str(name).lower())
    return keys


def bot_info_from_user(user: Any, *, is_owned: bool, **fields: Any) -> BotInfo:
    return BotInfo(
        id=int(getattr(user, "id")),
        username=getattr(user, "username", None),
        name=str(getattr(user, "first_name", "") or ""),
        bio=fields.get("bio"),
        description=fields.get("description"),
        is_owned=is_owned,
        has_photo=bool(fields.get("has_photo", getattr(user, "photo", None) is not None)),
        commands=list(fields.get("commands") or []),
        group_rights=list(fields.get("group_rights") or []),
        channel_rights=list(fields.get("channel_rights") or []),
    )


async def list_admined_bots(client) -> list[Any]:
    return _users_from_result(await client(functions.bots.GetAdminedBotsRequest()))


async def list_bots(client) -> list[BotInfo]:
    return [bot_info_from_user(user, is_owned=True) for user in await list_admined_bots(client)]


async def resolve_bot(client, reference: str | int) -> ResolvedBot:
    key = str(reference).strip().lstrip("@").lower()
    for user in await list_admined_bots(client):
        if key in _bot_keys(user):
            return ResolvedBot(user=user, input_user=await client.get_input_entity(user), is_owned=True)

    resolved = await resolve_chat(client, reference)
    if not getattr(resolved.entity, "bot", False):
        raise EntityResolutionError(f"{reference!r} is not a bot.")
    return ResolvedBot(user=resolved.entity, input_user=resolved.input_entity, is_owned=False)


async def get_bot_profile(client, resolved: ResolvedBot) -> BotInfo:
    result = await client(functions.users.GetFullUserRequest(id=resolved.input_user))
    full_user = getattr(result, "full_user", None)
    bot_info = getattr(full_user, "bot_info", None)
    commands = [
        BotCommandInfo(
            command=str(getattr(command, "command", "")),
            description=str(getattr(command, "description", "")),
        )
        for command in (getattr(bot_info, "commands", None) or [])
    ]
    return bot_info_from_user(
        resolved.user,
        is_owned=resolved.is_owned,
        bio=getattr(full_user, "about", None),
        description=getattr(bot_info, "description", None),
        commands=commands,
        group_rights=rights_to_names(getattr(full_user, "bot_group_admin_rights", None)),
        channel_rights=rights_to_names(getattr(full_user, "bot_broadcast_admin_rights", None)),
        has_photo=getattr(full_user, "profile_photo", None) is not None or getattr(resolved.user, "photo", None) is not None,
    )


def _or_not_set(value: str | None) -> str:
    return value if value else "(not set)"


def format_bot_table(bots: list[BotInfo]) -> str:
    if not bots:
        return "No bots found."

    width = max(len(str(bot.id)) for bot in bots)
    lines = ["My Bots", "=" * len("My Bots")]
    for bot in bots:
        username = f"@{bot.username}" if bot.username else "(no username)"
        lines.append(f"{bot.id:<{width}}  {username}  {bot.name}")
    return "\n".join(lines)


def format_bot_profile(bot: BotInfo) -> str:
    lines = [
        bot.name or "(unnamed)",
        f"Bot ID: {bot.id}",
        f"Username: @{bot.username}" if bot.username else "Username: (none)",
        f"Bio: {_or_not_set(bot.bio)}",
        f"Description: {_or_not_set(bot.description)}",
        f"Profile photo: {'set' if bot.has_photo else 'not set'}",
        f"Default group rights: {', '.join(bot.group_rights) or '(none)'}",
        f"Default channel rights: {', '.join(bot.channel_rights) or '(none)'}",
    ]
    if not bot.is_owned:
        lines.append("Note: not owned by you - read-only.")

    lines.extend(["", "Commands", "--------------------------------------------"])
    if bot.commands:
        width = max(len(command.command) for command in bot.commands)
        lines.extend(f"/{command.command:<{width}}  {command.description}" for command in bot.commands)
    else:
        lines.append("(none)")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bots.py -v`
Expected: PASS — the 10 new tests plus the 12 from Task 2.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_tools/bots.py tests/test_bots.py
git commit -m "feat: list owned bots and read a bot profile"
```

---

### Task 4: Edit plan, confirmation, and owner-session writes

**Files:**
- Modify: `src/telegram_tools/bots.py`
- Test: `tests/test_bot_edits.py` (create)

**Interfaces:**
- Consumes: `BotInfo`, `BotCommandInfo`, `parse_rights`, `rights_to_names`, `DEFAULT_LANG_CODE` (Tasks 2–3).
- Produces:
  - `EditChange(field: str, rail: str, old: str, new: str, value: Any)`
  - `EditPlan(changes: list[EditChange], skipped: list[str])` with `.owner_changes`, `.bot_changes`, `.is_empty`
  - `build_edit_plan(current: BotInfo, requested: Mapping[str, Any]) -> EditPlan`
  - `format_edit_plan(plan: EditPlan) -> str`
  - `confirm_bot_edits(plan, *, read=input, write=print) -> bool`
  - `apply_owner_edits(client, input_user, changes: list[EditChange]) -> list[str]`

`requested` keys are the CLI flag names with dashes turned into underscores: `name`, `bio`, `description`, `photo` (path string), `remove_photo` (bool), `commands` (`list[BotCommandInfo]`), `clear_commands` (bool), `group_rights` / `channel_rights` (`ChatAdminRights`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bot_edits.py`:

```python
import asyncio
from types import SimpleNamespace

from telegram_tools.bots import (
    apply_owner_edits,
    build_edit_plan,
    confirm_bot_edits,
    format_edit_plan,
    parse_rights,
)
from telegram_tools.models import BotCommandInfo, BotInfo


def current_bot(**overrides):
    defaults = dict(
        id=12345,
        username="harrybot",
        name="Harry",
        bio="Assistant",
        description="Does things",
        is_owned=True,
        has_photo=True,
        commands=[BotCommandInfo(command="start", description="Start the bot")],
        group_rights=["delete_messages"],
        channel_rights=[],
    )
    defaults.update(overrides)
    return BotInfo(**defaults)


class RecordingClient:
    def __init__(self):
        self.requests = []
        self.uploaded = []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace()

    async def upload_file(self, path):
        self.uploaded.append(path)
        return SimpleNamespace(name=str(path))


def test_build_edit_plan_keeps_only_real_changes():
    plan = build_edit_plan(current_bot(), {"name": "Harry", "bio": "New bio"})

    assert [change.field for change in plan.changes] == ["bio"]
    assert plan.skipped == ["name"]


def test_build_edit_plan_treats_clearing_a_set_field_as_a_change():
    plan = build_edit_plan(current_bot(), {"bio": ""})

    assert [change.field for change in plan.changes] == ["bio"]
    assert plan.skipped == []


def test_build_edit_plan_skips_clearing_a_field_that_is_already_unset():
    plan = build_edit_plan(current_bot(bio=None), {"bio": ""})

    assert plan.is_empty is True


def test_build_edit_plan_splits_rails():
    plan = build_edit_plan(
        current_bot(),
        {"name": "Harry Two", "group_rights": parse_rights("ban_users")},
    )

    assert [change.field for change in plan.owner_changes] == ["name"]
    assert [change.field for change in plan.bot_changes] == ["group_rights"]


def test_build_edit_plan_skips_commands_that_already_match():
    plan = build_edit_plan(
        current_bot(),
        {"commands": [BotCommandInfo(command="start", description="Start the bot")]},
    )

    assert plan.is_empty is True
    assert plan.skipped == ["commands"]


def test_build_edit_plan_skips_clearing_when_there_is_nothing_to_clear():
    plan = build_edit_plan(current_bot(commands=[]), {"clear_commands": True})

    assert plan.is_empty is True


def test_build_edit_plan_skips_removing_a_photo_that_does_not_exist():
    plan = build_edit_plan(current_bot(has_photo=False), {"remove_photo": True})

    assert plan.is_empty is True


def test_build_edit_plan_always_treats_a_new_photo_as_a_change():
    plan = build_edit_plan(current_bot(), {"photo": "face.png"})

    assert [change.field for change in plan.changes] == ["photo"]


def test_build_edit_plan_skips_rights_that_already_match():
    plan = build_edit_plan(current_bot(), {"group_rights": parse_rights("delete_messages")})

    assert plan.is_empty is True


def test_format_edit_plan_shows_old_and_new_and_truncates_long_text():
    plan = build_edit_plan(current_bot(), {"description": "x" * 200})

    output = format_edit_plan(plan)

    assert "description" in output
    assert "Does things" in output
    assert "..." in output


def test_confirm_bot_edits_requires_a_y():
    plan = build_edit_plan(current_bot(), {"bio": "New bio"})
    written = []

    assert confirm_bot_edits(plan, read=lambda _prompt: "n", write=written.append) is False
    assert confirm_bot_edits(plan, read=lambda _prompt: "Y", write=written.append) is True


def test_apply_owner_edits_sends_one_set_bot_info_request_for_text_fields():
    plan = build_edit_plan(current_bot(), {"name": "Harry Two", "bio": "New bio"})
    client = RecordingClient()
    input_user = SimpleNamespace(user_id=12345)

    applied = asyncio.run(apply_owner_edits(client, input_user, plan.owner_changes))

    assert applied == ["bio", "name"]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert type(request).__name__ == "SetBotInfoRequest"
    assert request.name == "Harry Two"
    assert request.about == "New bio"
    assert request.description is None


def test_apply_owner_edits_uploads_and_sets_a_photo():
    plan = build_edit_plan(current_bot(), {"photo": "face.png"})
    client = RecordingClient()

    applied = asyncio.run(apply_owner_edits(client, SimpleNamespace(user_id=12345), plan.owner_changes))

    assert applied == ["photo"]
    assert client.uploaded == ["face.png"]
    assert type(client.requests[0]).__name__ == "UploadProfilePhotoRequest"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bot_edits.py -v`
Expected: FAIL — `ImportError: cannot import name 'EditChange'`

- [ ] **Step 3: Implement the plan builder and formatter**

Append to `src/telegram_tools/bots.py` (add `from collections.abc import Callable, Mapping` to the imports):

```python
_DISPLAY_WIDTH = 60


@dataclass(frozen=True)
class EditChange:
    field: str
    rail: str
    old: str
    new: str
    value: Any


@dataclass(frozen=True)
class EditPlan:
    changes: list[EditChange]
    skipped: list[str]

    @property
    def owner_changes(self) -> list[EditChange]:
        return [change for change in self.changes if change.rail == "owner"]

    @property
    def bot_changes(self) -> list[EditChange]:
        return [change for change in self.changes if change.rail == "bot"]

    @property
    def is_empty(self) -> bool:
        return not self.changes


def _truncate(value: str) -> str:
    return value if len(value) <= _DISPLAY_WIDTH else f"{value[:_DISPLAY_WIDTH]}..."


def _commands_display(commands: list[BotCommandInfo]) -> str:
    return ", ".join(f"/{command.command}" for command in commands) or "(none)"


def build_edit_plan(current: BotInfo, requested: Mapping[str, Any]) -> EditPlan:
    changes: list[EditChange] = []
    skipped: list[str] = []

    def add(field: str, rail: str, old: str, new: str, value: Any, *, changed: bool) -> None:
        if changed:
            changes.append(EditChange(field=field, rail=rail, old=old, new=new, value=value))
        else:
            skipped.append(field)

    for field_name, existing in (("name", current.name), ("bio", current.bio), ("description", current.description)):
        if field_name in requested:
            new_value = str(requested[field_name])
            add(field_name, "owner", _or_not_set(existing), new_value, new_value, changed=new_value != (existing or ""))

    if "photo" in requested:
        add("photo", "owner", "set" if current.has_photo else "not set", str(requested["photo"]), requested["photo"], changed=True)

    if requested.get("remove_photo"):
        add("remove_photo", "bot", "set" if current.has_photo else "not set", "not set", True, changed=current.has_photo)

    if "commands" in requested:
        new_commands = list(requested["commands"])
        changed = [(command.command, command.description) for command in new_commands] != [
            (command.command, command.description) for command in current.commands
        ]
        add("commands", "bot", _commands_display(current.commands), _commands_display(new_commands), new_commands, changed=changed)

    if requested.get("clear_commands"):
        add("clear_commands", "bot", _commands_display(current.commands), "(none)", True, changed=bool(current.commands))

    for field_name, existing_names in (("group_rights", current.group_rights), ("channel_rights", current.channel_rights)):
        if field_name in requested:
            rights = requested[field_name]
            new_names = rights_to_names(rights)
            add(
                field_name,
                "bot",
                ", ".join(existing_names) or "(none)",
                ", ".join(new_names) or "(none)",
                rights,
                changed=sorted(new_names) != sorted(existing_names),
            )

    return EditPlan(changes=changes, skipped=skipped)


def format_edit_plan(plan: EditPlan) -> str:
    lines = ["Changes", "--------------------------------------------"]
    lines.extend(f"{change.field}: {_truncate(change.old)} -> {_truncate(change.new)}" for change in plan.changes)
    if plan.skipped:
        lines.append(f"Unchanged (skipped): {', '.join(plan.skipped)}")
    return "\n".join(lines)


def confirm_bot_edits(plan: EditPlan, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    write(format_edit_plan(plan))
    return read("Apply these changes? [y/N]: ").strip().lower() == "y"


async def apply_owner_edits(client, input_user, changes: list[EditChange]) -> list[str]:
    applied: list[str] = []
    info_fields = {change.field: change.value for change in changes if change.field in {"name", "bio", "description"}}
    if info_fields:
        await client(
            functions.bots.SetBotInfoRequest(
                bot=input_user,
                lang_code=DEFAULT_LANG_CODE,
                name=info_fields.get("name"),
                about=info_fields.get("bio"),
                description=info_fields.get("description"),
            )
        )
        applied.extend(sorted(info_fields))

    for change in changes:
        if change.field != "photo":
            continue
        uploaded = await client.upload_file(change.value)
        await client(functions.photos.UploadProfilePhotoRequest(bot=input_user, file=uploaded))
        applied.append("photo")

    return applied
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bot_edits.py -v`
Expected: PASS — 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_tools/bots.py tests/test_bot_edits.py
git commit -m "feat: build, show, confirm, and apply owner-session bot edits"
```

---

### Task 5: Token rail — commands, default admin rights, photo removal

**Files:**
- Create: `src/telegram_tools/bot_session.py`
- Test: `tests/test_bot_session.py` (create)

**Interfaces:**
- Consumes: `Config` (Task 1), `EditChange`, `DEFAULT_LANG_CODE` (Tasks 2 and 4).
- Produces:
  - `bot_client(config: Config, token: str)` — async context manager yielding a connected bot `TelegramClient`.
  - `apply_bot_edits(client, changes: list[EditChange]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bot_session.py`:

```python
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types

from telegram_tools.bots import EditChange, parse_rights
from telegram_tools.bot_session import apply_bot_edits, bot_client
from telegram_tools.config import Config
from telegram_tools.models import BotCommandInfo


class FakeBotClient:
    def __init__(self, *, photos=()):
        self.requests = []
        self.photos = list(photos)

    async def __call__(self, request):
        self.requests.append(request)
        if type(request).__name__ == "GetUserPhotosRequest":
            return SimpleNamespace(photos=self.photos)
        return SimpleNamespace()


def change(field, value, rail="bot"):
    return EditChange(field=field, rail=rail, old="", new="", value=value)


def test_apply_bot_edits_sets_commands():
    client = FakeBotClient()
    commands = [BotCommandInfo(command="start", description="Start the bot")]

    applied = asyncio.run(apply_bot_edits(client, [change("commands", commands)]))

    assert applied == ["commands"]
    request = client.requests[0]
    assert type(request).__name__ == "SetBotCommandsRequest"
    assert [command.command for command in request.commands] == ["start"]
    assert type(request.scope).__name__ == "BotCommandScopeDefault"


def test_apply_bot_edits_clears_commands():
    client = FakeBotClient()

    applied = asyncio.run(apply_bot_edits(client, [change("clear_commands", True)]))

    assert applied == ["clear_commands"]
    assert type(client.requests[0]).__name__ == "ResetBotCommandsRequest"


def test_apply_bot_edits_sets_group_and_channel_rights():
    client = FakeBotClient()
    changes = [change("group_rights", parse_rights("ban_users")), change("channel_rights", parse_rights("none"))]

    applied = asyncio.run(apply_bot_edits(client, changes))

    assert applied == ["group_rights", "channel_rights"]
    assert type(client.requests[0]).__name__ == "SetBotGroupDefaultAdminRightsRequest"
    assert type(client.requests[1]).__name__ == "SetBotBroadcastDefaultAdminRightsRequest"


def test_apply_bot_edits_removes_the_current_photo():
    # A real types.Photo, not a SimpleNamespace: utils.get_input_photo is isinstance-driven
    # and rejects a stand-in, and the point of this test is the real conversion.
    photo = types.Photo(id=7, access_hash=8, file_reference=b"ref", date=None, sizes=[], dc_id=2)
    client = FakeBotClient(photos=[photo])

    applied = asyncio.run(apply_bot_edits(client, [change("remove_photo", True)]))

    assert applied == ["remove_photo"]
    assert type(client.requests[0]).__name__ == "GetUserPhotosRequest"
    delete_request = client.requests[1]
    assert type(delete_request).__name__ == "DeletePhotosRequest"
    assert delete_request.id[0].id == 7
    assert delete_request.id[0].access_hash == 8


def test_apply_bot_edits_is_a_no_op_when_there_is_no_photo_to_remove():
    client = FakeBotClient(photos=[])

    applied = asyncio.run(apply_bot_edits(client, [change("remove_photo", True)]))

    assert applied == []
    assert [type(request).__name__ for request in client.requests] == ["GetUserPhotosRequest"]


class FakeTelegramClient:
    """Stands in for TelegramClient so bot_client can be tested without a network."""

    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.flood_sleep_threshold = None
        self.started_with = None
        self.disconnected = False
        self.start_error = None

    async def start(self, bot_token=None):
        self.started_with = bot_token
        if self.start_error:
            raise self.start_error

    async def disconnect(self):
        self.disconnected = True


def patch_client(monkeypatch, *, start_error=None):
    created = []

    def factory(session, api_id, api_hash):
        client = FakeTelegramClient(session, api_id, api_hash)
        client.start_error = start_error
        created.append(client)
        return client

    monkeypatch.setattr("telegram_tools.bot_session.TelegramClient", factory)
    return created


def fake_config():
    return Config(api_id=1, api_hash="hash", session_path=Path("unused"))


def test_bot_client_disconnects_after_a_normal_exit(monkeypatch):
    created = patch_client(monkeypatch)

    async def run():
        async with bot_client(fake_config(), "12345:AAOne") as client:
            assert client.started_with == "12345:AAOne"

    asyncio.run(run())

    assert created[0].disconnected is True


def test_bot_client_disconnects_when_start_fails(monkeypatch):
    created = patch_client(monkeypatch, start_error=RuntimeError("bad token"))

    async def run():
        async with bot_client(fake_config(), "12345:AAOne"):
            raise AssertionError("body must not run when start fails")

    with pytest.raises(RuntimeError, match="bad token"):
        asyncio.run(run())

    assert created[0].disconnected is True


def test_bot_client_keeps_the_token_out_of_the_session(monkeypatch):
    created = patch_client(monkeypatch)

    async def run():
        async with bot_client(fake_config(), "12345:AAOne"):
            pass

    asyncio.run(run())

    assert type(created[0].session).__name__ == "MemorySession"
```

The last three cover `bot_client` itself: it opens a real connection, so it is tested by
substituting the client class in the module namespace. They pin the two things that
matter — the connection is always closed, including when a bad token makes `start()`
raise before the body runs, and the session is never file-backed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bot_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telegram_tools.bot_session'`

- [ ] **Step 3: Implement the bot rail**

Create `src/telegram_tools/bot_session.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient, utils
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from telegram_tools.bots import DEFAULT_LANG_CODE, EditChange
from telegram_tools.config import Config


@asynccontextmanager
async def bot_client(config: Config, token: str) -> AsyncIterator[TelegramClient]:
    """Connect as the bot itself. MemorySession keeps the token off disk."""
    client = TelegramClient(MemorySession(), config.api_id, config.api_hash)
    client.flood_sleep_threshold = 24 * 60 * 60
    try:
        # start() must be inside the try: Telethon connects before it signs in, so a
        # bad token raises with the transport already open, and an asynccontextmanager
        # never runs its finally when the generator raises before yield.
        await client.start(bot_token=token)
        yield client
    finally:
        await client.disconnect()


async def _remove_photo(client) -> bool:
    result = await client(
        functions.photos.GetUserPhotosRequest(user_id=types.InputUserSelf(), offset=0, max_id=0, limit=1)
    )
    photos = list(getattr(result, "photos", []) or [])
    if not photos:
        return False
    await client(functions.photos.DeletePhotosRequest(id=[utils.get_input_photo(photos[0])]))
    return True


async def apply_bot_edits(client, changes: list[EditChange]) -> list[str]:
    applied: list[str] = []
    for change in changes:
        if change.field == "commands":
            await client(
                functions.bots.SetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code=DEFAULT_LANG_CODE,
                    commands=[
                        types.BotCommand(command=command.command, description=command.description)
                        for command in change.value
                    ],
                )
            )
        elif change.field == "clear_commands":
            await client(
                functions.bots.ResetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code=DEFAULT_LANG_CODE,
                )
            )
        elif change.field == "group_rights":
            await client(functions.bots.SetBotGroupDefaultAdminRightsRequest(admin_rights=change.value))
        elif change.field == "channel_rights":
            await client(functions.bots.SetBotBroadcastDefaultAdminRightsRequest(admin_rights=change.value))
        elif change.field == "remove_photo":
            if not await _remove_photo(client):
                continue
        else:
            raise ValueError(f"Unknown bot-token edit: {change.field}")
        applied.append(change.field)
    return applied
```

`utils.get_input_photo` turns the `Photo` returned by `photos.getUserPhotos` into the `InputPhoto` that `photos.deletePhotos` needs; the `UserProfilePhoto` on a user object does not carry the access hash, so it cannot be used here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bot_session.py -v`
Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_tools/bot_session.py tests/test_bot_session.py
git commit -m "feat: apply token-only bot edits over a memory-session bot client"
```

---

### Task 6: CLI command and interactive menu

**Files:**
- Modify: `src/telegram_tools/cli.py`
- Test: `tests/test_cli.py`, `tests/test_bot_cli.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `bot_edit_requests(args) -> dict[str, Any]` — raw argparse values keyed by field name.
  - `_run_bots(client, args, config) -> int`
  - `bots` subparser; interactive menu entries 7 and 8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_bots_command_defaults_to_listing():
    args = parse_args("bots")

    assert args.command == "bots"
    assert args.bot is None
    assert args.json_output is None
    assert args.yes is False


def test_bots_command_accepts_every_edit_flag():
    args = parse_args(
        "bots",
        "--bot", "harry",
        "--name", "Harry",
        "--bio", "Assistant",
        "--description", "Does things",
        "--commands", "cmds.json",
        "--photo", "face.png",
        "--group-rights", "delete_messages",
        "--channel-rights", "none",
        "--yes",
    )

    assert args.bot == "harry"
    assert args.name == "Harry"
    assert args.bio == "Assistant"
    assert args.description == "Does things"
    assert args.commands == "cmds.json"
    assert args.photo == "face.png"
    assert args.group_rights == "delete_messages"
    assert args.channel_rights == "none"
    assert args.yes is True


def test_bots_command_rejects_commands_with_clear_commands():
    with pytest.raises(SystemExit):
        parse_args("bots", "--bot", "harry", "--commands", "cmds.json", "--clear-commands")


def test_bots_command_rejects_photo_with_remove_photo():
    with pytest.raises(SystemExit):
        parse_args("bots", "--bot", "harry", "--photo", "face.png", "--remove-photo")
```

Create `tests/test_bot_cli.py`:

```python
import argparse

import pytest

from telegram_tools.cli import bot_edit_requests


def namespace(**kwargs):
    defaults = dict(
        bot=None,
        name=None,
        bio=None,
        description=None,
        commands=None,
        clear_commands=False,
        photo=None,
        remove_photo=False,
        group_rights=None,
        channel_rights=None,
        yes=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(command="bots", json_output=None, **defaults)


def test_bot_edit_requests_is_empty_without_edit_flags():
    assert bot_edit_requests(namespace(bot="harry")) == {}


def test_bot_edit_requests_collects_only_the_supplied_flags():
    requested = bot_edit_requests(namespace(bot="harry", name="Harry", clear_commands=True))

    assert requested == {"name": "Harry", "clear_commands": True}


def test_bot_edit_requests_keeps_an_empty_string_as_a_clearing_edit():
    assert bot_edit_requests(namespace(bot="harry", bio="")) == {"bio": ""}
```

Append the wiring tests to the same file — these cover the two paths that must never
touch Telegram by accident:

```python
import asyncio
from types import SimpleNamespace

from telegram_tools.bots import ResolvedBot
from telegram_tools.cli import _run_bots
from telegram_tools.config import Config
from telegram_tools.models import BotInfo


def fake_config(**tokens):
    from pathlib import Path

    return Config(api_id=1, api_hash="hash", session_path=Path("unused"), bot_tokens=dict(tokens))


def patch_bot_reads(monkeypatch, profile):
    async def fake_resolve_bot(_client, _reference):
        return ResolvedBot(user=SimpleNamespace(id=profile.id), input_user=SimpleNamespace(user_id=profile.id), is_owned=profile.is_owned)

    async def fake_get_bot_profile(_client, _resolved):
        return profile

    monkeypatch.setattr("telegram_tools.cli.resolve_bot", fake_resolve_bot)
    monkeypatch.setattr("telegram_tools.cli.get_bot_profile", fake_get_bot_profile)


def owned_profile(**overrides):
    defaults = dict(id=12345, username="harrybot", name="Harry", bio="Assistant", description="Does things", is_owned=True)
    defaults.update(overrides)
    return BotInfo(**defaults)


def test_run_bots_rejects_edit_flags_without_a_bot():
    with pytest.raises(ValueError, match="--bot is required"):
        asyncio.run(_run_bots(object(), namespace(name="Harry"), fake_config()))


def test_run_bots_cancels_without_applying_anything(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("no edit should be applied after a cancel")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: False)

    exit_code = _run_and_capture(namespace(bot="harry", name="Harry Two"), fake_config())

    assert exit_code == 1
    assert '"cancelled": true' in capsys.readouterr().out


def test_run_bots_skips_the_prompt_with_yes(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile())
    applied = []

    async def fake_apply_owner_edits(_client, _input_user, changes):
        applied.extend(change.field for change in changes)
        return list(applied)

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: pytest.fail("--yes must not prompt"))

    assert _run_and_capture(namespace(bot="harry", name="Harry Two", yes=True), fake_config()) == 0
    assert applied == ["name"]


def test_run_bots_refuses_token_only_fields_without_a_token(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(commands=[BotCommandInfo(command="start", description="Start")]))

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKENS"):
        _run_and_capture(namespace(bot="harry", clear_commands=True), fake_config())


def _run_and_capture(args, config):
    return asyncio.run(_run_bots(object(), args, config))
```

Add `from telegram_tools.models import BotCommandInfo` to the imports at the top of the
file, and move the appended `import` lines up into that block.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_bot_cli.py -v`
Expected: FAIL — `SystemExit` on the unknown `bots` command, and `ImportError` for `bot_edit_requests`.

- [ ] **Step 3: Add the subparser**

In `build_parser()` in `src/telegram_tools/cli.py`, before the `doctor` line:

```python
    bots_parser = subparsers.add_parser("bots", help="List the bots you own and edit their BotFather settings")
    bots_parser.add_argument("--bot", help="Bot nickname from TELEGRAM_BOT_TOKENS, @username, or numeric ID")
    bots_parser.add_argument("--json", dest="json_output", help="Write bot output to this JSON file")
    bots_parser.add_argument("--name", help="Set the display name shown in chat lists")
    bots_parser.add_argument("--bio", help="Set the short bio shown under the bot profile")
    bots_parser.add_argument("--description", help="Set the 'what can this bot do?' text shown before Start")
    commands_group = bots_parser.add_mutually_exclusive_group()
    commands_group.add_argument("--commands", help="Path to a JSON file of {command, description} objects (needs a bot token)")
    commands_group.add_argument("--clear-commands", action="store_true", help="Remove every command (needs a bot token)")
    photo_group = bots_parser.add_mutually_exclusive_group()
    photo_group.add_argument("--photo", help="Path to a new profile photo")
    photo_group.add_argument("--remove-photo", action="store_true", help="Remove the current profile photo (needs a bot token)")
    bots_parser.add_argument("--group-rights", help="Default admin rights for groups: comma-separated names, or none (needs a bot token)")
    bots_parser.add_argument("--channel-rights", help="Default admin rights for channels: comma-separated names, or none (needs a bot token)")
    bots_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
```

- [ ] **Step 4: Add the handler**

Add the imports at the top of `cli.py`:

```python
from telegram_tools.bot_session import apply_bot_edits, bot_client
from telegram_tools.bots import (
    apply_owner_edits,
    build_edit_plan,
    confirm_bot_edits,
    format_bot_profile,
    format_bot_table,
    get_bot_profile,
    list_bots,
    parse_commands_file,
    parse_rights,
    resolve_bot,
)
from telegram_tools.config import ConfigError, bot_id_from_token, load_config, lookup_bot_token
```

Then add these functions after `_run_search`:

```python
EDIT_FLAGS = ("name", "bio", "description", "commands", "clear_commands", "photo", "remove_photo", "group_rights", "channel_rights")


def bot_edit_requests(args) -> dict:
    requested = {}
    for flag in EDIT_FLAGS:
        value = getattr(args, flag, None)
        if value is None or value is False:
            continue
        requested[flag] = value
    return requested


def _write_json(payload, path: str | None) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n")


async def _run_bots(client, args, config) -> int:
    requested = bot_edit_requests(args)
    if requested and not args.bot:
        raise ValueError("--bot is required when editing a bot.")

    if not args.bot:
        bots = await list_bots(client)
        if args.json_output:
            _write_json([bot.to_dict() for bot in bots], args.json_output)
        else:
            print(format_bot_table(bots))
        return 0

    token = lookup_bot_token(config.bot_tokens, args.bot)
    reference = args.bot
    if token is not None:
        reference = bot_id_from_token(token) or args.bot

    resolved = await resolve_bot(client, reference)
    profile = await get_bot_profile(client, resolved)
    token = token or lookup_bot_token(config.bot_tokens, profile.username, profile.id)

    if not requested:
        if args.json_output:
            _write_json(profile.to_dict(), args.json_output)
        else:
            print(format_bot_profile(profile))
        return 0

    if not resolved.is_owned:
        raise PermissionError(f"You do not own @{profile.username or profile.id}; only its owner can edit it.")

    if "commands" in requested:
        requested["commands"] = parse_commands_file(requested["commands"])
    for field in ("group_rights", "channel_rights"):
        if field in requested:
            requested[field] = parse_rights(requested[field])

    plan = build_edit_plan(profile, requested)
    if plan.is_empty:
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": [], "skipped": plan.skipped, "cancelled": False}, indent=2))
        return 0

    if plan.bot_changes and token is None:
        fields = ", ".join(change.field for change in plan.bot_changes)
        raise ValueError(
            f"{fields} can only be changed with that bot's token. "
            "Set TELEGRAM_BOT_TOKENS=nickname:token[,nickname:token] in ~/.telegram-tools/.env."
        )

    if not args.yes and not confirm_bot_edits(plan):
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": [], "skipped": plan.skipped, "cancelled": True}, indent=2))
        return 1

    applied = []
    try:
        applied.extend(await apply_owner_edits(client, resolved.input_user, plan.owner_changes))
        if plan.bot_changes:
            async with bot_client(config, token) as bot:
                applied.extend(await apply_bot_edits(bot, plan.bot_changes))
    finally:
        print(json.dumps({"bot_id": profile.id, "username": profile.username, "applied": applied, "skipped": plan.skipped, "cancelled": False}, indent=2))
    return 0
```

The `finally` guarantees the applied list is reported even when a later write raises, so a partial edit is never silent.

- [ ] **Step 5: Wire the dispatch and the menu**

In `run()`, pass the config through and add the branch:

```python
        if args.command == "search":
            return await _run_search(client, args)
        if args.command == "bots":
            return await _run_bots(client, args, config)
```

In `run_interactive_menu`, add two entries to the printed list, before `"0. Exit"`:

```python
                "7. List my bots",
                "8. Edit a bot",
```

and the two branches before the `write("Unknown choice.")` line:

```python
    if choice == "7":
        return await run(_namespace(command="bots", bot=None, json_output=None, name=None, bio=None, description=None, commands=None, clear_commands=False, photo=None, remove_photo=False, group_rights=None, channel_rights=None, yes=False))
    if choice == "8":
        bot = read("Bot (nickname, @username, or numeric ID): ")
        name = read("New display name (blank to keep): ").strip() or None
        bio = read("New bio (blank to keep): ").strip() or None
        description = read("New description (blank to keep): ").strip() or None
        return await run(_namespace(command="bots", bot=bot, json_output=None, name=name, bio=bio, description=description, commands=None, clear_commands=False, photo=None, remove_photo=False, group_rights=None, channel_rights=None, yes=False))
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS — every test, including the pre-existing ones.

- [ ] **Step 7: Check the CLI help renders**

Run: `.venv/bin/python -m telegram_tools.cli bots --help`
Expected: the flag list above, with the three text-field descriptions spelling out where each one appears.

- [ ] **Step 8: Commit**

```bash
git add src/telegram_tools/cli.py tests/test_cli.py tests/test_bot_cli.py
git commit -m "feat: add the bots command and menu entries"
```

---

### Task 7: Documentation, examples, and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`, `.env.example`, `docs/telethon-api-notes.md`

**Interfaces:**
- Consumes: the finished CLI from Task 6.
- Produces: no code.

- [ ] **Step 1: Fix the two now-false README claims**

In `README.md`, the "What it does" list gains a `bots` entry:

```markdown
- **`bots`** — lists the bots you own with their numeric IDs, and edits what @BotFather edits: display name, bio, description, commands, profile photo, and default admin rights.
```

The "What it doesn't do (on purpose)" bullet currently reads `No sending messages, no bots, no automation loops.` Replace it with:

```markdown
- No sending messages and no automation loops. The `bots` command edits bot *settings*; it never runs a bot.
- No changing a bot's `@username`, creating or deleting bots, or reading/revoking bot tokens — those stay with @BotFather.
```

- [ ] **Step 2: Add usage to the README**

Add to the "30 seconds of usage" block:

```bash
# Which bots do I own, and what are their IDs?
telegram-tools bots

# Show one bot's full profile
telegram-tools bots --bot @mybot

# Rename it and update the text people see before pressing Start
telegram-tools bots --bot @mybot --name "My Bot" --description "Does the thing"
```

And a short subsection after the credentials section:

```markdown
### Optional: bot tokens

Most of `bots` runs on your normal login. Three edits — commands, profile photo removal,
and default admin rights — must be sent by the bot itself, so they need that bot's token:

```bash
# in ~/.telegram-tools/.env
TELEGRAM_BOT_TOKENS=mybot:12345:AAExampleToken,alerts:67890:BBExampleToken
```

Nicknames are yours to choose and can be used as `--bot mybot`. The tool only ever reads
this variable — it never writes a token anywhere, and never prints one.
```

- [ ] **Step 3: Update `.env.example`**

Append:

```
# Optional: tokens for bots you own, as nickname:token pairs.
# Only needed to edit bot commands, remove a profile photo, or set default admin rights.
#TELEGRAM_BOT_TOKENS=mybot:12345:AAExampleToken,alerts:67890:BBExampleToken
```

- [ ] **Step 4: Add the changelog entry**

Insert above the `## 3.0.0` section:

```markdown
## 3.1.0 - 2026-08-13

- Add `bots`: list the bots you own with their numeric IDs, show one bot's full profile, and edit its display name, bio, description, commands, profile photo, and default group/channel admin rights.
- Bot editing runs on your existing login. Commands, photo removal, and default admin rights are sent by the bot itself and need that bot's token in the new optional `TELEGRAM_BOT_TOKENS` variable (`nickname:token`, comma separated). Tokens are read only — never written to disk, printed, or exported. This is not a return of the 3.0.0 `bots.json` store.
- Edits print an old → new diff and ask for confirmation; `--yes` skips the prompt.
- `doctor` reports how many bot tokens are loaded, and nothing else about them.
- Changing a bot's `@username`, creating or deleting bots, and revoking tokens stay with @BotFather — no API exists for them.
```

- [ ] **Step 5: Bump the version**

In `pyproject.toml`: `version = "3.1.0"`.

- [ ] **Step 6: Record the API notes**

Append to `docs/telethon-api-notes.md`:

```markdown

Checked on 2026-08-13 for the `bots` command (Telethon 1.44.0):

- `bots.getAdminedBots` returns the bots the logged-in user owns; it is the API behind @BotFather's `/mybots`.
- `users.getFullUser` on a bot returns everything the profile view needs: `about` (the bio), `bot_info.description`, `bot_info.commands`, `bot_group_admin_rights`, and `bot_broadcast_admin_rights`. One request, and it works for bots the user does not own.
- `bots.setBotInfo` takes a `bot` parameter, so the owner's session can set `name`, `about`, and `description` without a bot token. `bots.getBotInfo` also exists but is not used — `getFullUser` covers the same fields in one call.
- `photos.uploadProfilePhoto` also takes a `bot` parameter, so setting a bot's photo needs no token.
- `bots.setBotCommands`, `bots.resetBotCommands`, `bots.setBotGroupDefaultAdminRights`, and `bots.setBotBroadcastDefaultAdminRights` have **no** `bot` parameter — they act on the caller, so they must be sent by a client authorized with the bot's token.
- `photos.deletePhotos` needs an `InputPhoto` with an access hash, which `UserProfilePhoto` does not carry; fetch the photo through `photos.getUserPhotos` and convert it with `telethon.utils.get_input_photo`.
- `bots.exportBotToken` exists and would let an owner read a bot's token. This project never calls it: it sits next to a `revoke` flag, and reading credentials is outside what the tool does.
- Changing a bot's `@username`, creating a bot, and deleting a bot have no user-facing API and remain @BotFather-only.
```

- [ ] **Step 7: Verify the docs claims against the code**

Run: `.venv/bin/pytest -v && grep -rn "no bots" README.md`
Expected: tests PASS, and the `grep` returns nothing — the stale claim is gone.

- [ ] **Step 8: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml .env.example docs/telethon-api-notes.md
git commit -m "docs: document the bots command and bump to 3.1.0"
```

---

### Task 8: Live verification against a real bot

**Files:**
- Modify: `docs/telethon-api-notes.md` (only if a check fails)

**Interfaces:**
- Consumes: the installed CLI from Tasks 1–7.
- Produces: a verified feature, or a corrected plan for whichever call fails.

Everything so far is tested against fakes. These five calls are read from Telethon's TL definitions and have never been run against Telegram. **Do not report the feature as working until this task is done.** Run these against a bot Sven owns, and stop and report if any step behaves differently.

- [ ] **Step 1: Confirm the bot list**

Run: `.venv/bin/python -m telegram_tools.cli bots`
Expected: every bot the account owns, with numeric IDs. If it returns nothing while @BotFather's `/mybots` lists bots, `bots.getAdminedBots` is not usable — record that and fall back to resolving bots by `@username` only.

- [ ] **Step 2: Confirm the profile read**

Run: `.venv/bin/python -m telegram_tools.cli bots --bot @<yourbot>`
Expected: name, bio, description, commands, and rights match what @BotFather shows.

- [ ] **Step 3: Confirm an owner-session text edit**

Run: `.venv/bin/python -m telegram_tools.cli bots --bot @<yourbot> --bio "telegram-tools check"`
Expected: a diff, a `y/N` prompt, then `"applied": ["bio"]`. Re-read with Step 2 **and** check the bot's profile in a Telegram client — if the edit applies but is invisible in the app, `lang_code=""` is wrong and `DEFAULT_LANG_CODE` needs the account's actual language code. Set the bio back afterwards.

- [ ] **Step 4: Confirm the photo set**

Run: `.venv/bin/python -m telegram_tools.cli bots --bot @<yourbot> --photo <some.png>`
Expected: `"applied": ["photo"]` and the new photo visible in Telegram. If `photos.uploadProfilePhoto(bot=…)` is rejected, move `photo` to the token rail (`BOT_FIELDS`) and use the bot client for it.

- [ ] **Step 5: Confirm the token rail**

With that bot in `TELEGRAM_BOT_TOKENS`:

Run: `.venv/bin/python -m telegram_tools.cli bots --bot <nickname> --commands <cmds.json>`
Expected: `"applied": ["commands"]`, and the new command list visible in the bot's menu. Then `--clear-commands`, then `--remove-photo`, checking each result.

- [ ] **Step 6: Confirm the no-token path fails cleanly**

With `TELEGRAM_BOT_TOKENS` unset:

Run: `.venv/bin/python -m telegram_tools.cli bots --bot @<yourbot> --clear-commands`
Expected: exit code 2 and a message naming `clear_commands` and the env var format — no traceback, no partial write.

- [ ] **Step 7: Commit any corrections**

```bash
git add -A
git commit -m "fix: correct bot API assumptions found in live verification"
```

---

## Notes for the reviewer

- The three text fields are easy to mix up. `--bio` maps to MTProto `about` / Bot API `setMyShortDescription`; `--description` maps to MTProto `description` / Bot API `setMyDescription`.
- `build_edit_plan` treats `--photo` as always-changed on purpose: a local file cannot be compared to a remote photo.
- `bio` and `description` are `None` in the list view because the list does not fetch full profiles — one request instead of one per bot.
- Nothing in this plan calls `bots.exportBotToken`. If a future task wants token-free command editing, that is the method to argue about, and it needs Sven's explicit decision.
