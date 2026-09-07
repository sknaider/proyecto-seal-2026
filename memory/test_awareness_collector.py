from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from awareness_collector import (
    normalize_chat_message,
    normalize_git_status,
    normalize_service_status,
    normalize_test_result,
    shadow_fixture_events,
)


def test_public_webchat_without_ada_does_not_require_response() -> None:
    event = normalize_chat_message(
        {"id": 74246, "from": "William", "to": "equipo", "channel": "web_chat", "content": "el modelo local es gemma 4"},
        "ADA",
    )

    assert event.kind == "chat_message"
    assert event.requires_response is False
    assert event.metadata["explicit_agent_mention"] is False
    assert event.content == "el modelo local es gemma 4"


def test_public_webchat_with_exact_ada_mention_requires_response() -> None:
    event = normalize_chat_message(
        {"id": 1, "from": "William", "to": "equipo", "channel": "web_chat", "content": "ada continua"},
        "ADA",
    )

    assert event.requires_response is True
    assert event.priority == "high"
    assert event.metadata["explicit_agent_mention"] is True


def test_private_william_dm_is_allowed_and_preserved() -> None:
    event = normalize_chat_message(
        {"id": 2, "from": "William", "to": "ADA", "channel": "dm:ada:william", "content": "continua sin mencionar nombre"},
        "ADA",
    )

    assert event.requires_response is True
    assert event.content == "continua sin mencionar nombre"
    assert event.metadata["redacted"] is False


def test_other_agent_dm_is_redacted_at_collection_boundary() -> None:
    event = normalize_chat_message(
        {"id": 3, "from": "ALICE", "to": "William", "channel": "dm:alice:william", "content": "secreto privado"},
        "ADA",
    )

    assert event.kind == "privacy_boundary"
    assert event.content == ""
    assert event.metadata["privacy_boundary"] is True
    assert event.metadata["redacted"] is True
    assert event.requires_response is False


def test_service_test_and_git_normalizers_mark_operational_risk() -> None:
    service = normalize_service_status("seal-chat.service", "failed")
    test = normalize_test_result("memory/test_attention_governor.py", False, evidence="pytest failed")
    git = normalize_git_status(["memory/attention_governor.py", "README.md"])

    assert service.priority == "high"
    assert service.metadata["healthy"] is False
    assert test.priority == "high"
    assert test.metadata["passed"] is False
    assert git.metadata["critical_paths_touched"] is True


def test_shadow_fixture_has_100_events_and_privacy_cases() -> None:
    events = shadow_fixture_events("ADA", 100)

    assert len(events) == 100
    assert all(event.event_id for event in events)
    assert any(event.kind == "privacy_boundary" for event in events)
    assert any(event.metadata.get("destructive_command") for event in events)

