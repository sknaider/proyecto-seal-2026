from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_lifecycle import (
    ClosureGateResult,
    LifecycleEvent,
    IntentContract,
    assess_lifecycle,
    default_contract,
    default_events,
    validate_intent_contract,
)


def test_default_contract_is_valid() -> None:
    valid, missing = validate_intent_contract(default_contract())

    assert valid is True
    assert missing == []


def test_contract_requires_scope_boundaries_risks_and_evidence() -> None:
    valid, missing = validate_intent_contract(
        IntentContract(
            agent="ADA",
            objective="",
            scope=[],
            out_of_scope=[],
            risks=[],
            expected_evidence=["command"],
        )
    )

    assert valid is False
    assert "objective" in missing
    assert "scope" in missing
    assert "out_of_scope" in missing
    assert "risks" in missing
    assert "expected_evidence:artifact" in missing
    assert "expected_evidence:output" in missing


def test_lifecycle_allows_close_when_contract_verified_and_audited() -> None:
    assessment = assess_lifecycle(default_contract(), default_events())

    assert assessment.allowed_to_close is True
    assert assessment.score == 100
    assert assessment.missing == []


def test_lifecycle_blocks_close_without_verified_evidence() -> None:
    events = [
        LifecycleEvent("intent_recorded", {"artifact": "contract"}),
        LifecycleEvent("executing", {"artifact": "impl"}),
        LifecycleEvent("verified", {"command": "pytest"}),
        LifecycleEvent("audited", {"reviewer": "NEXUS", "verdict": "pass"}),
        LifecycleEvent("closed", {"artifact": "run"}),
    ]
    assessment = assess_lifecycle(default_contract(), events)

    assert assessment.allowed_to_close is False
    assert "verified_evidence" in assessment.missing


def test_lifecycle_blocks_vacuous_green_when_contract_requires_opportunity() -> None:
    base = default_contract()
    contract = IntentContract(
        agent=base.agent,
        objective=base.objective,
        scope=base.scope,
        out_of_scope=base.out_of_scope,
        risks=base.risks,
        expected_evidence=[
            "command",
            "output",
            "artifact",
            "subject",
            "observed_at",
            "opportunity",
            "positive_control",
            "negative_control",
            "by_effect",
        ],
    )
    events = default_events()
    assessment = assess_lifecycle(contract, events)

    assert assessment.allowed_to_close is False
    assert "verified_evidence:opportunity" in assessment.missing
    assert "verified_evidence:positive_control" in assessment.missing
    assert "verified_evidence:negative_control" in assessment.missing


def test_lifecycle_accepts_non_vacuous_evidence_bundle() -> None:
    base = default_contract()
    strong_fields = [
        "subject",
        "observed_at",
        "opportunity",
        "expected",
        "actual",
        "positive_control",
        "negative_control",
        "by_effect",
    ]
    contract = IntentContract(
        agent=base.agent,
        objective=base.objective,
        scope=base.scope,
        out_of_scope=base.out_of_scope,
        risks=base.risks,
        expected_evidence=["command", "output", "artifact", *strong_fields],
    )
    events = default_events()
    verified = next(event for event in events if event.event_type == "verified")
    verified.evidence.update(
        {
            "subject": {"agent": "ADA", "unit": "seal-channel-monitor@ADA.service"},
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "opportunity": {"exercised": True, "operation": "restart"},
            "expected": {"active_state": "active"},
            "actual": {"active_state": "active"},
            "positive_control": {
                "expected": {"active_state": "active"},
                "observed": {"active_state": "active"},
                "passed": True,
            },
            "negative_control": {
                "expected": {"invocation_id": "same"},
                "observed": {"invocation_id": "same"},
                "passed": True,
            },
            "by_effect": {
                "before": {"invocation_id": "old"},
                "after": {"invocation_id": "new"},
                "changed": True,
            },
        }
    )

    assessment = assess_lifecycle(contract, events)

    assert assessment.allowed_to_close is True


def test_lifecycle_rejects_truthy_labels_as_strong_evidence() -> None:
    base = default_contract()
    contract = IntentContract(
        agent=base.agent,
        objective=base.objective,
        scope=base.scope,
        out_of_scope=base.out_of_scope,
        risks=base.risks,
        expected_evidence=[
            "command",
            "output",
            "artifact",
            "positive_control",
            "negative_control",
            "by_effect",
        ],
    )
    events = default_events()
    verified = next(event for event in events if event.event_type == "verified")
    verified.evidence.update(
        {
            "positive_control": "si",
            "negative_control": "ok",
            "by_effect": "ok",
        }
    )

    assessment = assess_lifecycle(contract, events)

    assert assessment.allowed_to_close is False
    assert "verified_evidence:positive_control" in assessment.missing
    assert "verified_evidence:negative_control" in assessment.missing
    assert "verified_evidence:by_effect" in assessment.missing


def test_lifecycle_rejects_by_effect_without_a_changed_value() -> None:
    base = default_contract()
    contract = IntentContract(
        agent=base.agent,
        objective=base.objective,
        scope=base.scope,
        out_of_scope=base.out_of_scope,
        risks=base.risks,
        expected_evidence=["command", "output", "artifact", "by_effect"],
    )
    events = default_events()
    verified = next(event for event in events if event.event_type == "verified")
    verified.evidence["by_effect"] = {
        "before": {"invocation_id": "same"},
        "after": {"invocation_id": "same"},
        "changed": True,
    }

    assessment = assess_lifecycle(contract, events)

    assert assessment.allowed_to_close is False
    assert "verified_evidence:by_effect" in assessment.missing


def test_lifecycle_blocks_close_without_audit_when_required() -> None:
    events = [
        LifecycleEvent("intent_recorded", {"artifact": "contract"}),
        LifecycleEvent("executing", {"artifact": "impl"}),
        LifecycleEvent("verified", {"command": "pytest", "output": "passed", "artifact": "test"}),
        LifecycleEvent("closed", {"artifact": "run"}),
    ]
    assessment = assess_lifecycle(default_contract(), events)

    assert assessment.allowed_to_close is False
    assert "event:audited" in assessment.missing
    assert "audit_evidence" in assessment.missing


def test_lifecycle_blocks_out_of_order_events() -> None:
    events = [
        LifecycleEvent("intent_recorded", {"artifact": "contract"}),
        LifecycleEvent("verified", {"command": "pytest", "output": "passed", "artifact": "test"}),
        LifecycleEvent("executing", {"artifact": "impl"}),
        LifecycleEvent("audited", {"reviewer": "NEXUS", "verdict": "pass"}),
        LifecycleEvent("closed", {"artifact": "run"}),
    ]
    assessment = assess_lifecycle(default_contract(), events)

    assert assessment.allowed_to_close is False
    assert "event_order" in assessment.missing


def test_closure_gate_result_expresses_blocking_decision() -> None:
    result = ClosureGateResult(
        task_id=10,
        contract_id=None,
        allowed_to_close=False,
        score=0,
        missing=["contract:not_found"],
        observed_events=[],
        evidence="task_id=10 allowed_to_close=False missing=contract:not_found",
    )

    assert result.allowed_to_close is False
    assert result.score == 0
    assert result.missing == ["contract:not_found"]
