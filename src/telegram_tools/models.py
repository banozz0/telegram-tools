from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TopicInfo:
    id: int
    title: str
    top_message: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "top_message": self.top_message,
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
