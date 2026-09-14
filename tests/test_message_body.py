"""The one seam three message kinds flow through: text, provenance, and the row.

`records.message_body` derives what a message is identified by; `records.record_marks`
derives what its line is marked with. The live record, the printed line, the export
columns and the archive row all read those two, so a message says the same thing about
itself wherever it is shown.

Every test here builds the message by hand -- a `SimpleNamespace` shaped like the
Telethon object, or Telethon's own media class where the derivation is dispatched on
that class -- and drives the real archive, so the FTS proof is the store's own index
and not a stand-in for it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl import types as tl

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


def forwarded(sender=None, chat=None, from_name=None):
    """`message.forward`: Telethon's attribution, with the ids a hidden forward never has."""
    return SimpleNamespace(
        sender_id=getattr(sender, "id", None),
        sender=sender,
        chat_id=getattr(chat, "id", None),
        chat=chat,
        from_name=from_name,
        date=datetime(2026, 9, 11, 9, 0, tzinfo=UTC),
    )


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


# -- a forward (card agent-bo-95422217) ------------------------------------

ALERTS = SimpleNamespace(id=-1001000000003, title="Alerts", username=None)


def test_a_forward_names_where_it_came_from_and_a_copy_names_nothing(archive):
    forward = fake_message(13, raw_text="campaign 3.1", message="campaign 3.1", forward=forwarded(sender=SENDER))
    copy = fake_message(14, raw_text="campaign 3.1", message="campaign 3.1")

    record, text = printed(forward)
    assert record["forwarded_from"]["sender_id"] == SENDER.id
    assert record["forwarded_from"]["sender"] == "@harry"
    assert record["forwarded_from"]["hidden"] is False
    assert record["forwarded_from"]["date"] == "2026-09-11T09:00:00+00:00"
    assert "[fwd @harry] campaign 3.1" in text

    plain_record, plain_text = printed(copy)
    assert "forwarded_from" not in plain_record, "a copy drops the author on purpose; the absent key is the fact"
    assert "[fwd" not in plain_text

    assert archived(archive, forward)["platform_json"]["forwarded_from"]["sender"] == "@harry"
    assert "forwarded_from" not in archived(archive, copy)["platform_json"]
    # The text is the text either way: a marker in front of it would rewrite
    # what the person actually wrote, and the two rows must stay comparable.
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM messages WHERE text = 'campaign 3.1'"
    ).fetchone()[0] == 2


def test_a_forwarded_channel_post_names_the_chat_and_a_signed_one_names_both():
    from_channel = fake_message(20, raw_text="deploy is green", forward=forwarded(chat=ALERTS))
    signed = fake_message(21, raw_text="deploy is green", forward=forwarded(sender=SENDER, chat=ALERTS))

    record, text = printed(from_channel)
    assert record["forwarded_from"]["chat_id"] == ALERTS.id and record["forwarded_from"]["sender"] is None
    assert "[fwd Alerts]" in text

    both, both_text = printed(signed)
    assert both["forwarded_from"]["label"] == "@harry in Alerts"
    assert "[fwd @harry in Alerts]" in both_text


def test_a_hidden_forward_keeps_the_name_invents_no_id_and_says_it_is_hidden():
    """Forwards restricted on the original author: Telegram sends a name and nothing else."""
    message = fake_message(22, raw_text="leaked", forward=forwarded(from_name="Alice"))

    record, text = printed(message)

    assert record["forwarded_from"] == {
        "sender_id": None,
        "sender": "Alice",
        "chat_id": None,
        "chat": None,
        "date": "2026-09-11T09:00:00+00:00",
        "hidden": True,
        "label": "Alice (hidden)",
    }
    assert "[fwd Alice (hidden)] leaked" in text


def test_a_forwarded_photo_is_marked_by_both_where_it_came_from_and_what_it_carries():
    message = fake_message(23, media=SimpleNamespace(photo=object()), forward=forwarded(sender=SENDER))

    _record, text = printed(message)

    assert "[fwd @harry] [media]" in text, "provenance first, then the kind"


def test_the_human_export_formats_carry_the_forward_and_leave_the_media_column_to_say_media():
    from telegram_tools.exporters import live_rows

    rows = live_rows(
        [
            message_to_record(fake_message(13, raw_text="campaign 3.1", forward=forwarded(sender=SENDER)), chat_id=CHAT_ID),
            message_to_record(fake_message(14, raw_text="campaign 3.1"), chat_id=CHAT_ID),
            message_to_record(fake_message(23, media=SimpleNamespace(photo=object())), chat_id=CHAT_ID),
        ],
        chat_title="Team Hermes",
    )

    assert rows[0]["text"] == "[fwd @harry] campaign 3.1"
    assert rows[1]["text"] == "campaign 3.1", "a copy reads as what it is: a plain line"
    assert rows[2]["text"] == "" and rows[2]["media"] == 1, "the media column already says it"


