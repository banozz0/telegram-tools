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
    heading = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert heading and heading.group(1) == __version__, (
        "a user-visible change gets its CHANGELOG entry and version bump in the same commit"
    )
