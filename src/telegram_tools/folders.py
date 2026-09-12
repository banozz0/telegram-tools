"""Chat folders' local side: the record, the flags, the gates, the screens.

Spec section 13, the Folders row. A folder is the account's own shelf over its
chat list -- Telegram calls it a dialog filter -- and four verbs manage them:
`folders list`, `create`, `edit` and `delete`. What is settled here:

* **Account only, and said so rather than hidden.** A bot has no dialog list,
  so it has no folders; `--as-bot folders …` refuses with
  `IDENTITY_MODE_UNSUPPORTED` before anything connects, rather than failing at
  the call.
* **A folder is a chat list plus categories.** `--include` and `--exclude`
  name chats; `--types` names whole categories Telegram matches for you
  (`contacts`, `groups`, `bots`, and the three `exclude_*` filters). A folder
  with neither matches nothing, which Telegram refuses -- so this refuses it
  first, by name.
* **Both list flags replace.** `--include a --include b` is the folder's
  include list afterwards, the way `admin rights` sets exactly the rights
  named; `--include none` empties it. A field no flag names is left alone,
  and that includes the pinned chats, which this tool never reorders.
* **Which gate.** `create` and `edit` are `prompt_y`, and `--yes` answers it
  with the preview still printed. `delete` is `typed_name`: a folder is a
  container, so it dry-runs by default, takes `--execute` plus the folder's
  exact title at a terminal, and has no `--yes`.
* **A shared folder is listed, never edited.** A folder someone handed over as
  a chatlist invite carries no exclude list and no categories, so the edit
  Telegram would accept is not the folder that comes back; both `edit` and
  `delete` refuse it by name and point at the app.

Nothing here talks to Telegram: the calls are `adapters/folders.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from telegram_tools._core import rid as _rid
from telegram_tools._core.redaction import redact_text
from telegram_tools.envelope import PREFIX, CommandError
from telegram_tools.manage import RULE, parse_rights

# Every category flag Telegram spells on a dialog filter, in its own words. The
# first five say which whole categories the folder holds; the last three say
# what it leaves out of whatever it holds.
TYPE_NAMES = (
    "bots",
    "broadcasts",
    "contacts",
    "exclude_archived",
    "exclude_muted",
    "exclude_read",
    "groups",
    "non_contacts",
)
# The three that subtract. A folder made only of these matches nothing.
EXCLUDING = ("exclude_archived", "exclude_muted", "exclude_read")
# Telegram's own range for a folder id: 0 is "All chats", 1 is reserved, and a
# client picks the lowest free one from 2.
FIRST_ID = 2
LAST_ID = 255
# The verbs, and the gate each takes.
VERBS = ("list", "create", "edit", "delete")
APPROVALS = {"create": "prompt_y", "edit": "prompt_y", "delete": "typed_name"}
MUTATIONS = {"create": "folder.create", "edit": "folder.edit", "delete": "folder.delete"}


def folder_rid(folder_id: int | str) -> str:
    return str(_rid.make(PREFIX, "folder", folder_id))


@dataclass(frozen=True)
class Folder:
    """One folder as the screens, the plan and the envelope see it.

    `peers` is the only field that is not plain data: the input peers Telegram
    handed over, kept so an edit can put back the pinned chats it was not asked
    to change. It never reaches an envelope.
    """

    id: int
    title: str
    emoticon: str | None = None
    types: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    # `folder` is one this account made; `shared` came from a chatlist invite.
    kind: str = "folder"
    peers: Mapping[str, tuple] = field(default_factory=dict, repr=False, compare=False)

    @property
    def rid(self) -> str:
        return folder_rid(self.id)

    @property
    def editable(self) -> bool:
        return self.kind == "folder"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rid": self.rid,
            "title": self.title,
            "emoticon": self.emoticon,
            "kind": self.kind,
            "types": list(self.types),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "pinned": list(self.pinned),
        }


def parse_types(text: str | None) -> tuple[str, ...]:
    """`--types` as Telegram's own flag names, or `none`; anything else is a usage error."""
    return parse_rights(text, universe=TYPE_NAMES, what="folder", noun="category")


