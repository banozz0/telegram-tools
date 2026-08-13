from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
        if not separator or not nickname or not token or not any(c.isalpha() for c in nickname):
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
    return Config(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        bot_tokens=parse_bot_tokens(env.get("TELEGRAM_BOT_TOKENS")),
    )
