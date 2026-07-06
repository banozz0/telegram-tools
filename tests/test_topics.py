from datetime import UTC, datetime
from types import SimpleNamespace

from telegram_tools.topics import topic_from_telethon


def test_topic_from_telethon_preserves_topic_id_and_top_message():
    raw_topic = SimpleNamespace(
        id=42,
        title="Deploys",
        top_message=42,
        date=datetime(2026, 7, 6, tzinfo=UTC),
    )

    topic = topic_from_telethon(raw_topic)

    assert topic.id == 42
    assert topic.title == "Deploys"
    assert topic.top_message == 42
