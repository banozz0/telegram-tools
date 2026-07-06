from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import load_dotenv


BotTransport = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class BotInventoryItem:
    masked_token: str
    ok: bool
    bot_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "id": self.bot_id,
            "username": self.username,
            "display_name": self.display_name,
            "token": self.masked_token,
            "error": self.error,
        }


def mask_token(token: str) -> str:
    if ":" in token:
        prefix, _secret = token.split(":", 1)
        return f"{prefix}:***"
    if len(token) <= 4:
        return "***"
    return f"{token[:4]}***"


def _split_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", value.strip()) if token]


def _dedupe(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def bot_tokens_from_env(env: Mapping[str, str] | None = None) -> list[str]:
    env = env or os.environ
    tokens: list[str] = []

    singular = env.get("TELEGRAM_BOT_TOKEN")
    if singular:
        tokens.extend(_split_tokens(singular))

    token_list = env.get("TELEGRAM_BOT_TOKENS")
    if token_list:
        tokens.extend(_split_tokens(token_list))

    for key in sorted(env):
        if key.startswith("TELEGRAM_BOT_TOKEN_"):
            tokens.extend(_split_tokens(env[key]))

    return _dedupe(tokens)


def bot_tokens_from_json(path: Path) -> list[str]:
    if not path.exists():
        return []

    data = json.loads(path.read_text())
    tokens: list[str] = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                tokens.append(item)
            elif isinstance(item, dict) and isinstance(item.get("token"), str):
                tokens.append(item["token"])
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, str):
                tokens.append(value)
            elif isinstance(value, dict) and isinstance(value.get("token"), str):
                tokens.append(value["token"])

    return _dedupe(tokens)


def _read_bot_json(path: Path) -> Any:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _write_bot_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_bot_tokens(
    *,
    env: Mapping[str, str] | None = None,
    bots_json: Path | str = Path("bots.json"),
    cwd: Path | None = None,
) -> list[str]:
    cwd = cwd or Path.cwd()
    if env is None:
        load_dotenv(dotenv_path=cwd / ".env", override=False)
        env = os.environ

    path = Path(bots_json)
    if not path.is_absolute():
        path = cwd / path

    return _dedupe(bot_tokens_from_env(env) + bot_tokens_from_json(path))


def telegram_get_me(token: str, timeout: int = 10) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getMe",
        headers={"User-Agent": "telegram-tools/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _display_name(result: Mapping[str, Any]) -> str | None:
    parts = [str(result.get(key, "")).strip() for key in ("first_name", "last_name")]
    value = " ".join(part for part in parts if part)
    return value or None


def validate_bot_token(
    token: str,
    *,
    transport: BotTransport = telegram_get_me,
    timeout: int = 10,
) -> BotInventoryItem:
    masked = mask_token(token)
    try:
        payload = transport(token, timeout)
    except urllib.error.HTTPError as exc:
        return BotInventoryItem(masked_token=masked, ok=False, error=f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return BotInventoryItem(masked_token=masked, ok=False, error=str(exc.reason))
    except Exception as exc:
        return BotInventoryItem(masked_token=masked, ok=False, error=exc.__class__.__name__)

    if not payload.get("ok"):
        return BotInventoryItem(
            masked_token=masked,
            ok=False,
            error=str(payload.get("description") or "Bot API returned ok=false"),
        )

    result = payload.get("result") or {}
    return BotInventoryItem(
        masked_token=masked,
        ok=True,
        bot_id=int(result["id"]) if result.get("id") is not None else None,
        username=result.get("username"),
        display_name=_display_name(result),
    )


def validate_bots(
    tokens: list[str],
    *,
    transport: BotTransport = telegram_get_me,
    timeout: int = 10,
) -> list[BotInventoryItem]:
    return [validate_bot_token(token, transport=transport, timeout=timeout) for token in tokens]


def add_bot_token(
    token: str,
    *,
    bots_json: Path | str = Path("bots.json"),
    transport: BotTransport = telegram_get_me,
    timeout: int = 10,
) -> BotInventoryItem:
    item = validate_bot_token(token, transport=transport, timeout=timeout)
    if not item.ok:
        return item

    path = Path(bots_json)
    data = _read_bot_json(path)
    if not isinstance(data, list):
        data = [{"name": key, "token": value} for key, value in data.items()] if isinstance(data, dict) else []

    existing_tokens = set()
    for entry in data:
        if isinstance(entry, str):
            existing_tokens.add(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("token"), str):
            existing_tokens.add(entry["token"])

    if token not in existing_tokens:
        data.append(
            {
                "name": item.display_name or item.username or str(item.bot_id or "bot"),
                "token": token,
            }
        )
        _write_bot_json(path, data)

    return item


def format_bot_inventory(items: list[BotInventoryItem]) -> str:
    if not items:
        return "No bot tokens found."

    lines = ["name\tbot ID\tusername\tmasked token\tstatus"]
    for item in items:
        status = "ok" if item.ok else f"error: {item.error or 'unknown'}"
        lines.append(
            "\t".join(
                [
                    item.display_name or "",
                    "" if item.bot_id is None else str(item.bot_id),
                    item.username or "",
                    item.masked_token,
                    status,
                ]
            )
        )
    return "\n".join(lines)
