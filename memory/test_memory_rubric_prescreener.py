from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_rubric import load_agent_rubric
from auto_extract_llm import normalize_fact_importance
from memory_importance_guard import normalize_memory_importance_for_write
from memory_admission import memory_auto_event_skip_reason, memory_skip_audit_record
from memory_prescreener import content_hash, safe_excerpt


def test_candidate_helpers_are_stable_and_compact() -> None:
    text = "  uno\n\n dos   tres  "

    assert content_hash(text) == content_hash(text)
    assert content_hash(text) != content_hash(text + "x")
    assert safe_excerpt(text, limit=7) == "uno dos"


def test_ada_rubric_loads_from_file() -> None:
    rubric = load_agent_rubric("ADA")

    assert rubric.agent == "ADA"
    assert rubric.chat_excerpt_importance_cap == 5
    assert "trust" in rubric.never_consolidate_categories


def test_structured_alice_rubric_uses_high_importance_gates() -> None:
    rubric = load_agent_rubric("ALICE")

    assert rubric.agent == "ALICE"
    assert rubric.minimum_chars_for_importance_9 == 250
    assert rubric.short_memory_token_threshold == 35
    assert "spec_authorship" in rubric.protected_categories


def test_legacy_root_rubric_path_is_supported() -> None:
    rubric = load_agent_rubric("NEXUS")

    assert rubric.agent == "NEXUS"
    assert rubric.raw.get("agent") == "NEXUS"


def test_chat_excerpt_importance_is_capped_for_noncritical_fact() -> None:
    fact = {
        "category": "insight",
        "content": "[ADA]: ruido, ignorar",
        "importance": 10,
    }

    normalized = normalize_fact_importance("ADA", fact)

    assert normalized["importance"] == 5


def test_critical_correction_can_keep_high_importance() -> None:
    fact = {
        "category": "correction",
        "content": "[ADA]: William corrigio una regla critica de privacidad DM.",
        "importance": 10,
    }

    normalized = normalize_fact_importance("ADA", fact)

    assert normalized["importance"] == 10


def test_william_directive_chat_excerpt_can_keep_high_importance() -> None:
    fact = {
        "category": "decision",
        "content": "[ADA]: William autorizo la regla critica de aprobacion por count exacto.",
        "importance": 10,
    }

    normalized = normalize_fact_importance("ADA", fact)

    assert normalized["importance"] == 10


def test_short_noncritical_high_importance_is_capped() -> None:
    fact = {
        "category": "insight",
        "content": "Pequeno insight no critico.",
        "importance": 9,
    }

    normalized = normalize_fact_importance("ADA", fact)

    assert normalized["importance"] == 6


def test_shared_importance_guard_records_metadata_patch() -> None:
    guarded = normalize_memory_importance_for_write(
        agent="ADA",
        category="insight",
        content="Pequeno insight no critico.",
        requested_importance=9,
        source="auto_llm_extract",
    )

    assert guarded.importance == 6
    assert guarded.metadata_patch is not None
    assert guarded.metadata_patch["importance_guard"]["original_importance"] == 9
    assert guarded.metadata_patch["importance_guard"]["normalized_importance"] == 6
    assert "short_noncritical_high_importance" in guarded.metadata_patch["importance_guard"]["reasons"]


def test_shared_importance_guard_caps_auto_noise() -> None:
    guarded = normalize_memory_importance_for_write(
        agent="ADA",
        category="status",
        content="[HB] ADA alive",
        requested_importance=10,
        source="heartbeat",
    )

    assert guarded.importance == 4
    assert guarded.metadata_patch is not None
    assert "auto_noise_cap:heartbeat_status" in guarded.metadata_patch["importance_guard"]["reasons"]


def test_shared_importance_guard_respects_force_memory() -> None:
    guarded = normalize_memory_importance_for_write(
        agent="ADA",
        category="status",
        content="[HB] ADA alive",
        requested_importance=10,
        source="heartbeat",
        metadata={"force_memory": True},
    )

    assert guarded.importance == 10
    assert guarded.metadata_patch is None


def test_auto_extract_llm_uses_shared_importance_guard_metadata() -> None:
    fact = {
        "category": "status",
        "content": "[HB] ADA alive",
        "importance": 10,
        "source": "heartbeat",
    }

    normalized = normalize_fact_importance("ADA", fact)

    assert normalized["importance"] == 4
    assert normalized["metadata"]["importance_guard"]["normalized_importance"] == 4


def test_auto_event_filter_skips_heartbeat_noise() -> None:
    reason = memory_auto_event_skip_reason(
        agent="JARVIS",
        category="conversation_turn",
        content="[HB] JARVIS alive",
        source="conversation",
        importance=5,
    )

    assert reason == "heartbeat_status"


def test_auto_event_filter_skips_local_dependency_paths() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="insight",
        content="Created file /home/dadito/project/.venv/lib/python3.12/site-packages/x.py",
        source="auto_llm_extract",
        importance=5,
    )

    assert reason == "local_dependency_path"


def test_auto_event_filter_preserves_william_directive() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="decision",
        content="William autorizo mantener el heartbeat como evidencia operacional.",
        source="conversation",
        importance=10,
    )

    assert reason is None


def test_auto_event_filter_skips_transcript_heartbeat_even_if_episodic() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="episodic",
        content="heartbeat ADA alive runtime codex",
        source="transcript_streamer",
        importance=6,
    )

    assert reason == "heartbeat_status"


def test_auto_event_filter_skips_shell_command_echo() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="conversation_turn",
        content="systemctl --user status seal-mcp-server.service",
        source="transcript_streamer",
        importance=5,
    )

    assert reason == "shell_command_echo"


def test_auto_event_filter_skips_short_ack() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="conversation_turn",
        content="recibido",
        source="auto_stop_hook",
        importance=5,
    )

    assert reason == "short_ack"


def test_auto_event_filter_preserves_high_value_milestone() -> None:
    reason = memory_auto_event_skip_reason(
        agent="ADA",
        category="milestone",
        content="Heartbeat architecture milestone approved by William for zero-token liveness.",
        source="daily_brief_writer",
        importance=9,
    )

    assert reason is None


def test_memory_skip_audit_record_uses_standard_event_shape() -> None:
    content, metadata_json = memory_skip_audit_record(
        category="status",
        content="[HB] ADA alive",
        source="heartbeat",
        importance=5,
        reason="heartbeat_status",
    )

    assert content == "memory_store skipped auto-event: heartbeat_status"
    assert '"action": "skip"' in metadata_json
    assert '"reason": "heartbeat_status"' in metadata_json
    assert '"content_preview": "[HB] ADA alive"' in metadata_json
