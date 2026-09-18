"""The adapters answer the shared seam, and nothing secret leaves through them."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import types
from telethon.tl.custom.participantpermissions import ParticipantPermissions

from telegram_tools._core import redaction
from telegram_tools._core.adapters import IdentityProvider, PermissionProbe, TargetResolver
from telegram_tools.adapters import AccountIdentity, ChatPermissions, ChatTargets
from telegram_tools.adapters.account import RIGHT_NAMES, account_label
from telegram_tools.adapters.bot import BotPermissions
from telegram_tools.resolver import ResolvedChat


# -- what `client.get_permissions` really returns ------------------------------
#
# Telethon's own `ParticipantPermissions` over a real participant object, never a
# namespace carrying the probe's vocabulary: the real object spells no
# send_messages, send_media or manage_topics at all, and a fake that did is how
# every live send came to warn while the suite stayed green. Every fake client
# in this suite builds its answer here.

# The admin flags a ChatAdminRights constructor takes, read off Telethon itself.
ADMIN_FIELDS = frozenset(inspect.signature(types.ChatAdminRights.__init__).parameters) - {"self"}
# The three rights a member holds unless Telegram bans them.
MEMBER_BANNABLE = ("send_messages", "send_media", "manage_topics")


def creator(*, chat: bool = False, user_id: int = 42) -> ParticipantPermissions:
    """The chat's creator. In a supergroup or channel Telegram hands the creator an admin_rights too."""
    if chat:
        return ParticipantPermissions(types.ChatParticipantCreator(user_id=user_id), True)
    rights = types.ChatAdminRights(**{name: True for name in ADMIN_FIELDS})
    return ParticipantPermissions(types.ChannelParticipantCreator(user_id=user_id, admin_rights=rights), False)


def admin(*rights: str, chat: bool = False, user_id: int = 42) -> ParticipantPermissions:
    """An admin holding exactly `rights`; a basic group's admin carries no rights object at all."""
    if chat:
        return ParticipantPermissions(types.ChatParticipantAdmin(user_id=user_id, inviter_id=1, date=None), True)
    unknown = set(rights) - ADMIN_FIELDS
    assert not unknown, f"not admin rights: {sorted(unknown)}"
    participant = types.ChannelParticipantAdmin(
        user_id=user_id, promoted_by=1, date=None, admin_rights=types.ChatAdminRights(**{name: True for name in rights})
    )
    return ParticipantPermissions(participant, False)


def member(*banned: str, left: bool = False, chat: bool = False, user_id: int = 42) -> ParticipantPermissions:
    """A member with no admin rights, restricted by `banned` on its own participant when any is named."""
    if chat:
        # A basic group has no per-person restriction: only the chat's defaults.
        assert not banned and not left, "a basic group restricts through its default rights only"
        return ParticipantPermissions(types.ChatParticipant(user_id=user_id, inviter_id=1, date=None), True)
    if not banned and not left:
        return ParticipantPermissions(types.ChannelParticipantSelf(user_id=user_id, inviter_id=1, date=None), False)
    participant = types.ChannelParticipantBanned(
        peer=types.PeerUser(user_id),
        kicked_by=1,
        date=None,
        banned_rights=types.ChatBannedRights(until_date=None, **{name: True for name in banned}),
        left=left,
    )
    return ParticipantPermissions(participant, False)


def restricted(*banned_names: str, until, user_id: int = 42) -> ParticipantPermissions:
    """A member restricted until `until`: a datetime, or None for forever."""
    participant = types.ChannelParticipantBanned(
        peer=types.PeerUser(user_id),
        kicked_by=1,
        date=None,
        banned_rights=types.ChatBannedRights(until_date=until, **{name: True for name in banned_names}),
        left=False,
    )
    return ParticipantPermissions(participant, False)


