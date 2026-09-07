from __future__ import annotations

import sqlite3

from pathlib import Path

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from telegram_tools._core.paths import FILE_MODE
from telegram_tools.config import Config, config_dir
from telegram_tools import login
from telegram_tools.profiles import make_private_tree

# Telethon's SQLite session raises this exact wording when a second client opens
# the same session file. It is the one lock message that means "someone else has
# it", rather than a corrupt or missing database.
_LOCKED = "database is locked"


class SessionInUseError(RuntimeError):
    """A second client tried to open a session file another one already holds."""

    envelope_code = "SESSION_IN_USE"
    envelope_hint = "Close the other telegram-tools - a menu open in another terminal counts."


class SessionUnreadableError(RuntimeError):
    """A session file that is there but holds no authorization this build can read."""

    envelope_code = "SESSION_UNREADABLE"
    envelope_hint = "Log the profile in again with `telegram-tools auth`."


def create_client(config: Config) -> TelegramClient:
    """A client for this run's profile, through this run's proxy.

    The session's directory is made 0700 and the file 0600: a login is the
    closest thing on disk to a credential, and the tool that writes it is the
    one that should be strict about it. The tighten comes *after* the client is
    built, because Telethon opens its SQLite session in the constructor and
    creates that file at the process umask -- 0644 on a normal machine, which
    every later write would then refuse over.
    """
    _prepare_session_dir(config.session_path)
    proxy = getattr(config, "proxy", None)
    client = TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        proxy=proxy.as_telethon() if proxy is not None else None,
    )
    client.flood_sleep_threshold = 24 * 60 * 60
    tighten_session(config.session_path)
    return client


def _prepare_session_dir(session_path: Path) -> None:
    """Make room for the session, 0700 when the directory is this tool's own.

    A session pointed somewhere else by `TELEGRAM_TOOLS_SESSION` gets its
    directory created and nothing more: tightening a path the user chose --
    which could be a shared one -- is not this tool's call to make.
    """
    make_private_tree(session_path.parent, config_dir())


def tighten_session(session_path: Path) -> Path | None:
    """Set the session file to 0600 if it is there; return it, or None when it is not.

    Called after the client is built and again after a login: Telethon creates
    the file in its constructor and rewrites it when a login lands, and both
    times it uses the umask rather than asking.
    """
    session_file = Path(f"{session_path}.session")
    if not session_file.exists():
        return None
    session_file.chmod(FILE_MODE)
    return session_file


async def start_client(client, *, authorize: bool = True):
    """Start `client`, turning a held session file into an answer, not a traceback.

    One session file is one connection. Running the menu in one terminal and a
    command in another is the ordinary way to hit this, and the raw
    `sqlite3.OperationalError` it produces says nothing about how to fix it.

    `authorize=False` connects and stops there, offering no login. Two callers
    want that: `auth`, which runs the login itself and would otherwise be
    racing Telethon's own prompt, and machine mode, where an unauthorised
    session must refuse rather than block on a question nobody will answer.
    """
    try:
        await (client.start() if authorize else client.connect())
    except sqlite3.OperationalError as exc:
        # The lock is hit *after* the socket is up, so this client is connected and
        # its read/write tasks are running. Without this disconnect the clean message
        # below arrives buried in "Task was destroyed but it is pending!" on exit,
        # which is the noise it exists to replace.
        await _disconnect_quietly(client)
        if _LOCKED not in str(exc).lower():
            raise
        raise SessionInUseError(
            "Another telegram-tools is already using the login session. "
            "Close the other one - a menu open in another terminal counts - and try again."
        ) from exc
    return client


async def _disconnect_quietly(client) -> None:
    try:
        result = client.disconnect()
        if result is not None:
            await result
    except Exception:
        # Already failing; a teardown error here would replace the real cause.
        pass


def detached_session(session_path: Path, profile: str = "default") -> StringSession:
    """The authorization in the profile's session file, copied into memory.

    Section 10.5: the runner never holds the SQLite session file. Telethon
    keeps one connection per session file and a second client on the same file
    is `SessionInUseError`, so a runner that opened it the ordinary way would
    lock every one-shot command out for as long as it ran. This reads the one
    row Telethon stores the authorization in -- read-only, no Telethon session
    object, so nothing is written and nothing is held -- and hands back a
    `StringSession` that lives only in this process's memory. Both connections
    then share one authorization, which Telegram permits for one device.

    The entity cache is deliberately left behind: it is a convenience the
    runner can refetch, and copying it would mean opening the file for real.
    """
    file = Path(f"{session_path}.session")
    if not file.exists():
        raise login.LoginRequired(profile)
    try:
        with sqlite3.connect(f"file:{file}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT dc_id, server_address, port, auth_key FROM sessions"
            ).fetchone()
    except sqlite3.Error as exc:
        raise SessionUnreadableError(
            f"The login session at {file.name} could not be read ({exc}). "
            "Run `telegram-tools auth` to log this profile in again."
        ) from exc
    if row is None or row[3] is None:
        raise login.LoginRequired(profile)
    session = StringSession()
    session.set_dc(int(row[0]), str(row[1]), int(row[2]))
    session.auth_key = AuthKey(bytes(row[3]))
    return session


def create_detached_client(config: Config) -> TelegramClient:
    """A client on a copy of the profile's authorization, holding no file.

    What `watch run` connects with. Everything else about it -- the proxy, the
    flood threshold -- is what `create_client` builds, because a runner that
    behaved differently from a one-shot command would be a second tool.
    """
    proxy = getattr(config, "proxy", None)
    client = TelegramClient(
        detached_session(config.session_path, getattr(config, "profile", "default")),
        config.api_id,
        config.api_hash,
        proxy=proxy.as_telethon() if proxy is not None else None,
    )
    client.flood_sleep_threshold = 24 * 60 * 60
    return client