def next_id(existing: Sequence[Folder]) -> int:
    """The lowest free folder id, from 2, which is what every Telegram client picks."""
    taken = {folder.id for folder in existing}
    for candidate in range(FIRST_ID, LAST_ID + 1):
        if candidate not in taken:
            return candidate
    raise CommandError(
        f"This account already has folders {FIRST_ID} to {LAST_ID}; there is no free id left.",
        code="PLATFORM_UNSUPPORTED",
    )


def find(existing: Sequence[Folder], folder_id: int) -> Folder:
    """The folder `--id` names, or a refusal listing the ids there are."""
    for folder in existing:
        if folder.id == int(folder_id):
            return folder
    ids = ", ".join(str(folder.id) for folder in existing) or "none"
    raise CommandError(
        f"This account has no folder {folder_id}.",
        code="TARGET_NOT_FOUND",
        hint=f"`telegram-tools folders list` shows them; the ids are {ids}.",
    )


def require_editable(folder: Folder, verb: str) -> None:
    """A shared folder is not this tool's to change: refuse by name, not by a call that half-works."""
    if folder.editable:
        return
    raise CommandError(
        f"{folder.title!r} came from a chatlist invite, so Telegram gives it no exclude list and no categories; "
        f"`folders {verb}` would not put back the folder that is there.",
        code="PLATFORM_UNSUPPORTED",
        hint="Leave or change a shared folder in the Telegram app.",
    )


def wanted(args: Any, base: Folder | None) -> dict[str, Any]:
    """What `create` or `edit` asks for: every field a flag named, over the folder there is.

    A flag left out leaves its field alone, which is why this reads `None`
    rather than a falsy value: `--emoji ''` clears the emoji and no `--emoji`
    at all keeps it.
    """
    fields: dict[str, Any] = {}
    if getattr(args, "title", None) is not None:
        fields["title"] = str(args.title).strip()
        if not fields["title"]:
            raise ValueError("--title cannot be empty: a folder is named.")
    if getattr(args, "emoji", None) is not None:
        fields["emoticon"] = str(args.emoji).strip() or None
    if getattr(args, "types", None) is not None:
        fields["types"] = parse_types(args.types)
    for key, flag in (("include", "include"), ("exclude", "exclude")):
        given = getattr(args, flag, None)
        if given:
            fields[key] = () if [text.strip().casefold() for text in given] == ["none"] else tuple(given)
    if base is None and "title" not in fields:
        raise ValueError("folders create needs --title.")
    if base is not None and not fields:
        raise ValueError("folders edit changes nothing: name at least one of --title, --emoji, --include, --exclude, --types.")
    return fields


def require_matches_something(types: Sequence[str], include: Sequence[Any]) -> None:
    """A folder that holds no chat and no category matches nothing; Telegram refuses it and so does this."""
    if include or [name for name in types if name not in EXCLUDING]:
        return
    raise ValueError(
        "A folder needs at least one chat (--include) or one category (--types "
        + ", ".join(name for name in TYPE_NAMES if name not in EXCLUDING)
        + "): with neither it would match nothing."
    )


# -- the screens ---------------------------------------------------------------


def format_folders(rows: Sequence[Folder]) -> str:
    if not rows:
        return "No folder on this account."
    lines = [f"{len(rows)} folder(s)", RULE]
    for folder in rows:
        marks = []
        if folder.types:
            marks.append(", ".join(folder.types))
        marks.append(f"{len(folder.include)} chat(s)")
        if folder.exclude:
            marks.append(f"{len(folder.exclude)} excluded")
        if folder.pinned:
            marks.append(f"{len(folder.pinned)} pinned")
        if not folder.editable:
            marks.append("shared, not editable here")
        emoji = f"{folder.emoticon} " if folder.emoticon else ""
        lines.append(f"{folder.id:>4}  {emoji}{redact_text(folder.title)}  {'; '.join(marks)}")
    return "\n".join(lines)


