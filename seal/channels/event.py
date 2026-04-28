"""Normalized message types crossing channel boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


@dataclass(frozen=True, slots=True)
class Attachment:
    """Inbound or outbound payload attached to a message."""

    kind: str                  # "image" | "audio" | "video" | "file" | "voice"
    mime: str
    url: Optional[str] = None  # remote URL when present
    data: Optional[bytes] = None  # inline bytes when small
    filename: Optional[str] = None
    duration_ms: Optional[int] = None  # for audio/voice
    size_bytes: Optional[int] = None


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """Inbound message normalized across channels.

    Channel adapters wrap their native event format into this shape so
    downstream agent code never branches on platform.
    """

    channel: str               # "matrix", "telegram", "discord", ...
    chat_id: str               # room/channel identifier scoped to the channel
    user_id: str               # author identifier scoped to the channel
    text: str
    message_id: str            # globally unique within the channel
    received_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    attachments: tuple[Attachment, ...] = ()
    reply_to: Optional[str] = None
    is_dm: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def starts_with_command(self, prefix: str) -> bool:
        return self.text.lstrip().startswith(prefix)

    def command_body(self, prefix: str) -> str:
        stripped = self.text.lstrip()
        if not stripped.startswith(prefix):
            return ""
        return stripped[len(prefix):].lstrip()


@dataclass(frozen=True, slots=True)
class SendResult:
    """Result of an outbound send across channels."""

    channel: str
    chat_id: str
    message_id: str
    sent_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    success: bool = True
    error: Optional[str] = None
