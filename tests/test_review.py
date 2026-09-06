"""The review queue on this tool: manifests from a sync, the two gates, the Telethon fetcher, and the P4 row.

Spec section 9 and the P4 row of section 21. Every run here is against a fake
account client under a socket guard: no session is opened, no socket, and the
store, quarantine and media directories live under a temporary home. The one
"network" is the fake: a client that serves history, re-reads a message and
streams a file from an offset with a kill switch, and a urllib opener over a
table of URLs that counts its requests -- zero until `review approve`.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import os
import socket
import stat
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    Photo,
    PhotoSize,
)

from telegram_tools import archive as archive_store
from telegram_tools import cli
from telegram_tools import doctor
from telegram_tools import review as review_ops
from telegram_tools._core.contract import validate_envelope
from telegram_tools._core.download import HttpFetcher
from telegram_tools._core.review import Candidate, ReviewError
from telegram_tools.adapters.archive import TelegramArchiveSource, scope_rid_for
from telegram_tools.adapters.media import TelegramMediaFetcher, candidates_of, links_of, media_locator
from test_archive_sync import ACCOUNT, CHANNEL_ID, FORUM_ID, HARRY, FakeClient, _dialog, envelope_of, home  # noqa: F401 - fixtures

ALERTS = scope_rid_for(str(CHANNEL_ID))
DEPLOYS = scope_rid_for(str(FORUM_ID), 141)
PAYLOAD = bytes(range(256)) * 40  # 10240 bytes, every byte value, so a skipped or doubled chunk changes the hash
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
URL = "https://files.example.invalid/report.bin"
MOVED = "https://cdn.example.invalid/report.bin"
PUBLIC = "93.184.216.34"


def run(coroutine):
    return asyncio.run(coroutine)


# -- the fakes ---------------------------------------------------------------


def document(number: int = 42, *, name: str | None = "report.bin", mime: str = "application/octet-stream", size: int = len(PAYLOAD)) -> Document:
    return Document(
        id=number,
        access_hash=7,
        file_reference=b"",
        date=datetime(2026, 9, 1, tzinfo=UTC),
        mime_type=mime,
        size=size,
        dc_id=2,
        attributes=[DocumentAttributeFilename(name)] if name else [],
        thumbs=None,
        video_thumbs=None,
    )


def photo(number: int = 99) -> Photo:
    return Photo(
        id=number,
        access_hash=8,
        file_reference=b"",
        date=datetime(2026, 9, 1, tzinfo=UTC),
        sizes=[PhotoSize(type="s", w=90, h=90, size=1200), PhotoSize(type="x", w=800, h=800, size=48000)],
        dc_id=2,
    )


def message(number: int, *, text: str = "", entities=(), media=None, topic: int | None = None):
    when = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=number)
    reply_to = SimpleNamespace(reply_to_msg_id=topic, reply_to_top_id=None, forum_topic=True) if topic else None
    return SimpleNamespace(
        id=number,
        date=when,
        message=text,
        raw_text=text,
        entities=list(entities),
        sender_id=HARRY.id,
        sender=HARRY,
        reply_to=reply_to,
        media=media,
        edit_date=None,
    )


LINK_TEXT = "the build is at https://files.example.invalid/report.bin now"
LINK_MESSAGE = message(300, text=LINK_TEXT, entities=[MessageEntityUrl(offset=LINK_TEXT.index("https"), length=len(URL))])
FILE_MESSAGE = message(301, text="", media=MessageMediaDocument(document=document()))


class ReviewClient(FakeClient):
    """The archive fake plus what a download needs: a message re-read, and bytes from an offset."""

    def __init__(self, *, fail_at: int | None = None, media_of=None, **kwargs):
        rows = kwargs.pop("rows", None) or {
            (CHANNEL_ID, None): [FILE_MESSAGE, LINK_MESSAGE],
            (FORUM_ID, 141): [message(10, text="deploy", topic=141)],
            (FORUM_ID, 217): [],
        }
        super().__init__(rows=rows, **kwargs)
        self.fail_at = fail_at
        self.media_of = media_of
        self.reads: list[tuple[int, int]] = []
        self.downloads: list[int] = []

    async def get_input_entity(self, entity):
        return SimpleNamespace(channel_id=entity.id, chat_id=-1000000000000 - entity.id)

    async def get_peer_id(self, entity):
        return -1000000000000 - entity.id

    async def get_messages(self, peer, ids=None):
        chat_id = getattr(peer, "chat_id", None)
        self.reads.append((chat_id, ids))
        for (row_chat, _topic), rows in self.rows.items():
            if row_chat != chat_id:
                continue
            for row in rows:
                if row.id == ids:
                    if self.media_of is not None:
                        return SimpleNamespace(**{**vars(row), "media": self.media_of(row)})
                    return row
        return None

    async def iter_download(self, media, *, offset=0, **_):
        self.downloads.append(int(offset))
        position = int(offset)
        while position < len(PAYLOAD):
            if self.fail_at is not None and position >= self.fail_at:
                raise ConnectionResetError("the connection dropped")
            piece = PAYLOAD[position : position + 1024]
            position += len(piece)
            yield piece


class FakeResponse:
    def __init__(self, status, headers, body=b""):
        self.status = status
        self.headers = headers
        self.body = body
        self.position = 0

    def read(self, count=-1):
        if count is None or count < 0:
            piece, self.position = self.body[self.position :], len(self.body)
        else:
            piece = self.body[self.position : self.position + count]
            self.position += len(piece)
        return piece

    def close(self):
        pass


def headers(**values):
    out = http.client.HTTPMessage()
    for key, value in values.items():
        out[key.replace("_", "-")] = str(value)
    return out


class FakeOpener:
    """urllib's opener over a table of URLs, counting every request it is asked for."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((request.get_method(), url))
        route = self.routes.get(url)
        if route is None:
            raise urllib.error.URLError("no route")
        status, head, body = route
        if status >= 300:
            raise urllib.error.HTTPError(url, status, "moved", head, None)
        if request.get_method() == "HEAD":
            return FakeResponse(status, head, b"")
        range_header = request.get_header("Range")
        if range_header:
            start = int(range_header.split("=")[1].rstrip("-"))
            return FakeResponse(206, headers(content_length=len(body) - start), body[start:])
        return FakeResponse(status, head, body)