def holding(names, *, chat: bool = False) -> ParticipantPermissions:
    """The participant a set of held names describes, for the fakes that speak in names.

    `is_creator` is a creator; `is_admin` an admin holding the admin rights
    named; anything else a member, banned from each member right it does not
    name.
    """
    names = set(names)
    if "is_creator" in names:
        return creator(chat=chat)
    if "is_admin" in names:
        return admin(*sorted(names & ADMIN_FIELDS), chat=chat)
    return member(*(name for name in MEMBER_BANNABLE if name not in names), chat=chat)


def supergroup(**fields) -> types.Channel:
    return types.Channel(id=1234567890, title="Agency", photo=None, date=None, megagroup=True, **fields)


def broadcast(**fields) -> types.Channel:
    return types.Channel(id=1234567891, title="Alerts", photo=None, date=None, broadcast=True, **fields)


def basic_group(**fields) -> types.Chat:
    return types.Chat(id=400000000005, title="Old Basic", photo=None, participants_count=3, date=None, version=1, **fields)


def test_the_fake_permissions_are_telethons_own_object():
    # The attribute set the card was verified against: Telethon 1.45.0 spells
    # no send right and no manage_topics on the object get_permissions returns.
    for permissions in (creator(), admin("pin_messages"), member(), member("send_messages"), creator(chat=True), member(chat=True)):
        assert isinstance(permissions, ParticipantPermissions)
        for name in MEMBER_BANNABLE:
            assert not hasattr(permissions, name), name


def test_each_adapter_answers_the_protocol_it_implements():
    account = AccountIdentity(SimpleNamespace(id=42, first_name="Sven"))
    assert isinstance(account, IdentityProvider)
    assert isinstance(ChatTargets(client=None), TargetResolver)
    assert isinstance(ChatPermissions(client=None, user=None), PermissionProbe)


def test_an_identity_names_the_account_without_carrying_a_credential():
    account = AccountIdentity(SimpleNamespace(id=42, first_name="Sven", username="sven"))

    identity = account.identity()

    assert identity.id == "tg:user:42"
    assert identity.label == "Sven (@sven)"
    assert identity.mode == "account"
    assert identity.profile == "default"
    assert not redaction.find(str(identity.to_dict()))


def test_a_display_name_that_reads_as_a_phone_number_is_redacted_not_refused():
    # Someone else chose that name; it should cost this run nothing.
    label = account_label(SimpleNamespace(id=7, first_name="+356 9912 3456"))

    assert not redaction.find(label)


def test_a_topic_target_names_its_chat_and_its_topic():
    resolved = ResolvedChat(
        id=-1001234567890,
        entity=SimpleNamespace(title="Agency", megagroup=True, forum=True),
        input_entity=object(),
    )
    chat = ChatTargets.chat_target(resolved, "@agency")
    topic = ChatTargets.topic_target(chat, SimpleNamespace(id=141, title="Deploys"))

    assert chat.rid == "tg:chat:-1001234567890"
    assert chat.type == "forum_group"
    assert topic.rid == "tg:topic:-1001234567890:141"
    assert topic.path == ("Agency", "Deploys")


def test_the_probe_separates_a_right_that_is_absent_from_one_that_is_unknown():
    client = SimpleNamespace(get_permissions=_returns(member()))

    # No chat to read the default rights off: what Telethon answers is
    # answered, and the member rights the chat decides are unknown.
    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert "delete_messages" in rights.answered and "delete_messages" not in rights.held
    assert rights.missing(("delete_messages",)) == ("delete_messages",)
    assert rights.unknown(("send_messages",)) == ("send_messages",)
    assert rights.missing(("send_messages",)) == ()


def test_a_creator_holds_every_right_the_chat_answered_for():
    client = SimpleNamespace(get_permissions=_returns(creator()))

    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert rights.missing(("delete_messages",)) == ()


