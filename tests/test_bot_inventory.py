import json
from pathlib import Path

from telegram_tools.bot_inventory import (
    add_bot_token,
    bot_tokens_from_env,
    format_bot_inventory,
    load_bot_tokens,
    mask_token,
    validate_bots,
)


def test_mask_token_never_exposes_secret_suffix():
    token = "123456789:secret"

    masked = mask_token(token)

    assert masked == "123456789:***"
    assert "secret" not in masked
    assert token not in masked


def test_bot_tokens_from_env_supports_singular_list_and_named_tokens():
    tokens = bot_tokens_from_env(
        {
            "TELEGRAM_BOT_TOKEN": "111:aaa",
            "TELEGRAM_BOT_TOKENS": "222:bbb, 333:ccc\n444:ddd",
            "TELEGRAM_BOT_TOKEN_WORKER": "555:eee",
        }
    )

    assert tokens == ["111:aaa", "222:bbb", "333:ccc", "444:ddd", "555:eee"]


def test_load_bot_tokens_reads_gitignored_bots_json_shapes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("bots.json").write_text(
        json.dumps(
            [
                "111:aaa",
                {"name": "example", "token": "222:bbb"},
                {"token": "111:aaa"},
            ]
        )
    )

    tokens = load_bot_tokens(env={}, bots_json=Path("bots.json"))

    assert tokens == ["111:aaa", "222:bbb"]


def test_load_bot_tokens_combines_env_and_bots_json_with_env_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("bots.json").write_text(json.dumps({"example": "222:bbb", "other": "333:ccc"}))

    tokens = load_bot_tokens(
        env={"TELEGRAM_BOT_TOKENS": "111:aaa 222:bbb"},
        bots_json=Path("bots.json"),
    )

    assert tokens == ["111:aaa", "222:bbb", "333:ccc"]


def test_validate_bots_uses_get_me_and_never_returns_full_tokens():
    calls = []

    def fake_transport(token, timeout):
        calls.append((token, timeout))
        return {
            "ok": True,
            "result": {
                "id": 123456789,
                "username": "example_bot",
                "first_name": "Example",
                "last_name": "Bot",
            },
        }

    results = validate_bots(["123456789:secret"], transport=fake_transport)

    assert calls == [("123456789:secret", 10)]
    assert results[0].ok is True
    assert results[0].bot_id == 123456789
    assert results[0].username == "example_bot"
    assert results[0].display_name == "Example Bot"
    assert results[0].masked_token == "123456789:***"
    assert "secret" not in repr(results[0])


def test_validate_bots_masks_tokens_on_invalid_response():
    def fake_transport(token, timeout):
        return {"ok": False, "description": "Unauthorized"}

    results = validate_bots(["123456789:secret"], transport=fake_transport)

    assert results[0].ok is False
    assert results[0].masked_token == "123456789:***"
    assert results[0].error == "Unauthorized"
    assert "secret" not in repr(results[0])


def test_format_bot_inventory_uses_clean_table_columns():
    results = validate_bots(
        ["123456789:secret"],
        transport=lambda token, timeout: {
            "ok": True,
            "result": {"id": 123456789, "username": "example_bot", "first_name": "Example"},
        },
    )

    text = format_bot_inventory(results)

    assert "name\tbot ID\tusername\tmasked token\tstatus" in text
    assert "Example" in text
    assert "123456789" in text
    assert "example_bot" in text
    assert "123456789:***" in text
    assert "secret" not in text


def test_add_bot_token_validates_and_stores_token_in_bots_json(tmp_path):
    path = tmp_path / "bots.json"

    item = add_bot_token(
        "123456789:secret",
        bots_json=path,
        transport=lambda token, timeout: {
            "ok": True,
            "result": {
                "id": 123456789,
                "username": "example_bot",
                "first_name": "Example",
            },
        },
    )

    assert item.ok is True
    assert item.masked_token == "123456789:***"
    assert "secret" not in repr(item)
    assert load_bot_tokens(env={}, bots_json=path, cwd=tmp_path) == ["123456789:secret"]
