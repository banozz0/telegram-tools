"""`skill install` and doctor's skill line: this release's SKILL.md, where an agent reads it.

Driven through `cli.main` the way a person or an agent runs it, and judged by
what they would see: the file on disk, the envelope, the exit code and the
audit log. Nothing here connects -- the command never does.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_tools import agent_skill, cli, doctor, menu
from telegram_tools import profiles as profile_store
from telegram_tools._core.contract import validate_envelope

REPO_SKILL = Path(__file__).resolve().parents[1] / "skill" / "SKILL.md"
BUNDLED = REPO_SKILL.read_text(encoding="utf-8")
VERSION = re.search(r"^version: (\S+)$", BUNDLED, re.MULTILINE).group(1)


def skill_text(version: str) -> str:
    return f"---\nname: telegram-tools\nversion: {version}\n---\n\nan older or newer copy\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine of its own, with no login and no ~/.telegram-tools yet."""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("TELEGRAM_TOOLS_PROFILE", raising=False)
    return tmp_path


@pytest.fixture
def run_cli(home, monkeypatch, capsys):
    """`main(argv)` with the terminal and the typed answer controlled."""

    def run(argv, *, isatty=False, answer=""):
        monkeypatch.setattr(
            cli.sys, "stdin", SimpleNamespace(isatty=lambda: isatty, read=lambda: "", readline=lambda: f"{answer}\n")
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": answer)
        code = cli.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def envelope_of(out: str) -> dict:
    envelope = json.loads(out)
    assert validate_envelope(envelope) == []
    return envelope


def signed_in(home: Path) -> None:
    """The record `auth` leaves: which account the default profile is."""
    profile_store.record_identity(profile_store.load(home=home), label="Sven (@sven)", user_id=4242)


def audit_lines(home: Path) -> list[dict]:
    path = home / ".telegram-tools" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- the install ----------------------------------------------------------------


def test_a_fresh_install_writes_the_bundled_skill_where_dir_says(home, run_cli):
    folder = home / "skills" / "telegram-tools"

    code, out, _err = run_cli(["--json", "skill", "install", "--dir", str(folder), "--yes"])

    envelope = envelope_of(out)
    assert code == 0
    assert (folder / "SKILL.md").read_text(encoding="utf-8") == BUNDLED
    assert (folder / "SKILL.md").stat().st_mode & 0o777 == 0o644
    assert envelope["command"] == "skill install"
    assert envelope["status"] == "ok"
    assert envelope["result"] == {
        "path": str(folder / "SKILL.md"),
        "action": "created",
        "installed_version": None,
        "bundled_version": VERSION,
        "cancelled": False,
    }
    assert envelope["evidence"]["readback"] == f"{folder / 'SKILL.md'} reads back identical to the bundled skill, version {VERSION}"


def test_with_no_dir_it_is_claude_codes_folder(home, run_cli):
    code, out, _err = run_cli(["--json", "skill", "install", "--yes"])

    assert code == 0
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    assert envelope_of(out)["result"]["path"] == str(target)
    assert target.read_text(encoding="utf-8") == BUNDLED


def test_before_any_login_it_installs_unsigned_and_says_so(home, run_cli):
    code, out, _err = run_cli(["--json", "skill", "install", "--yes"])

    envelope = envelope_of(out)
    assert code == 0
    assert envelope["plan"] is None
    assert any("unsigned" in warning and "auth" in warning for warning in envelope["warnings"])
    assert audit_lines(home) == []


def test_a_recorded_login_signs_the_plan_and_leaves_one_audit_line(home, run_cli):
    signed_in(home)

    code, out, _err = run_cli(["--json", "skill", "install", "--yes"])

    envelope = envelope_of(out)
    assert code == 0
    assert envelope["identity"]["id"] == "tg:user:4242"
    assert envelope["plan"]["approval"] == "prompt_y"
    [line] = audit_lines(home)
    assert line["command"] == "skill install"
    assert line["status"] == "ok"
    assert line["plan_id"] == envelope["plan"]["plan_id"]


def test_an_identical_file_asks_nothing_and_writes_nothing(home, run_cli):
    signed_in(home)
    run_cli(["--json", "skill", "install", "--yes"])
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    before = target.stat().st_mtime_ns

    # No terminal and no --yes: anything that asked would refuse.
    code, out, _err = run_cli(["--json", "skill", "install"])

    envelope = envelope_of(out)
    assert code == 0
    assert envelope["result"]["action"] == "unchanged"
    assert target.stat().st_mtime_ns == before
    assert len(audit_lines(home)) == 1


def test_with_no_terminal_and_no_yes_it_refuses_and_writes_nothing(home, run_cli):
    code, out, _err = run_cli(["--json", "skill", "install"])

    envelope = envelope_of(out)
    assert code == 3
    assert envelope["error"]["code"] == "APPROVAL_REQUIRED"
    assert not (home / ".claude").exists()


def test_an_older_copy_is_previewed_with_both_versions_and_updated_on_y(home, run_cli):
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(skill_text("0.1.0"))

    code, out, _err = run_cli(["skill", "install"], isatty=True, answer="y")

    assert code == 0
    assert "Install the agent skill" in out
    assert f"  File       {target}" in out
    assert "  Action     updated" in out
    assert "  Installed  0.1.0" in out
    assert f"  Bundled    {VERSION}" in out
    assert "newer than this release's" not in out
    assert f"Wrote {target} (version {VERSION}). A new agent session picks it up." in out
    assert target.read_text(encoding="utf-8") == BUNDLED


def test_a_newer_copy_says_installing_goes_back_and_a_no_keeps_it(home, run_cli):
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(skill_text("99.0.0"))

    code, out, _err = run_cli(["skill", "install"], isatty=True, answer="n")

    assert code == 1
    assert f"The installed skill is newer than this release's; installing goes back to {VERSION}." in out
    assert target.read_text() == skill_text("99.0.0")


def test_a_linked_skill_folder_is_refused_and_left_alone(home, run_cli, tmp_path):
    repo = tmp_path / "my-skills" / "telegram-tools"
    repo.mkdir(parents=True)
    (repo / "SKILL.md").write_text(skill_text("0.1.0"))
    link = home / ".claude" / "skills" / "telegram-tools"
    link.parent.mkdir(parents=True)
    link.symlink_to(repo)

    code, out, _err = run_cli(["--json", "skill", "install", "--yes"])

    assert code == 2
    assert envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"
    assert (repo / "SKILL.md").read_text() == skill_text("0.1.0")


def test_a_linked_skill_file_is_refused_and_left_alone(home, run_cli, tmp_path):
    real = tmp_path / "SKILL.md"
    real.write_text(skill_text("0.1.0"))
    folder = home / ".claude" / "skills" / "telegram-tools"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").symlink_to(real)

    code, out, _err = run_cli(["--json", "skill", "install", "--yes"])

    assert code == 2
    assert envelope_of(out)["error"]["code"] == "TARGET_KIND_MISMATCH"
    assert real.read_text() == skill_text("0.1.0")


def test_a_local_md_beside_the_skill_is_untouched(home, run_cli):
    folder = home / ".claude" / "skills" / "telegram-tools"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(skill_text("0.1.0"))
    (folder / "LOCAL.md").write_text("this machine's notes\n")

    code, _out, _err = run_cli(["--json", "skill", "install", "--yes"])

    assert code == 0
    assert (folder / "LOCAL.md").read_text() == "this machine's notes\n"
    assert sorted(path.name for path in folder.iterdir()) == ["LOCAL.md", "SKILL.md"]


def test_a_bot_does_not_install_skills(home, run_cli):
    code, out, _err = run_cli(["--json", "--as-bot", "alerts", "skill", "install", "--yes"])

    assert code == 2
    assert envelope_of(out)["error"]["code"] == "IDENTITY_MODE_UNSUPPORTED"
    assert not (home / ".claude").exists()


def test_the_menu_signs_the_install_as_the_profile_it_acts_as(home, monkeypatch):
    """A menu on `--profile work` must not install as `default`, or unsigned."""
    profile_store.record_identity(profile_store.load("work", home=home), label="Work (@work)", user_id=777)
    answers = iter(["", "1"])  # Enter = the default folder; 1 = Back to Check setup
    # The CLI's own y/N, which the menu leaves to it: `input` reads this stdin.
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True, read=lambda: "", readline=lambda: "y\n"))

    outcome = asyncio.run(
        menu._flow_skill_install(
            session=SimpleNamespace(profile="work"), runner=cli.run, read=lambda _prompt: next(answers), write=lambda _text: None
        )
    )

    assert outcome is True
    assert (home / ".claude" / "skills" / "telegram-tools" / "SKILL.md").read_text(encoding="utf-8") == BUNDLED
    [line] = audit_lines(home)
    assert line["command"] == "skill install"
    assert (line["identity"]["profile"], line["identity"]["id"]) == ("work", "tg:user:777")


