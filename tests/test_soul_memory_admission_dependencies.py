"""Clean-install contracts for the direct memory-extraction dependencies."""
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

from agent_rubric import load_agent_rubric  # noqa: E402
from memory_admission import memory_auto_event_skip_reason  # noqa: E402
from memory_importance_guard import normalize_memory_importance_for_write  # noqa: E402


def test_agent_rubric_loads_canonical_ada_rules() -> None:
    rubric = load_agent_rubric("ADA")
    assert rubric.agent == "ADA"
    assert rubric.chat_excerpt_importance_cap == 5
    assert "trust" in rubric.never_consolidate_categories


def test_low_value_operational_noise_is_rejected() -> None:
    assert memory_auto_event_skip_reason(
        agent="ADA", category="conversation_turn", content="recibido",
        source="auto_stop_hook", importance=5,
    ) == "short_ack"
    assert memory_auto_event_skip_reason(
        agent="ADA", category="status", content="[HB] ADA alive",
        source="heartbeat", importance=5,
    ) == "heartbeat_status"


def test_william_directive_is_never_filtered_as_noise() -> None:
    assert memory_auto_event_skip_reason(
        agent="ADA", category="decision",
        content="William autorizo mantener el heartbeat como evidencia operacional.",
        source="conversation", importance=10,
    ) is None


def test_importance_guard_caps_noise_but_honors_explicit_force() -> None:
    guarded = normalize_memory_importance_for_write(
        agent="ADA", category="status", content="[HB] ADA alive",
        requested_importance=10, source="heartbeat",
    )
    assert guarded.importance == 4
    forced = normalize_memory_importance_for_write(
        agent="ADA", category="status", content="[HB] ADA alive",
        requested_importance=10, source="heartbeat", metadata={"force_memory": True},
    )
    assert forced.importance == 10
