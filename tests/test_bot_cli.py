import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from telegram_tools.bots import ResolvedBot, format_edit_plan
from telegram_tools.cli import _run_bots, bot_edit_requests, main
from telegram_tools.config import Config
from telegram_tools.models import BotCommandInfo, BotInfo

BOT_TOKEN_SECRET = "12345:AAsecretvalue"


def namespace(*, json_output=None, **kwargs):
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
    return argparse.Namespace(command="bots", json_output=json_output, **defaults)


def test_bot_edit_requests_is_empty_without_edit_flags():
    assert bot_edit_requests(namespace(bot="harry")) == {}


def test_bot_edit_requests_collects_only_the_supplied_flags():
    requested = bot_edit_requests(namespace(bot="harry", name="Harry", clear_commands=True))

    assert requested == {"name": "Harry", "clear_commands": True}


def test_bot_edit_requests_keeps_an_empty_string_as_a_clearing_edit():
    assert bot_edit_requests(namespace(bot="harry", bio="")) == {"bio": ""}


async def _me():
    return SimpleNamespace(id=42, first_name="Sven", username="sven")


def account_client():
    """As much of the signed-in account as a bot edit needs: who is acting.

    A bot edit builds a plan and leaves an audit line, and both name the
    account making the change, so the client can no longer be a bare object.
    """
    return SimpleNamespace(get_me=_me)


def fake_config(**tokens):
    from pathlib import Path

    return Config(api_id=1, api_hash="hash", session_path=Path("unused"), bot_tokens=dict(tokens))


def patch_bot_reads(monkeypatch, profile):
    """Fake both network reads. Returns the recorded calls, including the reference
    resolution was asked for - which is what decides *which* bot gets edited."""
    calls = {}

    async def fake_resolve_bot(_client, reference):
        calls["reference"] = reference
        return ResolvedBot(user=SimpleNamespace(id=profile.id), input_user=SimpleNamespace(user_id=profile.id), is_owned=profile.is_owned)

    async def fake_get_bot_profile(_client, _resolved):
        return profile

    monkeypatch.setattr("telegram_tools.cli.resolve_bot", fake_resolve_bot)
    monkeypatch.setattr("telegram_tools.cli.get_bot_profile", fake_get_bot_profile)
    return calls


def owned_profile(**overrides):
    defaults = dict(id=12345, username="harrybot", name="Harry", bio="Assistant", description="Does things", is_owned=True)
    defaults.update(overrides)
    return BotInfo(**defaults)


def test_run_bots_rejects_edit_flags_without_a_bot():
    with pytest.raises(ValueError, match="--bot is required"):
        asyncio.run(_run_bots(account_client(), namespace(name="Harry"), fake_config()))


def test_run_bots_cancels_without_applying_anything(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("no edit should be applied after a cancel")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: False)

    exit_code = _run_and_capture(namespace(bot="harry", name="Harry Two"), fake_config())

    assert exit_code == 1
    assert '"cancelled": true' in capsys.readouterr().out


