from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from telegram_tools import archive as archive_store
from telegram_tools import profiles, proxy
from telegram_tools._core.archive import fts5_report
from telegram_tools._core.config import human_bytes
from telegram_tools.config import ConfigError, config_dir, parse_bot_tokens, parse_send_allowlist


MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class DoctorCheck:
    status: str
    message: str

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"

    def format(self) -> str:
        return f"{self.status:<4} {self.message}"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "message": self.message}


def check_python_version(version_info: tuple[int, ...] | None = None) -> DoctorCheck:
    version_info = version_info or sys.version_info[:3]
    if version_info >= MIN_PYTHON:
        return DoctorCheck("OK", "Python version is supported")
    return DoctorCheck("FAIL", "Python 3.11 or newer is required")


def check_config_presence(root: Path, env: Mapping[str, str], home: Path | None = None) -> DoctorCheck:
    if env.get("TELEGRAM_API_ID") and env.get("TELEGRAM_API_HASH"):
        return DoctorCheck("OK", "Telegram config is present")
    if (root / ".env").exists() or (config_dir(home) / ".env").exists():
        return DoctorCheck("OK", "Telegram config is present")
    return DoctorCheck("FAIL", "Telegram config is missing")


def check_session_storage(env: Mapping[str, str], home: Path | None = None, profile: str | None = None) -> DoctorCheck:
    """Whether the profile this run would use has a session. Never prints its path.

    Section 5.1: `doctor` says `profile default: session present`, and nothing
    more. Where the file is stays this tool's business.
    """
    name = profile or env.get("TELEGRAM_TOOLS_PROFILE") or profiles.DEFAULT_PROFILE
    override = env.get("TELEGRAM_TOOLS_SESSION")
    if override:
        session_path = Path(override)
        where = "the session named by TELEGRAM_TOOLS_SESSION"
    else:
        try:
            stored = profiles.load(name, home=home)
        except profiles.ProfileError as exc:
            return DoctorCheck("FAIL", str(exc))
        session_path = stored.session
        where = f"profile {name}"
    candidates = [session_path, Path(f"{session_path}.session")]
    if any(path.exists() for path in candidates):
        return DoctorCheck("OK", f"{where}: session present")
    return DoctorCheck("WARN", f"{where}: no session yet (run `telegram-tools auth` to log in)")


def check_profiles(home: Path | None = None) -> DoctorCheck:
    """How many named logins exist, and whether the old session could move into one."""
    try:
        found = profiles.names(home)
    except profiles.ProfileError as exc:
        return DoctorCheck("FAIL", str(exc))
    if not found:
        return DoctorCheck("WARN", "No profiles yet (run `telegram-tools auth` to make one)")
    if profiles.migration_needed(home):
        # A suggestion, never an action: the file is a login, and moving one
        # belongs behind a y/N that the person reading this line answers.
        return DoctorCheck(
            "OK",
            f"{len(found)} profile(s): {', '.join(found)} "
            "(default still uses the session from before profiles; `telegram-tools auth --migrate` moves it)",
        )
    return DoctorCheck("OK", f"{len(found)} profile(s): {', '.join(found)}")


def check_file_modes(home: Path | None = None) -> DoctorCheck:
    """Whether anything under the tool's directory is readable by group or others.

    A FAIL here is what `require_tight_modes` refuses writes on: the `.env`
    holds an application hash and the profiles hold logins, and a mode anyone
    on the machine can read makes both of those someone else's too.
    """
    paths = profiles.paths_for(home)
    if not paths.root.exists():
        return DoctorCheck("OK", "No local files yet, so nothing is readable by anyone else")
    loose = paths.loose_modes()
    if not loose:
        return DoctorCheck("OK", "Local files are private to you (0600 files, 0700 directories)")
    # Named relative to the root, never absolutely: `session.session` alone
    # does not say which profile it belongs to, and the home directory is a
    # path this tool has no business printing.
    names = ", ".join(f"{_under_root(path, paths.root)} ({mode:04o})" for path, mode in loose[:4])
    more = f" and {len(loose) - 4} more" if len(loose) > 4 else ""
    return DoctorCheck(
        "FAIL",
        f"{len(loose)} file(s) under ~/.telegram-tools are readable by others: {names}{more}. "
        "Fix with: chmod -R go-rwx ~/.telegram-tools",
    )


def _under_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def loose_mode_paths(home: Path | None = None) -> list[Path]:
    paths = profiles.paths_for(home)
    return [] if not paths.root.exists() else [path for path, _mode in paths.loose_modes()]


