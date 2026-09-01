from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TopicInfo:
    id: int
    title: str
    top_message: int | None = None
    icon_emoji: str | None = None

    @property
    def display_title(self) -> str:
        """The title with the topic's emoji in front, the way Telegram draws it.

        Deliberately not `title`: `--topic` matching and every export key on that,
        so the emoji stays out of it and lives only on the screen.
        """
        return f"{self.icon_emoji} {self.title}" if self.icon_emoji else self.title

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "top_message": self.top_message,
            "icon_emoji": self.icon_emoji,
        }


@dataclass(frozen=True)
class ChatChoice:
    """A chat as a picker sees it: enough to show and to pass as --chat, no more.

    Deliberately not ChatInfo, which carries an `is_admin` the light dialog walk
    never asks Telegram for and a `topics` list it does not fetch.
    """

    id: int
    title: str
    username: str | None
    type: str

    @property
    def is_forum(self) -> bool:
        return self.type == "forum_group"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "username": self.username,
            "type": self.type,
        }


@dataclass(frozen=True)
class ChatInfo:
    id: int
    title: str
    username: str | None
    type: str
    is_admin: bool
    topics: list[TopicInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "username": self.username,
            "type": self.type,
            "is_admin": self.is_admin,
            "topics": [topic.to_dict() for topic in self.topics],
        }


@dataclass(frozen=True)
class DeleteResult:
    matched: int
    deleted: int
    dry_run: bool
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "cleared": self.deleted,
            "dry_run": self.dry_run,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True)
class ContainerDeleteResult:
    """What `delete` was pointed at and whether it went through.

    Separate from `DeleteResult`: that one counts messages inside a chat that
    survives, this one is the chat or topic itself going away.
    """

    kind: str
    id: int
    title: str
    dry_run: bool
    topic_id: int | None = None
    deleted: bool = False
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "chat_id": self.id,
            "topic_id": self.topic_id,
            "title": self.title,
            "deleted": self.deleted,
            "dry_run": self.dry_run,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True)
class BotCommandInfo:
    command: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "description": self.description,
        }


@dataclass(frozen=True)
class BotInfo:
    id: int
    username: str | None
    name: str
    bio: str | None
    description: str | None
    is_owned: bool
    has_photo: bool = False
    commands: list[BotCommandInfo] = field(default_factory=list)
    group_rights: list[str] = field(default_factory=list)
    channel_rights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "bio": self.bio,
            "description": self.description,
            "is_owned": self.is_owned,
            "has_photo": self.has_photo,
            "commands": [command.to_dict() for command in self.commands],
            "group_rights": list(self.group_rights),
            "channel_rights": list(self.channel_rights),
        }
