from __future__ import annotations

import hashlib

import pytest

from collect_rigor_evidence import (
    RigorEvidenceError,
    build_evidence,
    canonical_bytes,
    record_sha256,
)


def _record(*, status: str = "FAIL") -> dict:
    check = {
        "evidence_id": "rigor:receipt:ada:m1",
        "kind": "direct_effect",
        "required": True,
        "status": status,
        "reason_code": "receipt_binding_or_verifier_mismatch",
        "expected": {"sha256_match": True},
        "observed": {"sha256_match": status == "PASS"},
        "source_ref": "research/flywire_results/nerves_orchestrator_inbox/ADA/m1.receipt.json",
    }
    record = {
        "schema": "seal.fable.rigor-claim-audit.v2",
        "ts": "2026-07-24T03:00:00+00:00",
        "agent": "FABLE",
        "action": "rigor_pulse",
        "target": "rigor_claim_audit",
        "action_source": "fable/rigor_claim_audit.py",
        "contract_rev": "2",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "state": "FINDING",
        "status": "issue",
        "claim": {
            "claim_id": "a" * 64,
            "kind": "nerves_receipt_integrity",
            "subject_ref": "research/flywire_results/nerves_orchestrator_inbox",
            "source_record_sha256": "b" * 64,
            "assertion": "One receipt is inconsistent.",
        },
        "calibration": {
            "probe_id": "receipt-hash-discriminant-v1",
            "probe_sha256": "c" * 64,
            "status": "PASS",
            "positive_control_id": "known-canonical-receipt",
            "negative_control_id": "single-byte-corruption",
        },
        "checks": [check],
        "findings": ["receipt_binding_or_verifier_mismatch"],
        "broken": [],
        "detail": "states=4 checks=1 required=1 failed=1 unknown=0",
    }
    record["event_id"] = hashlib.sha256(canonical_bytes(record)).hexdigest()
    return record


def test_builds_typed_finding_evidence():
    record = _record()
    evidence = build_evidence(
        "11111111-1111-4111-8111-111111111111",
        record,
        expected_record_sha256=record_sha256(record),
    )
    assert evidence["agent"] == "FABLE"
    assert evidence["checks"][0]["value"]["status"] == "FAIL"


def test_rejects_hash_and_derived_finding_mismatch():
    record = _record()
    with pytest.raises(RigorEvidenceError, match="sha256_mismatch"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            record,
            expected_record_sha256="0" * 64,
        )
    bad = record | {"findings": []}
    bad["event_id"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in bad.items() if k != "event_id"})
    ).hexdigest()
    with pytest.raises(RigorEvidenceError, match="derivation_mismatch"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            bad,
            expected_record_sha256=record_sha256(bad),
        )


def test_rejects_extra_fields_and_missing_calibration():
    record = _record()
    bad = record | {"stdout_tail": "untrusted"}
    with pytest.raises(RigorEvidenceError, match="record_shape_invalid"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            bad,
            expected_record_sha256=record_sha256(bad),
        )
    bad = _record()
    bad["calibration"] = bad["calibration"] | {"status": "FAIL"}
    bad["event_id"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in bad.items() if k != "event_id"})
    ).hexdigest()
    with pytest.raises(RigorEvidenceError, match="calibration_not_passed"):
        build_evidence(
            "11111111-1111-4111-8111-111111111111",
            bad,
            expected_record_sha256=record_sha256(bad),
        )
