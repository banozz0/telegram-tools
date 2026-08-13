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
    api_hash: str = field(repr=False)
    session_path: Path
    bot_tokens: dict[str, str] = field(default_factory=dict, repr=False)


def bot_id_from_token(token: str) -> int | None:
    prefix, _, _ = token.partition(":")
    return int(prefix) if prefix.isdigit() else None


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
        if not separator or not nickname or not token or bot_id_from_token(token) is None:
            raise ConfigError(f"TELEGRAM_BOT_TOKENS entry {position} must look like nickname:token.")
        tokens[nickname] = token
    return tokens


def _token_index(tokens: Mapping[str, str]) -> dict[str, str]:
    """Nicknames plus each token's own bot id.

    The id wins a collision: a nickname is a label a human typed and can name the
    wrong bot, while the id in the token prefix is the bot the token really opens.
    """
    index = dict(tokens)
    for token in tokens.values():
        bot_id = bot_id_from_token(token)
        if bot_id is not None:
            index[str(bot_id)] = token
    return index


def lookup_bot_token(tokens: Mapping[str, str], *references: Any) -> str | None:
    """Return the token stored under a nickname, or under a token's own bot id.

    A `@username` only matches when that username was also used as the nickname;
    usernames are not known here, so there is nothing else to match them against.
    """
    index = _token_index(tokens)
    for reference in references:
        if reference is None:
            continue
        key = str(reference).strip().lstrip("@").lower()
        if key in index:
            return index[key]
    return None


def resolve_bot_token(tokens: Mapping[str, str], reference: Any) -> tuple[str | None, Any]:
    """Pair `--bot X` with its token and with the reference to resolve the bot by.

    A nickname means nothing to Telegram, so a matched token replaces the reference
    with its own bot id, which also makes the token and the bot about to be edited
    the same bot by construction. `_run_bots` still verifies that against the
    resolved profile before writing anything.
    """
    token = lookup_bot_token(tokens, reference)
    if token is None:
        return None, reference
    return token, bot_id_from_token(token) or reference


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
