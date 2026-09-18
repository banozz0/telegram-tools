"""Every command a hint tells a person to run is one the parser accepts.

`--profile` hangs off the root parser, so `telegram-tools --profile work auth`
runs and `telegram-tools auth --profile work` dies in an argparse usage dump.
A hint that puts the flag after the verb is worse than no hint: the reader
copies it, gets the dump, and concludes the tool is broken. That is exactly what
happened on 2026-09-17 with `LoginRequired`'s hint, which is also the
`error.hint` an agent reads out of the `--json` envelope (card
agent-bo-95422340).

So this file walks the hints themselves rather than trusting a reviewer to spot
the order. A hint is a string that names both `--profile` and a
`telegram-tools ...` command - the flag alone is an argparse declaration or
prose about where the flag goes, and neither is something anyone copies:

- In the package's own output, every command in that string must parse, and the
  `--profile` must sit inside one of them. Prose that leaves the reader to
  assemble the command - "run `telegram-tools discover` (add --profile work if
  it is not the default)" - is the shape that broke, so it fails here too: the
  tool prints the command it means, whole.
- A hint that *names a profile* and then hints a command is held to the same
  rule even when it never spells the flag: "profile 'work' has no record ... run
  `telegram-tools auth`" reads as an instruction about `work` and runs against
  `default`, writing the record for the wrong login (card agent-bo-95422342).
  Naming one is the signal - "No profiles yet, log one in with `telegram-tools
  auth`" names none, and there is nothing to carry.
- In the docs a reader and an agent work from, only the command itself is
  checked. Prose *about* the flag belongs there - README and SKILL.md are where
  "before the subcommand" gets explained, and neither line is copied and run.

The scan is static, so a hint on a branch no test walks is checked too, and the
f-strings are read whole: a hint split across source lines in the middle of its
own command is where a per-line grep would give up. CHANGELOG.md is left out -
it records what was true then, not what to type today.
"""

from __future__ import annotations

import argparse
import ast
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

import telegram_tools
from telegram_tools import doctor
from telegram_tools.cli import build_parser
from telegram_tools.login import LoginRequired

PACKAGE = Path(telegram_tools.__file__).resolve().parent
ROOT = PACKAGE.parent.parent
DOCS = ("README.md", "AGENTS.md", "CONTEXT.md", "skill/SKILL.md")

# What an f-string's value or a doc's <placeholder> stands in for. Decimal, so a
# hint that also names an ID-typed flag still parses as the command it is.
STAND_IN = "123"
# Except where the placeholder *is* the subcommand slot - "use my other account"
# is written `telegram-tools --profile work <command>`, and what that row pins
# is where the flag sits relative to the verb, whichever verb it turns out to be.
VERB_STAND_IN = "discover"
VERB_PLACEHOLDERS = {"<command>", "<subcommand>", "<verb>"}

# `telegram-tools` as a command: not the `~/.telegram-tools/` directory, not the
# `telegram-tools-cli` distribution.
COMMAND = re.compile(r"(?<![\w./-])telegram-tools(?![\w.-])")
# Where a command written into a sentence stops.
COMMAND_END = re.compile(r"[`\n,;()]")
PLACEHOLDER = re.compile(r"<[^<>\s]+>")
FLAG = "--profile"
# A hint that names one profile: the word followed by a value the f-string fills
# in - `profile {name!r}`, `profile {name}`. "No profiles yet" and `profile(s):`
# name none, so neither is asked to carry a flag.
NAMED_PROFILE = re.compile(rf"\bprofile\s+['\"]?{STAND_IN}")


@dataclass(frozen=True)
class Hint:
    where: str
    text: str
    strays_matter: bool


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def command_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in COMMAND.finditer(text):
        rest = text[match.start() :]
        end = COMMAND_END.search(rest)
        spans.append((match.start(), match.start() + (end.start() if end else len(rest))))
    return spans


def stood_in(command: str) -> str:
    return PLACEHOLDER.sub(
        lambda found: VERB_STAND_IN if found.group() in VERB_PLACEHOLDERS else STAND_IN, command
    )


def argv_of(command: str) -> list[str]:
    """The command as argv, minus the program name.

    `comments=True` because these lines are read out of shell blocks, where a
    trailing `# log a second account in` is what the reader's own shell drops.
    """
    return shlex.split(stood_in(command), comments=True)[1:]


def refusal(command: str) -> str | None:
    """Why the parser would refuse this command, or None if it takes it."""
    try:
        argv = argv_of(command)
    except ValueError as exc:  # an unbalanced quote is not a runnable command
        return f"does not tokenise ({exc})"
    parser = build_parser()
    parser.exit_on_error = False
    try:
        parser.parse_args(argv)
    except (SystemExit, argparse.ArgumentError) as exc:
        return f"the parser refuses it ({exc})"
    return None


