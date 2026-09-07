"""Small tamper-evident audit chain for reference and tests."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .contracts import canonical_json, utc_now


GENESIS_HASH = "sha256:" + "0" * 64


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    correlation_id: str
    privacy_class: str
    payload: dict[str, Any]
    previous_event_hash: str
    event_hash: str


class AuditChain:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(
        self,
        event_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        privacy_class: str = "P4",
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        previous = self.events[-1].event_hash if self.events else GENESIS_HASH
        base = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": occurred_at or utc_now(),
            "correlation_id": correlation_id,
            "privacy_class": privacy_class,
            "payload": payload,
            "previous_event_hash": previous,
        }
        event_hash = "sha256:" + hashlib.sha256(canonical_json(base)).hexdigest()
        event = AuditEvent(**base, event_hash=event_hash)
        self.events.append(event)
        return event

    def verify(self) -> bool:
        previous = GENESIS_HASH
        for event in self.events:
            if event.previous_event_hash != previous:
                return False
            base = as_event_payload(event)
            expected = "sha256:" + hashlib.sha256(canonical_json(base)).hexdigest()
            if event.event_hash != expected:
                return False
            previous = event.event_hash
        return True


def as_event_payload(event: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "correlation_id": event.correlation_id,
        "privacy_class": event.privacy_class,
        "payload": event.payload,
        "previous_event_hash": event.previous_event_hash,
    }