class NoScanner:
    def scan(self, path):
        from telegram_tools._core.scanner import ScanResult

        return ScanResult("UNSCANNED", "no scanner on PATH: looked for clamdscan, clamscan", "clamav")

    def report(self):
        return {"scanner": "clamav", "binary": None, "command": None, "looked_for": ["clamdscan", "clamscan"]}


# -- fixtures ----------------------------------------------------------------


@pytest.fixture()
def no_network(monkeypatch):
    """Refuse every network socket; asyncio's own AF_UNIX self-pipe stays allowed."""
    real = socket.socket

    class Guarded(real):
        def __init__(self, family=-1, *args, **kwargs):
            if family in (socket.AF_INET, socket.AF_INET6):
                raise AssertionError("a network socket was opened")
            super().__init__(family, *args, **kwargs)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a network socket was opened")

    monkeypatch.setattr(socket, "socket", Guarded)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture()
def opener(monkeypatch, no_network):
    """The link fetcher over a table: one redirect, then the payload. Requests are counted."""
    fake = FakeOpener(
        {
            URL: (302, headers(location=MOVED), b""),
            MOVED: (200, headers(content_length=len(PAYLOAD), content_type="application/octet-stream"), PAYLOAD),
        }
    )
    monkeypatch.setattr(review_ops, "link_fetcher", lambda: HttpFetcher(fake))
    monkeypatch.setattr(review_ops, "resolver", lambda host, port: [PUBLIC])
    monkeypatch.setattr(review_ops, "scanner_factory", NoScanner)
    return fake


