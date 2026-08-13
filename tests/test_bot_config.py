from pathlib import Path

import pytest

from telegram_tools.config import (
    Config,
    ConfigError,
    bot_id_from_token,
    load_config,
    lookup_bot_token,
    parse_bot_tokens,
    resolve_bot_token,
)
from telegram_tools.doctor import check_bot_tokens


def test_parse_bot_tokens_returns_empty_mapping_when_unset():
    assert parse_bot_tokens(None) == {}
    assert parse_bot_tokens("") == {}


def test_parse_bot_tokens_keeps_the_colon_inside_the_token():
    tokens = parse_bot_tokens("harry:12345:AAExampleToken")

    assert tokens == {"harry": "12345:AAExampleToken"}


def test_parse_bot_tokens_reads_several_entries_and_trims_whitespace():
    tokens = parse_bot_tokens(" harry:12345:AAOne , alerts:67890:BBTwo ,")

    assert tokens == {"harry": "12345:AAOne", "alerts": "67890:BBTwo"}


def test_parse_bot_tokens_lowercases_nicknames():
    assert parse_bot_tokens("Harry:12345:AAOne") == {"harry": "12345:AAOne"}


def test_parse_bot_tokens_rejects_an_entry_without_a_nickname():
    with pytest.raises(ConfigError, match="entry 2"):
        parse_bot_tokens("harry:12345:AAOne,12345:AAOne")


def test_parse_bot_tokens_accepts_a_numeric_nickname():
    assert parse_bot_tokens("007:12345:AAOne") == {"007": "12345:AAOne"}


def test_parse_bot_tokens_error_never_echoes_the_token():
    with pytest.raises(ConfigError) as excinfo:
        parse_bot_tokens("harry:12345:AAOne,broken-entry")

    assert "broken-entry" not in str(excinfo.value)
    assert "AAOne" not in str(excinfo.value)


def test_lookup_bot_token_matches_a_nickname_ignoring_case_and_the_at_sign():
    tokens = {"harry": "12345:AAOne"}

    assert lookup_bot_token(tokens, "@Harry") == "12345:AAOne"
    assert lookup_bot_token(tokens, None, "harry") == "12345:AAOne"
    assert lookup_bot_token(tokens, "alerts") is None


def test_lookup_bot_token_matches_the_bot_id_inside_the_token():
    tokens = {"mybot": "12345:AAsecret"}

    assert lookup_bot_token(tokens, 12345) == "12345:AAsecret"
    assert lookup_bot_token(tokens, "12345") == "12345:AAsecret"


def test_lookup_bot_token_ignores_a_username_that_is_not_a_nickname():
    tokens = {"mybot": "12345:AAsecret"}

    assert lookup_bot_token(tokens, "@alertsbot") is None


def test_lookup_bot_token_prefers_the_token_id_over_a_nickname_that_disagrees():
    tokens = {"111": "999:BBother", "real": "111:AAmine"}

    assert lookup_bot_token(tokens, "111") == "111:AAmine"


def test_resolve_bot_token_swaps_a_nickname_for_the_tokens_own_bot_id():
    assert resolve_bot_token({"harry": "12345:AAOne"}, "harry") == ("12345:AAOne", 12345)


def test_resolve_bot_token_finds_a_token_by_numeric_id():
    assert resolve_bot_token({"harry": "12345:AAOne"}, "12345") == ("12345:AAOne", 12345)


def test_resolve_bot_token_keeps_the_reference_when_nothing_matches():
    assert resolve_bot_token({"harry": "12345:AAOne"}, "@alertsbot") == (None, "@alertsbot")


def test_config_repr_hides_the_api_hash_and_the_bot_tokens():
    config = Config(api_id=1, api_hash="hash-abc123", session_path=Path("unused"), bot_tokens={"harry": "12345:AAOne"})

    text = repr(config)

    assert "hash-abc123" not in text
    assert "AAOne" not in text
    assert "harry" not in text


def test_bot_id_from_token_reads_the_leading_digits():
    assert bot_id_from_token("12345:AAExampleToken") == 12345
    assert bot_id_from_token("not-a-token") is None


def test_load_config_exposes_bot_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "harry:12345:AAOne")

    config = load_config(home=tmp_path / "home")

    assert config.bot_tokens == {"harry": "12345:AAOne"}


def test_load_config_defaults_bot_tokens_to_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.delenv("TELEGRAM_BOT_TOKENS", raising=False)

    assert load_config(home=tmp_path / "home").bot_tokens == {}


def test_check_bot_tokens_counts_without_printing_tokens(tmp_path):
    check = check_bot_tokens(tmp_path, {"TELEGRAM_BOT_TOKENS": "harry:12345:AAOne,alerts:67890:BBTwo"}, home=tmp_path / "home")

    assert check.status == "OK"
    assert "2 bot token" in check.message
    assert "AAOne" not in check.message


def test_check_bot_tokens_warns_when_none_are_set(tmp_path):
    check = check_bot_tokens(tmp_path, {}, home=tmp_path / "home")

    assert check.status == "WARN"
    assert check.failed is False


def test_check_bot_tokens_fails_on_malformed_value(tmp_path):
    check = check_bot_tokens(tmp_path, {"TELEGRAM_BOT_TOKENS": "broken"}, home=tmp_path / "home")

    assert check.status == "FAIL"


def test_check_bot_tokens_reads_the_home_dotenv(tmp_path):
    home = tmp_path / "home"
    home.joinpath(".telegram-tools").mkdir(parents=True)
    home.joinpath(".telegram-tools", ".env").write_text("TELEGRAM_BOT_TOKENS=harry:12345:AAOne\n")

    check = check_bot_tokens(tmp_path, {}, home=home)

    assert check.status == "OK"
    assert "1 bot token" in check.message
