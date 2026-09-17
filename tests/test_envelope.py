"""The machine surface: one envelope per run, and nothing else on stdout.

These are the public acceptance checks for this tool's `--json`: every command
an outsider can run against a fake client produces an envelope that validates
against the shared schema, carries no secret the redaction fixture can find,
and exits with the code the shared table gives its status. Human mode is
checked here too, by its absence -- the same commands without the flag print
what they always printed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import UserNotParticipantError
from telethon.tl.types import ChatBannedRights, InputPeerUser, PeerChannel, User

from telegram_tools import cli
from telegram_tools._core import redaction
from telegram_tools._core.contract import validate_envelope
from telegram_tools.adapters.account import RIGHT_NAMES
from telegram_tools.envelope import Reporter
from test_adapters import admin, creator, member

ACCOUNT = SimpleNamespace(id=42, first_name="Sven", username="sven")
CHAT_ID = -1001234567890


def dialog(chat_id=CHAT_ID, title="Agency", username="agency"):
    entity = SimpleNamespace(
        id=abs(chat_id), title=title, username=username, megagroup=True, forum=False, broadcast=False
    )
    return SimpleNamespace(id=chat_id, title=title, entity=entity, input_entity=SimpleNamespace(channel_id=abs(chat_id)))


class FakeClient:
    """Enough Telethon for one command: who I am, what I can see, what I may do."""

    def __init__(self, dialogs=None, rights=None):
        self.dialogs = list(dialogs if dialogs is not None else [dialog()])
        # What Telethon returns, or the exception it raises instead.
        self.rights = rights if rights is not None else creator()
        self.sent = []
        self.disconnected = False

    async def get_me(self):
        return ACCOUNT

    async def iter_dialogs(self):
        for item in self.dialogs:
            yield item

    async def get_permissions(self, _entity, _user):
        if isinstance(self.rights, Exception):
            raise self.rights
        return self.rights

    async def get_entity(self, reference):
        for item in self.dialogs:
            if item.id == reference or getattr(item.entity, "username", None) == reference:
                return item.entity
        raise LookupError(reference)

    async def get_input_entity(self, entity):
        return SimpleNamespace(channel_id=getattr(entity, "id", 0))

    async def get_peer_id(self, entity):
        return -1000000000000 - getattr(entity, "id", 0)

    async def send_message(self, peer, text, reply_to=None):
        self.sent.append((peer, text, reply_to))
        return SimpleNamespace(id=9001)

    async def get_messages(self, _peer, ids=None):
        return SimpleNamespace(id=ids)

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine of its own: config, session and audit log all under tmp_path."""
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("TELEGRAM_TOOLS_SESSION", str(tmp_path / "session"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` against a fake client; gives back the exit code and both streams."""

    def run(argv, client=None, capsys=None, isatty=False, answer=""):
        fake = client or FakeClient()
        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", _started(fake))
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            # Under --json a gate reads the terminal directly, not input(), so
            # the stand-in needs a readline as well as an isatty.
            SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{answer}\n"),
        )
        code = cli.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run


def _started(fake):
    async def start_client(_client, *, authorize=True):
        # Mirrors the real signature: machine mode connects without offering a login.
        return fake

    return start_client


def envelope_of(out: str) -> dict:
    envelope = json.loads(out)
    problems = validate_envelope(envelope)
    assert problems == [], problems
    assert not redaction.find(out), redaction.find(out)
    return envelope


# -- the acceptance fixtures ----------------------------------------------


def test_doctor_under_json_is_one_valid_envelope(run_cli, capsys):
    code, out, err, _fake = run_cli(["--json", "doctor"], capsys=capsys)

    envelope = envelope_of(out)
    assert envelope["command"] == "doctor"
    # doctor runs before any login, so it names neither an identity nor a target.
    assert envelope["identity"] is None and envelope["target"] is None
    assert envelope["result"]["failed"] == 0
    assert (code, envelope["status"]) == (0, "ok")
    # Under --json stdout is the envelope alone, so the readable report moves over.
    assert "OK   Python version is supported" in err


def test_a_failed_doctor_check_still_exits_1(run_cli, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_API_ID")

    code, out, _err, _fake = run_cli(["--json", "doctor"], capsys=capsys)

    envelope = envelope_of(out)
    # 1 for a failed check is what doctor has always answered; `partial` carries it.
    assert (code, envelope["status"]) == (1, "partial")
    assert envelope["result"]["failed"] == 1


def test_discover_under_json_is_one_valid_envelope(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--json", "discover"], capsys=capsys)

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["identity"]["id"] == "tg:user:42"
    assert envelope["identity"]["label"] == "Sven (@sven)"
    assert [chat["id"] for chat in envelope["result"]["chats"]] == [CHAT_ID]


def test_send_under_json_carries_plan_evidence_and_an_audit_line(run_cli, home, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    code, out, _err, fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["target"]["rid"] == f"tg:chat:{CHAT_ID}"
    assert envelope["plan"]["approval"] == "yes_allowlist"
    assert envelope["plan"]["preflight"] == {
        "required": ["send_messages"],
        # A creator holds every right the preflight can name.
        "held": sorted(RIGHT_NAMES),
        "missing": [],
    }
    assert envelope["evidence"]["readback"] == "message 9001 is in Agency"
    assert envelope["result"]["sent"] is True
    assert fake.sent == [(fake.dialogs[0].input_entity, "ship it", None)]

    lines = (home / ".telegram-tools" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["command"] == "send"
    assert line["targets"] == [f"tg:chat:{CHAT_ID}"]
    assert line["plan_id"] == envelope["plan"]["plan_id"]
    assert line["approval"] == "yes_allowlist"
    assert not redaction.find(lines[0])


def test_an_unallowlisted_send_refuses_by_code(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "NOT_ALLOWLISTED"
    assert fake.sent == []


def test_a_gate_with_no_terminal_refuses_with_the_human_command(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it"], capsys=capsys, isatty=False
    )

    envelope = envelope_of(out)
    assert envelope["error"]["code"] == "APPROVAL_REQUIRED"
    # 3 is the one new exit code, and it is only ever reachable under --json.
    assert code == 3
    assert envelope["error"]["hint"] == f"telegram-tools send --chat {CHAT_ID} --text ship it"
    assert fake.sent == []


def test_a_target_renamed_after_the_gate_refuses_as_drift(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    class RenamingClient(FakeClient):
        """The chat is renamed between the plan being built and the send going out.

        That window -- after a person read the preview, before the message
        leaves -- is the whole reason the plan is re-derived.
        """

        resolutions = 0

        async def iter_dialogs(self):
            RenamingClient.resolutions += 1
            if RenamingClient.resolutions > 1:
                self.dialogs[0].title = "Agency (archived)"
                self.dialogs[0].entity.title = "Agency (archived)"
            for item in self.dialogs:
                yield item

    fake = RenamingClient()

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"],
        client=fake,
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "PLAN_DRIFT"
    assert fake.sent == []


def test_a_missing_right_refuses_before_the_send(run_cli, capsys):
    fake = FakeClient(rights=member("send_messages"))

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "PERMISSION_DENIED"
    assert "send_messages" in envelope["error"]["message"]
    assert fake.sent == []


def test_a_right_telegram_will_not_report_warns_instead_of_refusing(run_cli, monkeypatch, capsys):
    # Telegram answers no participant for an account outside a public chat.
    # Refusing there would break sends this tool has always made, so it says
    # so and proceeds, and Telegram decides.
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    fake = FakeClient(rights=UserNotParticipantError(None))

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["plan"]["preflight"]["missing"] == ["send_messages"]
    assert any("could not confirm send_messages" in warning for warning in envelope["warnings"])
    assert fake.sent


@pytest.mark.parametrize(
    "rights",
    [creator(), admin(), member()],
    ids=["creator", "admin", "member"],
)
def test_an_ordinary_send_confirms_its_right_and_warns_nothing(run_cli, monkeypatch, capsys, rights):
    # What Telethon really returns names no send right: the preflight reads it
    # off the participant and the chat, so a send anyone may make says nothing.
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, _err, fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=FakeClient(rights=rights), capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["warnings"] == []
    assert "send_messages" in envelope["plan"]["preflight"]["held"]
    assert envelope["plan"]["preflight"]["missing"] == []
    assert fake.sent


def test_a_send_the_chats_defaults_forbid_is_refused_by_name(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    fake = FakeClient(rights=member())
    fake.dialogs[0].entity.default_banned_rights = ChatBannedRights(until_date=None, send_messages=True)

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (2, "PERMISSION_DENIED")
    assert "send_messages" in envelope["error"]["message"]
    assert fake.sent == []


def test_a_direct_chat_send_asks_for_no_rights_and_warns_nothing(run_cli, monkeypatch, capsys):
    harry = SimpleNamespace(
        id=777,
        title="Harry",
        entity=User(id=777, first_name="Harry", username="harry"),
        input_entity=InputPeerUser(user_id=777, access_hash=1),
    )

    class DirectClient(FakeClient):
        async def get_permissions(self, _entity, _user):
            # What Telethon does for a person: ValueError, after a round trip.
            raise ValueError("You must pass either a channel or a chat")

    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", "777")
    fake = DirectClient([harry])

    code, out, err, _fake = run_cli(["--json", "send", "--chat", "777", "--text", "hi", "--yes"], client=fake, capsys=capsys)

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok"), out
    assert envelope["warnings"] == []
    assert envelope["plan"]["preflight"]["missing"] == []
    assert "ValueError" not in out + err
    assert fake.sent == [(harry.input_entity, "hi", None)]


def test_jsonl_streams_records_then_the_envelope(run_cli, capsys):
    code, out, _err, _fake = run_cli(["--jsonl", "discover"], capsys=capsys)

    lines = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert lines[0]["id"] == CHAT_ID
    assert lines[-1]["kind"] == "envelope"
    assert validate_envelope({key: value for key, value in lines[-1].items() if key != "kind"}) == []


# -- what did not change ---------------------------------------------------


def test_human_mode_prints_no_envelope(run_cli, capsys):
    code, out, _err, _fake = run_cli(["discover"], capsys=capsys)

    assert code == 0
    assert "Chat ID: -1001234567890" in out
    assert "cli-tools/envelope" not in out


def test_a_json_path_still_writes_the_file_it_always_wrote(run_cli, tmp_path, capsys):
    destination = tmp_path / "exports" / "chats.json"

    code, out, _err, _fake = run_cli(["discover", "--json", str(destination)], capsys=capsys)

    assert code == 0
    assert out == ""
    assert json.loads(destination.read_text(encoding="utf-8"))[0]["id"] == CHAT_ID


def test_a_bare_json_on_the_subcommand_means_the_envelope(run_cli, capsys):
    code, out, _err, _fake = run_cli(["discover", "--json"], capsys=capsys)

    assert code == 0
    assert envelope_of(out)["command"] == "discover"


def test_the_menu_never_builds_a_machine_reporter():
    # The menu builds its own namespaces and passes no reporter, so whatever a
    # flag did on the command line, a menu run stays on the human path.
    assert Reporter().machine is False


def test_an_exit_code_means_the_same_thing_it_did(run_cli, capsys):
    # The one guarantee a script that reads only the exit code depends on.
    from telegram_tools._core.contract import exit_code

    assert [exit_code(status) for status in ("ok", "empty", "dry_run")] == [0, 0, 0]
    assert [exit_code(status) for status in ("cancelled", "partial")] == [1, 1]
    assert [exit_code(status) for status in ("refused", "failed")] == [2, 2]
    assert exit_code("failed", "INTERRUPTED") == 130
    assert exit_code("refused", "APPROVAL_REQUIRED") == 3


# -- the destructive commands ---------------------------------------------


class ForumClient(FakeClient):
    """A forum group with one topic, and the raw requests those paths make."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dialogs[0].entity.forum = True
        self.requests = []
        self.messages = [SimpleNamespace(id=500), SimpleNamespace(id=501)]

    async def __call__(self, request):
        self.requests.append(type(request).__name__)
        name = type(request).__name__
        if name in ("GetForumTopicsRequest", "GetForumTopicsByIDRequest"):
            return SimpleNamespace(
                topics=[SimpleNamespace(id=141, title="Deploys", top_message=140, icon_emoji_id=None)], count=1
            )
        if name == "CreateChannelRequest":
            # A real peer, because create.py asks Telethon to turn it into the
            # -100… id a caller gets back, and Telethon will not cast a stand-in.
            return SimpleNamespace(chats=[PeerChannel(999)])
        if name == "CreateForumTopicRequest":
            return SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=777))])
        return SimpleNamespace()

    async def iter_messages(self, _peer, reply_to=None, wait_time=None):
        for message in self.messages:
            yield message

    async def delete_messages(self, _peer, ids):
        self.messages = [m for m in self.messages if m.id not in ids]
        return len(ids)


def test_a_clear_messages_dry_run_names_its_topics_and_changes_nothing(run_cli, capsys):
    fake = ForumClient()

    code, out, _err, _fake = run_cli(
        ["--json", "clear-messages", "--chat", str(CHAT_ID), "--topic", "141"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    # A dry run is done, not undone: exit 0, and nothing was deleted.
    assert (code, envelope["status"]) == (0, "dry_run")
    assert envelope["plan"]["approval"] == "typed_delete"
    assert envelope["result"] == {
        "matched": 2,
        "cleared": 0,
        "dry_run": True,
        "cancelled": False,
        "topics": [{"id": 141, "title": "Deploys", "matched": 2}],
    }
    assert envelope["evidence"] is None
    assert len(fake.messages) == 2


def test_a_delete_dry_run_names_the_target_it_would_remove(run_cli, capsys):
    code, out, _err, _fake = run_cli(
        ["--json", "delete", "group", "--chat", str(CHAT_ID)], client=ForumClient(), capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "dry_run")
    assert envelope["command"] == "delete group"
    assert envelope["target"]["rid"] == f"tg:chat:{CHAT_ID}"
    assert envelope["plan"]["approval"] == "typed_name"


def test_delete_execute_with_no_terminal_refuses_before_it_asks(run_cli, capsys):
    fake = ForumClient()

    code, out, _err, _fake = run_cli(
        ["--json", "delete", "group", "--chat", str(CHAT_ID), "--execute"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["error"]["code"]) == (3, "APPROVAL_REQUIRED")
    assert "DeleteChannelRequest" not in fake.requests


def test_delete_refuses_a_kind_the_chat_is_not(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["--json", "delete", "channel", "--chat", str(CHAT_ID)], client=ForumClient(), capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "TARGET_KIND_MISMATCH"
    assert envelope["error"]["hint"] == f"telegram-tools delete group --chat {CHAT_ID}"


def test_create_under_json_reads_the_new_chat_back(run_cli, home, capsys):
    fake = ForumClient()
    fake.dialogs.append(dialog(chat_id=-1000000000999, title="Hermes", username=None))

    code, out, _err, _fake = run_cli(
        ["--json", "create", "group", "--title", "Hermes", "--forum", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["command"] == "create group"
    # Nothing existed to point a mutation at, so the plan names no target.
    assert envelope["target"] is None
    assert envelope["result"]["created"] is True
    assert envelope["evidence"]["readback"] == "group Hermes exists as -1000000000999"
    assert json.loads((home / ".telegram-tools" / "audit.jsonl").read_text())["command"] == "create group"


def test_an_executed_clear_reads_back_and_leaves_one_0600_audit_line(run_cli, home, capsys):
    fake = ForumClient()

    code, out, err, _fake = run_cli(
        ["--json", "clear-messages", "--chat", str(CHAT_ID), "--topic", "141", "--execute"],
        client=fake,
        capsys=capsys,
        isatty=True,
        answer="DELETE",
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["result"]["cleared"] == 2
    assert envelope["evidence"]["readback"] == "topic 141 now holds 0 message(s)"
    assert fake.messages == []
    # The scan lines a person reads are on stderr; stdout stayed one envelope.
    assert "Scanning topic 141" in err

    log = home / ".telegram-tools" / "audit.jsonl"
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert (line["command"], line["approval"]) == ("clear-messages", "typed_delete")
    assert line["targets"] == [f"tg:topic:{CHAT_ID}:141"]
    assert line["evidence"]["readback"] == envelope["evidence"]["readback"]


class CampaignForumClient(ForumClient):
    """The live campaign's forum: six topics, which Telegram serves most recent first.

    Both topic calls answer in that order whatever order the ids were asked in,
    so nothing here can pass by leaning on Telegram's order.
    """

    TOPICS = ((31, "campaign 4.5 rerun"), (30, "campaign 4.5"), (2, "1"), (6, "3"), (4, "2"), (1, "General"))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The opener is the topic's own id and is never cleared; General has none.
        self.threads = {31: [31], 30: [30], 2: [12, 11, 10, 2], 6: [21, 20, 6], 4: [4], 1: []}

    async def __call__(self, request):
        name = type(request).__name__
        if name not in ("GetForumTopicsRequest", "GetForumTopicsByIDRequest"):
            return await super().__call__(request)
        self.requests.append(name)
        asked = getattr(request, "topics", None)
        rows = [
            SimpleNamespace(id=topic_id, title=title, top_message=self.threads[topic_id][0] if self.threads[topic_id] else topic_id, icon_emoji_id=None)
            for topic_id, title in self.TOPICS
            if asked is None or topic_id in asked
        ]
        return SimpleNamespace(topics=rows, count=len(rows))

    async def iter_messages(self, _peer, reply_to=None, wait_time=None):
        for message_id in list(self.threads.get(reply_to, [])):
            yield SimpleNamespace(id=message_id)

    async def delete_messages(self, _peer, ids):
        for thread in self.threads.values():
            thread[:] = [message_id for message_id in thread if message_id not in ids]
        return len(ids)


def _scan_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Scanning topic")]


def test_an_all_topics_dry_run_scans_in_id_order_and_counts_each_topic(run_cli, capsys):
    # Live: the menu's tick screen listed 1, 2, 4, 6, 30, 31, the dry-run scanned
    # 31, 30, 2, 6, 4, 1 and said only "19 topic messages would be cleared".
    code, out, _err, _fake = run_cli(
        ["clear-messages", "--chat", str(CHAT_ID), "--all-topics"], client=CampaignForumClient(), capsys=capsys
    )

    assert code == 0
    assert _scan_lines(out) == [
        "Scanning topic 1 (General): 0 to clear",
        "Scanning topic 2 (1): 3 to clear",
        "Scanning topic 4 (2): 0 to clear",
        "Scanning topic 6 (3): 2 to clear",
        "Scanning topic 30 (campaign 4.5): 0 to clear",
        "Scanning topic 31 (campaign 4.5 rerun): 0 to clear",
    ]
    assert "Dry-run: 5 topic messages would be cleared" in out.splitlines()
    # A person read each topic's count on its own line; the JSON under it keeps
    # the four keys it has always had rather than repeating them.
    printed, _end = json.JSONDecoder().raw_decode(out, out.index("{"))
    assert printed == {"matched": 5, "cleared": 0, "dry_run": True, "cancelled": False}


def test_named_topics_are_scanned_in_id_order_too(run_cli, capsys):
    # The menu passes the ticked topics as --topic; Telegram answers them in its
    # own order, which put 30 before 2.
    code, out, _err, _fake = run_cli(
        ["clear-messages", "--chat", str(CHAT_ID), "--topic", "30", "--topic", "2"],
        client=CampaignForumClient(),
        capsys=capsys,
    )

    assert code == 0
    assert _scan_lines(out) == ["Scanning topic 2 (1): 3 to clear", "Scanning topic 30 (campaign 4.5): 0 to clear"]
    assert "Dry-run: 3 topic messages would be cleared" in out.splitlines()


def test_an_all_topics_dry_run_under_json_carries_a_row_per_topic(run_cli, capsys):
    code, out, err, _fake = run_cli(
        ["--json", "clear-messages", "--chat", str(CHAT_ID), "--all-topics"],
        client=CampaignForumClient(),
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "dry_run")
    result = envelope["result"]
    assert {key: result[key] for key in ("matched", "cleared", "dry_run", "cancelled")} == {
        "matched": 5,
        "cleared": 0,
        "dry_run": True,
        "cancelled": False,
    }
    assert result["topics"] == [
        {"id": 1, "title": "General", "matched": 0},
        {"id": 2, "title": "1", "matched": 3},
        {"id": 4, "title": "2", "matched": 0},
        {"id": 6, "title": "3", "matched": 2},
        {"id": 30, "title": "campaign 4.5", "matched": 0},
        {"id": 31, "title": "campaign 4.5 rerun", "matched": 0},
    ]
    assert sum(row["matched"] for row in result["topics"]) == result["matched"]
    # The words a person reads are on stderr, in the same order.
    assert [line.rsplit(": ", 1)[0] for line in _scan_lines(err)] == [
        f"Scanning topic {row['id']} ({row['title']})" for row in result["topics"]
    ]


def test_an_executed_all_topics_clear_rechecks_the_topics_it_showed(run_cli, home, capsys):
    # The plan a person approved lists the topics in id order; the recheck after
    # DELETE asks Telegram again, and Telegram answers in its own order. Unsorted,
    # that reads as a changed forum and refuses a clear nothing changed.
    fake = CampaignForumClient()

    code, out, _err, _fake = run_cli(
        ["--json", "clear-messages", "--chat", str(CHAT_ID), "--all-topics", "--execute"],
        client=fake,
        capsys=capsys,
        isatty=True,
        answer="DELETE",
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok"), envelope["error"]
    assert "GetForumTopicsByIDRequest" in fake.requests
    assert envelope["result"]["cleared"] == 5
    assert envelope["evidence"]["readback"] == (
        "topic 1 now holds 0 message(s); topic 2 now holds 1 message(s); topic 4 now holds 1 message(s); "
        "topic 6 now holds 1 message(s); topic 30 now holds 1 message(s); topic 31 now holds 1 message(s)"
    )
    line = json.loads((home / ".telegram-tools" / "audit.jsonl").read_text(encoding="utf-8").strip())
    assert line["targets"] == [f"tg:topic:{CHAT_ID}:{topic_id}" for topic_id in (1, 2, 4, 6, 30, 31)]


def test_a_cancelled_gate_leaves_no_audit_line(run_cli, home, capsys):
    fake = ForumClient()

    code, out, _err, _fake = run_cli(
        ["--json", "clear-messages", "--chat", str(CHAT_ID), "--topic", "141", "--execute"],
        client=fake,
        capsys=capsys,
        isatty=True,
        answer="no",
    )

    envelope = envelope_of(out)
    # Not done is exit 1, and nothing happened, so nothing is recorded.
    assert (code, envelope["status"]) == (1, "cancelled")
    assert len(fake.messages) == 2
    assert not (home / ".telegram-tools" / "audit.jsonl").exists()


def test_search_under_json_carries_the_rows_the_table_would_have_shown(run_cli, monkeypatch, capsys):
    async def fake_search(*_args, **_kwargs):
        return [{"id": 500, "text": "deploy is green", "chat_id": CHAT_ID}]

    monkeypatch.setattr(cli, "search_messages", fake_search)

    code, out, _err, _fake = run_cli(
        ["--json", "search", "--chat", str(CHAT_ID), "--contains", "deploy"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["result"]["matched"] == 1
    assert envelope["result"]["messages"][0]["id"] == 500
    assert envelope["result"]["output"] is None


def test_search_to_a_file_names_the_file_instead_of_repeating_it(run_cli, tmp_path, monkeypatch, capsys):
    async def fake_search(*_args, **_kwargs):
        return [{"id": 500, "text": "deploy is green"}]

    monkeypatch.setattr(cli, "search_messages", fake_search)
    destination = tmp_path / "out.json"

    code, out, _err, _fake = run_cli(
        ["--json", "search", "--chat", str(CHAT_ID), "--output", str(destination)], capsys=capsys
    )

    envelope = envelope_of(out)
    assert code == 0
    assert envelope["result"]["output"] == str(destination)
    assert "messages" not in envelope["result"]
    assert json.loads(destination.read_text())[0]["id"] == 500


def test_bots_under_json_lists_what_the_table_listed(run_cli, monkeypatch, capsys):
    from telegram_tools.models import BotInfo

    async def fake_list_bots(_client):
        return [BotInfo(id=12345, username="harrybot", name="Harry", bio=None, description=None, is_owned=True)]

    monkeypatch.setattr(cli, "list_bots", fake_list_bots)

    code, out, _err, _fake = run_cli(["--json", "bots"], capsys=capsys)

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["result"]["bots"][0]["id"] == 12345


def test_discover_orders_a_forums_topics_by_id_whatever_order_telegram_served(run_cli, monkeypatch, capsys):
    """Both surfaces sort; neither follows the server.

    Telegram serves a forum's topics most-recent-activity first, so the same
    chat read twice is a different list -- proven live, where one posted message
    moved a topic to the front between two `discover` runs. The table and the
    `--json` envelope come off the same list, and an agent's envelope is the
    half that has nobody to notice, so both are pinned here.
    """
    from telegram_tools import discovery
    from telegram_tools.models import TopicInfo

    forum = dialog(title="Agency")
    forum.entity.forum = True
    # Activity order, exactly as the live chat served it after a write.
    served = [
        TopicInfo(id=6, title="3", top_message=6),
        TopicInfo(id=2, title="1", top_message=2),
        TopicInfo(id=4, title="2", top_message=4),
        TopicInfo(id=1, title="General", top_message=1),
    ]

    async def fake_get_forum_topics(_client, _peer, **_kwargs):
        return list(served)

    monkeypatch.setattr(discovery, "get_forum_topics", fake_get_forum_topics)

    code, out, _err, _fake = run_cli(["discover"], client=FakeClient([forum]), capsys=capsys)
    assert code == 0
    rows = out.splitlines()
    start = rows.index("Topics") + 2
    assert rows[start:start + 4] == ["1  General", "2  1", "4  2", "6  3"]

    code, out, _err, _fake = run_cli(["--json", "discover"], client=FakeClient([forum]), capsys=capsys)
    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert [topic["id"] for topic in envelope["result"]["chats"][0]["topics"]] == [1, 2, 4, 6]
# -- the readback a person reads ------------------------------------------


class SilentReadbackClient(FakeClient):
    """Telegram accepted the write and will not say what it holds afterwards."""

    async def get_messages(self, _peer, ids=None):
        return None


def test_a_human_send_prints_the_readback_it_verified(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, _err, _fake = run_cli(
        ["send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    # The same sentence `--json` ships as evidence.readback, in the plain output.
    assert "Read back: message 9001 is in Agency" in out
    assert code == 0


def test_a_readback_that_failed_says_so_in_human_mode_and_still_exits_0(run_cli, home, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, _err, _fake = run_cli(
        ["send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"],
        client=SilentReadbackClient(),
        capsys=capsys,
    )

    # The doubt reaches the person, not only the envelope: the send happened, so
    # the run did what it was asked and the exit code stays 0. `unverified` is a
    # fact about how much could be confirmed, never a failure.
    assert "Read back: unverified: the sent message could not be read back (LookupError)" in out
    assert code == 0
    # What was done is its own sentence, above the doubt about it, and it still
    # names the id Telegram answered with: the JSON that used to say so is gone.
    assert f"Sent message 9001 to Agency ({CHAT_ID}).\nRead back: unverified:" in out
    assert "{" not in out

    line = json.loads((home / ".telegram-tools" / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["status"] == "ok"
    assert line["evidence"]["readback"].startswith("unverified:")


def test_the_readback_sentence_stays_off_stdout_under_json(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["evidence"]["readback"] == "message 9001 is in Agency"
    # stdout is one envelope; the sentence is in it, not printed beside it.
    assert "Read back:" not in out and "Read back:" not in err


# -- the warnings a person reads ------------------------------------------
#
# A warning is raised before a write's gate, which is the one moment it can
# change what the person answers. It prints where it is raised, on the stream
# the preview uses, so it always lands above the question -- and a dry run
# shows it too, because the dry run is where the decision to execute is made.

UNREPORTED = SimpleNamespace()  # a chat Telegram answers no rights for


def _warning_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("warning: ")]


def test_a_human_send_prints_its_warning_above_the_question(run_cli, capsys):
    code, out, _err, fake = run_cli(
        ["send", "--chat", str(CHAT_ID), "--text", "ship it"],
        client=FakeClient(rights=UNREPORTED),
        capsys=capsys,
        answer="y",
    )

    assert code == 0 and fake.sent
    [warning] = _warning_lines(out)
    assert "could not confirm send_messages" in warning
    # Above the preview and the question, below the banner that names the account.
    assert out.index("Acting as:") < out.index(warning) < out.index("ship it") < out.index("Send it? [y/N]")
    # Read before anything was sent, so it cannot say the write already happened.
    assert "was attempted" not in warning


def test_the_envelope_carries_the_same_warning_and_the_gate_reader_sees_it(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"],
        client=FakeClient(rights=UNREPORTED),
        capsys=capsys,
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    [carried] = envelope["warnings"]
    # stdout is still one envelope; the words a person would read are on
    # stderr, where the banner and a gate's preview already go under --json.
    assert _warning_lines(err) == [f"warning: {carried}"]


def test_the_human_line_and_the_envelope_say_the_same_thing(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    argv = ["send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"]

    _code, human, _err, _fake = run_cli(argv, client=FakeClient(rights=UNREPORTED), capsys=capsys)
    _code, machine, _err, _fake = run_cli(["--json", *argv], client=FakeClient(rights=UNREPORTED), capsys=capsys)

    assert _warning_lines(human) == [f"warning: {text}" for text in envelope_of(machine)["warnings"]]


def test_a_dry_run_shows_the_warning_the_real_run_would_raise(run_cli, capsys):
    code, out, _err, _fake = run_cli(
        ["delete", "group", "--chat", str(CHAT_ID)], client=FakeClient(rights=UNREPORTED), capsys=capsys
    )

    assert code == 0
    [warning] = _warning_lines(out)
    assert "could not confirm is_creator" in warning
    assert out.index(warning) < out.index("Dry-run:")


def test_a_run_with_no_warnings_prints_no_warning_line_and_no_heading(run_cli, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))

    code, out, err, _fake = run_cli(
        ["send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys
    )
    assert code == 0
    assert "warning" not in out.lower() and "warning" not in err.lower()

    code, out, err, _fake = run_cli(["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], capsys=capsys)
    assert envelope_of(out)["warnings"] == []
    assert "warning" not in err.lower()


def test_a_warning_raised_twice_prints_once():
    printed = []
    report = Reporter(stdout=SimpleNamespace(write=printed.append, flush=lambda: None))

    report.warn("the rules do not load: bad file")
    report.warn("the rules do not load: bad file")

    assert "".join(printed) == "warning: the rules do not load: bad file\n"
    assert report.envelope()["warnings"] == ["the rules do not load: bad file"]


def test_a_permission_answer_that_names_other_rights_is_not_called_no_answer(run_cli, capsys):
    # What Telethon really returns for a supergroup: a participant object that
    # answers is_creator, is_admin and the admin flags, and has no send_messages
    # field at all. Telegram did report permissions; it did not name this one.
    reported = SimpleNamespace(is_creator=False, is_admin=False, delete_messages=False)

    _code, out, _err, _fake = run_cli(
        ["send", "--chat", str(CHAT_ID), "--text", "ship it"], client=FakeClient(rights=reported), capsys=capsys, answer="n"
    )

    [warning] = _warning_lines(out)
    assert "reports no permissions" not in warning
    assert "send_messages" in warning


def test_the_menu_shows_a_warning_before_it_offers_the_real_row(home, monkeypatch, capsys):
    from test_menu import DELETE, FakeSession, run_menu, screens

    fake = FakeClient([dialog(chat_id=-100111, title="Hermes", username="hermes")], rights=UNREPORTED)
    session = FakeSession()

    async def connected():
        return fake

    session.client = connected

    async def runner(args, *, client=None, config=None):
        return await cli.run(args, client=client, config=config)

    # Delete > a group or channel > Forum groups > Hermes > dry run, then back out.
    code, _calls, output = run_menu([DELETE, "1", "1", "1", "0", "0", "0", "0", "0"], session=session, runner=runner)

    assert code == 0
    printed = capsys.readouterr().out
    [warning] = _warning_lines(printed)
    assert "could not confirm is_creator" in warning
    assert printed.index(warning) < printed.index("Dry-run:")
    # The dry run ran and the real row was offered after it; nothing was deleted.
    assert "Delete it for real" in screens(output)
