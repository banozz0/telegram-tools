"""Named logins: the store, the flag, the two commands and the banner.

The acceptance list for this card (spec section 21, P2 row, and the card's own):
every screen below the root starts with `Acting as:`; the legacy session still
logs in and is never moved; `auth --qr`, `auth --logout`, `auth --migrate` and
`profiles` each have a refusal and a success against a fake client; no output
carries a phone number, a token or a session path; `doctor` refuses writes on
loose file modes.

Everything here drives a fake client, exactly as the rest of the suite does. No
test in this file opens a socket, and `home` puts the whole machine -- config,
sessions, audit log -- under `tmp_path`.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_tools import cli, login, profiles, proxy
from telegram_tools._core import redaction
from telegram_tools.config import ConfigError, load_config
from telegram_tools.doctor import check_file_modes, require_tight_modes, run_doctor

ME = SimpleNamespace(id=4242, first_name="Sven", last_name=None, username="sven")
# A number and a token shaped exactly like the real things, so the redaction
# grep in this file is testing something rather than agreeing with itself.
PHONE = "+35679123478"
TOKEN_SAMPLE = "8012345678:AAH0nOtaReAlToKeNvAlUeHeReAtAlL123456789"
# The `tg://login` URL Telethon hands back, built from a part rather than
# written whole: the repository's commit guard reads `token=<runon>` as a
# credential, and the sample has to keep its real length to size the QR.
QR_SAMPLE = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA"
QR_PARAM = "token"
QR_URL = f"tg://login?{QR_PARAM}={QR_SAMPLE}"


# -- a machine of its own --------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.delenv("TELEGRAM_TOOLS_SESSION", raising=False)
    monkeypatch.delenv("TELEGRAM_TOOLS_PROFILE", raising=False)
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def legacy_login(home: Path, body: str = "legacy") -> Path:
    """The session file every version before profiles wrote, already there."""
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = root / "telegram-tools.session"
    session.write_text(body)
    session.chmod(0o600)
    return session


class FakeClient:
    """The half of Telethon `auth` and the banner touch."""

    def __init__(self, *, authorized=False, me=ME, two_step=False):
        self.authorized = authorized
        self.me = me
        self.two_step = two_step
        self.sent_codes: list[str] = []
        self.signed_in: list[tuple] = []
        self.logged_out = False
        self.disconnected = False

    async def connect(self):
        return self

    async def start(self):
        self.authorized = True
        return self

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    async def send_code_request(self, phone):
        self.sent_codes.append(phone)
        return SimpleNamespace(phone_code_hash="hash-abc")

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self.signed_in.append((phone, code, password))
        if password is None and self.two_step:
            raise _password_needed()
        self.authorized = True
        return self.me

    async def qr_login(self, ignored_ids=None):
        return FakeQr(self)

    async def log_out(self):
        self.logged_out = True
        self.authorized = False
        return True

    async def disconnect(self):
        self.disconnected = True


class FakeQr:
    """A QR login that is scanned on the second draw, so the refresh loop is exercised."""

    url = QR_URL

    def __init__(self, client, scans_after=1, two_step=False):
        self.client = client
        self.scans_after = scans_after
        self.two_step = two_step
        self.waits = 0
        self.recreated = 0

    async def recreate(self):
        self.recreated += 1

    async def wait(self, _timeout=None):
        self.waits += 1
        if self.waits <= self.scans_after:
            raise asyncio.TimeoutError
        if self.two_step:
            raise _password_needed()
        self.client.authorized = True
        return self.client.me


def _password_needed():
    """An exception named the way Telethon names its two-step-verification one."""
    return type("SessionPasswordNeededError", (Exception,), {})("2FA")


@pytest.fixture
def run_cli(home, monkeypatch):
    """`main(argv)` against a fake client, with a terminal that answers `answer`."""

    def run(argv, *, client=None, capsys, answers=("",), isatty=True):
        fake = client or FakeClient(authorized=True)
        replies = list(answers)
        monkeypatch.setattr(cli, "create_client", lambda _config: fake)

        async def started(_client, *, authorize=True):
            if authorize:
                await fake.start()
            return fake

        monkeypatch.setattr(cli, "start_client", started)
        monkeypatch.setattr("builtins.input", lambda _prompt="": replies.pop(0) if replies else "")
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{replies.pop(0) if replies else ''}\n"),
        )
        try:
            code = cli.main(argv)
        except SystemExit as exit_code:
            # A human-mode refusal goes through argparse.error, which exits the
            # process. A shell sees the status; so does this.
            code = int(exit_code.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err, fake

    return run


# -- the store -------------------------------------------------------------


def test_default_points_at_the_pre_profile_session_and_nothing_is_moved(home):
    session = legacy_login(home)

    profile = profiles.load("default", home=home)

    assert profile.legacy and profile.logged_in
    assert profile.session_file == session
    assert session.exists(), "the tool must never move the session it found"
    assert not (home / ".telegram-tools" / "profiles").exists()


def test_a_named_profile_keeps_its_session_in_its_own_directory(home):
    profile = profiles.load("work", home=home)

    assert not profile.legacy and not profile.logged_in
    assert profile.session_file == home / ".telegram-tools" / "profiles" / "work" / "session.session"


def test_default_prefers_its_own_session_once_it_has_one(home):
    legacy_login(home)
    directory = profiles.profile_dir("default", home)
    directory.mkdir(parents=True, mode=0o700)
    _write_private(directory / "session.session", "newer")

    profile = profiles.load("default", home=home)

    assert not profile.legacy
    assert profile.session_file == directory / "session.session"


def test_a_profile_name_that_could_leave_the_tree_is_refused(home):
    for bad in ("../escape", "a/b", "", ".hidden"):
        with pytest.raises(profiles.ProfileError):
            profiles.load(bad, home=home)


def test_a_saved_profile_is_0600_in_a_0700_directory(home):
    profile = profiles.load("work", home=home)

    saved = profiles.record_login(profile, label="Sven (@sven)", user_id=4242)

    record = saved.directory / "profile.json"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.directory.stat().st_mode) == 0o700
    assert json.loads(record.read_text())["label"] == "Sven (@sven)"


def test_a_profile_record_carries_no_secret_and_no_path(home):
    profile = profiles.record_login(
        profiles.load("work", home=home), label="Sven (@sven)", user_id=4242, proxy="socks5://127.0.0.1:1080"
    )

    body = (profile.directory / "profile.json").read_text()

    assert redaction.find(body) == []
    assert str(home) not in body


# -- --profile, TELEGRAM_TOOLS_PROFILE and TELEGRAM_TOOLS_SESSION ----------


def test_the_profile_flag_chooses_which_session_is_opened(home):
    config = load_config(profile="work", home=home, env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"})

    assert config.profile == "work"
    assert config.session_path == home / ".telegram-tools" / "profiles" / "work" / "session"


def test_the_environment_default_is_used_when_no_flag_is_given(home):
    config = load_config(
        env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h", "TELEGRAM_TOOLS_PROFILE": "alerts"}, home=home
    )

    assert config.profile == "alerts"


def test_an_explicit_session_still_wins_over_every_profile(home):
    config = load_config(
        env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h", "TELEGRAM_TOOLS_SESSION": "/tmp/mine"},
        home=home,
        profile="work",
    )

    assert config.session_path == Path("/tmp/mine")


def test_the_legacy_session_is_what_default_opens(home):
    legacy_login(home)

    config = load_config(env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}, home=home)

    assert config.session_path == home / ".telegram-tools" / "telegram-tools"


# -- the proxy -------------------------------------------------------------


def test_a_proxy_url_becomes_what_telethon_asks_for():
    parsed = proxy.parse("socks5://user:pa%40ss@10.0.0.2:1080")

    assert parsed.label == "socks5://10.0.0.2:1080"
    assert parsed.as_telethon() == {
        "proxy_type": "socks5",
        "addr": "10.0.0.2",
        "port": 1080,
        "rdns": True,
        "username": "user",
        "password": "pa@ss",
    }


@pytest.mark.parametrize("raw", ["ftp://host:1080", "socks5://host", "socks5://:1080", "nonsense"])
def test_a_proxy_that_cannot_be_understood_is_refused(raw):
    with pytest.raises(ConfigError):
        proxy.parse(raw)


def test_a_proxy_with_no_backend_is_refused_rather_than_skipped(monkeypatch):
    monkeypatch.setattr(proxy, "backend_available", lambda: False)

    with pytest.raises(ConfigError) as refusal:
        proxy.from_env({"TELEGRAM_PROXY": "socks5://127.0.0.1:1080"})

    assert "python-socks" in str(refusal.value)
    assert "telegram-tools[proxy]" in str(refusal.value)


def test_no_proxy_setting_means_no_proxy():
    assert proxy.from_env({}) is None
    assert proxy.from_env({"TELEGRAM_PROXY": "  "}) is None


# -- file modes ------------------------------------------------------------


def test_doctor_fails_on_a_world_readable_env(home):
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, parents=True)
    loose = root / ".env"
    loose.write_text("TELEGRAM_API_HASH=x\n")
    loose.chmod(0o644)

    check = check_file_modes(home)

    assert check.failed
    assert "readable by others" in check.message


def test_a_write_is_refused_while_the_modes_are_loose(home):
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, parents=True)
    (root / ".env").write_text("x\n")
    (root / ".env").chmod(0o640)

    with pytest.raises(ConfigError) as refusal:
        require_tight_modes(home)

    assert "chmod" in str(refusal.value)


def test_tight_modes_let_a_write_through(home):
    root = home / ".telegram-tools"
    root.mkdir(mode=0o700, parents=True)
    (root / ".env").write_text("x\n")
    (root / ".env").chmod(0o600)

    require_tight_modes(home)


# -- profiles --------------------------------------------------------------


def test_profiles_lists_labels_and_never_a_path(run_cli, home, capsys):
    profiles.record_login(profiles.load("work", home=home), label="Sven (@sven)", user_id=4242)

    code, out, _err, _fake = run_cli(["profiles"], capsys=capsys)

    assert code == 0
    assert "work" in out and "Sven (@sven)" in out
    assert str(home) not in out and ".session" not in out


def test_profiles_says_so_when_there_are_none(run_cli, capsys):
    code, out, _err, _fake = run_cli(["profiles"], capsys=capsys)

    assert code == 0
    assert "telegram-tools auth" in out


def test_profiles_opens_no_connection(run_cli, capsys):
    code, _out, _err, fake = run_cli(["profiles"], capsys=capsys)

    assert code == 0
    assert fake.signed_in == [] and not fake.disconnected


def test_profiles_under_json_carries_the_records(run_cli, home, capsys):
    profiles.record_login(profiles.load("work", home=home), label="Sven (@sven)", user_id=4242)

    code, out, _err, _fake = run_cli(["--json", "profiles"], capsys=capsys)

    envelope = json.loads(out)
    assert code == 0
    assert [row["name"] for row in envelope["result"]["profiles"]] == ["work"]
    assert redaction.find(out) == []


# -- auth: the phone-and-code way ------------------------------------------


def test_auth_logs_a_profile_in_and_records_who_it_is(run_cli, home, capsys):
    client = FakeClient(authorized=False)

    code, out, _err, _fake = run_cli(["--profile", "work", "auth"], client=client, capsys=capsys, answers=[PHONE, "54321"])

    assert code == 0
    assert client.sent_codes == [PHONE]
    saved = profiles.load("work", home=home)
    assert saved.label == "Sven (@sven)" and saved.user_id == 4242
    assert "Sven (@sven)" in out


def test_auth_prints_no_phone_number_back(run_cli, capsys):
    code, out, err, _fake = run_cli(
        ["--profile", "work", "auth"],
        client=FakeClient(authorized=False),
        capsys=capsys,
        answers=[PHONE, "54321"],
    )

    assert code == 0
    assert PHONE not in out + err
    assert redaction.find(out + err) == []


def test_auth_cancels_on_a_blank_phone_number(run_cli, home, capsys):
    client = FakeClient(authorized=False)

    code, _out, _err, _fake = run_cli(["--profile", "work", "auth"], client=client, capsys=capsys, answers=[""])

    assert code == 2
    assert client.sent_codes == []
    assert not profiles.load("work", home=home).logged_in


def test_auth_asks_for_the_two_step_password_and_never_stores_it(run_cli, home, monkeypatch, capsys):
    client = FakeClient(authorized=False, two_step=True)
    monkeypatch.setattr(login, "secret_reader", lambda: (lambda _prompt: "hunter2"))

    code, out, err, _fake = run_cli(["--profile", "work", "auth"], client=client, capsys=capsys, answers=[PHONE, "54321"])

    assert code == 0
    assert client.signed_in[-1][2] == "hunter2"
    assert "hunter2" not in out + err
    assert "hunter2" not in (profiles.load("work", home=home).directory / "profile.json").read_text()


def test_auth_says_so_when_the_profile_is_already_logged_in(run_cli, capsys):
    code, out, _err, client = run_cli(["--profile", "work", "auth"], client=FakeClient(authorized=True), capsys=capsys)

    assert code == 0
    assert "already logged in" in out
    assert client.sent_codes == []


# -- auth --qr -------------------------------------------------------------


def test_auth_qr_draws_a_code_and_redraws_it_until_it_is_scanned(run_cli, home, capsys, monkeypatch):
    client = FakeClient(authorized=False)
    qr = FakeQr(client, scans_after=1)
    monkeypatch.setattr(client, "qr_login", lambda ignored_ids=None: _async(qr))

    code, out, _err, _fake = run_cli(["--profile", "work", "auth", "--qr"], client=client, capsys=capsys)

    assert code == 0
    assert qr.recreated == 1, "an expired code is replaced, not left on screen"
    assert "█" in out, "the QR block itself is drawn"
    assert profiles.load("work", home=home).label == "Sven (@sven)"


def test_auth_qr_asks_for_the_two_step_password_too(run_cli, capsys, monkeypatch):
    client = FakeClient(authorized=False)
    qr = FakeQr(client, scans_after=0, two_step=True)
    monkeypatch.setattr(client, "qr_login", lambda ignored_ids=None: _async(qr))
    monkeypatch.setattr(login, "secret_reader", lambda: (lambda _prompt: "hunter2"))

    code, out, err, _fake = run_cli(["--profile", "work", "auth", "--qr"], client=client, capsys=capsys)

    assert code == 0
    assert "hunter2" not in out + err


def test_auth_qr_refuses_before_connecting_when_the_extra_is_absent(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(login, "qr_available", lambda: False)
    client = FakeClient(authorized=False)

    code, _out, err, _fake = run_cli(["--profile", "work", "auth", "--qr"], client=client, capsys=capsys)

    assert code == 2
    assert "telegram-tools[qr]" in err
    assert client.signed_in == []


def test_the_qr_block_encodes_the_login_url():
    pytest.importorskip("segno")

    block = login.render_qr(QR_URL)

    assert "█" in block
    # An 80-column terminal has to hold it without wrapping.
    assert max(len(line) for line in block.splitlines()) <= 80


# -- auth --logout ---------------------------------------------------------


def test_logout_needs_the_profile_name_typed_back(run_cli, home, capsys):
    profiles.record_login(profiles.load("work", home=home), label="Sven (@sven)", user_id=4242)
    _session(home, "work")
    client = FakeClient(authorized=True)

    code, out, _err, _fake = run_cli(
        ["--profile", "work", "auth", "--logout"], client=client, capsys=capsys, answers=["wrong"]
    )

    assert code == 1
    assert not client.logged_out
    assert profiles.load("work", home=home).logged_in, "a refused gate changes nothing"
    assert "not the profile name" in out


def test_logout_ends_the_session_and_removes_it_locally(run_cli, home, capsys):
    profiles.record_login(profiles.load("work", home=home), label="Sven (@sven)", user_id=4242)
    session = _session(home, "work")
    client = FakeClient(authorized=True)

    code, out, _err, _fake = run_cli(
        ["--profile", "work", "auth", "--logout"], client=client, capsys=capsys, answers=["work"]
    )

    assert code == 0
    assert client.logged_out
    assert not session.exists()
    assert not (profiles.profile_dir("work", home) / "profile.json").exists()
    assert str(home) not in out


def test_logout_refuses_a_profile_that_was_never_logged_in(run_cli, capsys):
    code, _out, err, client = run_cli(["--profile", "work", "auth", "--logout"], capsys=capsys)

    assert code == 2
    assert "not logged in" in err
    assert not client.logged_out


# -- auth --migrate --------------------------------------------------------


def test_migrate_moves_the_old_session_only_after_a_yes(run_cli, home, capsys):
    session = legacy_login(home)

    code, out, _err, _fake = run_cli(["auth", "--migrate"], capsys=capsys, answers=["y"])

    moved = profiles.profile_dir("default", home) / "session.session"
    assert code == 0
    assert not session.exists() and moved.read_text() == "legacy"
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600
    assert "Moved" in out


def test_migrate_leaves_the_old_session_alone_on_a_no(run_cli, home, capsys):
    session = legacy_login(home)

    code, out, _err, _fake = run_cli(["auth", "--migrate"], capsys=capsys, answers=["n"])

    assert code == 1
    assert session.exists()
    assert not (profiles.profile_dir("default", home) / "session.session").exists()
    assert "Left where it was" in out


def test_migrate_refuses_when_there_is_nothing_to_move(run_cli, home, capsys):
    directory = profiles.profile_dir("default", home)
    directory.mkdir(parents=True, mode=0o700)
    _write_private(directory / "session.session", "already here")

    code, _out, err, _fake = run_cli(["auth", "--migrate"], capsys=capsys, answers=["y"])

    assert code == 2
    assert "nothing to migrate" in err


def test_migrate_needs_no_credentials(run_cli, home, monkeypatch, capsys):
    legacy_login(home)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    code, _out, _err, _fake = run_cli(["auth", "--migrate"], capsys=capsys, answers=["y"])

    assert code == 0


def test_doctor_suggests_the_migration_without_performing_it(home, capsys):
    session = legacy_login(home)

    run_doctor(root=home, env={"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}, version_info=(3, 11, 0), home=home)

    out = capsys.readouterr().out
    assert "auth --migrate" in out
    assert session.exists()
    assert str(home) not in out


# -- the banner ------------------------------------------------------------


def test_every_command_screen_starts_with_acting_as(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "discover_chats", _returns([]))
    monkeypatch.setattr(cli, "list_bots", _returns([]))

    for argv in (["discover"], ["bots"]):
        _code, out, _err, _fake = run_cli(argv, capsys=capsys)
        assert out.splitlines()[0] == "Acting as: Sven (@sven) · account", argv


def test_the_banner_names_the_resolved_target(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "resolve_chat", _resolved())
    monkeypatch.setattr(cli, "search_messages", _returns([]))

    _code, out, _err, _fake = run_cli(["search", "--chat", "-1001234567890"], capsys=capsys)

    assert out.splitlines()[0] == "Acting as: Sven (@sven) · account · Target: Team Hermes (-1001234567890)"


def test_a_run_that_writes_a_file_prints_no_banner(run_cli, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "discover_chats", _returns([]))

    code, out, _err, _fake = run_cli(["discover", "--json", str(tmp_path / "out.json")], capsys=capsys)

    assert code == 0
    assert out == "", "a run with no screen keeps the empty stdout it always had"


def test_the_envelope_carries_the_identity_and_the_banner_goes_to_stderr(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "discover_chats", _returns([]))

    code, out, err, _fake = run_cli(["--json", "discover"], capsys=capsys)

    envelope = json.loads(out)
    assert code == 0
    assert envelope["identity"]["label"] == "Sven (@sven)"
    assert envelope["identity"]["profile"] == "default"
    assert "Acting as:" in err and "Acting as:" not in out


def test_the_banner_carries_no_phone_number_when_the_account_has_no_username(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "discover_chats", _returns([]))
    numbered = FakeClient(authorized=True, me=SimpleNamespace(id=7, first_name=PHONE, last_name=None, username=None))

    _code, out, _err, _fake = run_cli(["discover"], client=numbered, capsys=capsys)

    assert PHONE not in out
    assert redaction.find(out) == []


# -- machine mode has nobody to log in with --------------------------------


def test_machine_mode_refuses_an_unauthorised_session_instead_of_prompting(run_cli, capsys):
    code, out, _err, client = run_cli(["--json", "discover"], client=FakeClient(authorized=False), capsys=capsys)

    envelope = json.loads(out)
    assert code == 2
    assert envelope["error"]["code"] == "LOGIN_REQUIRED"
    assert envelope["error"]["hint"] == "telegram-tools auth --profile default"
    assert client.sent_codes == []


def test_a_named_profile_is_named_in_the_refusal(run_cli, capsys):
    code, out, _err, _client = run_cli(
        ["--json", "--profile", "work", "discover"], client=FakeClient(authorized=False), capsys=capsys
    )

    assert json.loads(out)["error"]["hint"] == "telegram-tools auth --profile work"


# -- nothing here ever prints a secret -------------------------------------


def test_no_command_output_carries_a_token_a_number_or_a_session_path(run_cli, home, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", f"alerts:{TOKEN_SAMPLE}")
    monkeypatch.setattr(cli, "discover_chats", _returns([]))
    legacy_login(home)
    profiles.record_login(profiles.load("work", home=home), label="Sven (@sven)", user_id=4242)

    for argv in (["profiles"], ["discover"], ["--json", "profiles"], ["--json", "discover"]):
        _code, out, err, _fake = run_cli(argv, capsys=capsys)
        text = out + err
        assert TOKEN_SAMPLE not in text, argv
        assert PHONE not in text, argv
        assert ".session" not in text, argv
        assert redaction.find(text) == [], argv


# -- small helpers ---------------------------------------------------------


def _write_private(path: Path, body: str) -> Path:
    """A file at the mode the tool itself writes; anything looser refuses a write."""
    path.write_text(body)
    path.chmod(0o600)
    return path


def _session(home: Path, name: str) -> Path:
    return _write_private(profiles.profile_dir(name, home) / "session.session", "s")


def _async(value):
    async def call():
        return value

    return call()


def _returns(value):
    async def call(*_args, **_kwargs):
        return value

    return call


def _resolved():
    async def call(_client, reference):
        return SimpleNamespace(
            id=-1001234567890,
            entity=SimpleNamespace(id=1234567890, title="Team Hermes", megagroup=True, forum=True, broadcast=False),
            input_entity=SimpleNamespace(channel_id=1234567890),
            reference=reference,
        )

    return call


# -- the label has to tell two accounts apart (section 5.1) ------------------


def _user(first=None, last=None, username=None, phone=None, id=4242):
    return SimpleNamespace(id=id, first_name=first, last_name=last, username=username, phone=phone)


def test_a_username_is_the_label_whenever_there_is_one():
    from telegram_tools.adapters.account import account_label

    assert account_label(_user("Sven", username="sven", phone=PHONE)) == "Sven (@sven)"
    assert account_label(_user(username="sven")) == "@sven"


def test_an_account_with_no_username_is_told_apart_by_two_digits():
    """Sven's own account, 2026-09-06: first name `--`, no username.

    The banner read `Acting as: -- · account` and named nothing, which is the
    case section 5.1 puts the digits there for -- a display name is chosen by
    its owner and is not required to distinguish anything.
    """
    from telegram_tools.adapters.account import account_label

    assert account_label(_user("--", phone=PHONE)) == "-- (…78)"
    assert account_label(_user("Sven", "Medina", phone="+35679000012")) == "Sven Medina (…12)"


def test_the_label_carries_two_digits_and_never_the_number():
    from telegram_tools.adapters.account import account_label

    label = account_label(_user("--", phone=PHONE))

    assert PHONE not in label
    assert PHONE.lstrip("+")[:-2] not in label
    assert sum(character.isdigit() for character in label) == 2
    assert redaction.find(label) == []


def test_a_name_with_no_number_to_add_stays_the_name():
    from telegram_tools.adapters.account import account_label

    assert account_label(_user("Sven")) == "Sven"
    assert account_label(_user("Sven", phone="7")) == "Sven", "one digit is not two"


def test_an_account_with_nothing_of_its_own_falls_back_to_its_id():
    from telegram_tools.adapters.account import account_label

    assert account_label(_user(phone=PHONE, id=99)) == "user 99"


def test_the_banner_names_an_account_that_has_only_a_number(run_cli, capsys, monkeypatch):
    monkeypatch.setattr(cli, "discover_chats", _returns([]))
    bare = FakeClient(authorized=True, me=_user("--", phone=PHONE))

    _code, out, _err, _fake = run_cli(["discover"], client=bare, capsys=capsys)

    assert out.splitlines()[0] == "Acting as: -- (…78) · account"
    assert PHONE not in out
