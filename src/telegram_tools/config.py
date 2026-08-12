from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def config_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".telegram-tools"


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_path: Path


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Config:
    cwd = cwd or Path.cwd()
    if env is None:
        load_dotenv(dotenv_path=cwd / ".env", override=False)
        load_dotenv(dotenv_path=config_dir(home) / ".env", override=False)
        env = os.environ

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

    session_path = Path(env.get("TELEGRAM_TOOLS_SESSION", config_dir(home) / "telegram-tools"))
    return Config(api_id=api_id, api_hash=api_hash, session_path=session_path)
