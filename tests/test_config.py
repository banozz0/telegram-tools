from pathlib import Path

import pytest

from telegram_tools.config import ConfigError, load_config


def test_load_config_uses_env_and_local_session_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    config = load_config()

    assert config.api_id == 123456
    assert config.api_hash == "abc123"
    assert config.session_path == Path(tmp_path, ".telegram-tools", "telegram-tools")


def test_load_config_requires_api_id(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
        load_config()


def test_load_config_requires_integer_api_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "not-an-int")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="integer"):
        load_config()
