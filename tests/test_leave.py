"""Leaving a group or channel: this account's seat, never the chat.

The gate under test is `delete`'s: every path proves that a missing --execute,
a wrong title, or a missing terminal leaves Telegram untouched, that the
creator of a chat is refused in both modes -- Telegram has no call that takes
one out of its own chat -- and that a leave Telegram will not confirm is
reported as unconfirmed rather than done.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputUserSelf

from telegram_tools import cli
from test_cli import parse_args
from telegram_tools.delete import format_leave_preview, leave_chat, leave_kind_for_type
from telegram_tools.envelope import Reporter
from test_archive_sync import home  # noqa: F401 - fixture
from test_manage import audit_lines
from test_structure import BASIC_ID, CHANNEL_ID, FORUM_ID, FakeClient, envelope_of, run_cli  # noqa: F401 - fixture

CHANNEL_PEER = InputPeerChannel(channel_id=222, access_hash=0)
BASIC_PEER = InputPeerChat(chat_id=555)


class Recorder:
    """Records the requests it is called with, the way the delete tests do."""

    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return None


def never_asked(*_args):
    raise AssertionError("a dry run must not ask for a confirmation")


# -- the kinds ------------------------------------------------------------------


def test_every_chat_with_members_can_be_left_and_a_person_cannot():
    assert leave_kind_for_type("supergroup") == "group"
    assert leave_kind_for_type("forum_group") == "group"
    assert leave_kind_for_type("group") == "group"
    assert leave_kind_for_type("channel") == "channel"
    assert leave_kind_for_type("user") is None


# -- dry run --------------------------------------------------------------------


def test_dry_run_sends_nothing_and_never_prompts():
    client = Recorder()
    lines = []
    result = asyncio.run(
        leave_chat(client, CHANNEL_PEER, kind="group", title="Hermes", chat_id=-100111, confirm=never_asked, progress=lines.append)
    )
    assert result.dry_run is True and result.left is False
    assert client.requests == []
    printed = "\n".join(lines)
    assert "Hermes" in printed and "-100111" in printed and "--execute" in printed
    assert "Nothing is deleted" in printed


def test_neither_the_summary_nor_the_preview_discusses_the_creator():
    """A creator never reaches either one: `leave` refuses before this is called."""
    lines = []
    asyncio.run(
        leave_chat(Recorder(), CHANNEL_PEER, kind="channel", title="Alerts", chat_id=-100222, creator=True, confirm=never_asked, progress=lines.append)
    )
    assert "You created this chat" not in "\n".join(lines)
    assert "You created this chat" not in format_leave_preview("channel", "Alerts", -100222)


# -- the typed-title gate -------------------------------------------------------


def test_wrong_title_cancels_and_sends_nothing():
    client = Recorder()
    result = asyncio.run(
        leave_chat(client, CHANNEL_PEER, kind="group", title="Hermes", chat_id=-100111, execute=True, confirm=lambda _preview, _title: "Herme")
    )
    assert result.cancelled is True and result.left is False
    assert client.requests == []


def test_typing_DELETE_is_not_enough():
    client = Recorder()
    result = asyncio.run(
        leave_chat(client, CHANNEL_PEER, kind="group", title="Hermes", chat_id=-100111, execute=True, confirm=lambda _preview, _title: "DELETE")
    )
    assert result.cancelled is True
    assert client.requests == []


def test_right_title_leaves_a_channel_or_supergroup_through_leave_channel():
    client = Recorder()
    result = asyncio.run(
        leave_chat(client, CHANNEL_PEER, kind="group", title="Hermes", chat_id=-100111, execute=True, confirm=lambda _preview, title: title)
    )
    assert result.left is True
    assert len(client.requests) == 1 and isinstance(client.requests[0], LeaveChannelRequest)


def test_a_basic_group_is_left_by_removing_the_account_itself():
    client = Recorder()
    result = asyncio.run(
        leave_chat(client, BASIC_PEER, kind="group", title="Old Basic", chat_id=-555, execute=True, confirm=lambda _preview, _title: "  old basic ")
    )
    assert result.left is True
    request = client.requests[0]
    assert isinstance(request, DeleteChatUserRequest)
    assert request.chat_id == 555 and isinstance(request.user_id, InputUserSelf)


# -- the command -----------------------------------------------------------------


def test_leave_has_no_yes_flag():
    """Leaving always needs a human: there is no unattended path, by design."""
    with pytest.raises(SystemExit):
        parse_args("leave", "--chat", "@hermes", "--yes")


def test_a_bot_may_leave():
    assert "leave" in cli.BOT_MODE_COMMANDS
    assert cli.require_bot_mode_supports(SimpleNamespace(command="leave"), Reporter()) is None


class LeavingClient(FakeClient):
    """The structure fake, plus Telegram's answer to a leave: the entity is marked left.

    The structure fake hands out every right, `is_creator` included; leaving is
    the one command a creator may not run, so the ordinary member is the fake
    here and a creator is made one chat at a time by `creator_client`.
    """

    async def get_permissions(self, peer, user):
        permissions = await super().get_permissions(peer, user)
        permissions.is_creator = False
        return permissions

    async def __call__(self, request):
        name = type(request).__name__
        if name in ("LeaveChannelRequest", "DeleteChatUserRequest"):
            self.world.requests.append(request)
            for chat in self.world.chats.values():
                entity = chat["entity"]
                if (name == "LeaveChannelRequest" and entity.id == request.channel.channel_id) or (
                    name == "DeleteChatUserRequest" and entity.id == request.chat_id
                ):
                    entity.left = True
            return SimpleNamespace(updates=[])
        return await super().__call__(request)


def leaves(fake) -> list[str]:
    return [type(r).__name__ for r in fake.world.requests if type(r).__name__ in ("LeaveChannelRequest", "DeleteChatUserRequest")]


def test_the_dry_run_is_the_default(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", "@agencyalerts"], client=LeavingClient(), capsys=capsys, isatty=False)

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "dry_run")
    assert envelope["result"]["creator"] is False and envelope["result"]["left"] is False
    assert envelope["plan"]["approval"] == "typed_name"
    assert leaves(fake) == [] and audit_lines(home) == []


# -- the creator, who cannot leave at all ----------------------------------------

CREATOR_REFUSAL = (
    f"Telegram does not let the creator of a channel leave it: Alerts ({CHANNEL_ID}) stays this "
    "account's, and `channels.leaveChannel` from its creator returns without an error and without effect."
)


def creator_client(chat_id=CHANNEL_ID):
    client = LeavingClient()
    client.world.chats[chat_id]["entity"].creator = True
    return client


def test_a_creator_is_refused_before_anything_is_sent(run_cli, capsys, home):
    """The live defect: leaving printed `Left`, exited 0, and the account was still the creator."""
    code, out, _err, fake = run_cli(
        ["--json", "leave", "--chat", "@agencyalerts", "--execute"],
        client=creator_client(),
        capsys=capsys,
        isatty=True,
        answer="Alerts",
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused"), out
    assert envelope["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert envelope["error"]["message"] == CREATOR_REFUSAL
    assert envelope["error"]["hint"] == "Transfer ownership in Telegram, or run `telegram-tools delete channel --chat @agencyalerts --execute`."
    assert leaves(fake) == [] and audit_lines(home) == []


def test_the_creator_dry_run_refuses_too_so_the_menu_never_offers_the_real_row(run_cli, capsys, home):
    code, _out, err, fake = run_cli(["leave", "--chat", "@agencyalerts"], client=creator_client(), capsys=capsys, isatty=False)

    assert code == 2
    assert CREATOR_REFUSAL in err
    assert leaves(fake) == [] and audit_lines(home) == []


def test_a_basic_group_creator_is_refused_and_pointed_at_telegram(run_cli, capsys, home):
    """`delete` has no basic group either, so the hint names Telegram rather than a command that refuses."""
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", str(BASIC_ID), "--execute"], client=creator_client(BASIC_ID), capsys=capsys, isatty=True, answer="Old Basic")

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "PLATFORM_UNSUPPORTED"), out
    assert envelope["error"]["hint"] == "Transfer ownership in Telegram, or delete the chat in Telegram itself."
    assert leaves(fake) == [] and audit_lines(home) == []


def test_execute_refuses_without_a_terminal_in_either_mode(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", "@teamhermes", "--execute"], client=LeavingClient(), capsys=capsys, isatty=False, answer="Team Hermes")
    assert (code, envelope_of(out)["error"]["code"]) == (3, "APPROVAL_REQUIRED")
    assert leaves(fake) == []

    code, _out, err, fake = run_cli(["leave", "--chat", "@teamhermes", "--execute"], client=LeavingClient(), capsys=capsys, isatty=False, answer="Team Hermes")
    assert code == 3 and "no terminal" in err
    assert leaves(fake) == [] and audit_lines(home) == []


def test_the_wrong_title_cancels_and_leaves_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", "@teamhermes", "--execute"], client=LeavingClient(), capsys=capsys, isatty=True, answer="Team Hermez")

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (1, "cancelled")
    assert leaves(fake) == [] and audit_lines(home) == []


def test_the_right_title_leaves_reads_back_and_audits(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", "@teamhermes", "--execute"], client=LeavingClient(), capsys=capsys, isatty=True, answer="team hermes")

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok"), out
    assert envelope["result"]["left"] is True and envelope["result"]["chat_id"] == FORUM_ID
    assert leaves(fake) == ["LeaveChannelRequest"]
    assert envelope["evidence"]["readback"] == f"group Team Hermes ({FORUM_ID}) is marked left"
    rows = audit_lines(home)
    assert len(rows) == 1 and rows[0]["command"] == "leave" and rows[0]["status"] == "ok"


def test_a_basic_group_is_left_too(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "leave", "--chat", str(BASIC_ID), "--execute"], client=LeavingClient(), capsys=capsys, isatty=True, answer="Old Basic")

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok"), out
    assert leaves(fake) == ["DeleteChatUserRequest"]


# -- a leave Telegram will not confirm -------------------------------------------


class StubbornClient(LeavingClient):
    """Telegram taking the leave and still listing the account in the chat afterwards."""

    async def __call__(self, request):
        result = await super().__call__(request)
        for chat in self.world.chats.values():
            chat["entity"].left = False
        return result


def test_a_leave_the_readback_cannot_confirm_is_not_ok(run_cli, capsys, home):
    code, out, err, fake = run_cli(["--json", "leave", "--chat", "@teamhermes", "--execute"], client=StubbornClient(), capsys=capsys, isatty=True, answer="team hermes")

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (1, "partial"), out
    assert envelope["result"]["left"] == "unverified"
    assert envelope["evidence"]["readback"].startswith("unverified:")
    assert (
        f"The leave could not be confirmed: Telegram still lists this account in group Team Hermes ({FORUM_ID})."
        in err
    )
    assert "Left group" not in err
    assert leaves(fake) == ["LeaveChannelRequest"]
    rows = audit_lines(home)
    assert len(rows) == 1 and rows[0]["status"] == "partial"


def test_a_confirmed_leave_says_left_once_the_readback_agrees(run_cli, capsys, home):
    code, _out, err, _fake = run_cli(["--json", "leave", "--chat", "@teamhermes", "--execute"], client=LeavingClient(), capsys=capsys, isatty=True, answer="team hermes")

    assert code == 0
    assert f"Left group Team Hermes ({FORUM_ID})" in err
