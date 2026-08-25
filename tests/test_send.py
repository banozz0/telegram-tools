import asyncio
from types import SimpleNamespace

import pytest

from telegram_tools.config import parse_send_allowlist
from telegram_tools.models import TopicInfo
from telegram_tools.send import (
    SendNotAllowedError,
    SendTarget,
    confirm_send,
    format_send_preview,
    require_send_allowed,
    send_message,
)


class FakeClient:
    def __init__(self, message_id=9001):
        self.message_id = message_id
        self.sent = []

    async def send_message(self, entity, message, *, reply_to=None):
        self.sent.append({"entity": entity, "message": message, "reply_to": reply_to})
        return SimpleNamespace(id=self.message_id)


TOPIC = TopicInfo(id=141, title="Deploys", top_message=900)
TARGET = SendTarget(chat_id=-100111, chat_title="Hermes", topic=TOPIC)
WHOLE_CHAT = SendTarget(chat_id=-100222, chat_title="Alerts", topic=None)


def test_preview_names_destination_sender_and_body_verbatim():
    preview = format_send_preview(TARGET, "ship it\nsecond line", sender="Sven")

    assert "Hermes" in preview
    assert "-100111" in preview
    assert "141" in preview
    assert "Deploys" in preview
    assert "Sven" in preview
    assert "ship it\nsecond line" in preview


def test_preview_says_no_topic_when_the_chat_has_none():
    preview = format_send_preview(WHOLE_CHAT, "hi", sender="Sven")

    assert "Alerts" in preview
    assert "no topic" in preview.lower()


def test_confirm_accepts_only_y():
    assert confirm_send("preview", read=lambda _prompt: "y", write=lambda _line: None) is True
    assert confirm_send("preview", read=lambda _prompt: "n", write=lambda _line: None) is False


def test_confirm_treats_an_empty_answer_as_cancelled():
    lines = []
    assert confirm_send("preview", read=lambda _prompt: "", write=lines.append) is False
    assert any("cancelled" in line.lower() for line in lines)


def test_declining_sends_nothing():
    client = FakeClient()

    result = asyncio.run(send_message(client, "PEER", TARGET, "ship it", confirm=lambda: False))

    assert result.cancelled is True
    assert result.message_id is None
    assert client.sent == []
    assert result.to_dict()["sent"] is False


def test_confirming_sends_into_the_topic():
    client = FakeClient()

    result = asyncio.run(send_message(client, "PEER", TARGET, "ship it", confirm=lambda: True))

    assert client.sent == [{"entity": "PEER", "message": "ship it", "reply_to": 141}]
    assert result.message_id == 9001
    assert result.topic_id == 141
    assert result.cancelled is False
    assert result.to_dict()["sent"] is True


def test_a_chat_without_a_topic_sends_with_no_reply_to():
    client = FakeClient()

    asyncio.run(send_message(client, "PEER", WHOLE_CHAT, "hi", confirm=lambda: True))

    assert client.sent[0]["reply_to"] is None


def test_no_confirm_callable_sends_without_asking():
    client = FakeClient()

    result = asyncio.run(send_message(client, "PEER", WHOLE_CHAT, "hi"))

    assert result.cancelled is False
    assert len(client.sent) == 1


def test_allowlist_parses_chats_and_topic_scoped_entries():
    allowlist = parse_send_allowlist("-100111:141, @alerts ,-100222")

    assert [(entry.chat, entry.topic) for entry in allowlist] == [
        ("-100111", 141),
        ("alerts", None),
        ("-100222", None),
    ]


def test_allowlist_is_empty_when_unset():
    assert parse_send_allowlist(None) == ()
    assert parse_send_allowlist("  ") == ()


def test_allowlist_rejects_a_malformed_entry():
    with pytest.raises(Exception) as excinfo:
        parse_send_allowlist("-100111:notanumber")
    assert "TELEGRAM_SEND_ALLOWLIST" in str(excinfo.value)


def test_topic_scoped_entry_allows_only_that_topic():
    allowlist = parse_send_allowlist("-100111:141")

    require_send_allowed(allowlist, chat_id=-100111, username=None, topic_id=141)
    with pytest.raises(SendNotAllowedError):
        require_send_allowed(allowlist, chat_id=-100111, username=None, topic_id=217)


