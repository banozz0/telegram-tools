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

import asyncio
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
from telegram_tools.adapters.manage import TelegramManagePort, member_of, topic_edit_of
from telegram_tools.envelope import CommandError
from test_adapters import holding
from test_archive_sync import ACCOUNT, home  # noqa: F401 - fixture
from test_structure import DOBBY_ICON, FORUM_ID, CHANNEL_ID, BASIC_ID, FakeClient, World, envelope_of, run_cli  # noqa: F401 - fixture

FORUM = "@teamhermes"
INVITE = "https://t.me/+AbCdEfGh12345"
INVITE_TWO = "https://t.me/+ZyXwVuTs98765"

MUTATING = (
    "EditAdminRequest",
    "EditBannedRequest",
    "KickParticipantCall",
    "HideChatJoinRequestRequest",
    "ExportChatInviteRequest",
    "EditExportedChatInviteRequest",
    "ToggleSlowModeRequest",
    "EditTitleRequest",
    "EditChatAboutRequest",
    "ToggleForumRequest",
    "EditForumTopicRequest",
)


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


# What Telegram itself does with the rights a write names, spelled out from the
# live 2026-09-17 runs (campaign transcript telegram-6-member-rows, section 6):
# a mute that named send_messages read back fifteen rights, a restrict that
# named send_media read back seven, a ban read back every right there is until
# 2038-01-19T03:14:07Z, and both promotions read back `other` beside what was
# asked for. Written here as literals rather than read off the tool, so these
# tests are a witness to Telegram and not to `manage.BANNED_FAMILY`.
MUTED_BACK = ("embed_links", "send_audios", "send_docs", "send_games", "send_gifs", "send_inline", "send_media", "send_messages", "send_photos", "send_plain", "send_polls", "send_roundvideos", "send_stickers", "send_videos", "send_voices")
RESTRICTED_MEDIA_BACK = ("send_audios", "send_docs", "send_media", "send_photos", "send_roundvideos", "send_videos", "send_voices")
FOREVER = datetime(2038, 1, 19, 3, 14, 7, tzinfo=timezone.utc)


def telegram_widens(names) -> set[str]:
    """Every banned right the server sets when a write names `names`."""
    taken = set(names)
    if "send_messages" in taken:
        taken.update(MUTED_BACK)
    if "send_media" in taken:
        taken.update(RESTRICTED_MEDIA_BACK)
    if "view_messages" in taken:
        taken.update(manage_ops.BANNED_RIGHT_NAMES)
    return taken


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


class KickParticipantCall:
    """What the fake records for `client.kick_participant`, beside the raw requests."""

    def __init__(self, channel, user) -> None:
        self.channel, self.user = channel, user


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
        return holding(self.held, chat=isinstance(peer, types.InputPeerChat))

    async def kick_participant(self, channel, user):
        # Telethon's kick: a ban then an unban, recorded here as the one call
        # the port makes. The person is out afterwards and no ban row remains.
        marked, _chat = self.world.by_peer(channel)
        uid = self._user_id(user)
        self.world.requests.append(KickParticipantCall(channel, user))
        self.world.people[marked].pop(uid, None)
        return None

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
            # Telegram sets `other` on any promotion, whoever asked for what.
            if names and "other" not in names:
                names = sorted([*names, "other"])
            world.people[marked][uid] = admin(uid, *names, rank=request.rank or None) if names else plain(uid)
            return SimpleNamespace(updates=[])
        if name == "EditBannedRequest":
            marked, _chat = world.by_peer(request.channel)
            world.requests.append(request)
            uid = self._user_id(request.participant)
            asked = {n for n in manage_ops.BANNED_RIGHT_NAMES if getattr(request.banned_rights, n, False)}
            # Telegram widens one send flag into the family beneath it, and a
            # ban into every right there is, with no end of its own.
            taken = telegram_widens(asked)
            names = [n for n in manage_ops.BANNED_RIGHT_NAMES if n in taken]
            until = FOREVER if "view_messages" in taken else request.banned_rights.until_date
            world.people[marked][uid] = banned(uid, *names, until=until, left="view_messages" in names) if names else plain(uid)
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