def test_a_client_that_will_not_answer_says_so_rather_than_claiming_nothing_is_held():
    def raising(*_args, **_kwargs):
        raise RuntimeError("boom")

    client = SimpleNamespace(get_permissions=raising)

    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert rights.unreadable == "RuntimeError reading permissions"
    assert rights.unknown(("send_messages",)) == ("send_messages",)


# -- where each right comes from ------------------------------------------------
#
# Telethon answers is_creator, is_admin and the admin flags; send_messages,
# send_media and manage_topics are read off the participant it wraps and the chat.

EVERY = set(RIGHT_NAMES)
DERIVED = ("send_messages", "send_media", "manage_topics")


def probe(permissions, chat, peer=None):
    client = SimpleNamespace(get_permissions=_returns(permissions))
    return asyncio.run(ChatPermissions(client, user=None).probe(peer or object(), chat))


def banned(**names):
    return types.ChatBannedRights(until_date=None, **names)


@pytest.mark.parametrize("permissions, chat", [(creator(), supergroup()), (creator(), broadcast()), (creator(chat=True), basic_group())])
def test_a_creator_holds_every_right_the_preflight_can_ask_about(permissions, chat):
    rights = probe(permissions, chat)

    assert set(rights.held) == EVERY and set(rights.answered) == EVERY
    assert rights.unknown(tuple(EVERY)) == () and rights.missing(tuple(EVERY)) == ()


def test_a_creator_needs_no_chat_to_be_read():
    rights = probe(creator(), None)

    assert set(rights.held) == EVERY and rights.unknown(DERIVED) == ()


def test_a_supergroup_admin_sends_whatever_the_members_may_not():
    # Restrictions bind members, not admins: the defaults ban sending here.
    chat = supergroup(default_banned_rights=banned(send_messages=True, send_media=True, manage_topics=True))

    rights = probe(admin("pin_messages"), chat)

    assert {"is_admin", "pin_messages", "send_messages", "send_media"} <= rights.held
    assert rights.missing(("send_messages", "send_media")) == ()
    # manage_topics is an admin right, and this admin was not given it.
    assert rights.missing(("manage_topics",)) == ("manage_topics",)
    assert rights.missing(("delete_messages", "ban_users")) == ("delete_messages", "ban_users")
    assert rights.unknown(tuple(EVERY)) == ()


@pytest.mark.parametrize("defaults", [None, banned(manage_topics=True)])
def test_an_admins_manage_topics_comes_from_its_admin_rights(defaults):
    # Whatever the members may do: the admin's own rights answer for it.
    chat = supergroup(forum=True, default_banned_rights=defaults)

    assert "manage_topics" in probe(admin("manage_topics"), chat).held
    assert probe(admin("change_info"), chat).missing(("manage_topics",)) == ("manage_topics",)
    assert "manage_topics" in probe(admin("manage_topics"), None).held


def test_a_broadcast_admin_posts_only_with_post_messages():
    assert {"send_messages", "send_media"} <= probe(admin("post_messages"), broadcast()).held

    rights = probe(admin("edit_messages", "delete_messages"), broadcast())
    assert rights.missing(("send_messages", "send_media")) == ("send_messages", "send_media")


def test_a_member_holds_the_member_rights_nothing_bans():
    # Telegram's own default: stickers and gifs off, everything else on.
    chat = supergroup(forum=True, default_banned_rights=banned(send_stickers=True, send_gifs=True))

    rights = probe(member(), chat)

    assert {"send_messages", "send_media"} <= rights.held
    assert "is_admin" not in rights.held and rights.missing(("pin_messages",)) == ()
    # Unbanned, manage_topics lets a member open a topic but not edit another's,
    # so Telegram's answer does not settle it either way.
    assert rights.unknown(tuple(EVERY)) == ("manage_topics",)
    assert rights.missing(("manage_topics",)) == ()


