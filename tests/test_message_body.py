"""The one seam three message kinds flow through: text, provenance, and the row.

`records.message_body` derives what a message is identified by; `records.record_marks`
derives what its line is marked with. The live record, the printed line, the export
columns and the archive row all read those two, so a message says the same thing about
itself wherever it is shown.

Every test here builds the message by hand -- a `SimpleNamespace` shaped like the
Telethon object -- and drives the real archive, so the FTS proof is the store's own
index and not a stand-in for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from telegram_tools._core.archive import Archive, MessageRecord, ScopeListing
from telegram_tools._core.identity import Identity, Target
from telegram_tools.adapters.archive import Cursor, _Scope, message_record
from telegram_tools.records import message_to_record
from telegram_tools.search import format_message_records

IDENTITY = Identity(platform="telegram", mode="account", label="Sven (@sven)", id="tg:user:4242", profile="default")
CHAT_ID = -1001000000001
TOPIC_ID = 141
SCOPE = _Scope(
    target=Target(
        rid=f"tg:topic:{CHAT_ID}:{TOPIC_ID}",
        kind="topic",
        title="Deploys",
        path=("Team Hermes", "Deploys"),
        platform="telegram",
        ids={"chat": str(CHAT_ID), "topic": str(TOPIC_ID)},
    ),
    topic_id=TOPIC_ID,
)
CURSOR = Cursor(20, 1, True)
SENDER = SimpleNamespace(id=5046366253, first_name="Harry", username="harry", bot=False)


def fake_message(number: int, **fields):
    """A Telethon message as the walk sees one: text, no media, nothing forwarded."""
    base = {
        "id": number,
        "date": datetime(2026, 9, 12, 16, 42, tzinfo=UTC),
        "raw_text": "",
        "message": "",
        "sender_id": SENDER.id,
        "sender": SENDER,
        "reply_to": None,
        "media": None,
        "edit_date": None,
        "forward": None,
        "action": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def poll_media(question: str, *answers: str):
    """`MessageMediaPoll`, with the `TextWithEntities` the current layer wraps every string in."""
    return SimpleNamespace(
        poll=SimpleNamespace(
            question=SimpleNamespace(text=question, entities=[]),
            answers=[SimpleNamespace(text=SimpleNamespace(text=answer, entities=[]), option=b"") for answer in answers],
        )
    )


@pytest.fixture
def archive(tmp_path):
    """A real store, migrated, with one scope to write into."""
    with Archive.open(tmp_path / "archive.sqlite") as store:
        store.record_identity(IDENTITY)
        store.upsert_scope(ScopeListing(target=SCOPE.target), IDENTITY.id)
        yield store


def archived(store, message):
    """One message through the archive writer and into the store; the row it became."""
    record = message_record(message, SCOPE, CURSOR)
    store.upsert_messages(SCOPE.target.rid, IDENTITY.id, [MessageRecord.from_record(record)])
    return record


def printed(message):
    """One message through the live record and the printed line."""
    record = message_to_record(message, chat_id=CHAT_ID, topic_id=TOPIC_ID)
    return record, format_message_records([record])


# -- a poll (card agent-bo-95422216) ---------------------------------------


def test_a_poll_prints_its_question_and_answers_instead_of_the_media_placeholder():
    message = fake_message(16, media=poll_media("campaign 3.11 poll?", "alpha", "beta"))

    record, text = printed(message)

    assert record["poll"] == {"question": "campaign 3.11 poll?", "answers": ["alpha", "beta"]}
    assert "campaign 3.11 poll?" in text and "alpha / beta" in text
    assert "[poll]" in text
    assert "[media]" not in text, "the placeholder is what hid the question; a poll names itself"
    # The shipped keys are where they were: a poll is still a media message.
    assert record["has_media"] is True and record["id"] == 16


def test_a_poll_reaches_the_archive_as_searchable_text_and_a_platform_json_key(archive):
    record = archived(archive, fake_message(16, media=poll_media("campaign 3.11 poll?", "alpha", "beta")))

    assert record["text"] == "[poll] campaign 3.11 poll? — alpha / beta"
    assert record["platform_json"]["poll"]["question"] == "campaign 3.11 poll?"
    assert record["platform_json"]["has_media"] is True, "the shipped key keeps its meaning"

    hits = archive.search('"campaign"')
    assert [hit.message_id for hit in hits] == ["16"], "a word from the question finds the row"
    assert "campaign 3.11 poll?" in hits[0].text


def test_a_poll_with_a_caption_keeps_the_caption_and_still_carries_the_question():
    message = fake_message(17, raw_text="vote here", message="vote here", media=poll_media("ship it?", "yes", "no"))

    record, text = printed(message)

    assert record["text"] == "vote here", "nothing a person typed is ever displaced"
    assert record["poll"]["question"] == "ship it?"
    assert "[poll] vote here" in text, "the caption is the text, so the line is where the kind is named"
    assert "[media]" not in text


def test_a_poll_from_an_older_layer_carrying_plain_strings_reads_the_same():
    """`Poll.question` and `PollAnswer.text` were bare strings before layer 179."""
    media = SimpleNamespace(
        poll=SimpleNamespace(question="ship it?", answers=[SimpleNamespace(text="yes"), SimpleNamespace(text="no")])
    )

    record = message_to_record(fake_message(18, media=media), chat_id=CHAT_ID)

    assert record["poll"] == {"question": "ship it?", "answers": ["yes", "no"]}


def test_a_photo_is_untouched_by_the_poll_derivation(archive):
    message = fake_message(19, media=SimpleNamespace(photo=object()))

    record, text = printed(message)

    assert "poll" not in record, "a key is added only for a message that has it"
    assert record["text"] == ""
    assert "[media]" in text
    assert "poll" not in archived(archive, message)["platform_json"]
