"""Deleting the chat or topic itself, not the messages inside it.

The gate under test is the typed title: every path here proves that a wrong
title, a wrong kind, or a missing --execute leaves Telegram untouched.
"""

import asyncio

import pytest
from telethon.tl.functions.channels import DeleteChannelRequest
from telethon.tl.functions.messages import DeleteTopicHistoryRequest

from telegram_tools.delete import (
    DELETE_KIND_TYPES,
    GENERAL_TOPIC_ID,
    delete_chat,
    delete_topic,
    format_delete_preview,
    kind_for_type,
)
from telegram_tools.models import TopicInfo

DEPLOYS = TopicInfo(id=141, title="Deploys")
GENERAL = TopicInfo(id=GENERAL_TOPIC_ID, title="General")


class FakeClient:
    """Records the requests it is called with, the way the create tests do."""

    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return None


def never_asked(*_args):
    raise AssertionError("a dry run must not ask for a confirmation")


# -- dry run --------------------------------------------------------------


def test_dry_run_deletes_nothing_and_never_prompts():
    client = FakeClient()
    result = asyncio.run(
        delete_chat(client, "peer", kind="group", title="Hermes", chat_id=-100111, confirm=never_asked)
    )
    assert result.dry_run is True
    assert result.deleted is False
    assert client.requests == []


def test_dry_run_shows_the_target_so_the_id_can_be_checked():
    client = FakeClient()
    lines = []
    asyncio.run(
        delete_chat(
            client, "peer", kind="group", title="Hermes", chat_id=-100111, confirm=never_asked, progress=lines.append
        )
    )
    printed = "\n".join(lines)
    assert "Hermes" in printed
    assert "-100111" in printed
    assert "--execute" in printed


# -- the typed-title gate -------------------------------------------------


def test_wrong_title_cancels_and_deletes_nothing():
    client = FakeClient()
    result = asyncio.run(
        delete_chat(
            client,
            "peer",
            kind="group",
            title="Hermes",
            chat_id=-100111,
            execute=True,
            confirm=lambda _preview, _title: "Herme",
        )
    )
    assert result.cancelled is True
    assert client.requests == []


def test_typing_DELETE_is_not_enough():
    """The clear-messages gate word must not open this one: it proves intent, not target."""
    client = FakeClient()
    result = asyncio.run(
        delete_chat(
            client,
            "peer",
            kind="group",
            title="Hermes",
            chat_id=-100111,
            execute=True,
            confirm=lambda _preview, _title: "DELETE",
        )
    )
    assert result.cancelled is True
    assert client.requests == []


def test_right_title_deletes_the_chat():
    client = FakeClient()
    result = asyncio.run(
        delete_chat(
            client,
            "peer",
            kind="group",
            title="Hermes",
            chat_id=-100111,
            execute=True,
            confirm=lambda _preview, title: title,
        )
    )
    assert result.deleted is True
    assert isinstance(client.requests[0], DeleteChannelRequest)


def test_title_match_ignores_case_and_padding():
    """A dead caps lock must not make deletion impossible; knowing which one must."""
    client = FakeClient()
    result = asyncio.run(
        delete_chat(
            client,
            "peer",
            kind="channel",
            title="Alerts",
            chat_id=-100222,
            execute=True,
            confirm=lambda _preview, _title: "  aLeRtS  ",
        )
    )
    assert result.deleted is True
    assert len(client.requests) == 1


# -- topics ---------------------------------------------------------------


def test_topic_dry_run_touches_nothing():
    client = FakeClient()
    result = asyncio.run(
        delete_topic(client, "peer", DEPLOYS, chat_id=-100111, chat_title="Hermes", confirm=never_asked)
    )
    assert result.dry_run is True
    assert result.topic_id == 141
    assert client.requests == []


def test_topic_delete_goes_through_delete_topic_history():
    """Telegram has no delete-topic method: deleting every message, the one that
    opened the topic included, is how a client removes one."""
    client = FakeClient()
    result = asyncio.run(
        delete_topic(
            client,
            "peer",
            DEPLOYS,
            chat_id=-100111,
            chat_title="Hermes",
            execute=True,
            confirm=lambda _preview, title: title,
        )
    )
    assert result.deleted is True
    request = client.requests[0]
    assert isinstance(request, DeleteTopicHistoryRequest)
    assert request.top_msg_id == 141


def test_wrong_topic_title_deletes_nothing():
    client = FakeClient()
    result = asyncio.run(
        delete_topic(
            client,
            "peer",
            DEPLOYS,
            chat_id=-100111,
            chat_title="Hermes",
            execute=True,
            confirm=lambda _preview, _title: "Deploy",
        )
    )
    assert result.cancelled is True
    assert client.requests == []


def test_the_general_topic_is_refused_with_a_reason():
    client = FakeClient()
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            delete_topic(
                client,
                "peer",
                GENERAL,
                chat_id=-100111,
                chat_title="Hermes",
                execute=True,
                confirm=never_asked,
            )
        )
    assert "General" in str(excinfo.value)
    assert "clear-messages" in str(excinfo.value)
    assert client.requests == []


# -- the kind vocabulary --------------------------------------------------


def test_kind_for_type_covers_what_create_makes():
    assert kind_for_type("supergroup") == "group"
    assert kind_for_type("forum_group") == "group"
    assert kind_for_type("channel") == "channel"


def test_a_basic_group_is_not_deletable_because_create_cannot_make_one():
    """Sven's rule: what the tool can delete, the tool can make again."""
    assert kind_for_type("group") is None
    assert "group" not in DELETE_KIND_TYPES["group"]


def test_previews_tell_the_truth_about_reach():
    group = format_delete_preview("group", "Hermes", -100111)
    assert "EVERY member" in group
    assert "Only the creator" in group

    topic = format_delete_preview("topic", "Deploys", -100111, where="Hermes")
    assert "rest of the group is untouched" in topic
    assert "In      Hermes" in topic


# -- what the dry-run and the confirm each say ----------------------------


def test_the_dry_run_does_not_print_the_confirm_banner():
    """Seen twice in one menu flow, a warning banner becomes wallpaper."""
    client = FakeClient()
    lines = []
    asyncio.run(
        delete_chat(
            client,
            "peer",
            kind="group",
            title="Hermes",
            chat_id=-100111,
            confirm=never_asked,
            progress=lines.append,
        )
    )
    printed = "\n".join(lines)
    assert "WARNING: DELETE" not in printed
    # The consequences still show; only the box is gone.
    assert "EVERY member" in printed
    assert "Nothing has been deleted" in printed


def test_the_confirm_still_carries_the_full_banner():
    client = FakeClient()
    seen = {}

    def confirm(preview, title):
        seen["preview"] = preview
        return title

    asyncio.run(
        delete_chat(
            client, "peer", kind="group", title="Hermes", chat_id=-100111, execute=True, confirm=confirm
        )
    )
    assert "WARNING: DELETE GROUP" in seen["preview"]


def test_a_topic_dry_run_names_the_group_it_is_in():
    client = FakeClient()
    lines = []
    asyncio.run(
        delete_topic(
            client, "peer", DEPLOYS, chat_id=-100111, chat_title="Hermes", confirm=never_asked, progress=lines.append
        )
    )
    printed = "\n".join(lines)
    assert "in Hermes" in printed
    assert "WARNING: DELETE" not in printed