def rendered(node: ast.AST) -> str | None:
    """A whole string literal, an f-string with its values stood in, or None.

    Adjacent literals are one node by the time the parser is done, so a message
    written across several source lines arrives here in one piece.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            piece.value if isinstance(piece, ast.Constant) and isinstance(piece.value, str) else STAND_IN
            for piece in node.values
        )
    return None


def strings_in(node: ast.AST):
    text = rendered(node)
    if text is not None:
        yield node, text
        return
    for child in ast.iter_child_nodes(node):
        yield from strings_in(child)


def collect() -> list[Hint]:
    hints = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node, text in strings_in(ast.parse(source)):
            if (FLAG in text or NAMED_PROFILE.search(text)) and command_spans(text):
                hints.append(Hint(f"{path.relative_to(ROOT)}:{node.lineno}", text, strays_matter=True))
    for name in DOCS:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for start, stop in command_spans(text):
            command = text[start:stop]
            if FLAG in command:
                hints.append(Hint(f"{name}:{line_of(text, start)}", command, strays_matter=False))
    return hints


HINTS = collect()
# The ones that name a profile, whether or not they spell the flag.
NAMING = [hint for hint in HINTS if hint.strays_matter and NAMED_PROFILE.search(hint.text)]


def carries_its_profile(where: str, text: str) -> None:
    """A hint naming a profile puts that profile inside the command it prints."""
    spans = command_spans(text)
    assert spans, f"{where} names a profile but no command: {text!r}"
    assert any(FLAG in text[start:stop] for start, stop in spans), (
        f"{where} names a profile and then hints a command with no {FLAG}, so following it "
        f"acts on the default login: {text!r}"
    )


@pytest.mark.parametrize("hint", HINTS, ids=lambda hint: hint.where)
def test_a_hint_naming_the_profile_flag_runs_as_written(hint):
    spans = command_spans(hint.text)
    for start, stop in spans:
        command = hint.text[start:stop].strip().rstrip(".")
        why = refusal(command)
        assert why is None, f"{hint.where} hints `{command}` and {why}"
    if not hint.strays_matter:
        return
    for match in re.finditer(re.escape(FLAG), hint.text):
        inside = any(start <= match.start() < stop for start, stop in spans)
        assert inside, (
            f"{hint.where} leaves {FLAG} outside the command it names, so the reader appends it to a "
            f"verb the parser has already taken: {hint.text!r}"
        )


def test_the_scan_still_finds_the_tool_s_own_hints():
    """A scan that quietly finds nothing would pass every assertion above."""
    files = {hint.where.split(":")[0] for hint in HINTS if hint.strays_matter}
    assert {"src/telegram_tools/cli.py", "src/telegram_tools/login.py"} <= files, (
        f"the extractor stopped seeing the known {FLAG} hints; it found {sorted(files)}"
    )


def test_the_scan_still_finds_the_docs_own_commands():
    """The doc half is a second extractor, and fails silent the same way."""
    files = {hint.where.split(":")[0] for hint in HINTS if not hint.strays_matter}
    assert {"README.md", "skill/SKILL.md"} <= files, (
        f"the extractor stopped seeing the documented {FLAG} commands; it found {sorted(files)}"
    )


@pytest.mark.parametrize("profile", ["default", "work"])
def test_the_login_required_refusal_hints_a_command_that_runs(profile):
    """The hint as an agent reads it, not as the source spells it.

    `error.hint` in the `--json` envelope is the one line a caller copies when a
    profile is not logged in, so it is checked off the built refusal rather than
    off the literal the static scan already walked.
    """
    hint = LoginRequired(profile).as_error().hint

    assert hint is not None, "LOGIN_REQUIRED stopped saying how to fix itself"
    spans = command_spans(hint)
    assert spans, f"the refusal hints {hint!r}, which names no command to run"
    for start, stop in spans:
        command = hint[start:stop].strip().rstrip(".")
        assert refusal(command) is None, f"LOGIN_REQUIRED hints `{command}` and the parser refuses it"
    for match in re.finditer(re.escape(FLAG), hint):
        assert any(start <= match.start() < stop for start, stop in spans), (
            f"LOGIN_REQUIRED names {FLAG} outside a command: {hint!r}"
        )


@pytest.mark.parametrize("hint", NAMING, ids=lambda hint: hint.where)
def test_a_hint_that_names_a_profile_carries_it_into_the_command(hint):
    carries_its_profile(hint.where, hint.text)


def test_the_scan_still_finds_the_hints_that_name_a_profile():
    """The profile half fails silent the same way the flag half does."""
    assert NAMING, "the extractor stopped seeing the hints that name a profile"


def test_the_no_session_warning_carries_the_profile_it_names(tmp_path):
    """`doctor`'s warning assembles its profile at run time, so it is checked there.

    The static scan reads `f"{where}: no session yet ..."` and cannot know that
    `where` is `profile work`; the warning a person reads does say so, and it is
    the line they copy.
    """
    check = doctor.check_session_storage({}, home=tmp_path, profile="work")

    assert check.status == "WARN", f"expected the no-session warning, got {check!r}"
    assert "profile work" in check.message
    carries_its_profile("doctor.check_session_storage", check.message)
