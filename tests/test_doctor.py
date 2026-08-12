from __future__ import annotations

from telegram_tools.doctor import run_doctor


def test_doctor_passes_without_printing_secret_values_or_paths(tmp_path, capsys):
    home = tmp_path / "home"
    session = home / ".telegram-tools" / "telegram-tools.session"
    session.parent.mkdir(parents=True)
    session.write_text("")

    result = run_doctor(
        root=tmp_path,
        env={
            "TELEGRAM_API_ID": "123456",
            "TELEGRAM_API_HASH": "api-hash-that-must-not-print",
        },
        version_info=(3, 11, 0),
        home=home,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "OK   Python version is supported" in output
    assert "OK   Telegram config is present" in output
    assert "OK   Session storage exists" in output
    assert "api-hash-that-must-not-print" not in output
    assert str(tmp_path) not in output


def test_doctor_accepts_dotenv_presence_without_reading_it(tmp_path, capsys):
    tmp_path.joinpath(".env").write_text("TELEGRAM_API_HASH=secret-from-dotenv\n")

    result = run_doctor(
        root=tmp_path,
        env={},
        version_info=(3, 11, 0),
        home=tmp_path / "home",
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "OK   Telegram config is present" in output
    assert "secret-from-dotenv" not in output


def test_doctor_accepts_home_dotenv_presence(tmp_path, capsys):
    home = tmp_path / "home"
    home.joinpath(".telegram-tools").mkdir(parents=True)
    home.joinpath(".telegram-tools", ".env").write_text("TELEGRAM_API_HASH=x\n")

    result = run_doctor(
        root=tmp_path,
        env={},
        version_info=(3, 11, 0),
        home=home,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "OK   Telegram config is present" in output


def test_doctor_uses_explicit_empty_env_instead_of_process_env(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "process-env-secret")

    result = run_doctor(
        root=tmp_path,
        env={},
        version_info=(3, 11, 0),
        home=tmp_path / "home",
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "FAIL Telegram config is missing" in output
    assert "process-env-secret" not in output
