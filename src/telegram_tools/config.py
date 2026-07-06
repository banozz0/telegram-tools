from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_path: Path


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
) -> Config:
    env = env or os.environ
    cwd = cwd or Path.cwd()

    raw_api_id = env.get("TELEGRAM_API_ID")
    if not raw_api_id:
        raise ConfigError("TELEGRAM_API_ID is required.")

    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise ConfigError("TELEGRAM_API_ID must be an integer.") from exc

    api_hash = env.get("TELEGRAM_API_HASH")
    if not api_hash:
        raise ConfigError("TELEGRAM_API_HASH is required.")

    session_path = Path(env.get("TELEGRAM_TOOLS_SESSION", cwd / ".telegram-tools" / "telegram-tools"))
    return Config(api_id=api_id, api_hash=api_hash, session_path=session_path)
