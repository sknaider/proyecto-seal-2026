from __future__ import annotations

from pathlib import Path

import pytest

from nerves_global_behavior import (
    GlobalBehaviorEngine,
    GlobalBehaviorError,
    make_event,
    run_behavior_matrix,
)
from nerves_global_catalog import REQUIRED_AGENTS, REQUIRED_GLOBAL_NERVES


AGENTS = sorted(REQUIRED_AGENTS)
NERVES = sorted(REQUIRED_GLOBAL_NERVES)


def _break_behavior(event: dict) -> None:
    nerve_id = event["nerve_id"]
    signal = event["signal"]
    breakers = {
        "care_protection": lambda: signal.__setitem__("mutation_count", 1),
        "continuity": lambda: signal.__setitem__("restored_sha256", "0" * 64),
        "communication_delivery": lambda: signal.__setitem__("delivered", False),
        "human_priority_response": lambda: signal.__setitem__("ack_ms", 2001),
        "identity_integrity": lambda: signal.__setitem__("session_agent", "OTHER"),
        "privacy_boundary": lambda: signal.__setitem__("foreign_scope_denied", False),
        "runtime_health": lambda: signal.__setitem__("independently_confirmed", False),
        "coordination_non_collision": lambda: signal.__setitem__("writer_count", 2),
        "effect_truthfulness": lambda: signal.__setitem__(
            "verifier", signal["builder"]
        ),
        "stop_hold_preemption": lambda: signal.__setitem__("effects_after_command", 1),
        "productive_initiative": lambda: signal.__setitem__("human_pending", True),
        "verified_learning": lambda: signal.__setitem__("verified_outcomes", 2),
    }
    breakers[nerve_id]()


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("nerve_id", NERVES)
def test_positive_protocol_cell_records_private_envelope(
    tmp_path: Path,
    agent: str,
    nerve_id: str,
) -> None:
    engine = GlobalBehaviorEngine(tmp_path / "ledger" / "events.jsonl")
    event = make_event(agent, nerve_id)
    first = engine.process(event)
    replay = engine.process(event)
    assert first.status == "PROTOCOL_VALIDATED"
    assert first.joined is False
    assert replay.joined is True
    assert first.record_sha256 == replay.record_sha256
    assert engine.ledger_path.stat().st_mode & 0o777 == 0o600
    assert engine.ledger_path.parent.stat().st_mode & 0o777 == 0o700
    assert len(engine.ledger_path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("nerve_id", NERVES)
def test_negative_behavior_cell_is_rejected(
    tmp_path: Path,
    agent: str,
    nerve_id: str,
) -> None:
    engine = GlobalBehaviorEngine(tmp_path / "ledger" / "events.jsonl")
    event = make_event(agent, nerve_id, suffix="negative")
    _break_behavior(event)
    with pytest.raises(GlobalBehaviorError, match="behavior_proof_failed"):
        engine.process(event)
    assert not engine.ledger_path.exists()


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("nerve_id", NERVES)
def test_fail_closed_behavior_cell_rejects_malformed_evidence(
    tmp_path: Path,
    agent: str,
    nerve_id: str,
) -> None:
    engine = GlobalBehaviorEngine(tmp_path / "ledger" / "events.jsonl")
    event = make_event(agent, nerve_id, suffix="malformed")
    event["evidence_sha256"] = "not-a-sha"
    with pytest.raises(GlobalBehaviorError, match="event_evidence_hash_invalid"):
        engine.process(event)
    assert not engine.ledger_path.exists()


def test_protocol_matrix_covers_72_cells_and_deduplicates(tmp_path: Path) -> None:
    result = run_behavior_matrix(tmp_path / "matrix")
    assert result["ok"] is True
    assert result["protocol_cells"] == 72
    assert result["idempotent_replays"] == 72
    assert result["behavior_claims_proven"] == 0
    assert result["production_mutations"] == 0


def test_same_event_id_with_different_payload_fails_replay(tmp_path: Path) -> None:
    engine = GlobalBehaviorEngine(tmp_path / "ledger" / "events.jsonl")
    event = make_event("ADA", "care_protection")
    engine.process(event)
    drift = make_event("ADA", "care_protection")
    drift["signal"]["risk_class"] = "CRITICAL"
    with pytest.raises(GlobalBehaviorError, match="event_id_replay_drift"):
        engine.process(drift)
