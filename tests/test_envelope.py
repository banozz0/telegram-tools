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
from telethon.tl.types import PeerChannel

from telegram_tools import cli
from telegram_tools._core import redaction
from telegram_tools._core.contract import validate_envelope
from telegram_tools.envelope import Reporter

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
        self.rights = rights if rights is not None else SimpleNamespace(
            is_creator=True, is_admin=True, send_messages=True, delete_messages=True
        )
        self.sent = []
        self.disconnected = False

    async def get_me(self):
        return ACCOUNT

    async def iter_dialogs(self):
        for item in self.dialogs:
            yield item

    async def get_permissions(self, _entity, _user):
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

    def run(argv, client=None, capsys=None, isatty=False):
        fake = client or FakeClient()
        monkeypatch.setattr(cli, "create_client", lambda _config: fake)
        monkeypatch.setattr(cli, "start_client", _started(fake))
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: ""))
        code = cli.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run


def _started(fake):
    async def start_client(_client):
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
        "held": ["delete_messages", "is_admin", "is_creator", "send_messages"],
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
    fake = FakeClient(rights=SimpleNamespace(is_creator=False, is_admin=False, send_messages=False))

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (2, "refused")
    assert envelope["error"]["code"] == "PERMISSION_DENIED"
    assert "send_messages" in envelope["error"]["message"]
    assert fake.sent == []


def test_a_right_telegram_will_not_report_warns_instead_of_refusing(run_cli, monkeypatch, capsys):
    # A private chat has no participant permissions at all. Refusing there
    # would break sends this tool has always made, so it says so and proceeds.
    monkeypatch.setenv("TELEGRAM_SEND_ALLOWLIST", str(CHAT_ID))
    fake = FakeClient(rights=SimpleNamespace())

    code, out, _err, _fake = run_cli(
        ["--json", "send", "--chat", str(CHAT_ID), "--text", "ship it", "--yes"], client=fake, capsys=capsys
    )

    envelope = envelope_of(out)
    assert (code, envelope["status"]) == (0, "ok")
    assert envelope["plan"]["preflight"]["missing"] == ["send_messages"]
    assert any("could not confirm send_messages" in warning for warning in envelope["warnings"])
    assert fake.sent


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
    assert envelope["result"] == {"matched": 2, "cleared": 0, "dry_run": True, "cancelled": False}
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
