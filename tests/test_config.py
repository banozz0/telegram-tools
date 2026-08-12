from pathlib import Path

import pytest

from telegram_tools.config import ConfigError, load_config


def test_load_config_uses_env_and_home_session_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    home = tmp_path / "home"
    config = load_config(home=home)

    assert config.api_id == 123456
    assert config.api_hash == "abc123"
    assert config.session_path == Path(home, ".telegram-tools", "telegram-tools")


def test_load_config_session_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_TOOLS_SESSION", str(tmp_path / "custom" / "sess"))

    config = load_config(home=tmp_path / "home")

    assert config.session_path == tmp_path / "custom" / "sess"


def test_load_config_reads_home_dotenv_when_cwd_dotenv_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    home = tmp_path / "home"
    home.joinpath(".telegram-tools").mkdir(parents=True)
    home.joinpath(".telegram-tools", ".env").write_text(
        "TELEGRAM_API_ID=777\n"
        "TELEGRAM_API_HASH=from-home-dotenv\n"
    )

    config = load_config(home=home)

    assert config.api_id == 777
    assert config.api_hash == "from-home-dotenv"


def test_load_config_reads_local_dotenv_when_shell_env_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    tmp_path.joinpath(".env").write_text(
        "TELEGRAM_API_ID=654321\n"
        "TELEGRAM_API_HASH=from-dotenv\n"
    )

    config = load_config(home=tmp_path / "home")

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

    config = load_config(home=tmp_path / "home")

    assert config.api_id == 123456
    assert config.api_hash == "from-shell"


def test_load_config_requires_api_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
        load_config(home=tmp_path / "home")


def test_load_config_requires_integer_api_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "not-an-int")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")

    with pytest.raises(ConfigError, match="integer"):
        load_config(home=tmp_path / "home")