@pytest.mark.parametrize(
    "permissions, defaults, missing",
    [
        # The chat's defaults ban it for every member.
        (member(), banned(send_messages=True), ("send_messages",)),
        (member(), banned(send_media=True), ("send_media",)),
        (member(), banned(manage_topics=True), ("manage_topics",)),
        # The member's own restriction bans it for this member alone.
        (member("send_messages"), banned(), ("send_messages",)),
        (member("send_media", "manage_topics"), banned(), ("send_media", "manage_topics")),
        # Nothing on either: nothing missing.
        (member(), banned(), ()),
        (member(), None, ()),
    ],
)
def test_a_member_right_is_held_unless_telegram_bans_it(permissions, defaults, missing):
    rights = probe(permissions, supergroup(default_banned_rights=defaults))

    assert rights.missing(DERIVED) == missing
    assert rights.unknown(("send_messages", "send_media")) == ()
    assert rights.unknown(("manage_topics",)) == (() if "manage_topics" in missing else ("manage_topics",))


# -- the three Telethon answers for admins only ---------------------------------
#
# `ParticipantPermissions.pin_messages` and its twins are built by `_admin_prop`,
# which returns False for anyone who is not an admin (Telethon 1.45.0, read at
# `tl/custom/participantpermissions.py`). Telegram's own rule is the opposite:
# `ChatBannedRights` carries all three, so a chat that bans none of them lets
# every member pin, invite and rename. Taking Telethon's False as an answer
# refused those writes by name before the call (card agent-bo-95422318).

DEFAULTED = ("pin_messages", "change_info", "invite_users")


def test_a_member_holds_the_rights_the_chats_defaults_leave_alone():
    rights = probe(member(), supergroup(default_banned_rights=banned(send_stickers=True)))

    assert set(DEFAULTED) <= rights.held
    assert rights.missing(DEFAULTED) == () and rights.unknown(DEFAULTED) == ()


@pytest.mark.parametrize(
    "permissions, defaults, missing",
    [
        (member(), banned(pin_messages=True), ("pin_messages",)),
        (member(), banned(change_info=True, invite_users=True), ("change_info", "invite_users")),
        (member("pin_messages"), banned(), ("pin_messages",)),
        (member(), banned(), ()),
    ],
)
def test_a_defaulted_right_is_held_unless_this_chat_or_this_member_bans_it(permissions, defaults, missing):
    assert probe(permissions, supergroup(default_banned_rights=defaults)).missing(DEFAULTED) == missing


def test_an_admin_keeps_the_answer_its_own_rights_give():
    """Restrictions bind members only, and an admin holds what it was promoted with."""
    banning = supergroup(default_banned_rights=banned(pin_messages=True, change_info=True, invite_users=True))

    assert "pin_messages" in probe(admin("pin_messages"), banning).held
    # Allowed to every member, and still not this admin's: an admin is not one.
    assert probe(admin("ban_users"), supergroup()).missing(("pin_messages",)) == ("pin_messages",)


def test_the_defaulted_rights_are_unknown_when_the_chat_cannot_say():
    for chat in (None, supergroup(min=True)):
        rights = probe(member(), chat)
        assert rights.unknown(DEFAULTED) == DEFAULTED and rights.missing(DEFAULTED) == ()


def test_a_subscriber_holds_none_of_them_in_a_broadcast_channel():
    assert probe(member(), broadcast()).missing(DEFAULTED) == DEFAULTED


def test_banning_plain_text_alone_still_bans_a_text_message():
    """`send_plain` bans text and leaves media alone; `send_messages` bans both."""
    chat = supergroup(default_banned_rights=banned(send_plain=True))

    rights = probe(member(), chat)

    assert rights.missing(("send_messages",)) == ("send_messages",)
    assert "send_media" in rights.held


def test_a_restriction_that_has_run_out_no_longer_binds():
    """Telegram lifts a timed restriction when it expires; the object can outlive it."""
    over = datetime(2020, 1, 1, tzinfo=timezone.utc)

    rights = probe(restricted("send_messages", "pin_messages", until=over), supergroup())

    assert rights.missing(("send_messages", "pin_messages")) == ()


