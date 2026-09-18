"""How to install an optional extra, in the words that work where this tool runs.

Spec: section 5.2. Both extras refuse rather than degrade, so every refusal
carries the command that fixes it -- and a command the reader's machine does
not have is the same as no command at all. The shipped install is `pipx`, which
puts the tool in a venv of its own with no `pip` on `PATH`: pasting
`pip install 'telegram-tools[qr]'` there answers `command not found: pip`
(Sven, 2026-09-18, card agent-bo-95422362).

So the hint is derived from where this interpreter lives. A pipx venv is
`<PIPX_HOME>/venvs/<distribution>`, and the way to add a library to one is
`pipx inject`, which takes packages rather than extras -- hence `PACKAGES`.
Anywhere else the line stays pip, spelled `python -m pip` because that runs
wherever the interpreter running this does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

# This tool's distribution name: the pipx venv is named after it too.
DISTRIBUTION = "telegram-tools"
# What each extra actually installs. `pipx inject` takes distributions, not
# extras, so the mapping has to be spelled out; `tests/test_extras.py` holds it
# against `pyproject.toml` so an extra that gains a library cannot drift.
PACKAGES = {
    "proxy": "python-socks[asyncio]",
    "qr": "segno",
}


def under_pipx(prefix: str | None = None, env: Mapping[str, str] | None = None) -> bool:
    """Whether this interpreter is a pipx venv's."""
    root = Path(prefix or sys.prefix)
    if root.parent.name != "venvs":
        return False
    home = (os.environ if env is None else env).get("PIPX_HOME")
    return root.parent.parent.name == "pipx" or bool(home and Path(home) == root.parent.parent)


def _quoted(package: str) -> str:
    """`python-socks[asyncio]` as a shell writes it: brackets are a glob in zsh."""
    return f"'{package}'" if "[" in package else package


def install_hint(
    extra: str, *, prefix: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    """The command that installs `extra` for the install this run is part of."""
    if under_pipx(prefix, env):
        return f"pipx inject {DISTRIBUTION} {_quoted(PACKAGES[extra])}"
    return f"python -m pip install {_quoted(f'{DISTRIBUTION}[{extra}]')}"
