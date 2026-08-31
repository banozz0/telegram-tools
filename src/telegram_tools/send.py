from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram_tools.models import TopicInfo

RULE = "--------------------------------------------"


class SendNotAllowedError(PermissionError):
    """A --yes send aimed somewhere TELEGRAM_SEND_ALLOWLIST does not name."""


@dataclass(frozen=True)
class SendTarget:
    """Where a message is going, named the way the preview shows it."""

    chat_id: int
    chat_title: str
    topic: TopicInfo | None = None

    @property
    def topic_id(self) -> int | None:
        return None if self.topic is None else self.topic.id


@dataclass(frozen=True)
class SendResult:
    chat_id: int
    topic_id: int | None
    message_id: int | None
    cancelled: bool = False
    files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "topic_id": self.topic_id,
            "message_id": self.message_id,
            "files": self.files,
            "sent": not self.cancelled and self.message_id is not None,
            "cancelled": self.cancelled,
        }


def format_size(size: int) -> str:
    """Bytes as the file manager would show them, so a wrong file is obvious."""
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_send_preview(
    target: SendTarget, text: str | None, *, sender: str, files: Sequence[str] = ()
) -> str:
    """The whole message and its destination, so a y/N is never answered blind."""
    topic = "(no topic - the chat itself)" if target.topic is None else f"{target.topic.id} {target.topic.display_title}"
    lines = [
        "Sending as " + sender,
        RULE,
        f"Chat    {target.chat_title} ({target.chat_id})",
        f"Topic   {topic}",
    ]
    for index, raw in enumerate(files):
        path = Path(raw)
        # Sizes come off disk, not from the argument: naming a file that is not the
        # one you meant is the mistake a preview exists to catch.
        size = format_size(path.stat().st_size) if path.is_file() else "missing"
        lines.append(f"{'Files   ' if index == 0 else '        '}{path.name} ({size})")
    lines.append(RULE)
    lines.append(text if text else "(no caption)" if files else "")
    lines.append(RULE)
    return "\n".join(lines)


def confirm_send(preview: str, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> bool:
    write(preview)
    answer = read("Send it? [y/N]: ").strip().lower()
    if not answer:
        # Same reason as the bot-edit confirm: a stray newline left in the terminal
        # buffer reads as an empty answer, and a silent cancel looks like a bug.
        write("No answer read - cancelled.")
        return False
    return answer == "y"


def _destination(chat_id: int, topic_id: int | None) -> str:
    return str(chat_id) if topic_id is None else f"{chat_id}:{topic_id}"


def require_send_allowed(allowlist: Sequence[Any], *, chat_id: int, username: str | None, topic_id: int | None) -> None:
    """Raise unless TELEGRAM_SEND_ALLOWLIST names this destination.

    Only the unattended path (`--yes`) goes through here. A human who saw the
    preview and typed `y` has already made the decision this list exists to make
    on their behalf, so the menu and the interactive CLI are not restricted.
    """
    keys = {str(chat_id)}
    if username:
        keys.add(str(username).lstrip("@").lower())

    for entry in allowlist:
        if entry.chat in keys and entry.topic in (None, topic_id):
            return

    raise SendNotAllowedError(
        f"--yes refuses to send to {_destination(chat_id, topic_id)}: it is not in TELEGRAM_SEND_ALLOWLIST. "
        f"Add it in ~/.telegram-tools/.env as TELEGRAM_SEND_ALLOWLIST={_destination(chat_id, topic_id)} "
        "(comma-separated for several), or run without --yes and confirm the preview yourself."
    )


async def send_message(
    client,
    peer: Any,
    target: SendTarget,
    text: str | None,
    *,
    files: Sequence[str] | None = None,
    confirm: Callable[[], bool] | None = None,
) -> SendResult:
    files = list(files or [])
    if confirm is not None and not confirm():
        return SendResult(
            chat_id=target.chat_id, topic_id=target.topic_id, message_id=None, cancelled=True, files=len(files)
        )

    if files:
        # Always a list, even for one file: Telethon groups a list into a single
        # album, which is what several attachments in one send should look like.
        sent = await client.send_file(peer, files, caption=text or None, reply_to=target.topic_id)
        first = sent[0] if isinstance(sent, list) else sent
    else:
        first = await client.send_message(peer, text, reply_to=target.topic_id)

    return SendResult(
        chat_id=target.chat_id,
        topic_id=target.topic_id,
        message_id=int(getattr(first, "id")),
        cancelled=False,
        files=len(files),
    )
