"""The version is in two files, and they have to agree.

`__version__` is what the envelope reports, what the plan id is hashed from
and what every audit line carries; `pyproject.toml` is what a user installed.
They drifted once already -- the package said 3.6.0 through two releases --
and nothing noticed, because nothing had ever read `__version__`.
"""

from __future__ import annotations

import re
from pathlib import Path

from telegram_tools import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_version_matches_pyproject():
    declared = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert declared and declared.group(1) == __version__


def test_the_changelog_leads_with_this_version():
    """One heading may sit above it, and only one: `## Unreleased`.

    A wave of fix branches lands its entries as each merges and one release
    commit afterwards renames that heading to the version it bumps to, so the
    entries are never written twice from memory. What the file may still never
    do is lag the installed version: the first *version* heading is the one
    `__version__` reports, whether or not a staging heading leads.
    """
    headings = re.findall(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    if headings[:1] == ["Unreleased"]:
        headings = headings[1:]
    assert headings[:1] == [__version__], (
        "a user-visible change gets its CHANGELOG entry in the same commit, and the "
        "release that ships it renames `## Unreleased` to the version it bumps to"
    )
