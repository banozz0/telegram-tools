from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# Returned when the user presses 0. Every screen offers it, so callers compare
# with `is BACK` rather than testing a magic string a chat title could collide with.
BACK = object()
# Returned by edit_field when the user chose to empty a field, which is a different
# answer from "keep it" (BACK) and from any value the field could hold.
CLEAR = object()

_NEXT = object()
_PREV = object()

RULE = "--------------------------------------------"
PAGE_SIZE = 9


@dataclass(frozen=True)
class Extra:
    """A caller-owned row printed after the paging rows."""

    key: str
    label: str


def _screen(title: str, labels: Sequence[str], back_label: str) -> str:
    rows = [f"{number}. {label}" for number, label in enumerate(labels, start=1)]
    return "\n".join([title, RULE, *rows, f"0. {back_label}"])


def choose(labels: Sequence[str], *, title: str, read, write, back_label: str = "Back") -> Any:
    """Print a numbered list and return the chosen 0-based index, or BACK."""
    while True:
        write(_screen(title, labels, back_label))
        answer = read("Choose: ").strip()
        if answer == "0":
            return BACK
        if answer.isdigit() and 1 <= int(answer) <= len(labels):
            return int(answer) - 1
        write("Pick one of the numbers listed.")


def pick(
    items: Sequence[Any],
    *,
    title: str,
    label: Callable[[Any], str],
    read,
    write,
    extras: Sequence[Extra] = (),
    page_size: int = PAGE_SIZE,
) -> Any:
    """Page through `items`. Returns an item, an Extra's key, or BACK."""
    if not items:
        write("Nothing to pick from.")
        return BACK

    page = 0
    while True:
        start = page * page_size
        window = list(items[start : start + page_size])
        labels = [label(item) for item in window]
        keys: list[Any] = list(window)

        remaining = len(items) - (start + len(window))
        if remaining > 0:
            labels.append(f"Next page ({remaining} more)")
            keys.append(_NEXT)
        if page > 0:
            labels.append("Previous page")
            keys.append(_PREV)
        for extra in extras:
            labels.append(extra.label)
            keys.append(extra.key)

        choice = choose(labels, title=title, read=read, write=write)
        if choice is BACK:
            return BACK

        key = keys[choice]
        if key is _NEXT:
            page += 1
        elif key is _PREV:
            page -= 1
        else:
            return key


def pick_many(
    items: Sequence[Any],
    *,
    title: str,
    label: Callable[[Any], str],
    read,
    write,
    preselected: Sequence[Any] = (),
    page_size: int = PAGE_SIZE,
) -> Any:
    """Toggle items on and off. Returns the selected items in list order, or BACK."""
    if not items:
        write("Nothing to pick from.")
        return BACK

    selected = {index for index, item in enumerate(items) if item in preselected}
    page = 0
    while True:
        start = page * page_size
        window = list(enumerate(items))[start : start + page_size]
        labels = [f"[{'x' if index in selected else ' '}] {label(item)}" for index, item in window]
        keys: list[Any] = [index for index, _item in window]

        remaining = len(items) - (start + len(window))
        if remaining > 0:
            labels.append(f"Next page ({remaining} more)")
            keys.append(_NEXT)
        if page > 0:
            labels.append("Previous page")
            keys.append(_PREV)
        labels.append("Select all")
        keys.append("all")
        labels.append(f"Continue ({len(selected)} selected)")
        keys.append("continue")

        choice = choose(labels, title=title, read=read, write=write)
        if choice is BACK:
            return BACK

        key = keys[choice]
        if key is _NEXT:
            page += 1
        elif key is _PREV:
            page -= 1
        elif key == "all":
            selected = set(range(len(items)))
        elif key == "continue":
            if not selected:
                write("Tick at least one, or press 0 to go back.")
                continue
            return [item for index, item in enumerate(items) if index in selected]
        else:
            selected.symmetric_difference_update({key})


def ask_text(label: str, *, read, write, current: str | None = None) -> Any:
    """Free text. Blank cancels and returns BACK — keeping and clearing are their own rows."""
    suffix = f" [{current}]" if current else ""
    value = read(f"{label}{suffix} (blank cancels): ").strip()
    return value or BACK


def ask_int(label: str, *, read, write, current: int | None = None) -> Any:
    """A positive whole number, matching the CLI's own `positive_int`. Blank cancels."""
    suffix = f" [{current}]" if current else ""
    while True:
        value = read(f"{label}{suffix} (blank cancels): ").strip()
        if not value:
            return BACK
        if value.isdigit() and int(value) >= 1:
            return int(value)
        write("Type a whole number of 1 or more.")


def edit_field(title: str, current_display: str, *, read, write, ask: Callable[[], Any], allow_clear: bool) -> Any:
    """Keep / change / clear for one field.

    Returns BACK to keep the current value, CLEAR to empty it, or whatever `ask`
    returned. `ask` returning BACK also means keep, so cancelling out of the value
    prompt cannot stage a change.
    """
    labels = [f"Keep it as {current_display}", "Change it"]
    if allow_clear:
        labels.append("Clear it")

    choice = choose(labels, title=title, read=read, write=write)
    if choice is BACK or choice == 0:
        return BACK
    if choice == 1:
        return ask()
    return CLEAR


def after_action(*, read, write) -> bool:
    """True to go back to the menu, False to exit."""
    return read("Enter = menu, 0 = exit: ").strip() != "0"
