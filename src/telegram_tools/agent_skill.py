"""The agent skill this release ships, and where an agent reads it.

The repository's `skill/SKILL.md` is the source of truth. The wheel carries a
copy of it inside the package (`pyproject.toml` force-includes it as
`telegram_tools/skill/SKILL.md`), so every install holds the skill matching its
own version; an editable install has no such copy and reads the repository's
file instead. `skill install` and `doctor` both read it through here.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

TOOL = "telegram-tools"
SKILL_FILE = "SKILL.md"
# `default_dir()` as a person reads it: the --dir help, the menu's folder
# prompt and doctor's line all print this rather than a home directory.
DEFAULT_SHOWN = f"~/.claude/skills/{TOOL}"


def default_dir(home: Path | None = None) -> Path:
    """Claude Code's folder for this skill: `~/.claude/skills/telegram-tools`."""
    return (home or Path.home()) / ".claude" / "skills" / TOOL


def bundled_text() -> str:
    """The SKILL.md this release carries: the packaged copy, else the repository's file."""
    packaged = resources.files("telegram_tools").joinpath("skill", SKILL_FILE)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (Path(__file__).resolve().parents[2] / "skill" / SKILL_FILE).read_text(encoding="utf-8")
