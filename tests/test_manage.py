"""Administration on this tool: admins, members, join requests, invite links, settings, and the P7 row.

Spec section 13 and the P7 row of section 21. The fake Telegram is the
structure tests' `World` with people in it: a creator, two admins with
different rights, members, one banned, a bot, and pending join requests and
invite links per chat. It answers the participant reads and the edit calls
the port makes, moves people between statuses the way Telegram does, and
records every request, so "a missing right is named before any mutation" is
checked by looking at what reached the fake rather than trusting the message.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import types
from telethon.tl.types import ChatAdminRights, ChatBannedRights

from telegram_tools import cli
from telegram_tools import manage as manage_ops
from telegram_tools._core.redaction import find
from telegram_tools.adapters.account import RIGHT_NAMES
from telegram_tools.adapters.manage import TelegramManagePort, member_of
from telegram_tools.envelope import CommandError
from test_archive_sync import ACCOUNT, home  # noqa: F401 - fixture
from test_structure import FORUM_ID, CHANNEL_ID, BASIC_ID, FakeClient, World, envelope_of, run_cli  # noqa: F401 - fixture

FORUM = "@teamhermes"
INVITE = "https://t.me/+AbCdEfGh12345"
INVITE_TWO = "https://t.me/+ZyXwVuTs98765"

MUTATING = ("EditAdminRequest", "EditBannedRequest", "HideChatJoinRequestRequest", "ExportChatInviteRequest", "EditExportedChatInviteRequest", "ToggleSlowModeRequest")


def user(user_id: int, first: str, username: str | None = None, *, bot: bool = False) -> types.User:
    return types.User(id=user_id, first_name=first, username=username, bot=bot, access_hash=1)


SVEN = user(4242, "Sven", "sven")
HARRY = user(777, "Harry", "harry")
DOBBY = user(888, "Dobby", "dobby")
MOODY = user(999, "Moody", None)
TROLL = user(555, "Troll", "troll")
NEWBIE = user(666, "Newbie", "newbie")
ALERTS_BOT = user(123456, "Alerts", "alertsbot", bot=True)
USERS = {u.id: u for u in (SVEN, HARRY, DOBBY, MOODY, TROLL, NEWBIE, ALERTS_BOT)}


def admin(user_id: int, *names: str, by: int = 4242, can_edit: bool = True, rank: str | None = None):
    return types.ChannelParticipantAdmin(user_id=user_id, promoted_by=by, date=None, admin_rights=ChatAdminRights(**{n: True for n in names}), can_edit=can_edit, rank=rank)


def plain(user_id: int):
    return types.ChannelParticipant(user_id=user_id, date=None)


def banned(user_id: int, *names: str, until=None, left: bool = True, by: int = 4242):
    return types.ChannelParticipantBanned(peer=types.PeerUser(user_id), kicked_by=by, date=None, banned_rights=ChatBannedRights(until_date=until, **{n: True for n in names}), left=left)


def invite(link: str, **fields):
    return types.ChatInviteExported(link=link, admin_id=4242, date=None, **fields)


class PeopledWorld(World):
    """The structure world plus who is in each chat, who asked to join, and which links exist."""

    def __init__(self) -> None:
        super().__init__()
        self.people: dict[int, dict[int, object]] = {
            FORUM_ID: {
                4242: types.ChannelParticipantCreator(user_id=4242, admin_rights=ChatAdminRights(**{n: True for n in ("change_info", "post_messages", "edit_messages", "delete_messages", "ban_users", "invite_users", "pin_messages", "add_admins", "manage_topics")})),
                888: admin(888, "change_info", "pin_messages", rank="helper"),
                999: admin(999, "ban_users", "add_admins", "delete_messages", "change_info", can_edit=False),
                777: plain(777),
                555: banned(555, "view_messages"),
                123456: admin(123456, "ban_users", "invite_users", "add_admins"),
            },
            CHANNEL_ID: {4242: types.ChannelParticipantCreator(user_id=4242, admin_rights=ChatAdminRights(post_messages=True, add_admins=True)), 777: plain(777)},
        }
        self.requests_to_join: dict[int, list] = {FORUM_ID: [types.ChatInviteImporter(user_id=666, date=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc), requested=True, about="hi, from the club")]}
        self.invites: dict[int, list] = {FORUM_ID: [invite(INVITE, title="club", usage=3), invite(INVITE_TWO, revoked=True)]}
        # Who the fake thinks "self" is: the account, or the bot under --as-bot.
        self.self_id = 4242

    def participant_of(self, marked: int, user_id: int):
        return self.people.setdefault(marked, {}).get(user_id)


class PeopledClient(FakeClient):
    """The structure fake, plus users, participants and the administration calls."""

    def __init__(self, world: PeopledWorld | None = None, *, me=None, held=None) -> None:
        super().__init__(world or PeopledWorld())
        self.me = me or ACCOUNT
        # The rights `get_permissions` reports for the acting identity; everything by default.
        self.held = set(RIGHT_NAMES) if held is None else set(held)

    async def get_me(self):
        return self.me

    async def get_entity(self, reference):
        for uid, person in USERS.items():
            if reference == uid or (isinstance(reference, str) and person.username and reference.lstrip("@") == person.username):
                return person
        return await super().get_entity(reference)

    async def get_input_entity(self, entity):
        if isinstance(entity, types.User):
            return types.InputPeerUser(user_id=entity.id, access_hash=1)
        return await super().get_input_entity(entity)

    async def get_permissions(self, peer, user):
        return SimpleNamespace(**{name: name in self.held for name in RIGHT_NAMES})

    def _user_id(self, input_user) -> int:
        if isinstance(input_user, types.InputUserSelf):
            return self.world.self_id
        return int(input_user.user_id)

    async def __call__(self, request):
        name = type(request).__name__
        world = self.world
        if name == "GetParticipantRequest":
            marked, _chat = world.by_peer(request.channel)
            uid = self._user_id(request.participant)
            world.requests.append(request)
            participant = world.participant_of(marked, uid)
            if participant is None:
                from telethon.errors import UserNotParticipantError

                raise UserNotParticipantError(request)
            return SimpleNamespace(participant=participant, users=[USERS[uid]])
        if name == "GetParticipantsRequest":
            marked, _chat = world.by_peer(request.channel)
            world.requests.append(request)
            kind = type(request.filter).__name__
            rows = list(world.people.get(marked, {}).values())
            if kind == "ChannelParticipantsAdmins":
                rows = [p for p in rows if getattr(p, "admin_rights", None) is not None]
            elif kind == "ChannelParticipantsKicked":
                rows = [p for p in rows if getattr(p, "banned_rights", None) is not None and p.banned_rights.view_messages]
            elif kind == "ChannelParticipantsBanned":
                rows = [p for p in rows if getattr(p, "banned_rights", None) is not None and not p.banned_rights.view_messages]
            else:
                rows = [p for p in rows if getattr(p, "banned_rights", None) is None]
                if kind == "ChannelParticipantsSearch" and request.filter.q:
                    q = request.filter.q.casefold()
                    rows = [p for p in rows if q in (USERS[_uid(p)].first_name or "").casefold() or q in (USERS[_uid(p)].username or "").casefold()]
            page = rows[request.offset : request.offset + request.limit]
            return SimpleNamespace(participants=page, users=[USERS[_uid(p)] for p in page])
        if name == "EditAdminRequest":
            marked, _chat = world.by_peer(request.channel)
            world.requests.append(request)
            uid = self._user_id(request.user_id)
            names = [n for n in manage_ops.ADMIN_RIGHT_NAMES if getattr(request.admin_rights, n, False)]
            world.people[marked][uid] = admin(uid, *names, rank=request.rank or None) if names else plain(uid)
            return SimpleNamespace(updates=[])
        if name == "EditBannedRequest":
            marked, _chat = world.by_peer(request.channel)
            world.requests.append(request)
            uid = self._user_id(request.participant)
            names = [n for n in manage_ops.BANNED_RIGHT_NAMES if getattr(request.banned_rights, n, False)]
            world.people[marked][uid] = banned(uid, *names, until=request.banned_rights.until_date, left="view_messages" in names) if names else plain(uid)
            return SimpleNamespace(updates=[])
        if name == "GetChatInviteImportersRequest":
            marked, _chat = world.by_peer(request.peer)
            world.requests.append(request)
            rows = world.requests_to_join.get(marked, [])
            return SimpleNamespace(importers=rows, users=[USERS[r.user_id] for r in rows])
        if name == "HideChatJoinRequestRequest":
            marked, _chat = world.by_peer(request.peer)
            world.requests.append(request)
            uid = self._user_id(request.user_id)
            world.requests_to_join[marked] = [r for r in world.requests_to_join.get(marked, []) if r.user_id != uid]
            if request.approved:
                world.people[marked][uid] = plain(uid)
            return SimpleNamespace(updates=[])
        if name == "GetExportedChatInvitesRequest":
            marked, _chat = world.by_peer(request.peer)
            world.requests.append(request)
            rows = [i for i in world.invites.get(marked, []) if bool(i.revoked) == bool(request.revoked)]
            return SimpleNamespace(invites=rows, users=[])
        if name == "ExportChatInviteRequest":
            marked, _chat = world.by_peer(request.peer)
            world.requests.append(request)
            made = invite("https://t.me/+NewLinkAbc9876", title=request.title, expire_date=request.expire_date, usage_limit=request.usage_limit, request_needed=request.request_needed)
            world.invites.setdefault(marked, []).append(made)
            return made
        if name == "EditExportedChatInviteRequest":
            marked, _chat = world.by_peer(request.peer)
            world.requests.append(request)
            for row in world.invites.get(marked, []):
                if row.link == request.link:
                    row.revoked = True
                    return SimpleNamespace(invite=row)
            raise RuntimeError("no such link (the fake)")
        return await super().__call__(request)


def _uid(participant) -> int:
    return int(getattr(participant, "user_id", None) or participant.peer.user_id)


def audit_lines(home):
    path = home / ".telegram-tools" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def mutations(fake) -> list[str]:
    return [type(r).__name__ for r in fake.world.requests if type(r).__name__ in MUTATING]


# -- the domain ---------------------------------------------------------------


def test_parse_until_takes_durations_and_iso_and_is_bounded():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert manage_ops.parse_until("2h", now=now) == now + timedelta(hours=2)
    assert manage_ops.parse_until("7d", now=now) == now + timedelta(days=7)
    assert manage_ops.parse_until("2026-09-07T12:00:00Z", now=now) == now + timedelta(days=1)
    with pytest.raises(ValueError, match="less than a minute"):
        manage_ops.parse_until("10s", now=now) if False else manage_ops.parse_until("2026-09-06T12:00:30Z", now=now)
    with pytest.raises(ValueError, match="more than a year"):
        manage_ops.parse_until("400d", now=now)
    with pytest.raises(ValueError, match="neither a duration"):
        manage_ops.parse_until("soon", now=now)


def test_parse_rights_checks_the_names_against_telegram():
    assert manage_ops.parse_rights("pin_messages, change_info", universe=manage_ops.ADMIN_RIGHT_NAMES, what="admin") == ("change_info", "pin_messages")
    assert manage_ops.parse_rights("none", universe=manage_ops.ADMIN_RIGHT_NAMES, what="admin") == ()
    with pytest.raises(ValueError, match="Unknown admin right"):
        manage_ops.parse_rights("fly", universe=manage_ops.ADMIN_RIGHT_NAMES, what="admin")


def test_the_hierarchy_rule_names_both_rights_sets():
    actor = manage_ops.Member(id=888, label="Dobby (@dobby)", username="dobby", status="admin", rights=("change_info", "pin_messages"))
    with pytest.raises(CommandError) as refused:
        manage_ops.require_hierarchy(actor, target=None, granting=("ban_users", "pin_messages"), chat_title="Team Hermes")
    assert refused.value.code == "HIERARCHY_DENIED"
    assert "You hold change_info, pin_messages" in str(refused.value)
    assert "ban_users, pin_messages" in str(refused.value)
    bigger = manage_ops.Member(id=999, label="Moody", username=None, status="admin", rights=("ban_users", "change_info"))
    with pytest.raises(CommandError, match="holds ban_users which you do not"):
        manage_ops.require_hierarchy(actor, target=bigger, granting=(), chat_title="Team Hermes")
    creator = manage_ops.Member(id=4242, label="Sven", username="sven", status="creator")
    with pytest.raises(CommandError, match="created Team Hermes"):
        manage_ops.require_hierarchy(actor, target=creator, granting=(), chat_title="Team Hermes")
    # The creator is never refused here.
    manage_ops.require_hierarchy(creator, target=bigger, granting=("add_admins",), chat_title="Team Hermes")


def test_member_of_reads_every_participant_shape():
    assert member_of(admin(888, "pin_messages", rank="helper"), DOBBY).status == "admin"
    assert member_of(admin(888, "pin_messages", rank="helper"), DOBBY).rights == ("pin_messages",)
    assert member_of(admin(888, "pin_messages", rank="helper"), DOBBY).rank == "helper"
    assert member_of(banned(555, "view_messages"), TROLL).status == "banned"
    restricted = member_of(banned(777, "send_messages", until=datetime(2026, 9, 7, tzinfo=timezone.utc), left=False), HARRY)
    assert (restricted.status, restricted.rights, restricted.until) == ("restricted", ("send_messages",), "2026-09-07T00:00:00Z")
    assert member_of(plain(777), HARRY).status == "member"
    assert member_of(plain(777), HARRY).typed_label == "@harry"
    assert manage_ops.labels_match("harry", member_of(plain(777), HARRY))
    assert manage_ops.labels_match("@Harry", member_of(plain(777), HARRY))
    assert not manage_ops.labels_match("dobby", member_of(plain(777), HARRY))


# -- the reads -----------------------------------------------------------------


def test_admin_list_shows_the_creator_and_every_admin_with_rights(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "admin", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    rows = envelope_of(out)["result"]["admins"]
    assert [row["status"] for row in rows] == ["creator", "admin", "admin", "admin"]
    dobby = next(row for row in rows if row["id"] == 888)
    assert (dobby["rights"], dobby["rank"]) == (["change_info", "pin_messages"], "helper")
    assert mutations(fake) == []


def test_member_list_filters_by_query_and_lists_the_banned(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "member", "list", "--chat", FORUM, "--query", "har"], client=PeopledClient(), capsys=capsys)
    assert code == 0
    assert [row["label"] for row in envelope_of(out)["result"]["members"]] == ["Harry (@harry)"]
    code, out, _err, _fake = run_cli(["--json", "member", "list", "--chat", FORUM, "--banned"], client=PeopledClient(), capsys=capsys)
    assert [row["status"] for row in envelope_of(out)["result"]["members"]] == ["banned"]


def test_join_requests_list_shows_who_is_waiting(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "join-requests", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    rows = envelope_of(out)["result"]["requests"]
    assert rows[0]["label"] == "Newbie (@newbie)" and rows[0]["about"] == "hi, from the club"


def test_settings_show_reads_slow_mode_join_approval_and_default_rights(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "settings", "show", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    settings = envelope_of(out)["result"]["settings"]
    assert (settings["slow_mode_seconds"], settings["join_request"], settings["default_banned_rights"]) == (30, True, ["send_gifs", "send_stickers"])


def test_a_basic_group_is_refused_with_the_supergroup_reason(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "admin", "list", "--chat", str(BASIC_ID)], client=PeopledClient(), capsys=capsys)
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert mutations(fake) == []


# -- P7: a missing right is named before any mutation ------------------------------


def test_p7_a_missing_right_is_named_before_any_mutation(run_cli, capsys):
    fake = PeopledClient(held=set(RIGHT_NAMES) - {"add_admins", "is_creator"})
    code, out, _err, _fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@harry", "--rights", "pin_messages"], client=fake, capsys=capsys)
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PERMISSION_DENIED" and "add_admins" in error["message"]
    assert mutations(fake) == []


def test_p7_a_ban_without_ban_users_is_refused_before_the_gate(run_cli, capsys):
    fake = PeopledClient(held=set(RIGHT_NAMES) - {"ban_users", "is_creator"})
    code, out, _err, _fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--execute"], client=fake, capsys=capsys, isatty=True, answer="@harry")
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"
    assert mutations(fake) == []


# -- P7: HIERARCHY_DENIED ------------------------------------------------------------


def test_p7_granting_a_right_the_account_lacks_is_hierarchy_denied(run_cli, capsys):
    # Dobby acts: an admin with change_info and pin_messages who also holds add_admins per the probe.
    fake = PeopledClient(me=DOBBY, held={"is_admin", "add_admins", "change_info", "pin_messages"})
    fake.world.self_id = 888
    code, out, _err, _fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@harry", "--rights", "ban_users"], client=fake, capsys=capsys, isatty=True, answer="y")
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "HIERARCHY_DENIED"
    assert "You hold change_info, pin_messages" in error["message"] and "ban_users" in error["message"]
    assert mutations(fake) == []


def test_editing_an_admin_with_more_rights_is_hierarchy_denied(run_cli, capsys):
    fake = PeopledClient(me=DOBBY, held={"is_admin", "add_admins", "change_info", "pin_messages"})
    fake.world.self_id = 888
    code, out, _err, _fake = run_cli(["--json", "admin", "demote", "--chat", FORUM, "--user", "999", "--execute"], client=fake, capsys=capsys, isatty=True, answer="Moody")
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "HIERARCHY_DENIED" and "ban_users" in error["message"]
    assert mutations(fake) == []


def test_banning_an_admin_is_hierarchy_denied_and_names_demote(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@dobby", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@dobby")
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "HIERARCHY_DENIED" and "admin demote" in error["hint"]
    assert mutations(fake) == []


# -- the typed gate: ban and demote ----------------------------------------------------


def test_member_ban_is_a_dry_run_by_default_and_records_the_reason_locally(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--reason", "spam"], client=PeopledClient(), capsys=capsys)
    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "dry_run" and envelope["result"]["member"]["status"] == "member"
    assert envelope["plan"]["approval"] == "typed_name"
    assert mutations(fake) == [] and audit_lines(home) == []

    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--reason", "spam", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@harry")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["status"] == "ok" and envelope["result"]["member"]["status"] == "banned" and envelope["result"]["reason"] == "spam"
    assert "reason: spam" in envelope["evidence"]["readback"]
    assert mutations(fake) == ["EditBannedRequest"]
    lines = audit_lines(home)
    assert len(lines) == 1 and lines[0]["command"] == "member ban" and lines[0]["approval"] == "typed_name"
    assert "reason: spam" in lines[0]["evidence"]["readback"]


def test_member_ban_refuses_the_wrong_label_and_changes_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="dobby")
    assert code == 1
    assert envelope_of(out)["status"] == "cancelled"
    assert mutations(fake) == [] and audit_lines(home) == []


def test_ban_and_demote_refuse_without_a_terminal_in_either_mode(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=False, answer="@harry")
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert mutations(fake) == []
    code, _out, err, fake = run_cli(["admin", "demote", "--chat", FORUM, "--user", "@dobby", "--execute"], client=PeopledClient(), capsys=capsys, isatty=False, answer="@dobby")
    assert code == 3 and "no terminal" in err
    assert mutations(fake) == []


def test_ban_and_demote_have_no_yes_flag():
    parser = cli.build_parser()
    for argv in (["member", "ban", "--chat", "x", "--user", "y", "--yes"], ["admin", "demote", "--chat", "x", "--user", "y", "--yes"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_admin_demote_takes_every_right_away_behind_the_typed_label(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "admin", "demote", "--chat", FORUM, "--user", "@dobby", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@dobby")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["member"]["status"] == "member"
    assert mutations(fake) == ["EditAdminRequest"]
    assert [line["command"] for line in audit_lines(home)] == ["admin demote"]


def test_a_person_who_changed_between_the_gate_and_the_call_is_plan_drift(run_cli, capsys, home):
    def promote_harry(world):
        world.people[FORUM_ID][777] = admin(777, "pin_messages")

    code, out, _err, fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@harry", before_answer=promote_harry)
    assert code == 2
    assert envelope_of(out)["error"]["code"] == "PLAN_DRIFT"
    assert mutations(fake) == [] and audit_lines(home) == []


# -- the y/N verbs ---------------------------------------------------------------------


def test_admin_promote_sets_the_rights_and_rank_and_reads_them_back(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@harry", "--rights", "pin_messages,manage_topics", "--rank", "ops"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["plan"]["approval"] == "prompt_y"
    assert envelope["result"]["member"]["rights"] == ["manage_topics", "pin_messages"] and envelope["result"]["member"]["rank"] == "ops"
    assert "manage_topics, pin_messages" in envelope["evidence"]["readback"]
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditAdminRequest"][0]
    assert sent.admin_rights.pin_messages and sent.admin_rights.manage_topics and not sent.admin_rights.ban_users
    assert [line["command"] for line in audit_lines(home)] == ["admin promote"]


def test_admin_rights_edits_an_existing_admin_and_promote_refuses_one(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@dobby", "--rights", "pin_messages"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"
    code, out, _err, fake = run_cli(["--json", "admin", "rights", "--chat", FORUM, "--user", "@dobby", "--rights", "ban_users"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["rights"] == ["ban_users"]
    assert mutations(fake) == ["EditAdminRequest"]


def test_a_declined_prompt_changes_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "mute", "--chat", FORUM, "--user", "@harry", "--until", "2h"], client=PeopledClient(), capsys=capsys, isatty=True, answer="n")
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    assert mutations(fake) == [] and audit_lines(home) == []


def test_member_restrict_without_until_exits_2_and_never_connects(run_cli, capsys):
    code, out, err, fake = run_cli(["--json", "member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media"], client=PeopledClient(), capsys=capsys)
    assert code == 2
    assert "--until" in err
    assert fake.world.requests == []
    code, out, err, fake = run_cli(["--json", "member", "mute", "--chat", FORUM, "--user", "@harry"], client=PeopledClient(), capsys=capsys)
    assert code == 2 and "--until" in err


def test_member_restrict_is_bounded(run_cli, capsys):
    """A bad `--until` is a usage mistake: exit 2 and the reason on stderr, nothing sent."""
    code, _out, err, fake = run_cli(["member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media", "--until", "2y"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "neither a duration" in err
    code, _out, err, fake = run_cli(["member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media", "--until", "400d"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "more than a year" in err
    assert mutations(fake) == []
    code, _out, err, fake = run_cli(["member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "view_messages", "--until", "2h"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "member ban" in err
    assert mutations(fake) == []


def test_p7_mute_and_restrict_carry_their_end_and_unmute_lifts_it(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "mute", "--chat", FORUM, "--user", "@harry", "--until", "2h"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    member = envelope_of(out)["result"]["member"]
    assert member["status"] == "restricted" and member["rights"] == ["send_messages"] and member["until"]
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditBannedRequest"][0]
    assert sent.banned_rights.send_messages and sent.banned_rights.until_date is not None
    assert not sent.banned_rights.view_messages

    world = fake.world
    code, out, _err, fake = run_cli(["--json", "member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media,send_stickers", "--until", "7d"], client=PeopledClient(world), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["rights"] == ["send_media", "send_stickers"]

    code, out, _err, fake = run_cli(["--json", "member", "unmute", "--chat", FORUM, "--user", "@harry"], client=PeopledClient(world), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["status"] == "member"
    assert [line["command"] for line in audit_lines(home)] == ["member mute", "member restrict", "member unmute"]


def test_member_unban_lifts_a_ban_and_refuses_a_member(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "member", "unban", "--chat", FORUM, "--user", "@troll"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["status"] == "member"
    code, out, _err, _fake = run_cli(["--json", "member", "unban", "--chat", FORUM, "--user", "@harry"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"


def test_join_requests_approve_and_decline(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "join-requests", "approve", "--chat", FORUM, "--user", "@newbie"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["status"] == "member"
    sent = [r for r in fake.world.requests if type(r).__name__ == "HideChatJoinRequestRequest"][0]
    assert sent.approved is True
    code, out, _err, fake = run_cli(["--json", "join-requests", "decline", "--chat", FORUM, "--user", "@newbie"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["status"] == "none"
    assert [line["command"] for line in audit_lines(home)] == ["join-requests approve", "join-requests decline"]


# -- invite links: shown only where asked for ----------------------------------------------


def test_p7_invite_links_appear_only_in_list_and_create(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(["--json", "invite", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    envelope = envelope_of(out)
    assert envelope["result"]["invites"][0]["link"] == INVITE
    assert envelope["target"]["title"] == "Team Hermes"

    code, out, _err, fake = run_cli(["--json", "invite", "create", "--chat", FORUM, "--title", "night", "--expires", "7d", "--usage-limit", "5", "--request-needed"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["invite"]["link"] == "https://t.me/+NewLinkAbc9876"
    assert envelope["result"]["invite"]["title"] == "night" and envelope["result"]["invite"]["usage_limit"] == 5
    assert "t.me/+" not in envelope["evidence"]["readback"]
    assert "t.me/+" not in json.dumps(audit_lines(home))

    code, out, _err, fake = run_cli(["--json", "invite", "revoke", "--chat", FORUM, "--link", INVITE], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["invite"]["revoked"] is True
    assert find(out) == [], find(out)
    assert INVITE not in out
    assert [line["command"] for line in audit_lines(home)] == ["invite create", "invite revoke"]
    assert find(json.dumps(audit_lines(home))) == []


def test_invite_list_needs_the_invite_right(run_cli, capsys):
    fake = PeopledClient(held=set(RIGHT_NAMES) - {"invite_users", "is_creator"})
    code, out, _err, _fake = run_cli(["--json", "invite", "list", "--chat", FORUM], client=fake, capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "PERMISSION_DENIED"
    assert fake.world.requests == [] or all(type(r).__name__ != "GetExportedChatInvitesRequest" for r in fake.world.requests)


# -- settings ------------------------------------------------------------------------------


def test_settings_set_slow_mode_toggles_it_and_reads_it_back(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "settings", "set", "--chat", FORUM, "--slow-mode", "60"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["evidence"]["readback"] == "slow mode in Team Hermes is now 60s"
    assert mutations(fake) == ["ToggleSlowModeRequest"] and fake.world.chats[FORUM_ID]["slowmode_seconds"] == 60
    assert [line["command"] for line in audit_lines(home)] == ["settings set"]
    code, _out, err, _fake = run_cli(["settings", "set", "--chat", FORUM, "--slow-mode", "7"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "--slow-mode takes one of" in err
    code, out, _err, _fake = run_cli(["--json", "settings", "set", "--chat", "@agencyalerts", "--slow-mode", "60"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and envelope_of(out)["error"]["code"] == "PLATFORM_UNSUPPORTED"


def test_settings_set_without_change_info_is_named_before_the_gate(run_cli, capsys):
    fake = PeopledClient(held=set(RIGHT_NAMES) - {"change_info", "is_creator"})
    code, out, _err, _fake = run_cli(["--json", "settings", "set", "--chat", FORUM, "--slow-mode", "60"], client=fake, capsys=capsys, isatty=True, answer="y")
    assert code == 2 and "change_info" in envelope_of(out)["error"]["message"]
    assert mutations(fake) == []


# -- bot mode -------------------------------------------------------------------------------


@pytest.fixture
def run_as_bot(run_cli, monkeypatch):
    """The same run, with `--as-bot alerts` served by a fake whose `get_me` is the bot."""

    def go(argv, *, bot, capsys, isatty=False, answer=""):
        @asynccontextmanager
        async def fake_bot_client(_config, token):
            yield bot

        monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "alerts:123456:ABCDEFghijklmnopqrstuvwxyz0123456789")
        monkeypatch.setattr(cli, "bot_client", fake_bot_client)
        return run_cli(["--json", "--as-bot", "alerts", *argv], client=PeopledClient(), capsys=capsys, isatty=isatty, answer=answer)

    return go


def test_a_bot_that_is_an_admin_with_the_right_bans_behind_the_same_gate(run_as_bot, capsys, home):
    bot = PeopledClient(me=ALERTS_BOT, held={"is_admin", "ban_users", "invite_users", "add_admins"})
    bot.world.self_id = 123456
    code, out, _err, _fake = run_as_bot(["member", "ban", "--chat", "-1001000000001", "--user", "@harry", "--execute"], bot=bot, capsys=capsys, isatty=True, answer="@harry")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["identity"]["mode"] == "bot" and envelope["result"]["member"]["status"] == "banned"
    assert mutations(bot) == ["EditBannedRequest"]
    assert audit_lines(home)[-1]["identity"]["mode"] == "bot"


def test_a_bot_that_is_not_an_admin_is_refused_by_mode(run_as_bot, capsys):
    bot = PeopledClient(me=ALERTS_BOT, held={"send_messages"})
    bot.world.self_id = 123456
    code, out, _err, _fake = run_as_bot(["admin", "list", "--chat", "-1001000000001"], bot=bot, capsys=capsys)
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "IDENTITY_MODE_UNSUPPORTED" and "not an admin" in error["message"]
    assert mutations(bot) == []


def test_a_bot_admin_without_the_specific_right_is_refused_by_name(run_as_bot, capsys):
    bot = PeopledClient(me=ALERTS_BOT, held={"is_admin", "invite_users"})
    bot.world.self_id = 123456
    code, out, _err, _fake = run_as_bot(["member", "mute", "--chat", "-1001000000001", "--user", "@harry", "--until", "1h"], bot=bot, capsys=capsys, isatty=True, answer="y")
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PERMISSION_DENIED" and "ban_users" in error["message"]
    assert mutations(bot) == []


# -- screens ----------------------------------------------------------------------------------


def test_human_screens_name_the_person_the_chat_and_the_gate(run_cli, capsys):
    code, out, _err, _fake = run_cli(["member", "ban", "--chat", FORUM, "--user", "@harry", "--reason", "spam"], client=PeopledClient(), capsys=capsys)
    assert code == 0
    assert "Acting as: Sven (@sven) · account · Target: Team Hermes" in out
    assert "member ban: Team Hermes" in out and "Who     Harry (@harry) (777), now member" in out
    assert "Reason  spam" in out and "Telegram stores no reason" in out
    assert "Dry-run. Add --execute" in out
    code, out, _err, _fake = run_cli(["admin", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert "4 admin(s) in Team Hermes" in out and "[helper]" in out
    code, out, _err, _fake = run_cli(["invite", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert INVITE in out and "3 used" in out
    code, out, _err, _fake = run_cli(["settings", "show", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert "Slow mode     30s" in out and "Join approval on" in out


def test_the_port_lists_admins_creator_first():
    fake = PeopledClient()
    port = TelegramManagePort(fake)
    import asyncio

    rows = asyncio.run(port.admins(SimpleNamespace(channel_id=1000000001, chat_id=FORUM_ID)))
    assert [row.status for row in rows][:2] == ["creator", "admin"]
    assert rows[0].label == "Sven (@sven)"
