from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from soul_ingestion.adapters.chat import ChatAdapter, ChatMessage
from soul_ingestion.contracts import Scope
from soul_ingestion.engine import IngestionEngine
from soul_ingestion.profiles import extract_gtl_fields


TENANT = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)


def message(message_id: int, channel: str, content: str) -> ChatMessage:
    return ChatMessage(
        message_id=message_id,
        sender="William" if message_id == 10 else "ADA",
        timestamp=NOW,
        content=content,
        channel=channel,
    )


def test_chat_adapter_preserves_ids_sender_order_and_content() -> None:
    artifact = ChatAdapter().acquire(
        [message(10, "web_chat", "Construye el motor."), message(11, "web_chat", "Responsable: ADA.")],
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.TEAM,
        allowed_channel="web_chat",
        observed_at=NOW,
    )
    result = IngestionEngine().process(artifact, profile_id="team_conversation_v1", now=NOW)
    text = result.document.normalized_text
    assert "message_id=10 sender=William" in text
    assert "message_id=11 sender=ADA" in text
    assert text.index("message_id=10") < text.index("message_id=11")
    assert {anchor.kind for anchor in result.derivations[0].evidence_anchors} == {"message"}


def test_chat_adapter_rejects_cross_channel_dm_rows() -> None:
    with pytest.raises(PermissionError):
        ChatAdapter().acquire(
            [message(10, "dm:alice:william", "privado")],
            tenant_id=TENANT,
            owner_agent="ADA",
            scope=Scope.PRIVATE,
            allowed_channel="dm:ada:william",
            observed_at=NOW,
        )


def test_chat_adapter_rejects_reordered_or_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        ChatAdapter().acquire(
            [message(11, "web_chat", "B"), message(10, "web_chat", "A")],
            tenant_id=TENANT,
            owner_agent="ADA",
            scope=Scope.TEAM,
            allowed_channel="web_chat",
            observed_at=NOW,
        )


def test_gtl_fields_are_exact_and_conflicts_are_not_hidden() -> None:
    text = (
        "AWB: 123-12345678. AWB: 999-99999999. "
        "Piezas: 12. Peso: 48.5 kg. Vuelo: LA 2450. Origen: LIM. Destino: MIA."
    )
    fields = extract_gtl_fields(text)
    assert [item["value"] for item in fields["awb"]] == ["123-12345678", "999-99999999"]
    assert fields["pieces"][0]["value"] == "12"
    assert fields["weight"][0]["value"] == "48.5 kg"
    assert fields["origin"][0]["source_text"] == "Origen: LIM"