@pytest.fixture()
def run_cli(home, monkeypatch, opener):
    """`main(argv)` against the review fake, with a terminal or not, and answers in order."""

    def run_it(argv, *, client=None, capsys, isatty=False, answers=("y",)):
        fake = client or ReviewClient()
        remaining = list(answers)

        async def started(_client, *, authorize=True):
            return fake

        def readline():
            return (remaining.pop(0) if remaining else "") + "\n"

        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=readline))
        monkeypatch.setattr("builtins.input", lambda prompt="": readline().rstrip("\n"))
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run_it


def synced(run_cli, capsys, client=None):
    client = client or ReviewClient()
    code, out, _err, _fake = run_cli(["--json", "archive", "sync"], client=client, capsys=capsys)
    assert code == 0, out
    return client, envelope_of(out)


def queue_rows(home):
    with archive_store.open_archive() as archive:
        return {row.manifest_id: row for row in review_ops.queue_for(archive).list()}


def audit_lines(home):
    path = home / ".telegram-tools" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- what a sync notes -----------------------------------------------------


def test_links_are_read_as_written_with_utf16_offsets_and_text_urls():
    text = "🚀 see https://a.example/x and docs"
    url_at = text.index("https")
    utf16_offset = len(text[:url_at].encode("utf-16-le")) // 2
    msg = message(1, text=text, entities=[MessageEntityUrl(offset=utf16_offset, length=len("https://a.example/x")), MessageEntityTextUrl(offset=0, length=1, url="https://b.example/y")])

    assert links_of(msg) == ["https://a.example/x", "https://b.example/y"]


def test_a_message_becomes_its_links_then_its_file_and_a_preview_is_not_a_file():
    found = candidates_of(message(5, text=LINK_TEXT, entities=LINK_MESSAGE.entities, media=MessageMediaDocument(document=document())), ALERTS, "tg:user:777")
    assert [(item.kind, item.url or item.locator) for item in found] == [("link", URL), ("media", "document:42")]
    media = found[1]
    assert (media.claimed_type, media.claimed_size, media.display_name) == ("application/octet-stream", len(PAYLOAD), "report.bin")

    picture = candidates_of(message(6, media=MessageMediaPhoto(photo=photo())), ALERTS)[0]
    assert (picture.locator, picture.claimed_type, picture.claimed_size, picture.display_name) == ("photo:99", "image/jpeg", 48000, None)

    assert media_locator(MessageMediaWebPage(webpage=SimpleNamespace(url=URL))) is None
    assert candidates_of(message(7, media=MessageMediaWebPage(webpage=SimpleNamespace(url=URL))), ALERTS) == []


def test_a_media_candidate_never_carries_a_url():
    with pytest.raises(ReviewError, match="never fetch through one"):
        Candidate(kind="media", source_rid=ALERTS, source_message_id="1", locator="document:1", url=URL)


def test_a_sync_produces_manifests_and_fetches_nothing(run_cli, capsys, home, opener):
    client, envelope = synced(run_cli, capsys)

    assert envelope["result"]["manifests"] == {"link": 1, "media": 1}
    rows = queue_rows(home)
    assert {(row.kind, row.state) for row in rows.values()} == {("link", "queued"), ("media", "queued")}
    link = next(row for row in rows.values() if row.kind == "link")
    assert link.url == URL, "the URL exactly as the message wrote it"
    assert link.source_rid == ALERTS and link.source_message_id == "300" and link.sender == "Harry"
    assert client.downloads == [] and client.reads == [], "a sync reads history and nothing else"
    assert opener.requests == [], "no host was contacted"

    # A second sync serves only what is new, so it notes nothing; a full walk
    # serves everything again, and the same link in the same message is the same row.
    _client, again = synced(run_cli, capsys)
    assert again["result"]["manifests"] == {"link": 0, "media": 0}
    client = ReviewClient()
    code, out, _err, _fake = run_cli(["--json", "archive", "sync", "--full"], client=client, capsys=capsys)
    assert code == 0 and envelope_of(out)["result"]["manifests"] == {"link": 1, "media": 1}
    assert set(queue_rows(home)) == set(rows)