# -- where the skill comes from -------------------------------------------------


def test_an_editable_install_reads_the_repositorys_skill():
    assert agent_skill.bundled_text() == BUNDLED


def test_a_wheel_reads_the_copy_packaged_beside_the_code(tmp_path, monkeypatch):
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_text("the packaged copy\n")
    monkeypatch.setattr(agent_skill.resources, "files", lambda _package: tmp_path)

    assert agent_skill.bundled_text() == "the packaged copy\n"


# -- doctor's line --------------------------------------------------------------


def test_doctor_says_where_the_skill_would_go_when_it_is_not_there(home):
    line = doctor.check_agent_skill(home)

    assert line.status == "OK"
    assert line.message == (
        f"Agent skill: not installed in ~/.claude/skills/telegram-tools; `telegram-tools skill install` puts version {VERSION} there"
    )


def test_doctor_says_current_older_and_newer(home):
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    target.parent.mkdir(parents=True)

    target.write_text(BUNDLED)
    assert doctor.check_agent_skill(home) == doctor.DoctorCheck("OK", f"Agent skill: version {VERSION} in ~/.claude/skills/telegram-tools, current")

    target.write_text(skill_text("0.1.0"))
    assert doctor.check_agent_skill(home) == doctor.DoctorCheck(
        "WARN",
        f"Agent skill: version 0.1.0 in ~/.claude/skills/telegram-tools; this release ships {VERSION}, and `telegram-tools skill install` updates it",
    )

    target.write_text(skill_text("99.0.0"))
    assert doctor.check_agent_skill(home) == doctor.DoctorCheck(
        "OK", f"Agent skill: version 99.0.0 in ~/.claude/skills/telegram-tools is newer than the {VERSION} this release ships"
    )


def test_doctor_calls_a_linked_folder_managed_by_hand(home, tmp_path):
    repo = tmp_path / "elsewhere"
    repo.mkdir()
    link = home / ".claude" / "skills" / "telegram-tools"
    link.parent.mkdir(parents=True)
    link.symlink_to(repo)

    assert doctor.check_agent_skill(home) == doctor.DoctorCheck(
        "OK", "Agent skill: ~/.claude/skills/telegram-tools is a link or not a plain folder, so it is managed by hand"
    )


def test_the_skill_line_never_changes_doctors_exit_code(home, tmp_path):
    target = home / ".claude" / "skills" / "telegram-tools" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(skill_text("0.1.0"))
    lines: list[str] = []
    env = {"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef"}

    code = doctor.run_doctor(root=tmp_path, env=env, home=home, report=SimpleNamespace(info=lines.append, result=lambda *_a, **_k: None))

    assert any(line.startswith("WARN Agent skill: version 0.1.0") for line in lines)
    assert not any(line.startswith("FAIL") for line in lines)
    assert code == 0
