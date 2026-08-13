import argparse
import asyncio
from types import SimpleNamespace

import pytest

from telegram_tools.bots import ResolvedBot
from telegram_tools.cli import _run_bots, bot_edit_requests
from telegram_tools.config import Config
from telegram_tools.models import BotCommandInfo, BotInfo


def namespace(**kwargs):
    defaults = dict(
        bot=None,
        name=None,
        bio=None,
        description=None,
        commands=None,
        clear_commands=False,
        photo=None,
        remove_photo=False,
        group_rights=None,
        channel_rights=None,
        yes=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(command="bots", json_output=None, **defaults)


def test_bot_edit_requests_is_empty_without_edit_flags():
    assert bot_edit_requests(namespace(bot="harry")) == {}


def test_bot_edit_requests_collects_only_the_supplied_flags():
    requested = bot_edit_requests(namespace(bot="harry", name="Harry", clear_commands=True))

    assert requested == {"name": "Harry", "clear_commands": True}


def test_bot_edit_requests_keeps_an_empty_string_as_a_clearing_edit():
    assert bot_edit_requests(namespace(bot="harry", bio="")) == {"bio": ""}


def fake_config(**tokens):
    from pathlib import Path

    return Config(api_id=1, api_hash="hash", session_path=Path("unused"), bot_tokens=dict(tokens))


def patch_bot_reads(monkeypatch, profile):
    async def fake_resolve_bot(_client, _reference):
        return ResolvedBot(user=SimpleNamespace(id=profile.id), input_user=SimpleNamespace(user_id=profile.id), is_owned=profile.is_owned)

    async def fake_get_bot_profile(_client, _resolved):
        return profile

    monkeypatch.setattr("telegram_tools.cli.resolve_bot", fake_resolve_bot)
    monkeypatch.setattr("telegram_tools.cli.get_bot_profile", fake_get_bot_profile)


def owned_profile(**overrides):
    defaults = dict(id=12345, username="harrybot", name="Harry", bio="Assistant", description="Does things", is_owned=True)
    defaults.update(overrides)
    return BotInfo(**defaults)


def test_run_bots_rejects_edit_flags_without_a_bot():
    with pytest.raises(ValueError, match="--bot is required"):
        asyncio.run(_run_bots(object(), namespace(name="Harry"), fake_config()))


def test_run_bots_cancels_without_applying_anything(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("no edit should be applied after a cancel")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: False)

    exit_code = _run_and_capture(namespace(bot="harry", name="Harry Two"), fake_config())

    assert exit_code == 1
    assert '"cancelled": true' in capsys.readouterr().out


def test_run_bots_skips_the_prompt_with_yes(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile())
    applied = []

    async def fake_apply_owner_edits(_client, _input_user, changes):
        applied.extend(change.field for change in changes)
        return list(applied)

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: pytest.fail("--yes must not prompt"))

    assert _run_and_capture(namespace(bot="harry", name="Harry Two", yes=True), fake_config()) == 0
    assert applied == ["name"]


def test_run_bots_refuses_token_only_fields_without_a_token(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(commands=[BotCommandInfo(command="start", description="Start")]))

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKENS"):
        _run_and_capture(namespace(bot="harry", clear_commands=True), fake_config())


def _run_and_capture(args, config):
    return asyncio.run(_run_bots(object(), args, config))
