"""Every time a person types is read in one stated zone: this machine's local time.

`search` bounds and `member --until` read a bare `2026-09-06T14:30` as UTC while
`send --at` and `schedule post --at` read it as this machine's clock, and one
help line of nine said which. Sven's call (card agent-bo-95422371): a bare time
is local everywhere, an explicit offset or a trailing `Z` always wins, the
preview echoes the resolved moment with its offset, and JSON stays UTC.

The zone is pinned to Europe/Malta so "local" is never accidentally UTC on the
machine running the suite, and so the two sides of a clock change are checked.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from telegram_tools import cli, menu, records
from telegram_tools import manage as manage_ops
from telegram_tools import watch as watch_ops
from telegram_tools.records import BARE_TIME_IS_LOCAL, parse_date_bound


@pytest.fixture
def malta(monkeypatch):
    """This machine's clock is Malta's for one test: CEST (+02:00) in summer, CET (+01:00) in winter."""
    monkeypatch.setenv("TZ", "Europe/Malta")
    time.tzset()
    yield ZoneInfo("Europe/Malta")
    monkeypatch.undo()
    time.tzset()


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _parsers():
    return {
        "search --since": lambda text: parse_date_bound(text, end_of_day=False),
        "search --until": lambda text: parse_date_bound(text, end_of_day=True),
        "member --until": lambda text: manage_ops.parse_until(text, now=NOW),
        "--at": watch_ops.parse_when,
    }


def test_every_parser_reads_a_bare_typed_time_as_the_same_local_moment(malta):
    for name, parse in _parsers().items():
        assert parse("2026-09-06T14:30") == datetime(2026, 9, 6, 12, 30, tzinfo=UTC), name


def test_a_bare_time_takes_the_offset_of_its_own_date_not_of_today(malta):
    """A post typed in September for November lands at 09:00 on a winter clock, not an hour early."""
    for name, parse in _parsers().items():
        assert parse("2026-11-01T09:00") == datetime(2026, 11, 1, 8, 0, tzinfo=UTC), name


def test_an_offset_or_a_trailing_z_always_wins(malta):
    for name, parse in _parsers().items():
        assert parse("2026-09-06T14:30Z") == datetime(2026, 9, 6, 14, 30, tzinfo=UTC), name
        assert parse("2026-09-06T14:30+05:00") == datetime(2026, 9, 6, 9, 30, tzinfo=UTC), name


def test_a_bare_date_is_the_local_day_from_its_first_instant_to_its_last(malta):
    assert parse_date_bound("2026-09-06", end_of_day=False) == datetime(2026, 9, 5, 22, 0, tzinfo=UTC)
    assert parse_date_bound("2026-09-06", end_of_day=True) == datetime(2026, 9, 6, 21, 59, 59, 999999, tzinfo=UTC)


def test_the_archive_is_asked_in_utc_for_the_local_bound(malta):
    """The store compares its UTC `...Z` dates as text, so a typed bound reaches it as one."""
    args = SimpleNamespace(since="2026-09-06", until="2026-09-06T14:30")
    kwargs = cli._query_kwargs(args, SimpleNamespace(id="tg:user:1"))
    assert (kwargs["since"], kwargs["until"]) == ("2026-09-05T22:00:00Z", "2026-09-06T12:30:00Z")
    whole_day = cli._query_kwargs(SimpleNamespace(until="2026-09-06"), SimpleNamespace(id="tg:user:1"))
    assert whole_day["until"] == "2026-09-06T21:59:59Z" and whole_day["since"] is None


def test_the_preview_shows_the_resolved_moment_with_its_offset_and_json_stays_utc(malta):
    moment = manage_ops.parse_until("2026-09-06T14:30", now=NOW)
    assert manage_ops.until_shown(moment) == "2026-09-06T14:30:00+02:00"
    assert manage_ops.until_text(moment) == "2026-09-06T12:30:00Z"
    assert manage_ops.until_shown(None) is None


TIME_DESTS = ("since", "until", "at", "expires")


def _time_flags():
    found = []

    def walk(parser, trail):
        for action in parser._actions:
            if hasattr(action, "_name_parser_map"):
                for name, sub in action._name_parser_map.items():
                    walk(sub, f"{trail} {name}".strip())
            elif action.dest in TIME_DESTS:
                found.append((f"{trail} --{action.dest}", action.help or ""))

    walk(cli.build_parser(), "")
    return found


def test_every_flag_that_takes_a_time_says_which_zone_reads_it():
    flags = _time_flags()
    assert len(flags) >= 12  # search 2, archive sync 1, archive search/export 4, mute, restrict, invite, schedule, send
    silent = [where for where, text in flags if BARE_TIME_IS_LOCAL not in text]
    assert silent == []


def test_every_menu_prompt_that_takes_a_time_says_which_zone_reads_it(monkeypatch):
    """The row keeps its short label; the prompt a person types into carries the sentence."""
    forms = [menu.SCHEDULE_POST_FIELDS, *menu.MANAGE_FORMS.values()]
    timed = {key: label for fields in forms for key, label, _kind in fields if key in TIME_DESTS}
    assert set(timed) == {"at", "until", "expires"}
    assert [key for key, label in timed.items() if BARE_TIME_IS_LOCAL not in menu.time_prompt(key, label)] == []
    assert menu.time_prompt("title", "Title") == "Title"

    asked = []
    monkeypatch.setattr(menu, "ask_text", lambda label, **_io: asked.append(label) or "2026-09-06")
    assert menu.ask_date("Since", read=None, write=None) == "2026-09-06"
    assert asked[0].startswith("Since") and BARE_TIME_IS_LOCAL in asked[0]


def test_the_sentence_is_the_one_schedule_post_has_always_printed():
    assert records.BARE_TIME_IS_LOCAL == "a time with no offset is this machine's local time"