def test_member_kick_is_a_dry_run_that_says_it_is_a_ban_then_an_unban(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@harry", "--reason", "spam"], client=PeopledClient(), capsys=capsys)
    assert code == 0
    envelope = envelope_of(out)
    assert envelope["status"] == "dry_run" and envelope["result"]["member"]["status"] == "member"
    assert envelope["plan"]["approval"] == "typed_name"
    assert fake.world.requests[-1].__class__.__name__ == "GetParticipantRequest"
    assert mutations(fake) == [] and audit_lines(home) == []
    code, out, _err, _fake = run_cli(["member", "kick", "--chat", FORUM, "--user", "@harry", "--reason", "spam"], client=PeopledClient(), capsys=capsys)
    assert "member kick: Team Hermes" in out and "Who     Harry (@harry) (777), now member" in out
    assert "ban followed at once by an unban" in out and "may rejoin" in out and "no ban row remains" in out
    assert "Reason  spam" in out and "Telegram stores no reason for a kick" in out
    assert "Dry-run. Add --execute" in out


def test_member_kick_executed_calls_kick_participant_once_and_records_the_reason_locally(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@harry", "--reason", "spam", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@harry")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["status"] == "ok" and envelope["result"]["member"]["status"] == "none" and envelope["result"]["reason"] == "spam"
    assert "no longer in Team Hermes and may rejoin (reason: spam)" in envelope["evidence"]["readback"]
    assert mutations(fake) == ["KickParticipantCall"]
    lines = audit_lines(home)
    assert len(lines) == 1 and lines[0]["command"] == "member kick" and lines[0]["approval"] == "typed_name"
    assert "reason: spam" in lines[0]["evidence"]["readback"]


def test_member_kick_refuses_the_wrong_label_and_changes_nothing(run_cli, capsys, home):
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="dobby")
    assert code == 1
    assert envelope_of(out)["status"] == "cancelled"
    assert mutations(fake) == [] and audit_lines(home) == []


