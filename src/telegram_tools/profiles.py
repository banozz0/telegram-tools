"""Named logins: where each one's session lives, and what is known about it.

Spec: section 5.2. A profile is a directory under `~/.telegram-tools/profiles/`
holding the Telethon session and a `profile.json` that carries nothing secret --
a label, the account id, when it was made, when it last logged in, and the name
of the proxy it goes through. The API id and hash stay where they have always
been, in `~/.telegram-tools/.env` or the environment; a profile may carry its
own `.env` beside its session when it needs a second application id or its own
proxy.

Two rules the rest of the tool leans on:

* **The old session is not moved.** A `default` with no directory of its own
  resolves to `~/.telegram-tools/telegram-tools.session`, exactly where it has
  always been, and `TELEGRAM_TOOLS_SESSION` still wins over both. `auth
  --migrate` is the only thing that moves it, and only after a y/N.
* **The path handed to Telethon has no suffix.** Telethon appends `.session`
  to whatever it is given, so this module stores `<dir>/session` and the file
  on disk is `<dir>/session.session`. `session_file` is the one that exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from telegram_tools._core.paths import PathError, ToolPaths, make_private_dir, safe_name, write_private

TOOL = "telegram-tools"
DEFAULT_PROFILE = "default"
# The session file every version before profiles wrote, and still writes.
LEGACY_STEM = "telegram-tools"
# What Telethon is handed inside a profile directory; the file gains `.session`.
SESSION_STEM = "session"
PROFILE_FILE = "profile.json"


class ProfileError(ValueError):
    """A profile that cannot be named, found or read."""

    envelope_code = "CONFIG_INVALID"

    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.envelope_code = code
        self.envelope_hint = hint


def paths_for(home: Path | None = None) -> ToolPaths:
    return ToolPaths.for_tool(TOOL, home)


def legacy_session(home: Path | None = None) -> Path:
    """The pre-profile session path, without Telethon's suffix."""
    return paths_for(home).root / LEGACY_STEM


def check_name(name: str) -> str:
    """`name` when it is usable as a directory, else a refusal that says why."""
    try:
        return safe_name(name)
    except PathError as exc:
        raise ProfileError(
            f"{name!r} is not a usable profile name: letters, digits, dot, dash and underscore, "
            "starting with a letter or digit.",
            hint="Pick a name like work, personal or alerts.",
        ) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Profile:
    """One named login, as the store knows it.

    `session` is what Telethon is handed; `session_file` is what exists on
    disk. `legacy` marks the `default` that still points at the pre-profile
    file, which is the one profile whose session lives outside its directory.
    """

    name: str
    directory: Path
    session: Path
    legacy: bool = False
    label: str | None = None
    user_id: int | None = None
    created: str | None = None
    last_login: str | None = None
    proxy: str | None = None

    @property
    def session_file(self) -> Path:
        return Path(f"{self.session}.session")

    @property
    def env_file(self) -> Path:
        return self.directory / ".env"

    @property
    def logged_in(self) -> bool:
        return self.session_file.exists()

    def to_dict(self) -> dict[str, Any]:
        """What `profiles` reports and an envelope carries: never a path, never a secret."""
        return {
            "name": self.name,
            "label": self.label,
            "user_id": self.user_id,
            "created": self.created,
            "last_login": self.last_login,
            "proxy": self.proxy,
            "logged_in": self.logged_in,
            "legacy_session": self.legacy,
        }

    def stored(self) -> dict[str, Any]:
        """The `profile.json` body: the non-secret record, plus a session path only when
        it is not the one this directory implies."""
        body: dict[str, Any] = {
            "schema": "telegram-tools/profile/1",
            "label": self.label,
            "user_id": self.user_id,
            "created": self.created,
            "last_login": self.last_login,
            "proxy": self.proxy,
        }
        if self.legacy:
            # The by-reference default: the record says where the session it uses
            # actually is, so a reader is never left guessing which file is live.
            body["session"] = str(self.session)
            body["legacy_session"] = True
        return body


def profile_dir(name: str, home: Path | None = None) -> Path:
    return paths_for(home).profile(check_name(name))


def make_private_tree(directory: Path, root: Path) -> Path:
    """`directory` at 0700, and every level of `root` above it too.

    `Path.mkdir(parents=True)` applies its mode to the leaf only: the parents it
    creates get the process umask, which on a fresh machine would leave
    `~/.telegram-tools` and `profiles/` readable by everyone. `doctor` would then
    fail the tree this tool had just written, and every write would refuse. So
    the chain from `root` down is walked and each level made or tightened.

    A `directory` outside `root` is created and otherwise left alone: a path
    someone pointed elsewhere is theirs to set the modes on.
    """
    if directory != root and root not in directory.parents:
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    chain = [directory]
    while chain[-1] != root:
        chain.append(chain[-1].parent)
    for level in reversed(chain):
        make_private_dir(level)
    return directory


def read_record(directory: Path) -> Mapping[str, Any]:
    """`profile.json` as a mapping, or empty when there is none."""
    path = directory / PROFILE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(
            f"{path} is not readable JSON, so the profile it describes cannot be trusted.",
            hint=f"Fix or delete {path}, then log in again.",
        ) from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{path} does not hold a profile record.")
    return data


