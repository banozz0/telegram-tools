"""`MediaFetcher` for a Telethon client, and what a sync notes about a message's links and files.

Spec sections 9.1 and 9.2. Two jobs, both platform-only, both above the one
SDK seam:

* **What a sync records.** A message's URL entities become link candidates,
  the URL exactly as the message wrote it; its photo or document becomes a
  media candidate carrying Telegram's own file key as the locator, the mime
  type and size Telegram claimed, and the filename the sender gave it as
  display metadata. Nothing here fetches: a candidate is a row in the queue
  and stays one until a human says otherwise (`review approve`).
* **How approved bytes arrive.** `TelegramMediaFetcher.stream` re-reads the
  source message, checks it still carries the file the manifest names, and
  streams it through `iter_download` from the byte offset the pipeline hands
  it, so a fetch killed halfway resumes where the payload on disk ends and
  the sha256 the pipeline takes over the whole file is the same as an
  uninterrupted run's. The pipeline writes the chunks, counts them and runs
  the checks; this class never touches the disk and never decides a verdict.

Platform media never travel through a URL the message supplied: the locator
is a file key, never an address, and a media candidate built with a URL is
refused by the shared queue before it is stored.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping

from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from telegram_tools._core import rid as _rid
from telegram_tools._core.review import Candidate
from telegram_tools.resolver import resolve_chat

# Telegram's own name for a photo it never labels: every PhotoSize is a JPEG.
PHOTO_TYPE = "image/jpeg"


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """The characters an entity covers. Telegram counts offsets in UTF-16 code units,
    which is not what Python's `str` indexes, so the text is sliced as UTF-16."""
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    piece = encoded[offset * 2 : (offset + length) * 2]
    return piece.decode("utf-16-le", errors="ignore")


def links_of(message: Any) -> list[str]:
    """Every URL the message's entities name, as written, in order, each once.

    A `MessageEntityUrl` is a URL typed into the text; a `MessageEntityTextUrl`
    is text linked to one, and the link is what the reader would open.
    """
    entities = getattr(message, "entities", None) or ()
    text = getattr(message, "message", None)
    if text is None:
        text = getattr(message, "raw_text", "") or ""
    found: list[str] = []
    for entity in entities:
        if isinstance(entity, MessageEntityTextUrl):
            url = str(entity.url or "").strip()
        elif isinstance(entity, MessageEntityUrl):
            url = _utf16_slice(str(text), int(entity.offset), int(entity.length)).strip()
        else:
            continue
        if url and url not in found:
            found.append(url)
    return found


def media_locator(media: Any) -> str | None:
    """Telegram's file key for a message's photo or document, or None when it holds no file.

    A web-page preview, a poll, a location or a contact is not a file, and the
    link a preview came from is already a link candidate.
    """
    if media is None:
        return None
    document = getattr(media, "document", None)
    if document is not None and getattr(document, "id", None) is not None:
        return f"document:{document.id}"
    photo = getattr(media, "photo", None)
    if photo is not None and getattr(photo, "id", None) is not None:
        return f"photo:{photo.id}"
    return None


def _display_name(document: Any) -> str | None:
    for attribute in getattr(document, "attributes", None) or ():
        name = getattr(attribute, "file_name", None)
        if name:
            return str(name)
    return None


def _photo_size(photo: Any) -> int | None:
    """The largest size Telegram lists, which is the one `iter_download` serves."""
    sizes = [int(getattr(size, "size", 0) or 0) for size in getattr(photo, "sizes", None) or ()]
    sizes = [size for size in sizes if size > 0]
    return max(sizes) if sizes else None


def media_candidate(message: Any, source_rid: str, sender_rid: str | None) -> Candidate | None:
    media = getattr(message, "media", None)
    locator = media_locator(media)
    if locator is None:
        return None
    document = getattr(media, "document", None)
    if document is not None and locator.startswith("document:"):
        return Candidate(
            kind="media",
            source_rid=source_rid,
            source_message_id=str(int(getattr(message, "id"))),
            sender_rid=sender_rid,
            claimed_type=getattr(document, "mime_type", None) or None,
            claimed_size=int(document.size) if getattr(document, "size", None) is not None else None,
            locator=locator,
            display_name=_display_name(document),
        )
    photo = getattr(media, "photo", None)
    return Candidate(
        kind="media",
        source_rid=source_rid,
        source_message_id=str(int(getattr(message, "id"))),
        sender_rid=sender_rid,
        claimed_type=PHOTO_TYPE,
        claimed_size=_photo_size(photo),
        locator=locator,
    )


def candidates_of(message: Any, source_rid: str, sender_rid: str | None = None) -> list[Candidate]:
    """What a sync hands the review queue for one message: its links, then its file. Never fetched."""
    message_id = str(int(getattr(message, "id")))
    found = [
        Candidate(kind="link", source_rid=source_rid, source_message_id=message_id, sender_rid=sender_rid, url=url)
        for url in links_of(message)
    ]
    media = media_candidate(message, source_rid, sender_rid)
    if media is not None:
        found.append(media)
    return found


class TelegramMediaFetcher:
    """`MediaFetcher` over one signed-in client: the bytes of an approved media manifest, from an offset."""

    def __init__(self, client) -> None:
        self.client = client

    async def stream(self, manifest: Mapping[str, Any], offset: int = 0) -> AsyncIterator[bytes]:
        """Chunks of the file the manifest names, from byte `offset` to the end.

        The source message is read again rather than trusted from the row: a
        file reference Telegram handed out weeks ago has expired, and a message
        whose file was replaced or removed must not quietly serve something
        else. The locator from the sync has to match what the message carries
        now, or nothing is streamed and the download is recorded `failed`
        with that reason.
        """
        locator = manifest.get("locator")
        if manifest.get("kind") != "media" or not locator:
            raise LookupError("only a media manifest with a file key is fetched through Telegram")
        parsed = _rid.parse(str(manifest["source_rid"]))
        resolved = await resolve_chat(self.client, parsed.ids[0])
        message_id = int(manifest["source_message_id"])
        message = await self.client.get_messages(resolved.input_entity, ids=message_id)
        if isinstance(message, list):
            message = message[0] if message else None
        if message is None:
            raise LookupError(f"message {message_id} is no longer in {manifest['source_rid']}")
        media = getattr(message, "media", None)
        current = media_locator(media)
        if current != locator:
            raise LookupError(
                f"message {message_id} no longer carries {locator}" + (f" (it carries {current} now)" if current else "")
            )
        async for chunk in self.client.iter_download(media, offset=int(offset)):
            yield bytes(chunk)


__all__ = ["PHOTO_TYPE", "TelegramMediaFetcher", "candidates_of", "links_of", "media_candidate", "media_locator"]
