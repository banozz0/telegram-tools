from types import SimpleNamespace

from telegram_tools.discovery import classify_entity, dialog_to_chat_info
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
