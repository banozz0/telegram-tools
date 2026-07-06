from __future__ import annotations

import stat

from telegram_tools.doctor import run_doctor


def _make_executable(path):
    path.write_text("#!/bin/bash\ntrue\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_doctor_passes_without_printing_secret_values_or_paths(tmp_path, capsys):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _make_executable(scripts / "telegram-tools.command")

    session = tmp_path / ".telegram-tools" / "telegram-tools.session"
    session.parent.mkdir()
    session.write_text("")

    bots_json = tmp_path / "bots.json"
    bots_json.write_text('[{"name": "example", "token": "123456:secret"}]\n')

    result = run_doctor(
        root=tmp_path,
        bots_json=bots_json,
        env={
            "TELEGRAM_API_ID": "123456",
            "TELEGRAM_API_HASH": "api-hash-that-must-not-print",
        },
        version_info=(3, 11, 0),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "OK   Python version is supported" in output
    assert "OK   Telegram config is present" in output
    assert "OK   Session storage exists" in output
    assert "OK   Launcher scripts are executable" in output
    assert "OK   bots.json is valid JSON" in output
    assert "secret" not in output
    assert "api-hash-that-must-not-print" not in output
    assert str(tmp_path) not in output


def test_doctor_accepts_dotenv_presence_without_reading_it(tmp_path, capsys):
    tmp_path.joinpath(".env").write_text("TELEGRAM_API_HASH=secret-from-dotenv\n")

    result = run_doctor(
        root=tmp_path,
        bots_json=tmp_path / "bots.json",
        env={},
        version_info=(3, 11, 0),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "OK   Telegram config is present" in output
    assert "secret-from-dotenv" not in output


def test_doctor_uses_explicit_empty_env_instead_of_process_env(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "process-env-secret")

    result = run_doctor(
        root=tmp_path,
        bots_json=tmp_path / "bots.json",
        env={},
        version_info=(3, 11, 0),
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "FAIL Telegram config is missing" in output
    assert "process-env-secret" not in output


def test_doctor_fails_on_invalid_bots_json_without_printing_contents(tmp_path, capsys):
    bots_json = tmp_path / "bots.json"
    bots_json.write_text('{"token": "123456:secret"\n')

    result = run_doctor(
        root=tmp_path,
        bots_json=bots_json,
        env={"TELEGRAM_API_ID": "123456", "TELEGRAM_API_HASH": "hash"},
        version_info=(3, 11, 0),
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "FAIL bots.json is not valid JSON" in output
    assert "secret" not in output