def format_folder(folder: Folder) -> str:
    emoji = f"  {folder.emoticon}" if folder.emoticon else ""
    return "\n".join(
        [
            f"{redact_text(folder.title)} (folder {folder.id}){emoji}",
            RULE,
            f"Categories  {', '.join(folder.types) or '(none)'}",
            f"Chats       {', '.join(folder.include) or '(none)'}",
            f"Excluded    {', '.join(folder.exclude) or '(none)'}",
            f"Pinned      {', '.join(folder.pinned) or '(none)'}",
        ]
    )


def format_preview(verb: str, *, actor: str, folder: Folder | None, details: Sequence[str], execute: bool | None) -> str:
    """What a person reads before the gate: who acts, on which folder, and what changes."""
    named = f"{redact_text(folder.title)} (folder {folder.id})" if folder is not None else "a new folder"
    lines = [f"folders {verb}: {named}", RULE, f"As      {actor}"]
    lines.extend(details)
    lines.append(RULE)
    if execute is None:
        lines.append("Nothing has happened yet.")
    elif execute:
        lines.append("Executing: the next prompt asks for the folder's exact title.")
    else:
        lines.append("Dry-run. Add --execute to do it; the folder's exact title is asked for then.")
    return "\n".join(lines)


def format_fields(fields: Mapping[str, Any], *, include: Sequence[str] = (), exclude: Sequence[str] = ()) -> tuple[str, ...]:
    """The preview's body: one line per field this run changes, in a fixed order."""
    lines = []
    if "title" in fields:
        lines.append(f"Title       {fields['title']!r}")
    if "emoticon" in fields:
        lines.append(f"Emoji       {fields['emoticon'] or '(none)'}")
    if "types" in fields:
        lines.append(f"Categories  {', '.join(fields['types']) or '(none)'}")
    if "include" in fields:
        lines.append(f"Chats       {', '.join(include) or '(none)'}")
    if "exclude" in fields:
        lines.append(f"Excluded    {', '.join(exclude) or '(none)'}")
    return tuple(lines)


def diff(before: Folder, after: Folder) -> str:
    """The readback: every field that actually moved, `old -> new`."""
    moved = []
    for label, old, new in (
        ("title", before.title, after.title),
        ("emoji", before.emoticon, after.emoticon),
        ("categories", ", ".join(before.types), ", ".join(after.types)),
        ("chats", ", ".join(before.include), ", ".join(after.include)),
        ("excluded", ", ".join(before.exclude), ", ".join(after.exclude)),
    ):
        if old != new:
            moved.append(f"{label} {old or '(none)'!r} -> {new or '(none)'!r}")
    return "; ".join(moved) if moved else "no field changed"


def titles_match(typed: str, folder: Folder) -> bool:
    """The typed gate, case-insensitive for `delete`'s reason: the proof is knowing which folder."""
    return str(typed).strip().casefold() == folder.title.casefold()


def confirm_typed_title(
    preview: str, folder: Folder, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> bool:
    write(preview)
    return titles_match(read(f"Type the exact title ({folder.title}) to continue: "), folder)


__all__ = [
    "APPROVALS",
    "EXCLUDING",
    "FIRST_ID",
    "LAST_ID",
    "MUTATIONS",
    "TYPE_NAMES",
    "VERBS",
    "Folder",
    "confirm_typed_title",
    "diff",
    "find",
    "folder_rid",
    "format_fields",
    "format_folder",
    "format_folders",
    "format_preview",
    "next_id",
    "parse_types",
    "require_editable",
    "require_matches_something",
    "titles_match",
    "wanted",
]