def test_archive_sync_prints_what_it_noted_in_human_mode(run_cli, capsys, home):
    code, out, _err, _fake = run_cli(["archive", "sync"], capsys=capsys)
    assert code == 0
    assert "1 link(s) and 1 file(s) noted for review; nothing was fetched" in out


# -- listing: no request ---------------------------------------------------


def test_review_list_shows_the_url_as_written_and_makes_no_request(run_cli, capsys, home, opener, monkeypatch):
    synced(run_cli, capsys)

    def never(_config):
        raise AssertionError("review list opened a client")

    monkeypatch.setattr(cli, "create_client", never)
    code, out, _err, _fake = run_cli(["--json", "review", "list"], capsys=capsys)
    assert code == 0
    envelope = envelope_of(out)
    assert envelope["result"]["count"] == 2
    assert {row["url"] for row in envelope["result"]["candidates"]} == {URL, None}
    assert opener.requests == [], "listing resolved no redirect and contacted no host"

    code, out, _err, _fake = run_cli(["--json", "review", "list", "--kind", "link", "--state", "queued"], capsys=capsys)
    assert [row["kind"] for row in envelope_of(out)["result"]["candidates"]] == ["link"]

    code, out, _err, _fake = run_cli(["review", "list"], capsys=capsys)
    assert "Review queue" in out and URL in out and "report.bin" in out
    assert "Nothing is fetched until you run review approve" in out


def test_review_status_reads_counts_budgets_and_the_scanner(run_cli, capsys, home):
    synced(run_cli, capsys)
    code, out, _err, _fake = run_cli(["--json", "review", "status"], capsys=capsys)
    assert code == 0
    result = envelope_of(out)["result"]
    assert result["states"]["queued"] == 2 and result["candidates"] == 2
    assert result["quarantine"]["downloads"] == 0
    assert result["scanner"]["binary"] is None and result["scanner"]["looked_for"] == ["clamdscan", "clamscan"]

    code, out, _err, _fake = run_cli(["review", "status"], capsys=capsys)
    assert "scanner     none on PATH (looked for clamdscan, clamscan); every verdict is UNSCANNED" in out
    assert "quarantine  0 download(s) held" in out


# -- the first gate ----------------------------------------------------------