def require_tight_modes(home: Path | None = None) -> None:
    """Refuse a write when the tool's own files are readable by anyone else.

    Reads are left alone: a person whose modes have drifted still needs to be
    able to run `doctor` and read the line that tells them so.
    """
    loose = loose_mode_paths(home)
    if not loose:
        return
    raise ConfigError(
        f"{len(loose)} file(s) under ~/.telegram-tools are readable by group or others, and this "
        "command writes. Fix with: chmod -R go-rwx ~/.telegram-tools, then run `telegram-tools doctor`.",
        code="CONFIG_INVALID",
    )


def check_search_index() -> DoctorCheck:
    """Whether the SQLite this Python links has FTS5, which the archive is built on."""
    report = fts5_report()
    if report["available"]:
        return DoctorCheck("OK", f"Archive search: {report['detail']}")
    return DoctorCheck("FAIL", f"Archive search: {report['detail']} (install a Python whose SQLite has FTS5)")


def check_archive(home: Path | None = None) -> DoctorCheck:
    """What the local archive holds and how much of its budget it takes. Reads the file; migrates nothing."""
    usage = archive_store.budget_usage(home)
    if usage is None:
        return DoctorCheck("WARN", "No archive yet (run `telegram-tools archive sync` to make one)")
    if "error" in usage:
        return DoctorCheck("FAIL", f"The archive could not be read: {usage['error']}")
    budget = usage["budgets"][0]
    status = "FAIL" if budget["over"] else "OK"
    return DoctorCheck(
        status,
        f"Archive: {usage['messages']} message(s) in {usage['scopes']} scope(s), "
        f"{human_bytes(usage['bytes'])} of the {budget['limit']} archive_max_bytes budget ({budget['percent']}%)",
    )


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


def check_send_allowlist(root: Path, env: Mapping[str, str], home: Path | None = None) -> DoctorCheck:
    try:
        allowlist = parse_send_allowlist(_effective_env(root, env, home).get("TELEGRAM_SEND_ALLOWLIST"))
    except ConfigError:
        return DoctorCheck("FAIL", "TELEGRAM_SEND_ALLOWLIST is malformed (expected chat[:topic], comma separated)")
    if not allowlist:
        # Counts only, never the destinations: same discipline as the token check.
        return DoctorCheck("WARN", "No send destinations allowlisted (send --yes is refused; send without it still asks)")
    return DoctorCheck("OK", f"{len(allowlist)} send destination(s) allowlisted")


def check_proxy(root: Path, env: Mapping[str, str], home: Path | None = None) -> DoctorCheck:
    """Whether `TELEGRAM_PROXY` is usable, which is not the same as set.

    Telethon's own behaviour when python-socks is missing is a warning and a
    direct connection. That is the failure this check exists to make loud:
    someone who asked to go through a proxy would otherwise connect from their
    own address and be told nothing.
    """
    raw = _effective_env(root, env, home).get("TELEGRAM_PROXY")
    try:
        parsed = proxy.parse(raw)
    except ConfigError as exc:
        return DoctorCheck("FAIL", str(exc))
    if parsed is None:
        return DoctorCheck("OK", "No proxy configured (connects directly)")
    if not proxy.backend_available():
        return DoctorCheck(
            "FAIL",
            f"TELEGRAM_PROXY is set to {parsed.label} and python-socks is not installed, "
            f"so every command refuses rather than connecting directly. Install it with: {proxy.EXTRA_HINT}",
        )
    return DoctorCheck("OK", f"Proxy {parsed.label} is configured and usable")


def run_doctor(
    *,
    root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    version_info: tuple[int, ...] | None = None,
    home: Path | None = None,
    profile: str | None = None,
    report=None,
) -> int:
    """Print every check and answer 1 if one failed.

    `report` decides where the lines go and carries the same checks out as the
    envelope's result; with none, this is the plain print it has always been.
    """
    root = Path(root) if root is not None else Path.cwd()
    env = os.environ if env is None else env
    checks = [
        check_python_version(version_info),
        check_config_presence(root, env, home),
        check_profiles(home),
        check_session_storage(env, home, profile),
        check_file_modes(home),
        check_proxy(root, env, home),
        check_bot_tokens(root, env, home),
        check_send_allowlist(root, env, home),
        check_search_index(),
        check_archive(home),
    ]

    for check in checks:
        (report.info if report is not None else print)(check.format())

    failed = [check for check in checks if check.failed]
    if report is not None:
        report.result(
            {"checks": [check.to_dict() for check in checks], "failed": len(failed)},
            # Exit 1 for a failed check is what doctor has always answered, and
            # `partial` is the status that carries it: some checks did not pass.
            status="partial" if failed else "ok",
        )
    return 1 if failed else 0
