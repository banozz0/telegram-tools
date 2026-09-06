"""The archive on this tool: the Telethon source, the six commands, and the P3 fixture row.

Spec section 8 and the P3 row of section 21. Every run here is against a fake
client: no session is opened, no socket, and the store is a temporary file
under a temporary home. The fake serves history the way Telegram does --
newest first, paged by `offset_id` and bounded by `min_id` -- which is what
the cursor scheme in `adapters/archive.py` is written against.
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools._core.archive import Archive, SKIPPED_REASONS
from telegram_tools._core.contract import validate_envelope
from telegram_tools._core.identity import Identity
from telegram_tools.adapters.archive import Cursor, TelegramArchiveSource, scope_rid_for
from telegram_tools.envelope import Reporter
from telegram_tools.exporters import write_records

ACCOUNT = SimpleNamespace(id=4242, first_name="Sven", username="sven", phone="+35699001122")
IDENTITY = Identity(platform="telegram", mode="account", label="Sven (@sven)", id="tg:user:4242", profile="default")
HARRY = SimpleNamespace(id=777, first_name="Harry", username="harry", bot=False)
FORUM_ID = -1001000000001
CHANNEL_ID = -1001000000003
FORUM_RID = scope_rid_for(str(FORUM_ID))
DEPLOYS = scope_rid_for(str(FORUM_ID), 141)
SUPPORT = scope_rid_for(str(FORUM_ID), 217)
ALERTS = scope_rid_for(str(CHANNEL_ID))
WORDS = ("deploy finished", "rollback the deploy", "staging is red", "green across the board", "notes for the week")


def run(coroutine):
    return asyncio.run(coroutine)


def message(number: int, *, topic: int | None = None, media=None):
    when = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=number)
    reply_to = SimpleNamespace(reply_to_msg_id=topic, reply_to_top_id=None, forum_topic=True) if topic else None
    return SimpleNamespace(
        id=number,
        date=when,
        raw_text=f"{WORDS[number % len(WORDS)]} {number}",
        sender_id=HARRY.id,
        sender=HARRY,
        reply_to=reply_to,
        media=media,
        edit_date=None,
    )


def history(count: int, *, start: int = 1, topic: int | None = None) -> list:
    """`count` messages of one scope, newest first, as Telegram serves them."""
    return [message(number, topic=topic) for number in range(start + count - 1, start - 1, -1)]


def _dialog(chat_id, title, *, forum=False, channel=False, username=None):
    entity = SimpleNamespace(
        id=abs(chat_id) - 1000000000000,
        title=title,
        username=username,
        megagroup=not channel,
        broadcast=channel,
        forum=forum,
    )
    return SimpleNamespace(id=chat_id, title=title, entity=entity, input_entity=SimpleNamespace(channel_id=entity.id, chat_id=chat_id))


class FakeClient:
    """A signed-in account: dialogs, forum topics, and paged history with a kill switch."""

    def __init__(self, *, rows=None, fail_after=None, fail_scope=None, raises=KeyboardInterrupt, flood=None):
        self.dialogs = [
            _dialog(FORUM_ID, "Team Hermes", forum=True, username="teamhermes"),
            _dialog(CHANNEL_ID, "Alerts", channel=True, username="agencyalerts"),
        ]
        self.topics = {FORUM_ID: [SimpleNamespace(id=141, title="Deploys", top_message=900), SimpleNamespace(id=217, title="Support", top_message=901)]}
        self.rows = rows if rows is not None else {
            (FORUM_ID, 141): history(40, topic=141),
            (FORUM_ID, 217): history(5, start=100, topic=217),
            (CHANNEL_ID, None): history(7, start=300),
        }
        self.fail_after = fail_after
        self.fail_scope = fail_scope
        self.raises = raises
        self.flood = dict(flood or {})
        self.pages: list[dict] = []
        self.disconnected = False

    async def get_me(self):
        return ACCOUNT

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnected = True

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_entity(self, reference):
        for dialog in self.dialogs:
            if dialog.id == reference or (isinstance(reference, str) and reference.lstrip("@") == dialog.entity.username):
                return dialog.entity
        raise ValueError(f"no entity {reference}")

    async def get_input_entity(self, entity):
        return SimpleNamespace(channel_id=entity.id, chat_id=-1000000000000 - entity.id)

    async def get_peer_id(self, entity):
        return -1000000000000 - entity.id

    async def __call__(self, request):
        name = type(request).__name__
        chat_id = getattr(request.peer, "chat_id", None)
        if name == "GetForumTopicsRequest":
            topics = self.topics.get(chat_id, [])
            return SimpleNamespace(topics=topics, count=len(topics))
        if name == "GetForumTopicsByIDRequest":
            found = [topic for topic in self.topics.get(chat_id, []) if topic.id in request.topics]
            return SimpleNamespace(topics=found, count=len(found))
        raise AssertionError(f"unexpected request {name}")

    async def iter_messages(self, peer, *, offset_id=None, min_id=0, reply_to=None, wait_time=None, **_):
        key = (getattr(peer, "chat_id", None), reply_to)
        self.pages.append({"scope": key, "offset_id": offset_id, "min_id": min_id})
        pending = self.flood.pop(key, None)
        if pending:
            raise FloodWaitError(request=None, capture=pending)
        served = 0
        for row in self.rows.get(key, []):
            if offset_id is not None and row.id >= offset_id:
                continue
            if min_id and row.id <= min_id:
                continue
            if self.fail_after is not None and self.fail_scope in (None, key) and served >= self.fail_after:
                raise self.raises("the connection dropped mid-scope")
            served += 1
            yield row


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.delenv("TELEGRAM_BOT_TOKENS", raising=False)
    monkeypatch.delenv("TELEGRAM_SEND_ALLOWLIST", raising=False)
    monkeypatch.delenv("TELEGRAM_TOOLS_PROFILE", raising=False)
    monkeypatch.setenv("TELEGRAM_TOOLS_SESSION", str(tmp_path / "session"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` against a fake account behind `create_client`/`start_client`."""

    def run(argv, *, client=None, capsys, isatty=False, answer=""):
        fake = client or FakeClient()

        async def started(_client, *, authorize=True):
            return fake

        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{answer}\n"),
        )
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            # argparse's own exit on a usage mistake, which is what human mode
            # has always done with one; the code is the answer either way.
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run