# -- a service message (card agent-bo-95422218) ----------------------------


def service(name: str, **fields):
    """A `MessageAction*`: the class name is the event, which is all Telegram sends."""
    return type(name, (SimpleNamespace,), {})(**fields)


def test_a_created_topic_and_a_pinned_message_name_their_event_instead_of_printing_blank():
    created = fake_message(6, action=service("MessageActionTopicCreate", title="Campaign", icon_color=0))
    pinned = fake_message(15, action=service("MessageActionPinMessage"))

    created_record, created_text = printed(created)
    assert created_record["service"] == {"action": "topic_create", "label": "topic created", "title": "Campaign"}
    assert created_record["text"] == "[event] topic created: Campaign"
    assert created_text.rstrip().endswith("[event] topic created: Campaign"), "the row ends in the event, not in nothing"

    pinned_record, pinned_text = printed(pinned)
    assert pinned_record["service"] == {"action": "pin_message", "label": "message pinned"}
    assert "[event] message pinned" in pinned_text


def test_a_service_message_reaches_the_archive_as_text_a_search_can_find(archive):
    record = archived(archive, fake_message(15, action=service("MessageActionPinMessage")))

    assert record["text"] == "[event] message pinned"
    assert record["platform_json"]["service"]["action"] == "pin_message"

    hits = archive.search('"pinned"')
    assert [hit.message_id for hit in hits] == ["15"], "a row with no text is a row nothing can search for"


def test_an_action_with_no_phrase_of_its_own_is_named_from_telegrams_own_class():
    """A Telegram release that adds an action is named the day it arrives, never dropped."""
    record, text = printed(fake_message(7, action=service("MessageActionGiftPremium", months=3)))

    assert record["service"] == {"action": "gift_premium", "label": "gift premium"}
    assert "[event] gift premium" in text


def test_a_run_of_service_messages_leaves_no_blank_row_and_keeps_the_ids_honest():
    """Section 2 row 2.1: four of ten rows of a fresh forum printed as empty lines."""
    records = [
        message_to_record(fake_message(6, action=service("MessageActionTopicCreate", title="General")), chat_id=CHAT_ID),
        message_to_record(fake_message(7, action=service("MessageActionChatAddUser", users=[1])), chat_id=CHAT_ID),
        message_to_record(fake_message(8, raw_text="campaign 3.1", message="campaign 3.1"), chat_id=CHAT_ID),
    ]

    rows = format_message_records(records).splitlines()[2:]

    assert [row.split("\t")[0] for row in rows] == ["6", "7", "8"], "every event keeps its id; nothing is dropped"
    assert not [row for row in rows if row.endswith("\t")], "no row ends in an empty text column"
    assert "[event] topic created: General" in rows[0]
    assert "[event] member added" in rows[1]


def test_the_export_formats_show_the_same_named_event_the_screen_does():
    from telegram_tools.exporters import live_rows

    record = message_to_record(fake_message(6, action=service("MessageActionTopicCreate", title="Campaign")), chat_id=CHAT_ID)
    row = live_rows([record], chat_title="Team Hermes")[0]

    assert row["text"] == "[event] topic created: Campaign", "a file and a screen agree"
    assert row["media"] == 0


# -- what a message carries (card agent-bo-95422219) -----------------------
#
# This section builds Telethon's own media classes rather than a namespace
# shaped like one: `records.attachment_of` dispatches on the class Telegram
# sends, so a stand-in named by hand would prove the dispatch works against the
# stand-in and nothing about the name Telethon actually uses.

PHONE = "+35679123478"
VCARD = f"BEGIN:VCARD\nFN:Alice Smith\nTEL;TYPE=CELL:{PHONE}\nEND:VCARD"


def document(*attributes):
    """`MessageMediaDocument`: every name a file has is one of its attributes."""
    return tl.MessageMediaDocument(
        document=tl.Document(
            id=7,
            access_hash=7,
            file_reference=b"",
            date=None,
            mime_type="application/octet-stream",
            size=2048,
            dc_id=2,
            attributes=list(attributes),
        )
    )


def checklist(title, *items):
    """`MessageMediaToDo`, the poll's closest sibling, with the same wrapped strings."""
    return tl.MessageMediaToDo(
        todo=tl.TodoList(
            title=tl.TextWithEntities(text=title, entities=[]),
            list=[
                tl.TodoItem(id=number, title=tl.TextWithEntities(text=item, entities=[]))
                for number, item in enumerate(items, start=1)
            ],
        )
    )


