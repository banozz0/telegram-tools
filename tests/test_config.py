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


def test_load_config_reads_local_dotenv_when_shell_env_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    tmp_path.joinpath(".env").write_text(
        "TELEGRAM_API_ID=654321\n"
        "TELEGRAM_API_HASH=from-dotenv\n"
    )

    config = load_config()

    assert config.api_id == 654321
    assert config.api_hash == "from-dotenv"


def test_load_config_keeps_shell_env_over_local_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "from-shell")
    tmp_path.joinpath(".env").write_text(
        "TELEGRAM_API_ID=654321\n"
        "TELEGRAM_API_HASH=from-dotenv\n"
    )

    config = load_config()

    assert config.api_id == 123456
    assert config.api_hash == "from-shell"


def test_load_config_requires_api_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
        load_config()


def test_load_config_requires_integer_api_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "not-an-int")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="integer"):
        load_config()