def envelope_of(out: str) -> dict:
    payload = json.loads(out)
    validate_envelope(payload)
    return payload


def ids_in(connection, rid=None):
    sql = "SELECT rid, message_id FROM messages" + (" WHERE rid = ?" if rid else "") + " ORDER BY rowid"
    return [tuple(row) for row in connection.execute(sql, (rid,) if rid else ())]


# -- the cursor ----------------------------------------------------------


def test_the_cursor_round_trips_and_refuses_what_it_did_not_write():
    assert Cursor.decode(Cursor(500, 12, False).encode()) == Cursor(500, 12, False)
    assert Cursor.decode("500:12:done") == Cursor(500, 12, True)
    assert Cursor.decode(None) is None
    assert Cursor.decode("41") is None, "a bare message id is another source's cursor, not a state"
    assert Cursor.decode("a:b:c") is None


# -- scopes --------------------------------------------------------------


def test_a_forum_is_listed_as_its_topics_and_a_channel_as_itself():
    source = TelegramArchiveSource(FakeClient())
    listings = run(_collect(source.scopes()))
    by_rid = {listing.rid: listing for listing in listings}
    assert set(by_rid) == {DEPLOYS, SUPPORT, ALERTS}, "the forum itself is not a scope: every row in it belongs to a topic"
    assert by_rid[DEPLOYS].parent_rid == FORUM_RID
    assert by_rid[DEPLOYS].target.title == "Deploys"
    assert by_rid[DEPLOYS].target.path == ("Team Hermes", "Deploys")
    assert by_rid[ALERTS].parent_rid is None
    assert by_rid[ALERTS].platform_json == {"type": "channel", "username": "agencyalerts"}
    assert all(listing.visible for listing in listings)


def test_scope_narrows_to_one_chat_one_topic_or_names_what_it_cannot_reach():
    only = TelegramArchiveSource(FakeClient(), only=[SUPPORT])
    assert [listing.rid for listing in run(_collect(only.scopes()))] == [SUPPORT]

    whole_forum = TelegramArchiveSource(FakeClient(), only=[FORUM_RID])
    assert [listing.rid for listing in run(_collect(whole_forum.scopes()))] == [DEPLOYS, SUPPORT]

    unreachable = TelegramArchiveSource(FakeClient(), only=["tg:chat:-1009999999999"])
    listing = run(_collect(unreachable.scopes()))[0]
    assert listing.visible is False and listing.skipped_reason == "no_access"
    assert listing.rid == "tg:chat:-1009999999999"

    not_a_place = TelegramArchiveSource(FakeClient(), only=["tg:user:777"])
    listing = run(_collect(not_a_place.scopes()))[0]
    assert listing.skipped_reason == "unsupported_kind"

    with pytest.raises(ValueError, match="not a rid"):
        run(_collect(TelegramArchiveSource(FakeClient(), only=["deploys"]).scopes()))