def test_run_bots_skips_the_prompt_with_yes(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fake_apply_owner_edits(_client, _input_user, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: pytest.fail("--yes must not prompt"))

    assert _run_and_capture(namespace(bot="harry", name="Harry Two", yes=True), fake_config()) == 0
    heading, _, json_blob = capsys.readouterr().out.partition("\n")
    assert heading == "Editing @harrybot (12345)"
    result = json.loads(json_blob)
    assert result["applied"] == ["name"]


def test_run_bots_refuses_token_only_fields_without_a_token(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(commands=[BotCommandInfo(command="start", description="Start")]))

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKENS"):
        _run_and_capture(namespace(bot="harry", clear_commands=True), fake_config())


def test_run_bots_writes_the_edit_result_to_json_when_requested(monkeypatch, tmp_path, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fake_apply_owner_edits(_client, _input_user, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)

    output_path = tmp_path / "result.json"
    args = namespace(bot="harry", name="Harry Two", yes=True, json_output=str(output_path))

    exit_code = asyncio.run(_run_bots(account_client(), args, fake_config()))

    assert exit_code == 0
    # The JSON result goes to the file only; the identifying heading still goes to
    # stdout, since it prints on every edit run regardless of --json or --yes.
    assert capsys.readouterr().out == "Editing @harrybot (12345)\n"
    result = json.loads(output_path.read_text())
    assert result["applied"] == ["name"]


def test_bot_token_never_appears_when_showing_a_bot_profile(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())
    config = fake_config(harry=BOT_TOKEN_SECRET)

    asyncio.run(_run_bots(account_client(), namespace(bot="harry"), config))

    assert BOT_TOKEN_SECRET not in capsys.readouterr().out


def test_bot_token_never_appears_in_the_confirm_diff(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())
    config = fake_config(harry=BOT_TOKEN_SECRET)

    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda plan: print(format_edit_plan(plan)) or False)

    exit_code = asyncio.run(_run_bots(account_client(), namespace(bot="harry", name="Harry Two"), config))

    assert exit_code == 1
    assert BOT_TOKEN_SECRET not in capsys.readouterr().out


def test_bot_token_never_appears_in_the_result_json(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())
    config = fake_config(harry=BOT_TOKEN_SECRET)
    received = {}

    async def fake_apply_owner_edits(_client, _input_user, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    @asynccontextmanager
    async def fake_bot_client(_config, token):
        received["token"] = token
        yield object()

    async def fake_apply_bot_edits(_bot, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)
    monkeypatch.setattr("telegram_tools.cli.bot_client", fake_bot_client)
    monkeypatch.setattr("telegram_tools.cli.apply_bot_edits", fake_apply_bot_edits)

    args = namespace(bot="harry", name="Harry Two", group_rights="ban_users", yes=True)
    exit_code = asyncio.run(_run_bots(account_client(), args, config))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert received["token"] == BOT_TOKEN_SECRET  # the real token really was used...
    assert BOT_TOKEN_SECRET not in out  # ...but never printed.


def test_editing_an_unowned_bot_raises_permission_error_and_applies_nothing(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(is_owned=False))

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("no edit should be applied to a bot you do not own")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)
    monkeypatch.setattr("telegram_tools.cli.apply_bot_edits", fail_if_called)

    with pytest.raises(PermissionError, match="do not own"):
        asyncio.run(_run_bots(account_client(), namespace(bot="harry", name="Harry Two"), fake_config()))


def test_editing_an_unowned_bot_without_a_username_names_it_by_id_not_at_id(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(username=None, is_owned=False))

    with pytest.raises(PermissionError, match=r"bot 12345") as excinfo:
        asyncio.run(_run_bots(account_client(), namespace(bot="harry", name="Harry Two"), fake_config()))

    assert "@12345" not in str(excinfo.value)


def test_mixed_plan_without_a_token_raises_before_applying_owner_changes(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("owner edits must not be applied before the token gate raises")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)

    args = namespace(bot="harry", name="Harry Two", group_rights="ban_users")
    with pytest.raises(ValueError, match="group_rights"):
        asyncio.run(_run_bots(account_client(), args, fake_config()))


def _run_and_capture(args, config):
    return asyncio.run(_run_bots(account_client(), args, config))


@asynccontextmanager
async def refusing_bot_client(_config, _token):
    raise AssertionError("the bot rail must not be opened")
    yield  # pragma: no cover - unreachable, keeps this an async generator


def test_run_bots_resolves_a_nickname_by_the_tokens_own_bot_id(monkeypatch):
    calls = patch_bot_reads(monkeypatch, owned_profile())

    asyncio.run(_run_bots(account_client(), namespace(bot="harry"), fake_config(harry=BOT_TOKEN_SECRET)))

    assert calls["reference"] == 12345


def test_run_bots_finds_a_token_by_the_numeric_bot_id(monkeypatch):
    patch_bot_reads(monkeypatch, owned_profile(commands=[BotCommandInfo(command="start", description="Start")]))
    received = {}

    @asynccontextmanager
    async def fake_bot_client(_config, token):
        received["token"] = token
        yield object()

    async def fake_apply_bot_edits(_bot, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    monkeypatch.setattr("telegram_tools.cli.bot_client", fake_bot_client)
    monkeypatch.setattr("telegram_tools.cli.apply_bot_edits", fake_apply_bot_edits)

    # The token is filed under "mybot", but --bot names the bot by its numeric id.
    exit_code = _run_and_capture(namespace(bot="12345", clear_commands=True, yes=True), fake_config(mybot=BOT_TOKEN_SECRET))

    assert exit_code == 0
    assert received["token"] == BOT_TOKEN_SECRET


def test_run_bots_refuses_a_token_that_belongs_to_another_bot(monkeypatch):
    other_bot_token = "999:BBotherbot"
    patch_bot_reads(monkeypatch, owned_profile(commands=[BotCommandInfo(command="start", description="Start")]))
    monkeypatch.setattr("telegram_tools.cli.bot_client", refusing_bot_client)

    with pytest.raises(ValueError) as excinfo:
        _run_and_capture(namespace(bot="harry", clear_commands=True, yes=True), fake_config(harry=other_bot_token))

    message = str(excinfo.value)
    assert "999" in message and "12345" in message
    assert "BBotherbot" not in message


def test_a_token_nicknamed_after_another_bot_never_edits_the_bot_it_names(monkeypatch):
    # TELEGRAM_BOT_TOKENS=harrybot:999:BB... - the nickname is @harrybot's username,
    # but the token opens bot 999. Editing @harrybot must not touch bot 999.
    patch_bot_reads(monkeypatch, owned_profile(id=111, commands=[BotCommandInfo(command="start", description="Start")]))
    monkeypatch.setattr("telegram_tools.cli.bot_client", refusing_bot_client)

    with pytest.raises(ValueError) as excinfo:
        _run_and_capture(namespace(bot="111", clear_commands=True, yes=True), fake_config(harrybot="999:BBotherbot"))

    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKENS" in message
    assert "BBotherbot" not in message


def test_the_confirm_prompt_names_the_bot_before_the_diff(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda plan: print(format_edit_plan(plan)) or False)

    exit_code = _run_and_capture(namespace(bot="harry", name="Harry Two"), fake_config())

    out = capsys.readouterr().out
    assert exit_code == 1
    assert out.index("Editing @harrybot (12345)") < out.index("Changes")


def test_run_bots_refuses_a_missing_photo_before_prompting_or_writing(monkeypatch, tmp_path):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("nothing may be sent when the photo file is missing")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fail_if_called)
    monkeypatch.setattr("telegram_tools.cli.confirm_bot_edits", lambda _plan: pytest.fail("must fail before the prompt"))

    args = namespace(bot="harry", name="Harry Two", photo=str(tmp_path / "nope.png"))
    with pytest.raises(FileNotFoundError, match="No photo file"):
        _run_and_capture(args, fake_config())


def test_main_turns_a_missing_file_into_a_usage_error(monkeypatch, capsys):
    async def fake_run(_args, **_report):
        raise FileNotFoundError(2, "No such file or directory", "/nope.json")

    monkeypatch.setattr("telegram_tools.cli.run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        main(["bots", "--bot", "harry", "--commands", "/nope.json"])

    assert excinfo.value.code == 2
    error_output = capsys.readouterr().err
    assert "/nope.json" in error_output
    assert "Traceback" not in error_output


def test_run_bots_reports_the_owner_edit_that_landed_before_the_bot_rail_failed(monkeypatch, capsys):
    patch_bot_reads(monkeypatch, owned_profile())

    async def fake_apply_owner_edits(_client, _input_user, changes, applied):
        applied.extend(change.field for change in changes)
        return applied

    @asynccontextmanager
    async def fake_bot_client(_config, _token):
        yield object()

    async def failing_apply_bot_edits(_bot, _changes, _applied):
        raise RuntimeError("rights failed")

    monkeypatch.setattr("telegram_tools.cli.apply_owner_edits", fake_apply_owner_edits)
    monkeypatch.setattr("telegram_tools.cli.bot_client", fake_bot_client)
    monkeypatch.setattr("telegram_tools.cli.apply_bot_edits", failing_apply_bot_edits)

    args = namespace(bot="harry", name="Harry Two", group_rights="ban_users", yes=True)
    with pytest.raises(RuntimeError, match="rights failed"):
        _run_and_capture(args, fake_config(harry=BOT_TOKEN_SECRET))

    heading, _, json_blob = capsys.readouterr().out.partition("\n")
    assert heading == "Editing @harrybot (12345)"
    result = json.loads(json_blob)
    assert result["applied"] == ["name"]
    assert result["bot_id"] == 12345
