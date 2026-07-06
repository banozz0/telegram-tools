from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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


def _resolve(root: Path, value: Path | str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def check_python_version(version_info: tuple[int, ...] | None = None) -> DoctorCheck:
    version_info = version_info or sys.version_info[:3]
    if version_info >= MIN_PYTHON:
        return DoctorCheck("OK", "Python version is supported")
    return DoctorCheck("FAIL", "Python 3.11 or newer is required")


def check_config_presence(root: Path, env: Mapping[str, str]) -> DoctorCheck:
    if env.get("TELEGRAM_API_ID") and env.get("TELEGRAM_API_HASH"):
        return DoctorCheck("OK", "Telegram config is present")
    if (root / ".env").exists():
        return DoctorCheck("OK", "Telegram config is present")
    return DoctorCheck("FAIL", "Telegram config is missing")


def check_session_storage(root: Path, env: Mapping[str, str]) -> DoctorCheck:
    session_value = env.get("TELEGRAM_TOOLS_SESSION", root / ".telegram-tools" / "telegram-tools")
    session_path = _resolve(root, session_value)
    candidates = [session_path, Path(f"{session_path}.session")]
    if any(path.exists() for path in candidates):
        return DoctorCheck("OK", "Session storage exists")
    return DoctorCheck("WARN", "Session storage was not found")


def check_launchers(root: Path) -> DoctorCheck:
    launchers = sorted((root / "scripts").glob("*.command"))
    if not launchers:
        return DoctorCheck("WARN", "No launcher scripts found")

    executable = [
        launcher
        for launcher in launchers
        if launcher.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ]
    if len(executable) == len(launchers):
        return DoctorCheck("OK", "Launcher scripts are executable")
    return DoctorCheck("FAIL", "Launcher scripts are not all executable")


def check_bots_json(root: Path, bots_json: Path | str) -> DoctorCheck:
    path = _resolve(root, bots_json)
    if not path.exists():
        return DoctorCheck("WARN", "bots.json is not present")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return DoctorCheck("FAIL", "bots.json is not valid JSON")

    if not isinstance(data, list | dict):
        return DoctorCheck("FAIL", "bots.json must be a JSON list or object")
    return DoctorCheck("OK", "bots.json is valid JSON")


def run_doctor(
    *,
    root: Path | str = Path.cwd(),
    bots_json: Path | str = Path("bots.json"),
    env: Mapping[str, str] | None = None,
    version_info: tuple[int, ...] | None = None,
) -> int:
    root = Path(root)
    env = os.environ if env is None else env
    checks = [
        check_python_version(version_info),
        check_config_presence(root, env),
        check_session_storage(root, env),
        check_launchers(root),
        check_bots_json(root, bots_json),
    ]

    for check in checks:
        print(check.format())

    return 1 if any(check.failed for check in checks) else 0
