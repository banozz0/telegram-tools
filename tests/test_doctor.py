from __future__ import annotations

from telegram_tools.doctor import run_doctor


def test_doctor_passes_without_printing_secret_values_or_paths(tmp_path, capsys):
    home = tmp_path / "home"
    session = home / ".telegram-tools" / "telegram-tools.session"
    session.parent.mkdir(parents=True, mode=0o700)
    session.write_text("")
    session.chmod(0o600)

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
    # Section 5.1: the profile is named, its session file never is.
    assert "OK   profile default: session present" in output
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
    # 0700/0600 is what the tool itself writes; anything looser is now a FAIL,
    # which is what test_doctor_fails_on_a_world_readable_env proves.
    home.joinpath(".telegram-tools").mkdir(parents=True, mode=0o700)
    env_file = home / ".telegram-tools" / ".env"
    env_file.write_text("TELEGRAM_API_HASH=x\n")
    env_file.chmod(0o600)

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


def test_doctor_warns_when_no_send_destination_is_allowlisted(tmp_path, capsys):
    run_doctor(root=tmp_path, env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}, version_info=(3, 11, 0), home=tmp_path / "home")

    output = capsys.readouterr().out
    assert "WARN No send destinations allowlisted" in output


def test_doctor_counts_allowlisted_destinations_without_naming_them(tmp_path, capsys):
    run_doctor(
        root=tmp_path,
        env={
            "TELEGRAM_API_ID": "1",
            "TELEGRAM_API_HASH": "h",
            "TELEGRAM_SEND_ALLOWLIST": "-100111:141,@alerts",
        },
        version_info=(3, 11, 0),
        home=tmp_path / "home",
    )

    output = capsys.readouterr().out
    assert "OK   2 send destination(s) allowlisted" in output
    assert "-100111" not in output
    assert "alerts" not in output


def test_doctor_fails_on_a_malformed_allowlist(tmp_path, capsys):
    result = run_doctor(
        root=tmp_path,
        env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h", "TELEGRAM_SEND_ALLOWLIST": "-100111:nope"},
        version_info=(3, 11, 0),
        home=tmp_path / "home",
    )

    assert result == 1
    assert "FAIL TELEGRAM_SEND_ALLOWLIST is malformed" in capsys.readouterr().out
