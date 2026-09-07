from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from working_state_soak import SoakMetrics, _rate, assess_production_readiness, live_probe_payload, synthetic_soak_payloads
from working_state_hook import event_from_hook_payload
from working_state_journal import project_events


def test_rate_handles_zero_and_rounds() -> None:
    assert _rate(1, 4) == 0.25
    assert _rate(1, 3) == 0.333
    assert _rate(1, 0) == 0.0


def test_synthetic_soak_payloads_reconstruct_failure_to_recovery() -> None:
    events = []
    for payload in synthetic_soak_payloads():
        decision = event_from_hook_payload(payload, temporary=True)
        assert decision.event is not None
        events.append(decision.event)

    projection = project_events("ADA", events)

    assert len(events) == 4
    assert [event.status for event in events] == ["failed", "success", "success", "success"]
    assert projection.score == 100
    assert projection.failed_paths == ["pytest -q memory/test_working_state_soak.py"]
    assert projection.last_result == "soak metrics verified"
    assert "memory/working_state_soak.py" in projection.evidence_items


def test_synthetic_soak_payloads_have_auditable_evidence() -> None:
    events = [event_from_hook_payload(payload, temporary=True).event for payload in synthetic_soak_payloads()]
    evidence_items = []
    for event in events:
        assert event is not None
        for key in ("command", "artifact", "output"):
            value = event.evidence.get(key)
            if value:
                evidence_items.append(value)

    assert "pytest -q memory/test_working_state_soak.py" in evidence_items
    assert "memory/working_state_soak.py" in evidence_items
    assert "soak metrics verified" in evidence_items


def test_live_probe_payload_builds_non_temporary_success_event() -> None:
    payload = live_probe_payload("marker_123")
    decision = event_from_hook_payload(payload, temporary=False)

    assert decision.event is not None
    assert decision.event.temporary is False
    assert decision.event.status == "success"
    assert decision.event.evidence["command"] == "seal-working-state-live-probe marker_123"
    assert "marker_123" in decision.event.evidence["output"]


def test_production_readiness_blocks_real_claim_without_real_events() -> None:
    metrics = SoakMetrics(
        agent="ADA",
        total_events=0,
        success_events=0,
        failed_events=0,
        blocked_events=0,
        temporary_events=0,
        failure_rate=0.0,
        success_rate=0.0,
        projection_score=40,
        projection_next_step="investigate_blocker",
        latest_event_at=None,
        event_types=[],
        evidence_items=[],
        missing=["events:not_found", "evidence_items"],
    )

    readiness = assess_production_readiness(metrics, synthetic_supported=True)

    assert readiness.ready_for_real_soak is False
    assert readiness.real_soak_claim_allowed is False
    assert readiness.real_event_count == 0
    assert readiness.reason == "real_events_not_observed"
    assert "real_events:not_observed" in readiness.missing
    assert "real_soak_claim_allowed=False" in readiness.evidence


def test_production_readiness_allows_claim_only_with_real_events_and_synthetic_support() -> None:
    metrics = SoakMetrics(
        agent="ADA",
        total_events=3,
        success_events=3,
        failed_events=0,
        blocked_events=0,
        temporary_events=0,
        failure_rate=0.0,
        success_rate=1.0,
        projection_score=100,
        projection_next_step="continue_with_evidence",
        latest_event_at="2026-05-19T21:00:00+00:00",
        event_types=["tool_result", "verification"],
        evidence_items=["pytest -q"],
        missing=[],
    )

    readiness = assess_production_readiness(metrics, synthetic_supported=True)

    assert readiness.ready_for_real_soak is True
    assert readiness.real_soak_claim_allowed is True
    assert readiness.reason == "real_events_observed"
    assert readiness.missing == []
