import asyncio
from types import SimpleNamespace

from telegram_tools import discovery
from telegram_tools.discovery import classify_entity, dialog_to_chat_info, filter_chats, format_discovery_table
from telegram_tools.models import TopicInfo


def test_classify_entity_distinguishes_channels_and_groups():
    assert classify_entity(SimpleNamespace(broadcast=True, megagroup=False)) == "channel"
    assert classify_entity(SimpleNamespace(broadcast=False, megagroup=True, forum=True)) == "forum_group"
    assert classify_entity(SimpleNamespace(broadcast=False, megagroup=True, forum=False)) == "supergroup"
    assert classify_entity(SimpleNamespace()) == "group"


def test_dialog_to_chat_info_includes_admin_and_topics():
    entity = SimpleNamespace(username="builds", broadcast=False, megagroup=True, forum=True)
    dialog = SimpleNamespace(id=-100123, title="Build Group", entity=entity)

    info = dialog_to_chat_info(
        dialog,
        is_admin=True,
        topics=[TopicInfo(id=10, title="Deploys", top_message=10)],
    )

    assert info.id == -100123
    assert info.title == "Build Group"
    assert info.username == "builds"
    assert info.type == "forum_group"
    assert info.is_admin is True
    assert info.topics[0].id == 10


def test_filter_chats_returns_only_admin_chats_when_requested():
    admin_chat = dialog_to_chat_info(
        SimpleNamespace(id=-1001, title="Admins", entity=SimpleNamespace(megagroup=True)),
        is_admin=True,
    )
    regular_chat = dialog_to_chat_info(
        SimpleNamespace(id=-1002, title="Regular", entity=SimpleNamespace(megagroup=True)),
        is_admin=False,
    )

    assert filter_chats([admin_chat, regular_chat], admin_only=True) == [admin_chat]
    assert filter_chats([admin_chat, regular_chat], admin_only=False) == [admin_chat, regular_chat]


def test_format_discovery_table_shows_human_readable_chats_and_topics():
    info = dialog_to_chat_info(
        SimpleNamespace(id=-1001234567890, title="Example Forum", entity=SimpleNamespace(megagroup=True, forum=True)),
        is_admin=True,
        topics=[
            TopicInfo(id=141, title="Deploys", top_message=141),
            TopicInfo(id=217, title="Support", top_message=217),
        ],
    )

    text = format_discovery_table([info])

    assert "Chat" in text
    assert "Example Forum" in text
    assert "Chat ID: -1001234567890" in text
    assert "Type: Forum Group" in text
    assert "Topics" in text
    assert "141  Deploys" in text
    assert "217  Support" in text


def test_discovery_shows_the_topic_emoji_telegram_draws_and_leaves_a_bare_topic_bare():
    info = dialog_to_chat_info(
        SimpleNamespace(id=-1004297050934, title="Hermes", entity=SimpleNamespace(megagroup=True, forum=True)),
        is_admin=True,
        topics=[
            TopicInfo(id=141, title="Dobby", top_message=141, icon_emoji="\U0001f4bb"),
            TopicInfo(id=217, title="Support", top_message=217),
        ],
    )

    text = format_discovery_table([info])

    assert "141  \U0001f4bb Dobby" in text
    assert "217  Support" in text


def test_format_discovery_table_groups_managed_chats():
    forum = dialog_to_chat_info(
        SimpleNamespace(id=-1001, title="Forum", entity=SimpleNamespace(megagroup=True, forum=True)),
        is_admin=True,
        topics=[TopicInfo(id=10, title="General", top_message=10)],
    )
    channel = dialog_to_chat_info(
        SimpleNamespace(id=-1002, title="Channel", entity=SimpleNamespace(broadcast=True)),
        is_admin=True,
    )
    group = dialog_to_chat_info(
        SimpleNamespace(id=-1003, title="Group", entity=SimpleNamespace(megagroup=True)),
        is_admin=True,
    )

    text = format_discovery_table([forum, channel, group])

    assert "Forum Groups" in text
    assert "Channels" in text
    assert "Other Admin Groups" in text
    assert "Forum" in text
    assert "Channel" in text
    assert "Group" in text


class FakeDialogClient:
    """Only what `list_dialog_choices` may touch. Anything else is a test failure."""

    def __init__(self, dialogs):
        self._dialogs = dialogs
        self.permission_calls = 0
        self.topic_calls = 0

    async def iter_dialogs(self):
        for dialog in self._dialogs:
            yield dialog

    async def get_permissions(self, *_args, **_kwargs):
        self.permission_calls += 1
        raise AssertionError("the picker must not ask for permissions")

    async def __call__(self, *_args, **_kwargs):
        self.topic_calls += 1
        raise AssertionError("the picker must not fetch topics")


def test_list_dialog_choices_maps_dialogs_without_permission_or_topic_calls():
    forum = SimpleNamespace(megagroup=True, forum=True, username="hermes")
    channel = SimpleNamespace(broadcast=True, megagroup=False, username=None)
    client = FakeDialogClient(
        [
            SimpleNamespace(id=-100111, title="Hermes", entity=forum),
            SimpleNamespace(id=-100222, title="Alerts", entity=channel),
        ]
    )

    choices = asyncio.run(discovery.list_dialog_choices(client))

    assert [choice.id for choice in choices] == [-100111, -100222]
    assert [choice.type for choice in choices] == ["forum_group", "channel"]
    assert [choice.username for choice in choices] == ["hermes", None]
    assert [choice.is_forum for choice in choices] == [True, False]
    assert client.permission_calls == 0
    assert client.topic_calls == 0


def test_list_dialog_choices_falls_back_to_the_dialog_name():
    entity = SimpleNamespace(megagroup=True, forum=False, username=None)
    client = FakeDialogClient([SimpleNamespace(id=7, title=None, name="Saved", entity=entity)])

    choices = asyncio.run(discovery.list_dialog_choices(client))

    assert choices[0].title == "Saved"


def test_list_dialog_choices_returns_empty_list_for_no_dialogs():
    client = FakeDialogClient([])

    choices = asyncio.run(discovery.list_dialog_choices(client))

    assert choices == []


def test_list_dialog_choices_empty_string_when_neither_title_nor_name():
    entity = SimpleNamespace(megagroup=True, forum=False, username=None)
    client = FakeDialogClient([SimpleNamespace(id=42, title=None, name=None, entity=entity)])

    choices = asyncio.run(discovery.list_dialog_choices(client))

    assert choices[0].title == ""
