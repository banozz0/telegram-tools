from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

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


def check_session_storage(env: Mapping[str, str], home: Path | None = None) -> DoctorCheck:
    session_path = Path(env.get("TELEGRAM_TOOLS_SESSION", config_dir(home) / "telegram-tools"))
    candidates = [session_path, Path(f"{session_path}.session")]
    if any(path.exists() for path in candidates):
        return DoctorCheck("OK", "Session storage exists")
    return DoctorCheck("WARN", "Session storage was not found (created on first login)")


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


def run_doctor(
    *,
    root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    version_info: tuple[int, ...] | None = None,
    home: Path | None = None,
) -> int:
    root = Path(root) if root is not None else Path.cwd()
    env = os.environ if env is None else env
    checks = [
        check_python_version(version_info),
        check_config_presence(root, env, home),
        check_session_storage(env, home),
        check_bot_tokens(root, env, home),
        check_send_allowlist(root, env, home),
    ]

    for check in checks:
        print(check.format())

    return 1 if any(check.failed for check in checks) else 0