def test_review_approve_without_a_terminal_exits_3_in_both_modes(run_cli, capsys, home, opener):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")

    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=False, answers=("y",))
    assert code == 3, out
    envelope = envelope_of(out)
    assert envelope["error"]["code"] == "APPROVAL_REQUIRED"
    assert envelope["error"]["hint"] == "telegram-tools review approve --ids " + link.manifest_id

    code, _out, err, _fake = run_cli(["review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=False, answers=("y",))
    assert code == 3, "a y piped into stdin is not a person at a terminal"
    assert "no terminal to ask on" in err

    assert queue_rows(home)[link.manifest_id].state == "queued", "nothing moved"
    assert opener.requests == [], "and nothing was contacted"


def test_a_declined_approve_moves_nothing_and_exits_1(run_cli, capsys, home, opener):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    code, out, err, _fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("n",))
    assert code == 1
    assert envelope_of(out)["status"] == "cancelled"
    assert "URL as written: " + URL in err, "the preview names the link as the message wrote it"
    assert queue_rows(home)[link.manifest_id].state == "queued"
    assert opener.requests == []


def test_approve_picks_at_the_terminal_when_no_ids_are_given(run_cli, capsys, home, opener):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    number = str([row.manifest_id for row in queue_rows(home).values()].index(link.manifest_id) + 1)
    # Tick the link, Continue (row 3 of two candidates plus Select all), then y.
    code, out, _err, fake = run_cli(["--json", "review", "approve"], capsys=capsys, isatty=True, answers=(number, "4", "y"))
    assert code == 0, out
    result = envelope_of(out)["result"]
    assert result["approved"] == [link.manifest_id]
    assert fake.downloads == [], "a link never goes through Telegram"


def test_an_unknown_id_is_target_not_found_and_a_wrong_state_is_refused(run_cli, capsys, home):
    synced(run_cli, capsys)
    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", "nope"], capsys=capsys, isatty=True)
    assert code == 2 and envelope_of(out)["error"]["code"] == "TARGET_NOT_FOUND"
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    code, out, _err, _fake = run_cli(["--json", "review", "accept", "--ids", link.manifest_id], capsys=capsys, isatty=True)
    error = envelope_of(out)["error"]
    assert code == 2 and error["code"] == "TARGET_KIND_MISMATCH" and "queued, not quarantined" in error["message"]


# -- approve continues into the fetch -----------------------------------------


def test_a_link_is_fetched_after_the_y_with_redirects_walked_only_then(run_cli, capsys, home, opener):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    assert opener.requests == []

    code, out, _err, fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 0, out
    envelope = envelope_of(out)
    (download,) = envelope["result"]["downloads"]
    assert (download["state"], download["verdict"], download["sha256"]) == ("quarantined", "UNSCANNED", DIGEST)
    assert download["redirect_chain"] == [MOVED] and download["final_url"] == MOVED
    assert [method for method, _url in opener.requests] == ["HEAD", "HEAD", "GET"]
    assert envelope["plan"]["approval"] == "prompt_y" and envelope["evidence"]["readback"].startswith("1 quarantined")
    assert fake.downloads == []

    row = queue_rows(home)[link.manifest_id]
    assert row.state == "quarantined" and row.verdict == "UNSCANNED"
    payload = home / ".telegram-tools" / "quarantine" / row.download_id / "payload"
    assert payload.read_bytes() == PAYLOAD
    assert stat.S_IMODE(payload.stat().st_mode) == 0o600 and stat.S_IMODE(payload.parent.stat().st_mode) == 0o700

    (line,) = audit_lines(home)
    assert line["command"] == "review approve" and line["status"] == "ok"

    code, out, _err, _fake = run_cli(["review", "status"], capsys=capsys)
    assert f"redirects={MOVED}" in out and "verdict=UNSCANNED" in out


def test_a_killed_media_download_resumes_to_the_same_sha256_through_telethon(run_cli, capsys, home, opener):
    client, _envelope = synced(run_cli, capsys)
    media = next(row for row in queue_rows(home).values() if row.kind == "media")
    kill_at = int(len(PAYLOAD) * 0.4)

    killed = ReviewClient(fail_at=kill_at)
    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", media.manifest_id], client=killed, capsys=capsys, isatty=True, answers=("y",))
    assert code == 1, "a fetch that did not finish is partial"
    envelope = envelope_of(out)
    (download,) = envelope["result"]["downloads"]
    assert download["state"] == "failed" and "ConnectionResetError" in download["error"]
    assert 0 < download["bytes_fetched"] < len(PAYLOAD)
    assert killed.reads == [(CHANNEL_ID, 301)], "the message was read again, not trusted from the row"
    assert killed.downloads == [0]
    on_disk = download["bytes_fetched"]
    row = queue_rows(home)[media.manifest_id]
    assert row.state == "failed" and row.resumable is True

    # No new approval: the human said yes once and the bytes are the same bytes.
    healthy = ReviewClient()
    code, out, _err, _fake = run_cli(["--json", "review", "retry", "--ids", media.manifest_id], client=healthy, capsys=capsys, isatty=False)
    assert code == 0, out
    (download,) = envelope_of(out)["result"]["downloads"]
    assert (download["state"], download["sha256"]) == ("quarantined", DIGEST)
    assert healthy.downloads == [on_disk], "iter_download started where the payload on disk ends"
    assert download["bytes_fetched"] == len(PAYLOAD)
    assert opener.requests == [], "platform media never travel through a URL"

    payload = home / ".telegram-tools" / "quarantine" / row.download_id / "payload"
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == DIGEST


def test_a_file_the_message_no_longer_carries_is_a_failed_download_not_another_file(run_cli, capsys, home):
    synced(run_cli, capsys)
    media = next(row for row in queue_rows(home).values() if row.kind == "media")
    swapped = ReviewClient(media_of=lambda _row: MessageMediaDocument(document=document(43)))
    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", media.manifest_id], client=swapped, capsys=capsys, isatty=True, answers=("y",))
    assert code == 1
    (download,) = envelope_of(out)["result"]["downloads"]
    assert download["state"] == "failed" and "no longer carries document:42" in download["error"] and "document:43" in download["error"]
    assert swapped.downloads == []


def test_the_fetcher_streams_from_the_offset_it_is_handed():
    client = ReviewClient()
    fetcher = TelegramMediaFetcher(client)
    manifest = {"kind": "media", "locator": "document:42", "source_rid": ALERTS, "source_message_id": "301"}

    async def collect():
        return b"".join([chunk async for chunk in fetcher.stream(manifest, 4096)])

    assert run(collect()) == PAYLOAD[4096:]
    assert client.downloads == [4096]

    async def wrong():
        return [chunk async for chunk in fetcher.stream({**manifest, "kind": "link", "locator": None}, 0)]

    with pytest.raises(LookupError, match="only a media manifest"):
        run(wrong())


# -- the second gate -----------------------------------------------------------


def approved_link(run_cli, capsys, home):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 0, out
    return queue_rows(home)[link.manifest_id]


def test_review_accept_shows_unscanned_with_no_scanner_and_moves_the_file(run_cli, capsys, home):
    row = approved_link(run_cli, capsys, home)

    code, out, _err, _fake = run_cli(["--json", "review", "accept", "--ids", row.manifest_id], capsys=capsys, isatty=False)
    assert code == 3 and envelope_of(out)["error"]["code"] == "APPROVAL_REQUIRED"

    code, out, err, _fake = run_cli(["--json", "review", "accept", "--ids", row.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 0, out
    envelope = envelope_of(out)
    assert "verdict: UNSCANNED (no scanner on PATH: looked for clamdscan, clamscan)" in err, "the verdict is shown before the question"
    (accepted,) = envelope["result"]["accepted"]
    assert accepted["verdict"] == "UNSCANNED" and accepted["sha256"] == DIGEST
    stored = home / ".telegram-tools" / accepted["storage_path"]
    assert stored.is_file() and stored.read_bytes() == PAYLOAD
    assert accepted["storage_path"] == f"media/{DIGEST[:2]}/{DIGEST}"
    assert not (home / ".telegram-tools" / "quarantine" / row.download_id).exists()
    assert queue_rows(home)[row.manifest_id].state == "accepted"
    assert [line["command"] for line in audit_lines(home)] == ["review approve", "review accept"]


def test_an_infected_verdict_is_refused_by_accept_and_cleared_by_reject(run_cli, capsys, home, monkeypatch, tmp_path):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")

    # A scanner on PATH that finds something: exit 1 and a FOUND line, as clamscan prints it.
    from telegram_tools._core.scanner import ClamAVAdapter

    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "clamscan"
    script.write_text("#!/bin/sh\necho \"$2: Eicar-Test-Signature FOUND\"\nexit 1\n")
    script.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(review_ops, "scanner_factory", ClamAVAdapter)

    code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 1, out
    (download,) = envelope_of(out)["result"]["downloads"]
    assert download["verdict"] == "INFECTED" and "Eicar-Test-Signature" in download["detail"]

    code, out, err, _fake = run_cli(["--json", "review", "accept", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 2
    envelope = envelope_of(out)
    assert envelope["error"]["code"] == "UNSAFE_BLOCKED" and "INFECTED" in envelope["error"]["message"]
    assert envelope["error"]["hint"] == f"review reject --ids {link.manifest_id}"
    assert "verdict: INFECTED" in err
    row = queue_rows(home)[link.manifest_id]
    assert row.state == "quarantined"
    assert (home / ".telegram-tools" / "quarantine" / row.download_id / "payload").exists()

    code, out, _err, _fake = run_cli(["--json", "review", "reject", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 0, out
    (rejected,) = envelope_of(out)["result"]["rejected"]
    assert rejected["freed_bytes"] > 0
    assert not (home / ".telegram-tools" / "quarantine" / row.download_id).exists()
    assert queue_rows(home)[link.manifest_id].state == "rejected"
    assert [line["command"] for line in audit_lines(home)] == ["review approve", "review reject"]


def test_reject_works_on_a_queued_candidate_and_asks_first(run_cli, capsys, home, opener):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    code, out, _err, _fake = run_cli(["--json", "review", "reject", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("n",))
    assert code == 1 and envelope_of(out)["status"] == "cancelled"
    code, out, _err, _fake = run_cli(["--json", "review", "reject", "--ids", link.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert code == 0
    assert queue_rows(home)[link.manifest_id].state == "rejected"
    assert opener.requests == []


# -- the boundaries ---------------------------------------------------------------


def test_review_refuses_under_as_bot_before_anything_connects(run_cli, capsys, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "alerts:98765:AAExampleBotModeTokenValueXYZ")

    def never(_config):
        raise AssertionError("a client was created")

    monkeypatch.setattr(cli, "create_client", never)
    code, out, _err, _fake = run_cli(["--json", "--as-bot", "alerts", "review", "list"], capsys=capsys)
    assert code == 2
    envelope = envelope_of(out)
    assert envelope["error"]["code"] == "IDENTITY_MODE_UNSUPPORTED"
    assert "reads no history" in envelope["error"]["message"]


def test_review_writes_refuse_while_the_tools_files_are_loose(run_cli, capsys, home):
    synced(run_cli, capsys)
    link = next(row for row in queue_rows(home).values() if row.kind == "link")
    os.chmod(home / ".telegram-tools", 0o755)
    try:
        code, out, _err, _fake = run_cli(["--json", "review", "approve", "--ids", link.manifest_id], capsys=capsys, isatty=True)
        assert code == 2 and envelope_of(out)["error"]["code"] == "CONFIG_INVALID"
        code, out, _err, _fake = run_cli(["--json", "review", "list"], capsys=capsys)
        assert code == 0, "reads are left alone"
    finally:
        os.chmod(home / ".telegram-tools", 0o700)


def test_no_review_output_carries_a_phone_number_or_an_absolute_path(run_cli, capsys, home):
    row = approved_link(run_cli, capsys, home)
    for argv in (["review", "list"], ["review", "status"], ["--json", "review", "list"], ["--json", "review", "status"]):
        _code, out, err, _fake = run_cli(argv, capsys=capsys)
        assert ACCOUNT.phone not in out + err
        assert str(home) not in out + err, argv
    code, out, _err, _fake = run_cli(["--json", "review", "accept", "--ids", row.manifest_id], capsys=capsys, isatty=True, answers=("y",))
    assert str(home) not in out


def test_doctor_reports_the_scanner_and_the_quarantine(run_cli, capsys, home, monkeypatch, tmp_path):
    bindir = tmp_path / "emptybin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    code, out, _err, _fake = run_cli(["doctor"], capsys=capsys)
    assert "WARN No scanner on PATH (looked for clamdscan, clamscan): every approved download is UNSCANNED" in out
    assert "OK   Quarantine: empty" in out

    approved_link(run_cli, capsys, home)
    code, out, _err, _fake = run_cli(["doctor"], capsys=capsys)
    assert "OK   Quarantine: 1 download(s) held, 11.4 KiB of 1.0 GiB" in out, "the payload and its manifest.json sidecar"

    found = doctor.check_scanner(SimpleNamespace(report=lambda: {"binary": "/usr/bin/clamdscan", "command": "clamdscan", "looked_for": ["clamdscan", "clamscan"]}))
    assert found.status == "OK" and "clamdscan found" in found.message


def test_the_menu_rows_build_the_same_namespaces_the_flags_do():
    # The Watch group builds `review <kind>` namespaces with no `yes` and no
    # `execute`; the pick and the y/N happen in the CLI, on the same terminal a
    # flag user sees. Pinned in tests/test_menu.py; here the flags it reaches.
    assert set(review_ops.kinds()) == {"link", "media"}
    assert "queued" in review_ops.states() and "accepted" in review_ops.states()
    assert review_ops.parse_ids(["a,b", " c ", "a"]) == ["a", "b", "c"]
