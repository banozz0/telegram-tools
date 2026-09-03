"""The command surface, frozen: every `--help` text must stay what it was.

`tests/fixtures/help/` holds the help of the root parser and of every
subcommand, captured at 80 columns from 3.7.2. This test re-renders them and
compares. Flags are the contract an agent and a script read; a flag that
quietly changes its name, its help line or its defaults breaks callers that
never see a changelog.

Two relaxations, both deliberate:

* `ALLOWED_ADDITIONS` lists, verbatim, the additions this card was allowed to
  make -- the global `--json`/`--jsonl` on the root parser, and the optional
  path on the per-command `--json`. They are removed from the live text before
  it is compared. Anything else that moved fails here.
* Whitespace is squeezed to single spaces on both sides. `--json [JSON_OUTPUT]`
  is two characters wider than `--json JSON_OUTPUT`, and argparse widens the
  whole help column of that parser to match, so a byte comparison would fail on
  padding that carries no meaning. Every word still has to be the same word, in
  the same order.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

from telegram_tools.cli import build_parser

FIXTURES = Path(__file__).parent / "fixtures" / "help"

# Which parser each fixture file holds. The name is the argv path, `-`-joined.
COMMANDS = {
    "root": (),
    "discover": ("discover",),
    "clear-messages": ("clear-messages",),
    "search": ("search",),
    "bots": ("bots",),
    "send": ("send",),
    "create": ("create",),
    "create-group": ("create", "group"),
    "create-channel": ("create", "channel"),
    "create-topic": ("create", "topic"),
    "delete": ("delete",),
    "delete-group": ("delete", "group"),
    "delete-channel": ("delete", "channel"),
    "delete-topic": ("delete", "topic"),
    "doctor": ("doctor",),
}

# The only text this card was allowed to add, spelled exactly as the help spells
# it (whitespace-squeezed, the way both sides are compared). Removed from the
# live help before the comparison; everything left over has to match the fixture.
ALLOWED_ADDITIONS = (
    "[--json] [--jsonl]",
    "--json Emit one machine-readable envelope on stdout instead of the human output",
    "--jsonl Stream one JSON line per record, then the envelope as the last line",
)

# The per-command `--json` gained an optional path, which argparse spells with
# brackets in both the usage line and the option list.
OPTIONAL_PATH = re.compile(r"\[([A-Z_]+)\]")


def render(parts: tuple[str, ...]) -> str:
    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        parser.parse_args([*parts, "--help"])
    return buffer.getvalue()


def squeeze(text: str) -> str:
    return " ".join(text.split())


def normalise(text: str) -> str:
    """The help as this test compares it: whitespace squeezed, an optional path spelled plainly."""
    return OPTIONAL_PATH.sub(r"\1", squeeze(text))


@pytest.fixture(autouse=True)
def _eighty_columns(monkeypatch):
    # The fixtures were captured at 80; argparse wraps to the terminal it is in.
    monkeypatch.setenv("COLUMNS", "80")


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_help_text_is_the_captured_one(name):
    live = normalise(render(COMMANDS[name]))
    for addition in ALLOWED_ADDITIONS:
        live = live.replace(normalise(addition), "")
    expected = normalise((FIXTURES / f"{name}.txt").read_text(encoding="utf-8"))

    assert squeeze(live) == expected, (
        f"{name} --help changed. Fixtures under tests/fixtures/help/ are the frozen surface: "
        "an addition this card allows belongs in ALLOWED_ADDITIONS, anything else is a break."
    )


def test_every_fixture_names_a_parser():
    captured = {path.stem for path in FIXTURES.glob("*.txt")}
    assert captured == set(COMMANDS)
