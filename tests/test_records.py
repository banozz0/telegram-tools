from datetime import UTC, datetime
from types import SimpleNamespace

from telegram_tools.records import message_to_record, parse_date_bound, topic_id_for_message


def test_topic_id_prefers_reply_to_top_id():
    message = SimpleNamespace(
        reply_to=SimpleNamespace(
            forum_topic=True,
            reply_to_msg_id=55,
            reply_to_top_id=10,
        )
    )

    assert topic_id_for_message(message) == 10


def test_topic_id_falls_back_to_reply_to_msg_id():
    message = SimpleNamespace(
        reply_to=SimpleNamespace(
            forum_topic=True,
            reply_to_msg_id=55,
            reply_to_top_id=None,
        )
    )

    assert topic_id_for_message(message) == 55


def test_message_to_record_keeps_metadata_and_text_without_media_download():
    message = SimpleNamespace(
        id=99,
        date=datetime(2026, 7, 6, 12, 30, tzinfo=UTC),
        sender_id=123,
        sender=SimpleNamespace(username="alice"),
        raw_text="hello",
        message="hello",
        media=object(),
        reply_to=SimpleNamespace(
            forum_topic=True,
            reply_to_msg_id=55,
            reply_to_top_id=10,
        ),
    )

    record = message_to_record(message, chat_id=-1001)

    assert record["id"] == 99
    assert record["chat_id"] == -1001
    assert record["topic_id"] == 10
    assert record["sender_id"] == 123
    assert record["sender_username"] == "alice"
    assert record["text"] == "hello"
    assert record["has_media"] is True


def test_parse_date_bound_supports_dates_and_datetimes():
    assert parse_date_bound("2026-07-06", end_of_day=False) == datetime(
        2026, 7, 6, 0, 0, tzinfo=UTC
    )
    assert parse_date_bound("2026-07-06", end_of_day=True) == datetime(
        2026, 7, 6, 23, 59, 59, 999999, tzinfo=UTC
    )
    assert parse_date_bound("2026-07-06T12:30:00+00:00", end_of_day=False) == datetime(
        2026, 7, 6, 12, 30, tzinfo=UTC
    )
