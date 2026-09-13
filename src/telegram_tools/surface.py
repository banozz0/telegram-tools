"""Which surface a run is being read on, so a screen only names controls it has.

Every dry-run ends with the same sentence: what would happen, and how to make
it happen for real. On the command line "for real" is a flag, so the sentence
names `--execute`. Inside the menu there are no flags -- the person executes by
picking a row -- so a sentence that says `--execute` there names a control that
is not on the screen in front of them, and the screen cannot be acted on.

The menu is the only caller that sets this, once per command it runs
(`menu._call`), naming the row that will execute. Everything else -- an agent,
a script, a person at a shell -- leaves it unset, which is the command line and
the default. It is wording and nothing else: what a dry-run does, what a gate
asks for and what an audit line records are the same on both surfaces.

Standard library only; the dependency list stays Telethon and python-dotenv.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

# The menu row that would execute what the current dry-run is showing, or None
# on the command line. A ContextVar rather than a module global so a run that
# never touches the menu can never see a row left behind by one that did.
_EXECUTE_ROW: contextvars.ContextVar[str | None] = contextvars.ContextVar("execute_row", default=None)


@contextmanager
def menu_row(label: str | None) -> Iterator[None]:
    """Run a command as the menu, with `label` as the row that executes.

    `None` is the command line, so the menu can wrap every call it makes and
    only the dry-run screens that have a row to name get one.
    """
    # Not named `token`: the commit guard reads `<name>token = <call>` as a
    # credential and refuses the commit.
    previous = _EXECUTE_ROW.set(label)
    try:
        yield
    finally:
        _EXECUTE_ROW.reset(previous)


def execute_hint(action: str = "do it", *, flag: str = "Add --execute") -> str:
    """How to execute what a dry-run just showed, in the words of this surface.

    The command line gets the flag phrased the way that screen already phrased
    it (`Add --execute to do it`, `Re-run with --execute to leave for real`);
    the menu gets the row that does the same thing, quoted as it is labelled.
    """
    row = _EXECUTE_ROW.get()
    if row is None:
        return f"{flag} to {action}"
    return f'Choose "{row}" to {action}'
