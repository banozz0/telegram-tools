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

    A user-visible change carries its CHANGELOG entry *and* its version bump
    in the same commit -- one change, one version -- so the first version
    heading is the one that shipped the entries under it. There is no wave:
    entries do not queue under a staging heading for a later release commit
    to rename, which is how an entry ends up written from memory weeks after
    the code. That heading survives for the one carve-out AGENTS.md names --
    a change touching neither behaviour nor the CLI surface, a README or a
    comment rewrite, may wait there until the next bump carries it rather
    than mint a version of its own -- so this check steps over one such
    heading and no more. What the file may never do is lag the installed
    version: the first *version* heading is the one `__version__` reports,
    whether or not that staging heading leads.
    """
    headings = re.findall(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    if headings[:1] == ["Unreleased"]:
        headings = headings[1:]
    assert headings[:1] == [__version__], (
        "a user-visible change gets its CHANGELOG entry and its version bump in the "
        "same commit; only a change with no behaviour and no CLI surface may wait "
        "under `## Unreleased`"
    )