def test_member_kick_refuses_an_admin_and_someone_who_is_not_in(run_cli, capsys):
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@dobby", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@dobby")
    error = envelope_of(out)["error"]
    assert code == 2 and error["code"] == "HIERARCHY_DENIED" and "cannot be kicked until demoted" in error["message"]
    assert "admin demote" in error["hint"] and mutations(fake) == []
    # Kicking a banned person would lift their ban (Telethon says so), so it names unban instead.
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@troll", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@troll")
    error = envelope_of(out)["error"]
    assert code == 2 and error["code"] == "TARGET_KIND_MISMATCH" and "they are banned" in error["message"]
    assert "member unban" in error["hint"] and mutations(fake) == []


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
    code, out, _err, fake = run_cli(["--json", "member", "kick", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=False, answer="@harry")
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert mutations(fake) == []


def test_ban_and_demote_have_no_yes_flag():
    parser = cli.build_parser()
    for argv in (["member", "ban", "--chat", "x", "--user", "y", "--yes"], ["member", "kick", "--chat", "x", "--user", "y", "--yes"], ["admin", "demote", "--chat", "x", "--user", "y", "--yes"]):
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
    # `other` is Telegram's own, on every promotion (card agent-bo-95422334).
    assert envelope["result"]["member"]["rights"] == ["manage_topics", "other", "pin_messages"] and envelope["result"]["member"]["rank"] == "ops"
    assert "manage_topics, other, pin_messages" in envelope["evidence"]["readback"]
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditAdminRequest"][0]
    assert sent.admin_rights.pin_messages and sent.admin_rights.manage_topics and not sent.admin_rights.ban_users
    assert [line["command"] for line in audit_lines(home)] == ["admin promote"]


def test_admin_rights_edits_an_existing_admin_and_promote_refuses_one(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@dobby", "--rights", "pin_messages"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"
    code, out, _err, fake = run_cli(["--json", "admin", "rights", "--chat", FORUM, "--user", "@dobby", "--rights", "ban_users"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["rights"] == ["ban_users", "other"]
    assert mutations(fake) == ["EditAdminRequest"]


# Every y/N verb takes `--yes` (card agent-bo-95422198), as its Discord
# counterpart does. The proof it skipped the prompt: no terminal under --json
# would otherwise be APPROVAL_REQUIRED, and the typed answer is "n".
YES_VERBS = {
    "admin promote": ["admin", "promote", "--chat", FORUM, "--user", "@harry", "--rights", "pin_messages", "--yes"],
    "admin rights": ["admin", "rights", "--chat", FORUM, "--user", "@dobby", "--rights", "ban_users", "--yes"],
    "member unban": ["member", "unban", "--chat", FORUM, "--user", "@troll", "--yes"],
    "member mute": ["member", "mute", "--chat", FORUM, "--user", "@harry", "--until", "2h", "--yes"],
    "member unmute": ["member", "unmute", "--chat", FORUM, "--user", "@troll", "--yes"],
    "member restrict": ["member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media", "--until", "2h", "--yes"],
    "join-requests approve": ["join-requests", "approve", "--chat", FORUM, "--user", "@newbie", "--yes"],
    "join-requests decline": ["join-requests", "decline", "--chat", FORUM, "--user", "@newbie", "--yes"],
    "invite create": ["invite", "create", "--chat", FORUM, "--title", "night", "--yes"],
    "invite revoke": ["invite", "revoke", "--chat", FORUM, "--link", INVITE, "--yes"],
}


@pytest.mark.parametrize("command", sorted(YES_VERBS))
def test_yes_answers_the_prompt_and_the_preview_still_prints(run_cli, capsys, home, command):
    code, out, err, fake = run_cli(["--json", *YES_VERBS[command]], client=PeopledClient(), capsys=capsys, isatty=False, answer="n")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["status"] == "ok" and envelope["plan"]["approval"] == "prompt_y"
    assert "Do it? [y/N]" not in err
    assert "Team Hermes" in err  # the preview went to stderr, as the prompt's would have
    assert mutations(fake) != []
    assert [line["command"] for line in audit_lines(home)] == [command]


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
    # Telegram widens send_messages into its family (card agent-bo-95422334).
    assert member["status"] == "restricted" and member["rights"] == sorted(MUTED_BACK) and member["until"]
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditBannedRequest"][0]
    assert sent.banned_rights.send_messages and sent.banned_rights.until_date is not None
    assert not sent.banned_rights.view_messages

    world = fake.world
    code, out, _err, fake = run_cli(["--json", "member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media,send_stickers", "--until", "7d"], client=PeopledClient(world), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert envelope_of(out)["result"]["member"]["rights"] == sorted({*RESTRICTED_MEDIA_BACK, "send_stickers"})

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
    assert envelope["evidence"]["readback"] == "Team Hermes: --slow-mode 30s -> 60s"
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


def test_settings_show_carries_the_chat_fields_and_one_topics_own(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "settings", "show", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    settings = envelope_of(out)["result"]["settings"]
    assert (settings["title"], settings["about"], settings["forum"]) == ("Team Hermes", "the agency's room", True)
    code, out, _err, fake = run_cli(["--json", "settings", "show", "--chat", FORUM, "--topic", "141"], client=PeopledClient(), capsys=capsys)
    assert code == 0
    topic = envelope_of(out)["result"]["settings"]
    assert (topic["id"], topic["title"], topic["icon_emoji_id"], topic["closed"], topic["hidden"]) == (141, "Dobby", DOBBY_ICON, False, False)
    assert mutations(fake) == []
    # A topic that is not there is a refusal, never a row whose title is its id.
    code, out, _err, _fake = run_cli(["--json", "settings", "show", "--chat", FORUM, "--topic", "9999"], client=PeopledClient(), capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_NOT_FOUND"


def test_settings_set_changes_a_chats_title_and_about_and_diffs_both(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--title", "Team Hermes 2", "--about", "the agency"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    assert mutations(fake) == ["EditTitleRequest", "EditChatAboutRequest"]
    readback = envelope_of(out)["evidence"]["readback"]
    assert "--title 'Team Hermes' -> 'Team Hermes 2'" in readback
    assert """--about "the agency's room" -> 'the agency'""" in readback
    assert [line["command"] for line in audit_lines(home)] == ["settings set"]


def test_settings_set_on_a_topic_needs_manage_topics_and_carries_the_flags(run_cli, capsys, home):
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--title", "Help", "--closed", "on"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["plan"]["preflight"]["required"] == ["manage_topics"]
    # The creator holds it, and Telegram's participant object is what says so.
    assert envelope["warnings"] == [] and envelope["plan"]["preflight"]["missing"] == []
    assert envelope["target"]["rid"] == f"tg:topic:{FORUM_ID}:217"
    sent = next(r for r in fake.world.requests if type(r).__name__ == "EditForumTopicRequest")
    # The fields the flags did not name stay None: Telegram leaves those alone.
    assert (sent.title, sent.closed, sent.icon_emoji_id, sent.hidden) == ("Help", True, None, None)
    readback = envelope["evidence"]["readback"]
    assert "--title 'Support' -> 'Help'" in readback and "--closed off -> on" in readback
    assert [line["command"] for line in audit_lines(home)] == ["settings set"]


class StaleTopicClient(PeopledClient):
    """Telegram as the live run of 2026-09-17 met it: a topic edit lands, and the read straight after serves the topic as it was.

    `witness` is whether the edit's own reply carries the service message the
    edit posted (`messageActionTopicEdit`, naming what it set) or nothing a
    reader can use; `stale` is how many topic reads after the edit still get
    the old topic; `applies=False` is an edit Telegram accepts and never applies.
    """

    def __init__(self, *, witness: bool = True, stale: int = 1, applies: bool = True) -> None:
        super().__init__()
        self.witness, self.stale, self.applies = witness, stale, applies
        self.old = None
        self.stale_left = 0

    async def __call__(self, request):
        name = type(request).__name__
        if name == "EditForumTopicRequest":
            _marked, chat = self.world.by_peer(request.peer)
            self.old = SimpleNamespace(**vars(next(topic for topic in chat["topics"] if topic.id == request.topic_id)))
            if self.applies:
                await super().__call__(request)
            else:
                self.world.requests.append(request)
            self.stale_left = self.stale
            updates = []
            if self.witness:
                action = types.MessageActionTopicEdit(title=request.title, icon_emoji_id=request.icon_emoji_id, closed=request.closed, hidden=request.hidden)
                # General's messages carry no topic header; every other topic's name it.
                header = None if request.topic_id == 1 else types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=request.topic_id)
                message = types.MessageService(id=9001, peer_id=types.PeerChannel(channel_id=1000000001), date=None, out=True, reply_to=header, action=action)
                updates.append(types.UpdateNewChannelMessage(message=message, pts=1, pts_count=1))
            return types.Updates(updates=updates, users=[], chats=[], date=None, seq=0)
        if name == "GetForumTopicsByIDRequest" and self.stale_left:
            self.stale_left -= 1
            self.world.requests.append(request)
            return SimpleNamespace(topics=[self.old], count=1)
        return await super().__call__(request)


@pytest.fixture
def slept(monkeypatch):
    """Every wait a readback asks for, recorded rather than slept."""
    waits: list[float] = []

    async def sleep(seconds, *_args, **_kwargs):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return waits


def test_a_topic_rename_reads_back_the_title_the_edit_set_when_telegram_serves_the_old_one(run_cli, capsys, home, slept):
    # Live, 2026-09-17: a rename that landed printed "no field changed" and
    # wrote the old title into its result and its audit line.
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--title", "Help"],
        client=StaleTopicClient(witness=True, stale=1), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    readback = envelope["evidence"]["readback"]
    assert readback.endswith(": --title 'Support' -> 'Help'"), readback
    assert envelope["result"]["settings"]["title"] == "Help"
    assert audit_lines(home)[-1]["evidence"]["readback"] == readback
    # The reply named the title, so nothing waited for Telegram to catch up.
    assert slept == []
    assert mutations(fake) == ["EditForumTopicRequest"]


def test_the_edit_reply_is_read_only_for_its_own_topic():
    def service(action, header):
        return types.MessageService(id=9001, peer_id=types.PeerChannel(channel_id=1000000001), date=None, reply_to=header, action=action)

    in_217 = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=217)
    renamed = types.UpdateNewChannelMessage(message=service(types.MessageActionTopicEdit(title="Help", closed=True), in_217), pts=1, pts_count=1)
    reply = types.Updates(updates=[renamed], users=[], chats=[], date=None, seq=0)
    assert topic_edit_of(reply, 217) == {"title": "Help", "closed": True}
    # Another topic's service message says nothing about this one.
    assert topic_edit_of(reply, 141) == {}
    # General's messages carry no topic header; an icon of 0 is no icon.
    hidden = types.UpdateNewChannelMessage(message=service(types.MessageActionTopicEdit(hidden=True, icon_emoji_id=0), None), pts=1, pts_count=1)
    assert topic_edit_of(types.UpdateShort(update=hidden, date=None), 1) == {"hidden": True, "icon_emoji_id": None}
    # A reply with nothing usable is nothing, never a guess.
    assert topic_edit_of(types.UpdatesTooLong(), 217) == {}
    assert topic_edit_of(None, 217) == {}


def test_the_settings_diff_reads_a_topic_with_no_icon_as_none():
    # A topic without an icon reads None, not 0, so an icon set or removed
    # has None on one side of the diff.
    assert manage_ops.settings_diff({"icon_emoji_id": DOBBY_ICON}, {"icon_emoji_id": None}, manage_ops.TOPIC_FIELDS) == f"--icon-emoji-id {DOBBY_ICON} -> (none)"
    assert manage_ops.settings_diff({"icon_emoji_id": None}, {"icon_emoji_id": DOBBY_ICON}, manage_ops.TOPIC_FIELDS) == f"--icon-emoji-id (none) -> {DOBBY_ICON}"


def test_an_icon_the_edit_removed_reads_back_removed_despite_a_stale_read(run_cli, capsys, slept):
    code, out, _err, _fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "141", "--icon-emoji-id", "0"],
        client=StaleTopicClient(witness=True, stale=1), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["evidence"]["readback"].endswith(f": --icon-emoji-id {DOBBY_ICON} -> (none)"), envelope["evidence"]
    assert envelope["result"]["settings"]["icon_emoji_id"] is None
    assert slept == []


def test_a_topic_edit_whose_reply_names_nothing_is_read_again_until_telegram_serves_it(run_cli, capsys, slept):
    code, out, _err, _fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--title", "Help", "--closed", "on"],
        client=StaleTopicClient(witness=False, stale=2), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    readback = envelope["evidence"]["readback"]
    assert "--title 'Support' -> 'Help'" in readback and "--closed off -> on" in readback, readback
    assert (envelope["result"]["settings"]["title"], envelope["result"]["settings"]["closed"]) == ("Help", True)
    # Two stale reads, two waits, and the third read is believed.
    assert len(slept) == 2


def test_a_topic_edit_telegram_never_applies_still_reads_no_field_changed_after_a_bounded_wait(run_cli, capsys, slept):
    code, out, _err, _fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--title", "Help"],
        client=StaleTopicClient(witness=False, stale=0, applies=False), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["evidence"]["readback"].endswith(": no field changed"), envelope["evidence"]
    assert envelope["result"]["settings"]["title"] == "Support"
    # It waited, and not for long: the read is believed in the end.
    assert 0 < len(slept) <= 3 and sum(slept) <= 5


@pytest.mark.parametrize("witness", [True, False], ids=["reply-names-it", "reply-silent"])
def test_a_rename_to_the_title_a_topic_already_has_still_reads_no_field_changed(run_cli, capsys, slept, witness):
    code, out, _err, _fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--title", "Support"],
        client=StaleTopicClient(witness=witness, stale=1), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["evidence"]["readback"].endswith(": no field changed"), envelope["evidence"]
    assert envelope["result"]["settings"]["title"] == "Support"
    assert slept == []


def test_a_missing_manage_topics_is_named_before_any_topic_mutation(run_cli, capsys):
    fake = PeopledClient(held=set(RIGHT_NAMES) - {"manage_topics", "is_creator"})
    code, out, _err, _fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "217", "--closed", "on"],
        client=fake, capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2
    error = envelope_of(out)["error"]
    assert error["code"] == "PERMISSION_DENIED" and "manage_topics" in error["message"]
    assert mutations(fake) == []


def test_a_flag_of_the_wrong_scope_is_a_usage_error_naming_it(run_cli, capsys):
    for argv, wanted in (
        (["settings", "set", "--chat", FORUM, "--topic", "217", "--about", "no"], "--about changes a chat, not a topic"),
        (["settings", "set", "--chat", FORUM, "--closed", "on"], "--closed changes a topic; add --topic ID"),
        (["settings", "set", "--chat", FORUM], "settings set changes nothing"),
    ):
        code, _out, err, fake = run_cli(argv, client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
        assert code == 2 and wanted in err, (argv, err)
        assert mutations(fake) == []


def test_the_general_topic_takes_only_what_telegram_takes(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "1", "--closed", "on"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2 and envelope_of(out)["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert mutations(fake) == []
    # Its title and `hidden` it does take.
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--topic", "1", "--hidden", "on"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    assert mutations(fake) == ["EditForumTopicRequest"]


def test_a_topic_flag_on_a_chat_with_no_topics_is_refused(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", "@agencyalerts", "--topic", "5", "--closed", "on"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 2 and envelope_of(out)["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert mutations(fake) == []


def test_switching_topics_off_dry_runs_then_asks_for_the_chats_exact_title(run_cli, capsys, home):
    # Dry-run by default: nothing is asked and nothing is sent.
    code, out, _err, fake = run_cli(["--json", "settings", "set", "--chat", FORUM, "--forum", "off"], client=PeopledClient(), capsys=capsys, isatty=True)
    assert code == 0 and envelope_of(out)["status"] == "dry_run"
    assert mutations(fake) == [] and audit_lines(home) == []
    # --execute with the wrong title changes nothing.
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--forum", "off", "--execute"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="Team Hermes 2",
    )
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    assert mutations(fake) == []
    # And with the right one: one call, the topics gone, one audit line.
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--forum", "off", "--execute"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="Team Hermes",
    )
    assert code == 0, out
    assert mutations(fake) == ["ToggleForumRequest"] and fake.world.chats[FORUM_ID]["topics"] == []
    assert "--forum on -> off" in envelope_of(out)["evidence"]["readback"]
    assert [line["command"] for line in audit_lines(home)] == ["settings set"]


def test_switching_topics_off_has_no_yes_and_needs_a_terminal(run_cli, capsys):
    code, _out, err, fake = run_cli(["settings", "set", "--chat", FORUM, "--forum", "off", "--yes"], client=PeopledClient(), capsys=capsys, isatty=True)
    assert code == 2 and "--yes" in err, err
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", FORUM, "--forum", "off", "--execute"],
        client=PeopledClient(), capsys=capsys, isatty=False,
    )
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"
    assert mutations(fake) == []


def test_switching_topics_on_is_one_y_and_no_dry_run(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "settings", "set", "--chat", "-1001000000002", "--forum", "on"],
        client=PeopledClient(), capsys=capsys, isatty=True, answer="y",
    )
    assert code == 0, out
    assert mutations(fake) == ["ToggleForumRequest"]
    assert "--forum off -> on" in envelope_of(out)["evidence"]["readback"]


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

    rows = asyncio.run(port.admins(types.InputPeerChannel(channel_id=1000000001, access_hash=0)))
    assert [row.status for row in rows][:2] == ["creator", "admin"]
    assert rows[0].label == "Sven (@sven)"


# -- card agent-bo-95422301: the screens say what happened, in words ------------------


def test_invite_revoke_shows_its_link_to_the_person_and_no_record_keeps_it(run_cli, capsys, home):
    # The y/N is about one link: the person's own screen shows which, and says the result in words.
    code, out, _err, _fake = run_cli(["invite", "revoke", "--chat", FORUM, "--link", INVITE], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert f"Link    {INVITE}" in out
    assert "Read back: the invite link to Team Hermes is now revoked, titled club\n" in out
    assert find(json.dumps(audit_lines(home))) == []
    # A machine run keeps the link out of the envelope and out of the preview on stderr.
    code, out, err, _fake = run_cli(["--json", "invite", "revoke", "--chat", FORUM, "--link", INVITE], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert INVITE not in out and INVITE not in err
    assert "Link    https://t.me/+<redacted>" in err
    assert envelope_of(out)["evidence"]["readback"] == "the invite link to Team Hermes is now revoked, titled club"
    assert find(json.dumps(audit_lines(home))) == []


def test_an_invite_list_of_the_revoked_says_revoked(run_cli, capsys):
    assert manage_ops.format_invites([], chat_title="Team Hermes") == "No invite link in Team Hermes."
    assert manage_ops.format_invites([], chat_title="Team Hermes", revoked=True) == "No revoked invite link in Team Hermes."
    code, out, _err, _fake = run_cli(["invite", "list", "--chat", FORUM, "--revoked"], client=PeopledClient(), capsys=capsys)
    assert code == 0 and "1 revoked invite link(s) in Team Hermes" in out
    world = PeopledWorld()
    world.invites = {}
    code, out, _err, _fake = run_cli(["invite", "list", "--chat", FORUM, "--revoked"], client=PeopledClient(world), capsys=capsys)
    assert code == 0 and "No revoked invite link in Team Hermes." in out


def test_admin_list_shows_the_creators_rights_on_the_human_screen(run_cli, capsys):
    code, out, _err, _fake = run_cli(["admin", "list", "--chat", FORUM], client=PeopledClient(), capsys=capsys)
    assert code == 0
    creator = next(line for line in out.splitlines() if "  creator " in line)
    assert "add_admins" in creator and "manage_topics" in creator, creator


# -- card agent-bo-95422334: a preview names every right the write takes -----------------


def takes_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Takes"))


def test_a_mute_preview_names_the_whole_family_telegram_takes(run_cli, capsys, home):
    # Live 6.2.5 previewed "Takes send_messages" and read back fifteen rights.
    # The preview is the screen the y answers, so it names all fifteen.
    code, out, err, fake = run_cli(["--json", "member", "mute", "--chat", FORUM, "--user", "@harry", "--until", "2h"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert takes_line(err) == f"Takes   {', '.join(sorted(MUTED_BACK))}"
    assert "Telegram widens send_messages into the family beneath it" in err
    # The preview and the readback now name the same fifteen.
    assert envelope["result"]["member"]["rights"] == sorted(MUTED_BACK)
    # What reached Telegram is still the one flag that was asked for.
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditBannedRequest"][0]
    assert sent.banned_rights.send_messages and not sent.banned_rights.send_photos


def test_a_restrict_preview_names_the_media_family_and_leaves_a_lone_right_alone(run_cli, capsys, home):
    # Live 6.2.7 previewed "Takes send_media" and read back seven rights.
    code, out, err, _fake = run_cli(["--json", "member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_media", "--until", "10m"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert takes_line(err) == f"Takes   {', '.join(sorted(RESTRICTED_MEDIA_BACK))}"
    assert envelope["result"]["member"]["rights"] == sorted(RESTRICTED_MEDIA_BACK)

    # A right with no family beneath it widens into nothing, and says nothing.
    code, out, err, _fake = run_cli(["--json", "member", "restrict", "--chat", FORUM, "--user", "@harry", "--rights", "send_stickers,embed_links", "--until", "10m"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert takes_line(err) == "Takes   embed_links, send_stickers"
    assert "widens" not in err
    assert envelope_of(out)["result"]["member"]["rights"] == ["embed_links", "send_stickers"]


def test_a_ban_preview_says_it_takes_every_right_and_never_ends(run_cli, capsys, home):
    # Live 6.2.2 previewed As, Who and the reason alone, while the write read
    # back 23 rights and Telegram's forever. The gate now says both.
    code, out, _err, fake = run_cli(["member", "ban", "--chat", FORUM, "--user", "@harry", "--reason", "spam", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="Harry (@harry)")
    assert code == 0, out
    assert "view_messages among them: they cannot even read Team Hermes" in out
    assert "Until   permanent (Telegram stores a ban with no end" in out
    # The date Telegram stores for it is the 32-bit ceiling, not an end anyone
    # set, so the readback says what it means. The JSON payload keeps the date.
    readback = next(line for line in out.splitlines() if "is now banned" in line)
    assert readback.endswith("in Team Hermes permanently (reason: spam)") and manage_ops.FOREVER not in readback


def test_a_ban_reads_back_every_right_and_the_json_keeps_the_date(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(["--json", "member", "ban", "--chat", FORUM, "--user", "@harry", "--execute"], client=PeopledClient(), capsys=capsys, isatty=True, answer="@harry")
    assert code == 0, out
    envelope = envelope_of(out)
    assert envelope["result"]["member"]["rights"] == sorted(manage_ops.BANNED_RIGHT_NAMES)
    # The machine field stays a date; only the screens read it as permanent.
    assert envelope["result"]["member"]["until"] == manage_ops.FOREVER
    assert envelope["evidence"]["readback"].endswith("in Team Hermes permanently")


def test_a_promote_preview_names_the_right_telegram_adds_for_itself(run_cli, capsys, home):
    # Live 6.1.2 previewed "Rights delete_messages, pin_messages" and read back
    # `other` as well, a right nobody asked for and no screen had named.
    code, out, err, fake = run_cli(["--json", "admin", "promote", "--chat", FORUM, "--user", "@harry", "--rights", "pin_messages,delete_messages"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    envelope = envelope_of(out)
    assert "Rights  delete_messages, other, pin_messages" in err
    assert "Telegram sets other on every admin" in err
    assert envelope["result"]["member"]["rights"] == ["delete_messages", "other", "pin_messages"]
    # `other` is never sent, so an actor that does not hold it can still promote.
    sent = [r for r in fake.world.requests if type(r).__name__ == "EditAdminRequest"][0]
    assert sent.admin_rights.pin_messages and not sent.admin_rights.other


def test_taking_the_last_admin_right_away_adds_nothing(run_cli, capsys, home):
    # `--rights none` is not a promotion, so Telegram's `other` has nothing to
    # come with; the preview must not claim a right the write will not set.
    code, out, err, _fake = run_cli(["--json", "admin", "rights", "--chat", FORUM, "--user", "@dobby", "--rights", "none"], client=PeopledClient(), capsys=capsys, isatty=True, answer="y")
    assert code == 0, out
    assert "Rights  none" in err and "other" not in err
    assert envelope_of(out)["result"]["member"]["status"] == "member"


def test_the_families_are_the_ones_telegram_showed_live():
    # The table the preview reads, pinned to the live readbacks it came from.
    assert manage_ops.expand_rights(manage_ops.MUTE_RIGHTS) == tuple(sorted(MUTED_BACK))
    assert manage_ops.expand_rights(("send_media",)) == tuple(sorted(RESTRICTED_MEDIA_BACK))
    assert manage_ops.expand_rights(manage_ops.BAN_RIGHTS) == tuple(sorted(manage_ops.BANNED_RIGHT_NAMES))
    assert manage_ops.expand_rights(("send_stickers",)) == ("send_stickers",)
    assert manage_ops.expand_rights(()) == ()
    assert manage_ops.expand_admin_rights(("pin_messages",)) == ("other", "pin_messages")
    assert manage_ops.expand_admin_rights(()) == ()
    assert manage_ops.until_phrase(manage_ops.FOREVER) == "permanently"
    assert manage_ops.until_phrase("2026-09-17T18:05:09Z") == "until 2026-09-17T18:05:09Z"
    assert manage_ops.until_phrase(None) == ""


def test_the_typed_gate_asks_for_the_label_every_other_screen_printed():
    # The gate's hint was the only place the short form appeared; both forms
    # were always accepted, so only the asking changes.
    member = member_of(plain(777), HARRY)
    asked = []
    assert manage_ops.confirm_typed_label("preview", member, read=lambda prompt: asked.append(prompt) or "Harry (@harry)", write=lambda _text: None)
    assert asked == ["Type the exact label (Harry (@harry)) to continue: "]
    assert manage_ops.labels_match("@harry", member) and manage_ops.labels_match("harry", member)
