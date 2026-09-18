"""The install command a refusal prints is one the reader's machine can run.

Both extras refuse rather than degrade, so the refusal is the only place the fix
is written down. The shipped install is pipx, and a pipx venv has no `pip` on
`PATH`: `pip install 'telegram-tools[qr]'` pasted there answers
`zsh: command not found: pip` (Sven, live 2026-09-18, card agent-bo-95422362).
So the hint is derived from where the interpreter lives rather than frozen.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

import telegram_tools
from telegram_tools import doctor, extras, login, proxy

PACKAGE = Path(telegram_tools.__file__).resolve().parent
ROOT = PACKAGE.parent.parent


@pytest.fixture
def pipx_prefix(tmp_path, monkeypatch):
    """This interpreter, pretending to be the one inside a pipx venv."""
    prefix = tmp_path / "pipx" / "venvs" / "telegram-tools"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return prefix


def test_the_hint_injects_when_the_tool_is_installed_with_pipx(pipx_prefix):
    assert extras.install_hint("qr") == "pipx inject telegram-tools segno"
    assert extras.install_hint("proxy") == "pipx inject telegram-tools 'python-socks[asyncio]'"


def test_the_hint_falls_back_to_pip_anywhere_else(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))

    assert extras.install_hint("qr") == "python -m pip install 'telegram-tools[qr]'"


def test_a_relocated_pipx_home_is_still_pipx(tmp_path, monkeypatch):
    """`PIPX_HOME` moves the venvs; the layout under it does not change."""
    home = tmp_path / "elsewhere"
    prefix = home / "venvs" / "telegram-tools"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setenv("PIPX_HOME", str(home))

    assert extras.install_hint("qr") == "pipx inject telegram-tools segno"


def test_the_qr_refusal_hints_the_pipx_command(pipx_prefix, monkeypatch):
    """`None` in `sys.modules` is what an absent segno looks like to the import."""
    monkeypatch.setitem(sys.modules, "segno", None)

    with pytest.raises(Exception) as caught:
        login.render_qr("tg://login?token=x")

    assert caught.value.hint == "pipx inject telegram-tools segno"


def test_the_proxy_refusal_hints_the_pipx_command(pipx_prefix, monkeypatch):
    monkeypatch.setattr(proxy, "backend_available", lambda: False)

    with pytest.raises(proxy.ProxyUnavailable) as caught:
        proxy.require_backend(proxy.Proxy("socks5", "127.0.0.1", 1080))

    assert "pipx inject telegram-tools 'python-socks[asyncio]'" in str(caught.value)
    assert "pip install" not in str(caught.value)


def test_doctor_hints_the_pipx_command_for_a_proxy_it_cannot_use(tmp_path, pipx_prefix, monkeypatch):
    monkeypatch.setattr(proxy, "backend_available", lambda: False)

    check = doctor.check_proxy(tmp_path, {"TELEGRAM_PROXY": "socks5://127.0.0.1:1080"})

    assert check.status == "FAIL"
    assert "pipx inject telegram-tools 'python-socks[asyncio]'" in check.message


def test_no_module_freezes_a_pip_line_of_its_own():
    """A second spelling would go stale the moment the install method changes."""
    frozen = [
        f"{path.relative_to(ROOT)}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if "_core" not in path.parts
        and path.name != "extras.py"
        and "pip install" in path.read_text(encoding="utf-8")
    ]

    assert frozen == [], f"{frozen} print a pip line instead of asking extras.install_hint()"


def test_every_extra_names_the_library_it_installs():
    """`pipx inject` takes distributions, so the mapping must match the extras."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["optional-dependencies"]

    for extra, package in extras.PACKAGES.items():
        requirements = [line.split(">")[0].split("=")[0].split(";")[0].strip() for line in declared[extra]]
        assert requirements == [package], f"the {extra} extra installs {requirements}, not {package}"