FILE = document(tl.DocumentAttributeFilename(file_name="quarterly-report.pdf"))
SONG = document(
    tl.DocumentAttributeAudio(duration=210, title="Nightcall", performer="Kavinsky"),
    tl.DocumentAttributeFilename(file_name="nightcall.mp3"),
)
VOICE = document(tl.DocumentAttributeAudio(duration=7, voice=True))
STICKER = document(tl.DocumentAttributeSticker(alt="🎉", stickerset=tl.InputStickerSetEmpty()))
CONTACT = tl.MessageMediaContact(phone_number=PHONE, first_name="Alice", last_name="Smith", vcard=VCARD, user_id=99)
VENUE = tl.MessageMediaVenue(
    geo=tl.GeoPointEmpty(),
    title="Trabuxu Bistro",
    address="1 Strait Street",
    provider="foursquare",
    venue_id="v1",
    venue_type="bar",
)
INVOICE = tl.MessageMediaInvoice(
    title="Pro plan", description="a year of everything", currency="EUR", total_amount=9900, start_param="p"
)
GAME = tl.MessageMediaGame(
    game=tl.Game(id=1, access_hash=1, short_name="corsairs", title="Corsairs", description="sail", photo=tl.PhotoEmpty(id=0))
)
GIVEAWAY = tl.MessageMediaGiveaway(
    channels=[CHAT_ID], quantity=5, until_date=None, prize_description="a year of Premium"
)
GIVEAWAY_RESULTS = tl.MessageMediaGiveawayResults(
    channel_id=CHAT_ID,
    launch_msg_id=2,
    winners_count=5,
    unclaimed_count=0,
    winners=[SENDER.id],
    until_date=None,
    prize_description="a year of Premium",
)
DICE = tl.MessageMediaDice(value=6, emoticon="🎲")

# The media, the line it is identified by, the additive `attachment` key, and a
# word an archive search finds the row by. The three kinds whose only word is
# the kind itself -- a voice note Telegram named nothing, a sticker whose name
# is an emoji, a thrown dice -- are found by that word, which is the whole
# reason the derived line carries it.
CARRIED = [
    (FILE, "[file] quarterly-report.pdf", {"kind": "file", "file_name": "quarterly-report.pdf"}, "quarterly"),
    (
        SONG,
        "[audio] Nightcall — Kavinsky",
        {"kind": "audio", "title": "Nightcall", "performer": "Kavinsky", "file_name": "nightcall.mp3"},
        "Kavinsky",
    ),
    (VOICE, "[voice]", {"kind": "voice"}, "voice"),
    (STICKER, "[sticker] 🎉", {"kind": "sticker", "emoji": "🎉"}, "sticker"),
    (
        checklist("Launch day", "book venue", "send invites"),
        "[checklist] Launch day — book venue / send invites",
        {"kind": "checklist", "title": "Launch day", "items": ["book venue", "send invites"]},
        "invites",
    ),
    (CONTACT, "[contact] Alice Smith", {"kind": "contact", "first_name": "Alice", "last_name": "Smith"}, "Alice"),
    (
        VENUE,
        "[venue] Trabuxu Bistro — 1 Strait Street",
        {"kind": "venue", "title": "Trabuxu Bistro", "address": "1 Strait Street"},
        "Trabuxu",
    ),
    (
        INVOICE,
        "[invoice] Pro plan — a year of everything",
        {"kind": "invoice", "title": "Pro plan", "description": "a year of everything"},
        "invoice",
    ),
    (GAME, "[game] Corsairs", {"kind": "game", "title": "Corsairs"}, "Corsairs"),
    (GIVEAWAY, "[giveaway] a year of Premium", {"kind": "giveaway", "prize_description": "a year of Premium"}, "Premium"),
    (
        GIVEAWAY_RESULTS,
        "[giveaway] a year of Premium",
        {"kind": "giveaway", "prize_description": "a year of Premium"},
        "Premium",
    ),
    (DICE, "[dice] 🎲 6", {"kind": "dice", "emoticon": "🎲", "value": 6}, "dice"),
]


