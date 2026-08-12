from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from telegram_tools.config import config_dir


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
    ]

    for check in checks:
        print(check.format())

    return 1 if any(check.failed for check in checks) else 0
