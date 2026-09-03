"""The seams this repository is not allowed to cross.

Two laws, both from the suite architecture (section 4.2):

7. **No cross-mention.** This tool does not know the other one exists. Its code
   and its contributor-facing documents never name the other platform or the
   other CLI, so a reader here reads one complete tool rather than half of a
   pair. `CHANGELOG.md` is exempt: it is history, and history is not edited.
8. **The shared copy is relocatable.** `_core/` is the same tree the sibling
   carries. It may not name *any* platform -- naming this one would be just as
   broken, because the copy has to read the same under either package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The other platform, and the other CLI built on it. Matched case-insensitively.
FOREIGN = ("discord", "discord-tools")
# Both platforms: nothing in the shared copy may name either of them.
PLATFORMS = ("telegram", "discord")

SKIP = {"__pycache__", ".pytest_cache", ".venv", ".git"}


def files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix != ".pyc" and not SKIP & set(item.parts)
    )


def scanned() -> list[Path]:
    """Every file law 7 covers: the whole package, and the documents a contributor reads."""
    targets = [ROOT / "src"]
    targets += [ROOT / name for name in ("README.md", "AGENTS.md", "CONTEXT.md", "skill/SKILL.md")]
    return [file for target in targets if target.exists() for file in files_under(target)]


def hits(path: Path, words: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    return [word for word in words if word in text]


@pytest.mark.parametrize("path", scanned(), ids=lambda path: str(path.relative_to(ROOT)))
def test_no_file_names_the_other_platform_or_the_other_tool(path):
    found = hits(path, FOREIGN)
    assert not found, (
        f"{path.relative_to(ROOT)} names {', '.join(found)}. This tool does not know the other one "
        "exists: an alert that should reach another platform goes to a command the user configured."
    )


def test_the_documents_law_7_covers_all_exist():
    # A missing file would pass the test above by being absent, which is the one
    # way this law can rot: the grep has to actually be greping something.
    for name in ("README.md", "AGENTS.md", "CONTEXT.md", "skill/SKILL.md"):
        assert (ROOT / name).exists(), f"{name} is missing; law 7 covers it"


@pytest.mark.parametrize(
    "path",
    files_under(ROOT / "src" / "telegram_tools" / "_core"),
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_the_shared_copy_names_no_platform(path):
    found = hits(path, PLATFORMS)
    assert not found, (
        f"_core/{path.name} names {', '.join(found)}. The shared copy is written once and carried "
        "by both tools: it has to read the same under either package. Fixes go to the workshop."
    )