@pytest.mark.parametrize(
    "media, line, attachment, word",
    CARRIED,
    ids=[
        "file",
        "audio",
        "voice-note",
        "sticker",
        "checklist",
        "contact",
        "venue",
        "invoice",
        "game",
        "giveaway",
        "giveaway-results",
        "dice",
    ],
)
def test_every_carried_kind_names_itself_on_the_screen_and_in_the_archive(archive, media, line, attachment, word):
    message = fake_message(31, media=media)

    record, text = printed(message)
    assert record["attachment"] == attachment, "the structured form rides as an additive key"
    assert record["text"] == line
    assert line in text
    assert "[media]" not in text, "the placeholder is what lost the name; the kind names itself"
    assert record["has_media"] is True, "the shipped key keeps its meaning"

    row = archived(archive, message)
    assert row["text"] == line, "the screen and the one column messages_fts indexes agree"
    assert row["platform_json"]["attachment"] == attachment

    hits = archive.search(f'"{word}"')
    assert [hit.message_id for hit in hits] == ["31"], "a word of it finds the row again"


def test_a_contact_shows_the_name_and_its_phone_number_reaches_nothing(archive, tmp_path):
    """The hard line on this card: the number Telegram sent lands in no place at all.

    Not the printed row, not the archive's `text`, not `platform_json`, not the
    store's FTS index and not one of the five export files. The sweep reads
    every byte under `tmp_path` -- the sqlite file, its write-ahead log and each
    export -- and proves it is looking at real content by finding the name.
    """
    from telegram_tools.exporters import live_rows, write_records

    message = fake_message(32, media=CONTACT)

    record, text = printed(message)
    assert record["attachment"] == {"kind": "contact", "first_name": "Alice", "last_name": "Smith"}
    assert record["text"] == "[contact] Alice Smith"

    row = archived(archive, message)
    rows = live_rows([record], chat_title="Team Hermes")
    for fmt in ("json", "csv", "jsonl", "markdown", "html"):
        write_records([record], tmp_path / f"export.{fmt}", fmt, chat_title="Team Hermes")

    digits = PHONE.lstrip("+")
    in_memory = json.dumps([record, row, rows, text], default=str, ensure_ascii=False)
    assert PHONE not in in_memory and digits not in in_memory
    assert "vcard" not in in_memory and "phone" not in in_memory

    seen = b""
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file():
            blob = path.read_bytes()
            assert digits.encode() not in blob, f"a phone number reached {path.name}"
            seen += blob
    assert b"Alice Smith" in seen, "the sweep read the store and the exports, not an empty directory"

    assert [hit.message_id for hit in archive.search('"Alice"')] == ["32"]
    assert list(archive.search(f'"{digits}"')) == [], "nothing indexed the number either"


def test_a_file_with_a_caption_keeps_the_caption_and_the_line_names_the_kind():
    message = fake_message(33, raw_text="here you go", message="here you go", media=FILE)

    record, text = printed(message)

    assert record["text"] == "here you go", "nothing a person typed is ever displaced"
    assert record["attachment"]["file_name"] == "quarterly-report.pdf"
    assert "[file] here you go" in text, "the caption is the text, so the line is where the kind is named"
    assert "[media]" not in text


def test_a_forwarded_file_is_marked_by_where_it_came_from_and_carries_its_name():
    message = fake_message(34, media=FILE, forward=forwarded(sender=SENDER))

    record, text = printed(message)

    assert record["text"] == "[file] quarterly-report.pdf"
    assert "[fwd @harry] [file] quarterly-report.pdf" in text, "provenance first, then the kind"


def test_the_export_formats_show_the_same_name_the_screen_does():
    from telegram_tools.exporters import live_rows

    row = live_rows([message_to_record(fake_message(35, media=FILE), chat_id=CHAT_ID)], chat_title="Team Hermes")[0]

    assert row["text"] == "[file] quarterly-report.pdf", "a file and a screen agree"
    assert row["media"] == 1, "the media column still says there is one"


@pytest.mark.parametrize(
    "media",
    [
        tl.MessageMediaPhoto(photo=tl.PhotoEmpty(id=0)),
        tl.MessageMediaGeo(geo=tl.GeoPointEmpty()),
        tl.MessageMediaWebPage(webpage=tl.WebPageEmpty(id=1)),
        tl.MessageMediaUnsupported(),
    ],
    ids=["photo", "geo", "web-page", "unsupported"],
)
def test_a_kind_with_no_name_of_its_own_keeps_the_media_placeholder(media):
    """Out of scope on purpose: none of these has a string a search could find."""
    record, text = printed(fake_message(36, media=media))

    assert "attachment" not in record, "a key is added only for a message that has one"
    assert record["text"] == ""
    assert "[media]" in text


def test_a_poll_is_still_a_poll_and_not_an_attachment():
    """Additive: the key the poll card shipped keeps its name and its meaning."""
    record, _text = printed(fake_message(37, media=poll_media("ship it?", "yes", "no")))

    assert "poll" in record and "attachment" not in record