def test_a_bot_identity_lists_only_what_live_events_will_fill():
    source = TelegramArchiveSource(FakeClient(), only=[ALERTS], mode="bot")
    listings = run(_collect(source.scopes()))
    assert [(listing.rid, listing.visible, listing.skipped_reason) for listing in listings] == [(ALERTS, False, "bot_live_only")]
    assert "bot_live_only" in SKIPPED_REASONS


async def _collect(iterator):
    return [item async for item in iterator]


# -- sync, resume, coverage ----------------------------------------------


def test_a_sync_archives_every_scope_and_walks_topics_through_reply_to(home):
    client = FakeClient()
    with archive_store.open_archive() as archive:
        report = run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=10))
        assert report.rows == 52 and report.status == "ok"
        assert len(ids_in(archive.connection, DEPLOYS)) == 40
        assert len(ids_in(archive.connection, ALERTS)) == 7
        checkpoint = archive.checkpoint(DEPLOYS, IDENTITY.id)
        assert Cursor.decode(checkpoint.cursor) == Cursor(40, 1, True)
        assert checkpoint.newest_id == "40" and checkpoint.oldest_id == "1"
        coverage = {row.rid: row for row in archive.coverage()}
        assert coverage[DEPLOYS].synced_from == "2026-09-01T00:01:00Z"
        assert coverage[DEPLOYS].synced_to == "2026-09-01T00:40:00Z"
        author = archive.connection.execute("SELECT label, username FROM authors").fetchone()
        assert tuple(author) == ("Harry", "harry")
        row = archive.connection.execute("SELECT reply_to, platform_json FROM messages WHERE rid = ? LIMIT 1", (DEPLOYS,)).fetchone()
        assert row["reply_to"] is None, "a reply to the topic's own root is structure, not a reply"
        assert json.loads(row["platform_json"])["topic_id"] == 141
    topic_pages = [page for page in client.pages if page["scope"] == (FORUM_ID, 141)]
    assert topic_pages and all(page["min_id"] == 0 for page in topic_pages)
    # The store's own files are private, like everything else under the root.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(home / ".telegram-tools" / "archive.sqlite") + suffix)
        if candidate.exists():
            assert candidate.stat().st_mode & 0o077 == 0, f"{candidate.name} is readable by others"
    assert (home / ".telegram-tools" / "config.json").stat().st_mode & 0o077 == 0


def test_a_sync_killed_mid_scope_and_resumed_yields_the_same_rows(home, tmp_path):
    """The P3 fixture on this tool: the interrupted sync ends up byte-equal to the clean one."""
    clean_home = tmp_path / "clean"
    with archive_store.open_archive(clean_home) as clean:
        run(clean.sync(TelegramArchiveSource(FakeClient()), IDENTITY, batch=10))
        whole = ids_in(clean.connection)

    with archive_store.open_archive(home) as killed:
        with pytest.raises(KeyboardInterrupt):
            run(killed.sync(TelegramArchiveSource(FakeClient(fail_after=25, fail_scope=(FORUM_ID, 141))), IDENTITY, batch=10))
        part = ids_in(killed.connection)
        assert 0 < len(part) < len(whole) and len(part) % 10 == 0
        stored = Cursor.decode(killed.checkpoint(DEPLOYS, IDENTITY.id).cursor)
        assert stored is not None and not stored.done and stored.top == 40

        client = FakeClient()
        resumed = run(killed.sync(TelegramArchiveSource(client), IDENTITY, batch=10))
        assert sorted(ids_in(killed.connection)) == sorted(whole)
        assert resumed.rows == len(whole) - len(part), "the resume fetched only what was missing"
        first_page = next(page for page in client.pages if page["scope"] == (FORUM_ID, 141))
        assert first_page["min_id"] == 40, "a resume first asks for what arrived above the old top"
        assert Cursor.decode(killed.checkpoint(DEPLOYS, IDENTITY.id).cursor) == Cursor(40, 1, True)


def test_a_second_sync_fetches_only_what_is_new_and_keeps_the_walk_done(home):
    with archive_store.open_archive() as archive:
        run(archive.sync(TelegramArchiveSource(FakeClient()), IDENTITY, batch=10))
        client = FakeClient()
        again = run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=10))
        assert again.rows == 0
        assert all(page["min_id"] for page in client.pages), "a done scope is asked only for what is above its top"

        client = FakeClient()
        client.rows[(FORUM_ID, 141)] = history(3, start=41, topic=141) + client.rows[(FORUM_ID, 141)]
        newer = run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=10))
        assert newer.rows == 3
        assert Cursor.decode(archive.checkpoint(DEPLOYS, IDENTITY.id).cursor) == Cursor(43, 1, True)
        assert len(ids_in(archive.connection, DEPLOYS)) == 43


