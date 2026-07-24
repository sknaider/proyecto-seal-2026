#!/usr/bin/env python3
"""Build canonical evidence from one admitted FABLE rigor-ledger record."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA = "seal.nerves.agent-evidence.v1"
RECORD_SCHEMA = "seal.fable.rigor-claim-audit.v2"
ALLOWED_RECORD_KEYS = frozenset(
    {
        "schema",
        "event_id",
        "ts",
        "agent",
        "action",
        "target",
        "action_source",
        "contract_rev",
        "run_id",
        "state",
        "status",
        "claim",
        "calibration",
        "checks",
        "findings",
        "broken",
        "detail",
    }
)
ALLOWED_CHECK_KEYS = frozenset(
    {
        "evidence_id",
        "kind",
        "required",
        "status",
        "reason_code",
        "expected",
        "observed",
        "source_ref",
    }
)


class RigorEvidenceError(ValueError):
    """Raised when a rigor record is outside the admitted contract."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def record_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(dict(record))).hexdigest()


def _bounded_json(value: Any, *, label: str, maximum: int = 4000) -> Any:
    try:
        raw = canonical_bytes(value)
    except (TypeError, ValueError) as exc:
        raise RigorEvidenceError(f"{label}_not_json") from exc
    if len(raw) > maximum:
        raise RigorEvidenceError(f"{label}_too_large")
    return value


def _bounded_string(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise RigorEvidenceError(f"{label}_invalid")
    return value


def _validate_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(record) != ALLOWED_RECORD_KEYS:
        raise RigorEvidenceError("record_shape_invalid")
    if (
        record.get("schema") != RECORD_SCHEMA
        or record.get("agent") != "FABLE"
        or record.get("action") != "rigor_pulse"
        or record.get("target") != "rigor_claim_audit"
        or record.get("action_source") != "fable/rigor_claim_audit.py"
        or record.get("contract_rev") != "2"
    ):
        raise RigorEvidenceError("record_route_mismatch")
    if (
        record.get("state") not in {"FINDING", "BROKEN"}
        or record.get("status") != "issue"
    ):
        raise RigorEvidenceError("record_not_actionable")
    event_id = _bounded_string(
        record.get("event_id"), label="event_id", maximum=64
    )
    without_event = dict(record)
    without_event.pop("event_id")
    if event_id != hashlib.sha256(canonical_bytes(without_event)).hexdigest():
        raise RigorEvidenceError("event_id_mismatch")
    claim = record.get("claim")
    calibration = record.get("calibration")
    if not isinstance(claim, dict) or set(claim) != {
        "claim_id",
        "kind",
        "subject_ref",
        "source_record_sha256",
        "assertion",
    }:
        raise RigorEvidenceError("claim_shape_invalid")
    if not isinstance(calibration, dict) or set(calibration) != {
        "probe_id",
        "probe_sha256",
        "status",
        "positive_control_id",
        "negative_control_id",
    }:
        raise RigorEvidenceError("calibration_shape_invalid")
    if calibration.get("status") != "PASS":
        raise RigorEvidenceError("calibration_not_passed")
    checks = record.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or len(checks) > 64
    ):
        raise RigorEvidenceError("checks_invalid")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != ALLOWED_CHECK_KEYS:
            raise RigorEvidenceError("check_shape_invalid")
        evidence_id = _bounded_string(
            check.get("evidence_id"), label="evidence_id", maximum=200
        )
        if evidence_id in seen:
            raise RigorEvidenceError("duplicate_evidence_id")
        seen.add(evidence_id)
        if check.get("kind") not in {
            "direct_effect",
            "provenance",
            "deployment_identity",
            "positive_control",
            "negative_control",
            "coverage",
        }:
            raise RigorEvidenceError("check_kind_invalid")
        if check.get("status") not in {"PASS", "FAIL", "UNKNOWN"}:
            raise RigorEvidenceError("check_status_invalid")
        if not isinstance(check.get("required"), bool):
            raise RigorEvidenceError("check_required_invalid")
        _bounded_string(
            check.get("reason_code"), label="reason_code", maximum=200
        )
        _bounded_string(
            check.get("source_ref"), label="source_ref", maximum=500
        )
        _bounded_json(check.get("expected"), label="expected")
        _bounded_json(check.get("observed"), label="observed")
        validated.append(dict(check))
    derived_findings = sorted(
        {
            check["reason_code"]
            for check in validated
            if check["required"] and check["status"] == "FAIL"
        }
    )
    derived_broken = sorted(
        {
            check["reason_code"]
            for check in validated
            if check["required"] and check["status"] == "UNKNOWN"
        }
    )
    if record.get("findings") != derived_findings:
        raise RigorEvidenceError("findings_derivation_mismatch")
    if record.get("broken") != derived_broken:
        raise RigorEvidenceError("broken_derivation_mismatch")
    if record["state"] == "FINDING" and (
        not derived_findings or derived_broken
    ):
        raise RigorEvidenceError("finding_state_semantics_invalid")
    if record["state"] == "BROKEN" and not derived_broken:
        raise RigorEvidenceError("broken_state_without_unknown")
    return validated


def build_evidence(
    mission_id: str,
    record: Mapping[str, Any],
    *,
    expected_record_sha256: str,
) -> dict[str, Any]:
    if record_sha256(record) != expected_record_sha256:
        raise RigorEvidenceError("record_sha256_mismatch")
    checks = _validate_record(record)
    evidence_checks = [
        {
            "evidence_id": check["evidence_id"],
            "kind": check["kind"],
            "ok": check["status"] == "PASS",
            "value": {
                "status": check["status"],
                "reason_code": check["reason_code"],
                "expected": check["expected"],
                "observed": check["observed"],
                "source_ref": check["source_ref"],
            },
        }
        for check in checks
        if check["required"]
    ]
    return {
        "schema": SCHEMA,
        "mission_id": mission_id,
        "agent": "FABLE",
        "action": "rigor_pulse",
        "read_only": True,
        "source_record_sha256": expected_record_sha256,
        "observed_at": str(record["ts"]),
        "summary": (
            f"state={record['state']}; required_checks={len(evidence_checks)}; "
            f"findings={len(record['findings'])}; "
            f"unverifiable={len(record['broken'])}; calibration=PASS"
        ),
        "checks": evidence_checks,
    }