def test_whole_chat_entry_allows_any_topic_in_it():
    allowlist = parse_send_allowlist("-100111")

    require_send_allowed(allowlist, chat_id=-100111, username=None, topic_id=217)
    require_send_allowed(allowlist, chat_id=-100111, username=None, topic_id=None)


def test_a_username_entry_matches_the_chat_username():
    allowlist = parse_send_allowlist("@alerts")

    require_send_allowed(allowlist, chat_id=-100999, username="Alerts", topic_id=None)


def test_an_empty_allowlist_refuses_everything_and_says_how_to_fix_it():
    with pytest.raises(SendNotAllowedError) as excinfo:
        require_send_allowed((), chat_id=-100111, username=None, topic_id=None)

    message = str(excinfo.value)
    assert "TELEGRAM_SEND_ALLOWLIST" in message
    assert "-100111" in message


def test_the_refusal_names_the_topic_scoped_destination():
    with pytest.raises(SendNotAllowedError) as excinfo:
        require_send_allowed((), chat_id=-100111, username=None, topic_id=141)

    assert "-100111:141" in str(excinfo.value)


# --- attachments ------------------------------------------------------------


class SendingFileClient(FakeClient):
    def __init__(self, message_ids=(9101, 9102)):
        super().__init__()
        self.message_ids = list(message_ids)
        self.files_sent = []

    async def send_file(self, entity, file, *, caption=None, reply_to=None):
        self.files_sent.append({"entity": entity, "file": file, "caption": caption, "reply_to": reply_to})
        sent = [SimpleNamespace(id=mid) for mid in self.message_ids[: len(file) if isinstance(file, list) else 1]]
        return sent if isinstance(file, list) else sent[0]


def test_files_go_through_send_file_with_the_text_as_a_caption():
    client = SendingFileClient()

    result = asyncio.run(
        send_message(client, "PEER", TARGET, "look at this", files=["/tmp/a.png"], confirm=lambda: True)
    )

    assert client.sent == []
    assert client.files_sent == [
        {"entity": "PEER", "file": ["/tmp/a.png"], "caption": "look at this", "reply_to": 141}
    ]
    assert result.message_id == 9101
    assert result.to_dict()["files"] == 1


def test_several_files_are_sent_together():
    client = SendingFileClient()

    result = asyncio.run(
        send_message(client, "PEER", WHOLE_CHAT, None, files=["/tmp/a.png", "/tmp/b.pdf"], confirm=lambda: True)
    )

    assert client.files_sent[0]["file"] == ["/tmp/a.png", "/tmp/b.pdf"]
    assert client.files_sent[0]["caption"] is None
    assert result.to_dict()["files"] == 2
    assert result.message_id == 9101


def test_declining_sends_no_files():
    client = SendingFileClient()

    result = asyncio.run(send_message(client, "PEER", TARGET, "hi", files=["/tmp/a.png"], confirm=lambda: False))

    assert client.files_sent == []
    assert result.cancelled is True


def test_a_text_only_send_reports_no_files():
    client = FakeClient()

    result = asyncio.run(send_message(client, "PEER", WHOLE_CHAT, "hi", confirm=lambda: True))

    assert result.to_dict()["files"] == 0


def test_preview_lists_each_attachment_with_its_size(tmp_path):
    small = tmp_path / "notes.txt"
    small.write_bytes(b"x" * 2048)
    big = tmp_path / "shot.png"
    big.write_bytes(b"y" * 3_000_000)

    preview = format_send_preview(TARGET, "caption", sender="Sven", files=[str(small), str(big)])

    assert "notes.txt" in preview
    assert "shot.png" in preview
    assert "2.0 kB" in preview
    assert "2.9 MB" in preview


def test_preview_says_when_a_file_has_no_caption(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"z")

    preview = format_send_preview(WHOLE_CHAT, None, sender="Sven", files=[str(path)])

    assert "no caption" in preview.lower()


def test_preview_without_files_has_no_files_row():
    assert "Files" not in format_send_preview(TARGET, "hi", sender="Sven")