def test_full_walks_from_the_top_again_and_since_bounds_the_walk(home):
    with archive_store.open_archive() as archive:
        run(archive.sync(TelegramArchiveSource(FakeClient()), IDENTITY, batch=10))
        client = FakeClient()
        full = run(archive.sync(TelegramArchiveSource(client), IDENTITY, batch=10, full=True))
        assert full.rows == 52 and len(ids_in(archive.connection)) == 52, "a full walk rewrites, never duplicates"
        assert all(page["min_id"] == 0 and page["offset_id"] is None for page in client.pages)

    with archive_store.open_archive(home / "since") as bounded:
        report = run(bounded.sync(TelegramArchiveSource(FakeClient()), IDENTITY, since="2026-09-01T00:30:00Z", batch=10))
        assert len(ids_in(bounded.connection, DEPLOYS)) == 11
        assert Cursor.decode(bounded.checkpoint(DEPLOYS, IDENTITY.id).cursor) == Cursor(40, 30, False), (
            "a walk stopped at a date floor is not done: the history below is still there for a later run"
        )
        # A plain sync afterwards continues below the floor and finishes the walk.
        run(bounded.sync(TelegramArchiveSource(FakeClient()), IDENTITY, batch=10))
        assert len(ids_in(bounded.connection, DEPLOYS)) == 40
        assert Cursor.decode(bounded.checkpoint(DEPLOYS, IDENTITY.id).cursor) == Cursor(40, 1, True)


def test_a_full_sync_marks_what_telegram_no_longer_has_as_deleted(run_cli, capsys, home):
    run_cli(["archive", "sync"], capsys=capsys)
    client = FakeClient()
    client.rows[(CHANNEL_ID, None)] = [row for row in client.rows[(CHANNEL_ID, None)] if row.id not in (303, 305)]
    code, out, _err, _fake = run_cli(["--json", "archive", "sync"], client=client, capsys=capsys)
    assert code == 0 and envelope_of(out)["result"]["deleted"] == {}, "an incremental sync cannot see a deletion"

    client = FakeClient()
    client.rows[(CHANNEL_ID, None)] = [row for row in client.rows[(CHANNEL_ID, None)] if row.id not in (303, 305)]
    code, out, err, _fake = run_cli(["--json", "archive", "sync", "--full"], client=client, capsys=capsys)
    assert code == 0 and envelope_of(out)["result"]["deleted"] == {ALERTS: 2}
    assert f"{ALERTS} Alerts: 2 marked deleted" in err
    with archive_store.open_archive() as archive:
        rows = archive.connection.execute(
            "SELECT message_id, deleted_at FROM messages WHERE rid = ? ORDER BY message_id", (ALERTS,)
        ).fetchall()
        assert [row["message_id"] for row in rows if row["deleted_at"]] == ["303", "305"], "the row and its text stay"
        assert len(rows) == 7
        hits = archive.search('"deploy"', scope=ALERTS)
        assert all(hit.message_id not in ("303", "305") for hit in hits), "a deleted row is out of a search by default"


def test_a_flood_wait_is_slept_counted_and_the_walk_continues_without_a_gap(home):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    client = FakeClient(flood={(CHANNEL_ID, None): 3})
    source = TelegramArchiveSource(client, sleep=sleep)
    with archive_store.open_archive() as archive:
        report = run(archive.sync(source, IDENTITY, batch=10))
        assert len(ids_in(archive.connection, ALERTS)) == 7
    assert report.status == "ok" and report.rows == 52
    assert slept == [3] and source.waited_ms == 3000


def test_a_flood_wait_over_the_limit_fails_that_scope_and_the_sync_moves_on(home):
    client = FakeClient(flood={(FORUM_ID, 141): 7200})
    source = TelegramArchiveSource(client, wait_limit=600)
    with archive_store.open_archive() as archive:
        report = run(archive.sync(source, IDENTITY, batch=10))
    assert report.status == "partial"
    assert [scope.rid for scope in report.failed] == [DEPLOYS]
    assert "7200" in report.failed[0].error
    assert source.waited_ms == 0
    assert report.rows == 12, "the scopes after the rate-limited one still synced"
    assert f"{DEPLOYS}\tDeploys\trate_limited (" in archive_store.format_coverage(report)


