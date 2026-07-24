#!/usr/bin/env python3
"""Build canonical NEXUS security evidence from one admitted pulse record."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA = "seal.nerves.agent-evidence.v1"
ALLOWED_RECORD_KEYS = frozenset(
    {
        "ts",
        "agent",
        "action",
        "action_source",
        "state",
        "status",
        "findings",
        "broken",
        "detail",
    }
)
ACTIONABLE_STATES = frozenset({"FINDING", "BROKEN"})


class SecurityEvidenceError(ValueError):
    """Raised when a security-pulse record is outside the admitted contract."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def record_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(dict(record))).hexdigest()


def _bounded_strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 10 or any(
        not isinstance(item, str) or not item or len(item) > 1000
        for item in value
    ):
        raise SecurityEvidenceError(f"{label}_invalid")
    return list(value)


def build_evidence(
    mission_id: str,
    record: Mapping[str, Any],
    *,
    expected_record_sha256: str,
) -> dict[str, Any]:
    if set(record) - ALLOWED_RECORD_KEYS:
        raise SecurityEvidenceError("record_contains_unadmitted_keys")
    if record_sha256(record) != expected_record_sha256:
        raise SecurityEvidenceError("record_sha256_mismatch")
    if record.get("agent") != "NEXUS" or record.get("action") != "security_pulse":
        raise SecurityEvidenceError("record_route_mismatch")
    if record.get("action_source") != "tools/nexus_nerves_watch.py":
        raise SecurityEvidenceError("record_source_mismatch")
    state = record.get("state")
    if state not in ACTIONABLE_STATES or record.get("status") != "issue":
        raise SecurityEvidenceError("record_not_actionable")
    findings = _bounded_strings(record.get("findings"), label="findings")
    broken = _bounded_strings(record.get("broken"), label="broken")
    detail = record.get("detail")
    if not isinstance(detail, str) or not detail or len(detail) > 800:
        raise SecurityEvidenceError("detail_invalid")
    if state == "FINDING" and not findings:
        raise SecurityEvidenceError("finding_state_without_findings")
    if state == "BROKEN" and not broken:
        raise SecurityEvidenceError("broken_state_without_failures")

    checks = [
        {
            "evidence_id": "security:pulse-state",
            "kind": "security_pulse_state",
            "ok": False,
            "value": state,
        },
        {
            "evidence_id": "security:findings",
            "kind": "confirmed_security_findings",
            "ok": not findings,
            "value": findings,
        },
        {
            "evidence_id": "security:instrument",
            "kind": "instrument_failures",
            "ok": not broken,
            "value": broken,
        },
        {
            "evidence_id": "security:integrity-summary",
            "kind": "bounded_integrity_summary",
            "ok": state not in ACTIONABLE_STATES,
            "value": detail,
        },
    ]
    return {
        "schema": SCHEMA,
        "mission_id": mission_id,
        "agent": "NEXUS",
        "action": "security_pulse",
        "read_only": True,
        "source_record_sha256": expected_record_sha256,
        "observed_at": str(record.get("ts") or ""),
        "summary": (
            f"state={state}; findings={len(findings)}; "
            f"instrument_failures={len(broken)}; detail={detail}"
        ),
        "checks": checks,
    }