def load(name: str = DEFAULT_PROFILE, *, home: Path | None = None) -> Profile:
    """The profile `name` names, whether or not it has ever been logged into.

    A profile with no directory is still a `Profile`: it says where its session
    would go, and `logged_in` is False. That is what lets `--profile work` fail
    with "no session" rather than with a missing-file traceback.
    """
    name = check_name(name)
    directory = profile_dir(name, home)
    record = read_record(directory)

    stored_session = record.get("session")
    if stored_session:
        session, legacy = Path(stored_session), bool(record.get("legacy_session"))
    elif name == DEFAULT_PROFILE and not (directory / f"{SESSION_STEM}.session").exists():
        # The upgrade path: `default` is the login this machine already has,
        # left exactly where every earlier version put it.
        session, legacy = legacy_session(home), True
    else:
        session, legacy = directory / SESSION_STEM, False

    return Profile(
        name=name,
        directory=directory,
        session=session,
        legacy=legacy,
        label=record.get("label"),
        user_id=record.get("user_id"),
        created=record.get("created"),
        last_login=record.get("last_login"),
        proxy=record.get("proxy"),
    )


def save(profile: Profile) -> Profile:
    """Write `profile.json` into a 0700 directory as a 0600 file."""
    # <root>/profiles/<name> by construction, so the root is two levels up.
    make_private_tree(profile.directory, profile.directory.parent.parent)
    write_private(profile.directory / PROFILE_FILE, json.dumps(profile.stored(), indent=2) + "\n")
    return profile


def record_login(profile: Profile, *, label: str, user_id: int, proxy: str | None = None) -> Profile:
    """The profile as it stands after a successful login, saved."""
    updated = Profile(
        name=profile.name,
        directory=profile.directory,
        session=profile.session,
        legacy=profile.legacy,
        label=label,
        user_id=user_id,
        created=profile.created or _now(),
        last_login=_now(),
        proxy=proxy,
    )
    return save(updated)


def names(home: Path | None = None) -> list[str]:
    """Every profile that exists on disk, `default` included when it has a session.

    `default` is listed whenever there is anything to list it for -- its own
    directory, or the legacy session it still points at -- so the upgrade path
    is visible without anything having been created.
    """
    root = paths_for(home).profiles
    found = set()
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                found.add(safe_name(entry.name))
            except PathError:
                # A directory nothing here made. Not ours to name or to delete.
                continue
    if Path(f"{legacy_session(home)}.session").exists():
        found.add(DEFAULT_PROFILE)
    return sorted(found)


def listing(home: Path | None = None) -> list[Profile]:
    return [load(name, home=home) for name in names(home)]


def migration_needed(home: Path | None = None) -> bool:
    """True when `default` is still the legacy file and could be moved into a profile."""
    profile = load(DEFAULT_PROFILE, home=home)
    return profile.legacy and profile.logged_in


def migrate(home: Path | None = None) -> tuple[Path, Path]:
    """Move the legacy session into `profiles/default/`, and say what moved where.

    The only path in this tool that moves a session file, and it is reached
    only after a typed y. Nothing is overwritten: a `default` that already has
    its own session refuses rather than replacing it.
    """
    profile = load(DEFAULT_PROFILE, home=home)
    if not profile.legacy:
        raise ProfileError(
            "The default profile already keeps its session in its own directory; nothing to migrate.",
            hint="telegram-tools profiles",
        )
    source = profile.session_file
    if not source.exists():
        raise ProfileError(
            "There is no session at the old location to migrate.",
            code="LOGIN_REQUIRED",
            hint="telegram-tools auth",
        )
    directory = make_private_tree(profile_dir(DEFAULT_PROFILE, home), paths_for(home).root)
    destination = directory / f"{SESSION_STEM}.session"
    if destination.exists():
        raise ProfileError(
            f"{destination.name} already exists in the default profile; the old session was left alone.",
            hint="Log in again with `telegram-tools auth` if the profile session is the wrong one.",
        )
    source.replace(destination)
    destination.chmod(0o600)
    save(
        Profile(
            name=DEFAULT_PROFILE,
            directory=directory,
            session=directory / SESSION_STEM,
            legacy=False,
            label=profile.label,
            user_id=profile.user_id,
            created=profile.created or _now(),
            last_login=profile.last_login,
            proxy=profile.proxy,
        )
    )
    return source, destination


def forget(profile: Profile) -> list[Path]:
    """Delete a profile's session and record, and say what was removed.

    The account is logged out of Telegram by `auth --logout` before this runs;
    this is only the local half. A legacy `default` loses its session file and
    keeps its directory, because that file is the one thing here it owns.
    """
    removed: list[Path] = []
    for path in (profile.session_file, profile.directory / PROFILE_FILE):
        if path.exists():
            path.unlink()
            removed.append(path)
    if profile.directory.is_dir() and not any(profile.directory.iterdir()):
        profile.directory.rmdir()
        removed.append(profile.directory)
    return removed