# -- the commands ----------------------------------------------------------


def test_archive_sync_under_as_bot_refuses_before_anything_connects(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda **_: (_ for _ in ()).throw(AssertionError("config was read")))
    for argv in (["--json", "--as-bot", "alerts", "archive", "sync"], ["--json", "--as-bot", "alerts", "search", "--archive", "--chat", "x"]):
        code, out, _err, fake = run_cli(argv, capsys=capsys)
        assert code == 2
        envelope = envelope_of(out)
        assert envelope["error"]["code"] == "IDENTITY_MODE_UNSUPPORTED"
        assert "--as-bot" not in envelope["error"]["hint"]
        assert not fake.pages and not fake.disconnected


def test_archive_sync_from_the_command_line_prints_progress_and_coverage(run_cli, capsys, home):
    code, out, err, fake = run_cli(["archive", "sync"], capsys=capsys)
    assert code == 0
    assert out.startswith("Acting as: Sven (@sven) · account")
    assert f"{DEPLOYS} Deploys: 40 rows" in out
    assert "Coverage" in out and "3 scope(s), 52 rows, 0 skipped, 0 failed" in out
    assert fake.disconnected

    code, out, err, _fake = run_cli(["--json", "archive", "sync", "--scope", "tg:chat:-1009999999999"], capsys=capsys)
    envelope = envelope_of(out)
    assert code == 0 and envelope["status"] == "empty"
    assert envelope["result"]["skipped"] == [{"rid": "tg:chat:-1009999999999", "reason": "no_access"}]
    assert envelope["meta"]["waited_ms"] == 0
    assert "skipped (no_access)" in err


def test_archive_sync_reports_waited_ms_in_the_envelope(run_cli, capsys, monkeypatch):
    async def sleep(_seconds):
        return None

    monkeypatch.setattr(cli.asyncio, "sleep", sleep)
    code, out, _err, _fake = run_cli(["--json", "archive", "sync"], client=FakeClient(flood={(CHANNEL_ID, None): 2}), capsys=capsys)
    envelope = envelope_of(out)
    assert code == 0 and envelope["meta"]["waited_ms"] == 2000
    assert envelope["result"]["waited_ms"] == 2000


def test_a_partial_sync_exits_1(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "archive", "sync"], client=FakeClient(flood={(FORUM_ID, 141): 9999}), capsys=capsys)
    assert code == 1 and envelope_of(out)["status"] == "partial"
    code, out, _err, _fake = run_cli(["--json", "archive", "status"], capsys=capsys)
    assert envelope_of(out)["result"]["coverage"]["reasons"] == {"rate_limited": 1}


def test_archive_status_opens_no_connection_and_prints_no_path(run_cli, capsys, home):
    run_cli(["archive", "sync"], capsys=capsys)
    code, out, _err, fake = run_cli(["--json", "archive", "status"], capsys=capsys)
    envelope = envelope_of(out)
    assert code == 0
    assert envelope["result"]["messages"] == 52 and envelope["result"]["scopes"] == 3
    assert envelope["result"]["fts5"] is True
    assert envelope["result"]["budgets"][0]["budget"] == "archive_max_bytes"
    assert "path" not in envelope["result"] and str(home) not in out
    assert not fake.pages, "status reads the file; it asks Telegram for nothing"

    code, out, _err, _fake = run_cli(["archive", "status"], capsys=capsys)
    assert code == 0 and "messages  52" in out and "FTS5 available" in out


def test_archive_search_and_all_five_export_formats_agree_on_ids_and_order(run_cli, capsys, home):
    """The P3 fixture on this tool's command line."""
    run_cli(["archive", "sync"], capsys=capsys)
    code, out, _err, _fake = run_cli(["--json", "archive", "search", "--query", "deploy", "--limit", "20"], capsys=capsys)
    envelope = envelope_of(out)
    assert code == 0 and envelope["status"] == "ok"
    searched = [(row["rid"], row["message_id"]) for row in envelope["result"]["messages"]]
    assert 0 < len(searched) <= 20
    assert all("«" in row["highlight"] for row in envelope["result"]["messages"])

    exports = home / ".telegram-tools" / "exports"
    seen = {}
    for fmt in ("json", "csv", "jsonl", "markdown", "html"):
        code, out, _err, _fake = run_cli(
            ["--json", "archive", "export", "--query", "deploy", "--limit", "20", "--format", fmt, "--output", f"deploys.{fmt}"],
            capsys=capsys,
        )
        result = envelope_of(out)["result"]
        assert code == 0 and result["matched"] == len(searched)
        path = Path(result["output"])
        assert path.parent == exports, "a relative name lands in the exports directory"
        assert path.stat().st_mode & 0o077 == 0
        seen[fmt] = _ids_from(path, fmt)
    for fmt, ids in seen.items():
        assert ids == searched, f"{fmt} disagrees with the search"
    assert "<script" not in (exports / "deploys.html").read_text(encoding="utf-8")