def test_a_restriction_still_running_binds():
    later = datetime.now(timezone.utc) + timedelta(days=1)

    rights = probe(restricted("send_messages", until=later), supergroup())

    assert rights.missing(("send_messages",)) == ("send_messages",)


def test_a_permanent_restriction_is_not_read_as_expired():
    """Telegram writes `until_date` 0 for "forever", and Telethon reads 0 as the epoch."""
    for forever in (None, datetime(1970, 1, 1, tzinfo=timezone.utc)):
        rights = probe(restricted("send_messages", until=forever), supergroup())
        assert rights.missing(("send_messages",)) == ("send_messages",), forever


@pytest.mark.parametrize("permissions", [member(left=True), member("view_messages"), member("send_polls", left=True)])
def test_a_member_who_is_out_of_the_chat_holds_no_member_right(permissions):
    assert probe(permissions, supergroup()).missing(DERIVED) == DERIVED


def test_a_broadcast_subscriber_cannot_post():
    rights = probe(member(), broadcast())

    assert rights.missing(DERIVED) == DERIVED


def test_a_basic_group_reads_the_chats_defaults():
    chat = basic_group(default_banned_rights=banned(send_messages=True))

    assert probe(member(chat=True), chat).missing(("send_messages", "send_media")) == ("send_messages",)
    assert probe(member(chat=True), basic_group()).missing(DERIVED) == ()
    assert probe(member(chat=True), basic_group()).unknown(("send_messages", "send_media")) == ()
    # A basic group's admin holds every admin right but add_admins (Telethon's rule), and every member right.
    rights = probe(admin(chat=True), chat)
    assert rights.missing(DERIVED) == ()
    assert rights.missing(("delete_messages", "add_admins")) == ("add_admins",)


def test_the_member_rights_are_unknown_when_the_chat_cannot_say():
    # No chat at all, or a min channel, which carries no rights of its own.
    for chat in (None, supergroup(min=True)):
        rights = probe(member(), chat)
        assert rights.unknown(DERIVED) == DERIVED and rights.missing(DERIVED) == ()


@pytest.mark.parametrize(
    "peer, chat",
    [
        (types.InputPeerUser(user_id=777, access_hash=1), None),
        (types.InputPeerSelf(), None),
        (object(), types.User(id=777, first_name="Harry")),
    ],
)
def test_a_direct_chat_needs_no_rights_check(peer, chat):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a direct chat has no participant to ask about")

    client = SimpleNamespace(get_permissions=refuse)

    rights = asyncio.run(ChatPermissions(client, user=None).probe(peer, chat))

    assert rights.unreadable is None
    assert rights.unknown(tuple(EVERY)) == ()
    assert {"send_messages", "send_media", "delete_messages", "pin_messages"} <= rights.held
    # Nobody administers a private chat, and nobody edits the other person's messages.
    assert rights.missing(("is_creator", "edit_messages")) == ("is_creator", "edit_messages")


def test_the_bot_probe_reads_the_same_way():
    client = SimpleNamespace(get_permissions=_returns(member("send_messages")))

    rights = asyncio.run(BotPermissions(client, SimpleNamespace(id=1, username="alertsbot")).probe(object(), supergroup()))

    assert rights.missing(DERIVED) == ("send_messages",)
    assert set(asyncio.run(BotPermissions(SimpleNamespace(get_permissions=_returns(creator())), None).probe(object(), supergroup())).held) == EVERY


def test_a_bot_needs_no_rights_check_in_a_direct_chat():
    rights = asyncio.run(BotPermissions(SimpleNamespace(), None).probe(types.InputPeerUser(user_id=777, access_hash=1)))

    assert rights.unknown(("send_messages",)) == () and rights.missing(("send_messages",)) == ()


def _returns(value):
    async def get_permissions(*_args, **_kwargs):
        return value

    return get_permissions
