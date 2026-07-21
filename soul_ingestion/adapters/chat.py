"""Read-only adapter for caller-supplied chat rows.

The adapter deliberately has no database connection and therefore cannot read
another agent's DM. A privileged caller must supply an already-authorized set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field

from ..contracts import RawArtifact, Scope, Sensitivity, SourceDescriptor, SourceKind, StrictModel, TrustTier


class ChatMessage(StrictModel):
    message_id: int = Field(gt=0)
    sender: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    content: str
    channel: str = Field(min_length=1, max_length=255)


class ChatAdapter:
    adapter_id = "chat_v1"
    adapter_version = "1.0.0"

    def acquire(
        self,
        messages: list[ChatMessage],
        *,
        tenant_id: UUID,
        owner_agent: str | None,
        scope: Scope,
        allowed_channel: str,
        observed_at: datetime | None = None,
    ) -> RawArtifact:
        if not messages:
            raise ValueError("chat range cannot be empty")
        ids = [message.message_id for message in messages]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("chat messages must be unique and ordered by message_id")
        if any(message.channel != allowed_channel for message in messages):
            raise PermissionError("supplied messages cross the authorized channel boundary")
        lines = []
        message_ranges = []
        cursor = 0
        for message in messages:
            timestamp = message.timestamp.astimezone(UTC).isoformat()
            content = message.content.replace("\x00", "�")
            rendered = f"[message_id={message.message_id} sender={message.sender} timestamp={timestamp}]\n{content}"
            if lines:
                cursor += 2
            start = cursor
            lines.append(rendered)
            cursor += len(rendered)
            message_ranges.append({"message_id": message.message_id, "start_char": start, "end_char": cursor})
        payload = "\n\n".join(lines).encode("utf-8")
        source = SourceDescriptor(
            source_kind=SourceKind.CHAT,
            source_ref=f"{allowed_channel}:{ids[0]}-{ids[-1]}",
            tenant_id=tenant_id,
            owner_agent=owner_agent,
            scope=scope,
            event_time=messages[-1].timestamp,
            observed_at=observed_at or datetime.now(UTC),
            trust_tier=TrustTier.TEAM_VERIFIED,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
        )
        return RawArtifact(
            source=source,
            media_type="application/vnd.seal.chat+text",
            content=payload,
            title=f"Chat {ids[0]}–{ids[-1]}",
            language="es",
            sensitivity=Sensitivity.CONFIDENTIAL if allowed_channel.startswith("dm:") else Sensitivity.INTERNAL,
            metadata={"message_ids": ids, "message_ranges": message_ranges, "channel": allowed_channel},
        )