def _ids_from(path: Path, fmt: str) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        return [(row["rid"], row["message_id"]) for row in json.loads(text)]
    if fmt == "jsonl":
        return [(row["rid"], row["message_id"]) for row in map(json.loads, text.splitlines())]
    if fmt == "csv":
        return [(row["rid"], row["message_id"]) for row in csv.DictReader(text.splitlines())]
    if fmt == "markdown":
        ids, rid = [], None
        for line in text.splitlines():
            heading = re.match(r"^## .*\((tg:[^)]+)\)$", line) or re.match(r"^## (tg:\S+)$", line)
            if heading:
                rid = heading.group(1)
            elif line.startswith("| ") and not line.startswith("| id") and not line.startswith("| ---"):
                ids.append((rid, line.split("|")[1].strip()))
        return ids

    class Rows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            found = dict(attrs)
            if tag == "tr" and "data-message-id" in found:
                self.ids.append((found["data-rid"], found["data-message-id"]))

    parser = Rows()
    parser.feed(text)
    return parser.ids


def test_archive_search_flags_reach_the_store(run_cli, capsys, home):
    run_cli(["archive", "sync"], capsys=capsys)
    code, out, _err, _fake = run_cli(
        ["--json", "archive", "search", "--query", "deploy", "--scope", ALERTS, "--from", "tg:user:777", "--regex", r"\d{3}$", "--context", "1", "--limit", "3"],
        capsys=capsys,
    )
    result = envelope_of(out)["result"]
    assert code == 0
    assert result["matched"] == 3
    assert {row["rid"] for row in result["messages"]} == {ALERTS}
    assert all(row["context_before"] or row["context_after"] for row in result["messages"])

    code, out, _err, _fake = run_cli(["archive", "search", "--query", "deploy", "--context", "1", "--limit", "1"], capsys=capsys)
    assert code == 0 and "«deploy»" in out and "\n    " in out, "a hit line, then its context indented under it"

    code, out, _err, _fake = run_cli(["--json", "archive", "search", "--query", "nothinghere"], capsys=capsys)
    assert code == 0 and envelope_of(out)["status"] == "empty"


