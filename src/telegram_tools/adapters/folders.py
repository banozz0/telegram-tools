"""The folders port: the two dialog-filter calls behind `folders`.

Spec section 13, the Folders row, on the Telethon seam. Telegram's whole
surface here is two requests -- `messages.getDialogFilters` reads the account's
filters in order, `messages.updateDialogFilter` writes one or, with no filter,
removes it -- so this module is the mapping between them and the `Folder`
record `folders.py` screens.

Three details of the wire shape live here rather than leaking upward:

* the list carries a `DialogFilterDefault`, which is Telegram's "All chats"
  row rather than a folder anyone made; it has no id and is dropped;
* a `DialogFilterChatlist` is a folder that arrived through a chatlist invite.
  It carries an include list and nothing else, so it is read as `kind:
  "shared"` and `folders.require_editable` refuses to write it back;
* a title is `TextWithEntities` on this layer and was a plain string on
  older ones, so it is read through both and written as the current shape.

A folder's chats are `InputPeer`s. Their marked ids come from
`utils.get_peer_id` with no call of any kind, which is why `folders list`
reaches Telegram exactly once.
"""

from __future__ import annotations

from typing import Any, Sequence

from telethon import utils
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import DialogFilter, TextWithEntities

from telegram_tools._core import rid as _rid
from telegram_tools.envelope import PREFIX
from telegram_tools.folders import TYPE_NAMES, Folder


def chat_rid(peer: Any) -> str:
    """One `InputPeer` as this tool's chat rid, with no network call."""
    return str(_rid.make(PREFIX, "chat", utils.get_peer_id(peer)))


def _title_text(title: Any) -> str:
    """The folder's name, on either layer: `TextWithEntities` now, a plain string before."""
    return str(getattr(title, "text", title) or "")


def folder_of(raw: Any) -> Folder | None:
    """One dialog filter as a `Folder`, or None for the "All chats" row that is not one."""
    folder_id = getattr(raw, "id", None)
    if folder_id is None:
        return None
    shared = type(raw).__name__ == "DialogFilterChatlist"
    include = tuple(getattr(raw, "include_peers", []) or [])
    exclude = tuple(getattr(raw, "exclude_peers", []) or [])
    pinned = tuple(getattr(raw, "pinned_peers", []) or [])
    return Folder(
        id=int(folder_id),
        title=_title_text(getattr(raw, "title", "")),
        emoticon=getattr(raw, "emoticon", None) or None,
        types=() if shared else tuple(name for name in TYPE_NAMES if getattr(raw, name, False)),
        include=tuple(chat_rid(peer) for peer in include),
        exclude=tuple(chat_rid(peer) for peer in exclude),
        pinned=tuple(chat_rid(peer) for peer in pinned),
        kind="shared" if shared else "folder",
        peers={"include": include, "exclude": exclude, "pinned": pinned},
    )


class TelegramFoldersPort:
    """The dialog-filter calls over one signed-in account."""

    def __init__(self, client) -> None:
        self.client = client

    async def read(self) -> list[Folder]:
        """Every folder this account has, in Telegram's own order."""
        result = await self.client(GetDialogFiltersRequest())
        raw_filters = getattr(result, "filters", result) or []
        return [folder for folder in (folder_of(raw) for raw in raw_filters) if folder is not None]

    def build(
        self,
        folder_id: int,
        *,
        title: str,
        emoticon: str | None,
        types: Sequence[str],
        include: Sequence[Any],
        exclude: Sequence[Any],
        pinned: Sequence[Any],
    ) -> DialogFilter:
        """The filter object one write sends. Every category flag is set explicitly, so an edit clears what it drops."""
        return DialogFilter(
            id=int(folder_id),
            title=TextWithEntities(text=str(title), entities=[]),
            pinned_peers=list(pinned),
            include_peers=list(include),
            exclude_peers=list(exclude),
            emoticon=emoticon or None,
            **{name: name in set(types) for name in TYPE_NAMES},
        )

    async def write(self, folder_id: int, filter_: DialogFilter) -> None:
        await self.client(UpdateDialogFilterRequest(id=int(folder_id), filter=filter_))

    async def remove(self, folder_id: int) -> None:
        """`messages.updateDialogFilter` with no filter: how Telegram deletes one."""
        await self.client(UpdateDialogFilterRequest(id=int(folder_id), filter=None))


__all__ = ["TelegramFoldersPort", "chat_rid", "folder_of"]
