"""The adapters answer the shared seam, and nothing secret leaves through them."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from telegram_tools._core import redaction
from telegram_tools._core.adapters import IdentityProvider, PermissionProbe, TargetResolver
from telegram_tools.adapters import AccountIdentity, ChatPermissions, ChatTargets
from telegram_tools.adapters.account import account_label
from telegram_tools.resolver import ResolvedChat


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


@pytest.mark.parametrize(
    "permissions, held, answered",
    [
        (SimpleNamespace(send_messages=True, delete_messages=False), {"send_messages"}, {"send_messages", "delete_messages"}),
        # A private chat has no participant permissions at all: nothing is
        # answered for, which is not the same as nothing being held.
        (SimpleNamespace(), set(), set()),
    ],
)
def test_the_probe_separates_a_right_that_is_absent_from_one_that_is_unknown(permissions, held, answered):
    client = SimpleNamespace(get_permissions=_returns(permissions))

    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert set(rights.held) == held
    assert set(rights.answered) == answered
    assert rights.missing(("delete_messages",)) == (("delete_messages",) if "delete_messages" in answered else ())
    assert rights.unknown(("delete_messages",)) == (() if "delete_messages" in answered else ("delete_messages",))


def test_a_creator_holds_every_right_the_chat_answered_for():
    client = SimpleNamespace(get_permissions=_returns(SimpleNamespace(is_creator=True, delete_messages=False)))

    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert rights.missing(("delete_messages",)) == ()


def test_a_client_that_will_not_answer_says_so_rather_than_claiming_nothing_is_held():
    def raising(*_args, **_kwargs):
        raise RuntimeError("boom")

    client = SimpleNamespace(get_permissions=raising)

    rights = asyncio.run(ChatPermissions(client, user=None).probe(object()))

    assert rights.unreadable == "RuntimeError reading permissions"
    assert rights.unknown(("send_messages",)) == ("send_messages",)


def _returns(value):
    async def get_permissions(*_args, **_kwargs):
        return value

    return get_permissions