def test_search_archive_is_the_live_flags_answered_offline(run_cli, capsys, home):
    run_cli(["archive", "sync"], capsys=capsys)
    code, out, _err, fake = run_cli(
        ["--json", "search", "--archive", "--chat", "@teamhermes", "--topic", "141", "--keyword", "rollback", "--from-user", "harry", "--limit", "5"],
        capsys=capsys,
    )
    envelope = envelope_of(out)
    assert code == 0 and envelope["result"]["archive"] is True
    assert {row["rid"] for row in envelope["result"]["messages"]} == {DEPLOYS}
    assert all("rollback" in row["text"] for row in envelope["result"]["messages"])
    assert not fake.pages, "search --archive asks Telegram for nothing"

    code, out, _err, _fake = run_cli(["--json", "search", "--archive", "--chat", str(FORUM_ID), "--keyword", "deploy", "--format", "html", "--output", str(home / "live.html")], capsys=capsys)
    assert code == 0 and (home / "live.html").exists()

    code, out, _err, _fake = run_cli(["--json", "search", "--archive", "--chat", "@nobody", "--keyword", "x"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_NOT_FOUND"

    code, _out, err, _fake = run_cli(["search", "--archive", "--chat", str(FORUM_ID)], capsys=capsys)
    assert code == 2 and "needs --keyword" in err


def test_retention_and_forget_dry_run_by_default_and_gate_on_the_typed_title(run_cli, capsys, home):
    run_cli(["archive", "sync"], capsys=capsys)
    audit = home / ".telegram-tools" / "audit.jsonl"

    code, out, _err, _fake = run_cli(["--json", "archive", "retention", "--scope", DEPLOYS, "--keep", "10"], capsys=capsys)
    envelope = envelope_of(out)
    assert code == 0 and envelope["status"] == "dry_run"
    assert envelope["result"]["executed"] is False
    assert envelope["plan"]["approval"] == "typed_name"
    assert not audit.exists()

    code, out, _err, _fake = run_cli(["--json", "archive", "retention", "--scope", DEPLOYS, "--keep", "10", "--execute"], capsys=capsys, isatty=True, answer="Support")
    envelope = envelope_of(out)
    assert code == 1 and envelope["status"] == "cancelled"
    with archive_store.open_archive() as archive:
        assert len(ids_in(archive.connection, DEPLOYS)) == 40

    code, out, _err, _fake = run_cli(["--json", "archive", "retention", "--scope", DEPLOYS, "--keep", "10", "--execute"], capsys=capsys, isatty=True, answer="Deploys")
    envelope = envelope_of(out)
    assert code == 0 and envelope["status"] == "ok"
    assert envelope["result"]["remaining"] == 10 and envelope["result"]["executed"] is True
    assert envelope["evidence"]["readback"].startswith("10 message(s) remain")
    lines = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [line["command"] for line in lines] == ["archive retention"]
    assert lines[0]["approval"] == "typed_name"

    code, out, _err, _fake = run_cli(["--json", "archive", "forget", "--scope", ALERTS, "--execute"], capsys=capsys)
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED", "no tty, no --yes: forget never runs unattended"

    code, out, _err, _fake = run_cli(["--json", "archive", "forget", "--scope", ALERTS, "--execute"], capsys=capsys, isatty=True, answer="alerts")
    assert code == 0 and envelope_of(out)["result"]["remaining"] == 0
    with archive_store.open_archive() as archive:
        assert archive.scope_target(ALERTS) is None
    assert len(audit.read_text().splitlines()) == 2

    code, out, _err, _fake = run_cli(["--json", "archive", "forget", "--scope", "tg:chat:-100404"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_NOT_FOUND"

    # An identity is forgotten under its label, re-read from its own row after the gate.
    code, out, _err, _fake = run_cli(["--json", "archive", "forget", "--identity", IDENTITY.id, "--execute"], capsys=capsys, isatty=True, answer="Sven (@sven)")
    envelope = envelope_of(out)
    assert code == 0 and envelope["result"]["remaining"] == 0 and envelope["target"]["rid"] == IDENTITY.id
    with archive_store.open_archive() as archive:
        assert archive.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert len(audit.read_text().splitlines()) == 3


def test_a_coded_refusal_from_the_store_is_an_exit_2_in_both_modes(run_cli, capsys, home, monkeypatch):
    monkeypatch.setattr("telegram_tools._core.archive.fts5_available", lambda connection=None: False)
    code, out, _err, _fake = run_cli(["--json", "archive", "status"], capsys=capsys)
    envelope = envelope_of(out)
    assert code == 2 and envelope["error"]["code"] == "ARCHIVE_UNAVAILABLE"
    code, _out, err, _fake = run_cli(["archive", "status"], capsys=capsys)
    assert code == 2 and "without FTS5" in err and "hint:" in err


def test_a_shared_exports_directory_is_not_a_reason_to_refuse_a_sync(run_cli, capsys, home):
    """Sven's machine keeps exports/ at 0755: that is where a shared file lands, and nothing secret is."""
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "exports").mkdir(mode=0o755)
    (root / "exports" / "old.json").write_text("[]\n")
    (root / "exports" / "old.json").chmod(0o644)
    code, out, _err, _fake = run_cli(["--json", "archive", "sync"], capsys=capsys)
    assert code == 0 and envelope_of(out)["status"] == "ok"
    code, out, _err, _fake = run_cli(["--json", "doctor"], capsys=capsys)
    modes = next(check for check in envelope_of(out)["result"]["checks"] if "private" in check["message"] or "readable" in check["message"])
    assert modes["status"] == "OK", modes


def test_archive_writes_refuse_while_the_tools_files_are_loose(run_cli, capsys, home):
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, exist_ok=True)
    (root / ".env").write_text("x=1\n")
    (root / ".env").chmod(0o644)
    code, out, _err, fake = run_cli(["--json", "archive", "sync"], capsys=capsys)
    assert code == 2 and envelope_of(out)["error"]["code"] == "CONFIG_INVALID"
    assert not fake.pages
    code, _out, _err, _fake = run_cli(["--json", "archive", "status"], capsys=capsys)
    assert code == 0, "a read still works, so doctor can be run and read"


# -- the live search's new formats ----------------------------------------


LIVE = [
    {"id": 2, "chat_id": FORUM_ID, "topic_id": 141, "date": "2026-09-01T00:02:00+00:00", "sender_id": 777, "sender_username": "harry", "reply_to_msg_id": 141, "reply_to_top_id": None, "has_media": False, "text": "deploy finished 💻"},
    {"id": 1, "chat_id": FORUM_ID, "topic_id": 141, "date": "2026-09-01T00:01:00+00:00", "sender_id": 777, "sender_username": None, "reply_to_msg_id": None, "reply_to_top_id": None, "has_media": True, "text": "photo"},
]


def test_search_json_and_csv_exports_are_byte_for_byte_what_they_were(tmp_path):
    write_records(LIVE, tmp_path / "out.json", "json")
    write_records(LIVE, tmp_path / "out.csv", "csv")
    assert (tmp_path / "out.json").read_bytes() == (json.dumps(LIVE, indent=2, default=str, ensure_ascii=False) + "\n").encode("utf-8")
    assert (tmp_path / "out.csv").read_bytes() == (
        b"id,chat_id,topic_id,date,sender_id,sender_username,reply_to_msg_id,reply_to_top_id,has_media,text\r\n"
        b"2,-1001000000001,141,2026-09-01T00:02:00+00:00,777,harry,141,,False,deploy finished \xf0\x9f\x92\xbb\r\n"
        b"1,-1001000000001,141,2026-09-01T00:01:00+00:00,777,,,,True,photo\r\n"
    )


def test_search_gains_jsonl_markdown_and_html_over_the_same_records(tmp_path):
    write_records(LIVE, tmp_path / "out.jsonl", "jsonl")
    write_records(LIVE, tmp_path / "out.md", "markdown", query="deploy", chat_title="Team Hermes")
    write_records(LIVE, tmp_path / "out.html", "html", query="deploy", chat_title="Team Hermes")
    assert [json.loads(line)["id"] for line in (tmp_path / "out.jsonl").read_text().splitlines()] == [2, 1]
    markdown = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert f"## Team Hermes ({DEPLOYS})" in markdown and "| 2 |" in markdown and "📎" in markdown
    html = (tmp_path / "out.html").read_text(encoding="utf-8")
    assert f'data-rid="{DEPLOYS}" data-message-id="2"' in html and "<script" not in html
    assert _ids_from(tmp_path / "out.html", "html") == [(DEPLOYS, "2"), (DEPLOYS, "1")]


def test_the_live_search_refuses_a_file_format_without_an_output(run_cli, capsys):
    for fmt in ("csv", "markdown", "html", "jsonl"):
        code, _out, err, _fake = run_cli(["search", "--chat", str(CHANNEL_ID), "--format", fmt], capsys=capsys)
        assert code == 2 and f"--output is required for {fmt.upper()} export" in err


def test_no_archive_output_carries_a_phone_number_or_a_path(run_cli, capsys, home):
    _code, out, err, _fake = run_cli(["archive", "sync"], capsys=capsys)
    _code, out2, err2, _fake = run_cli(["--json", "archive", "status"], capsys=capsys)
    for text in (out, err, out2, err2):
        assert "35699001122" not in text and str(home) not in text and ".session" not in text


# -- doctor ---------------------------------------------------------------


def test_doctor_reports_fts5_the_archive_rows_and_the_budget(run_cli, capsys, home):
    from telegram_tools.doctor import run_doctor

    code, out, _err, _fake = run_cli(["--json", "doctor"], capsys=capsys)
    lines = [check["message"] for check in envelope_of(out)["result"]["checks"]]
    assert any(line.startswith("Archive search: SQLite") and "has FTS5" in line for line in lines)
    assert "No archive yet (run `telegram-tools archive sync` to make one)" in lines

    run_cli(["archive", "sync"], capsys=capsys)
    code, out, _err, _fake = run_cli(["--json", "doctor"], capsys=capsys)
    envelope = envelope_of(out)
    archive_line = next(check["message"] for check in envelope["result"]["checks"] if check["message"].startswith("Archive:"))
    assert "52 message(s) in 3 scope(s)" in archive_line
    for budget, limit in (("archive_max_bytes", "2.0 GiB"), ("media_max_bytes", "5.0 GiB"), ("quarantine_max_bytes", "1.0 GiB")):
        assert f"{budget} " in archive_line and limit in archive_line, budget
    assert str(home) not in out

    # A file that is not a database is reported, not raised.
    (home / ".telegram-tools" / "archive.sqlite").write_bytes(b"not a database at all, just bytes")
    code = run_doctor(root=home, env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}, version_info=(3, 11, 0), home=home)
    assert code == 1
    assert "The archive could not be read" in capsys.readouterr().out
