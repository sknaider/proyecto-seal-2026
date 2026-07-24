#!/usr/bin/env python3
"""Build canonical ALICE/ORION evidence from one admitted status record."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA = "seal.nerves.agent-evidence.v1"
ALLOWED_RECORD_KEYS = frozenset(
    {
        "ts",
        "status",
        "service",
        "login_http",
        "backup_age_min",
        "db_user",
        "counts",
        "baseline",
        "fails",
        "post_remediation",
        "actions",
        "escalations",
        "fails_original",
    }
)
ACTIONABLE_STATUSES = frozenset(
    {"FAIL", "CRITICAL", "REMEDIATED", "UNVERIFIABLE"}
)


class OrionEvidenceError(ValueError):
    """Raised when an ORION status record is outside the admitted contract."""


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
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or len(item) > 1000
        for item in value
    ):
        raise OrionEvidenceError(f"{label}_invalid")
    return list(value)


def build_evidence(
    mission_id: str,
    record: Mapping[str, Any],
    *,
    expected_record_sha256: str,
) -> dict[str, Any]:
    if set(record) - ALLOWED_RECORD_KEYS:
        raise OrionEvidenceError("record_contains_unadmitted_keys")
    if record_sha256(record) != expected_record_sha256:
        raise OrionEvidenceError("record_sha256_mismatch")
    status = record.get("status")
    if status not in ACTIONABLE_STATUSES:
        raise OrionEvidenceError("record_status_not_actionable")
    service = record.get("service")
    if not isinstance(service, str) or not service:
        raise OrionEvidenceError("service_invalid")
    login_http = record.get("login_http")
    if isinstance(login_http, bool) or not isinstance(login_http, int):
        raise OrionEvidenceError("login_http_invalid")
    db_user = record.get("db_user", "not_rechecked")
    if not isinstance(db_user, str) or not db_user:
        raise OrionEvidenceError("db_user_invalid")
    failures = _bounded_strings(
        record.get("fails", record.get("fails_original", [])),
        label="fails",
    )
    escalations = record.get("escalations", [])
    if escalations:
        escalations = _bounded_strings(escalations, label="escalations")
    elif not isinstance(escalations, list):
        raise OrionEvidenceError("escalations_invalid")
    actions = record.get("actions", [])
    if not isinstance(actions, list) or any(
        not isinstance(action, dict)
        or not isinstance(action.get("verified_ok"), bool)
        for action in actions
    ):
        raise OrionEvidenceError("actions_invalid")

    checks = [
        {
            "evidence_id": "orion:status",
            "kind": "orion_status",
            "ok": False,
            "value": status,
        },
        {
            "evidence_id": "orion:service",
            "kind": "service_state",
            "ok": service == "active",
            "value": service,
        },
        {
            "evidence_id": "orion:login-http",
            "kind": "http_status",
            "ok": login_http == 200,
            "value": login_http,
        },
        {
            "evidence_id": "orion:db-identity",
            "kind": "database_identity",
            "ok": db_user == "svc_orion_exam",
            "value": db_user,
        },
        {
            "evidence_id": "orion:failures",
            "kind": "failure_list",
            "ok": not failures,
            "value": failures,
        },
        {
            "evidence_id": "orion:escalations",
            "kind": "escalation_list",
            "ok": not escalations,
            "value": escalations,
        },
        {
            "evidence_id": "orion:post-remediation",
            "kind": "post_remediation",
            "ok": (
                status == "REMEDIATED"
                and bool(actions)
                and all(action["verified_ok"] for action in actions)
                and not escalations
            ),
            "value": actions,
        },
    ]
    return {
        "schema": SCHEMA,
        "mission_id": mission_id,
        "agent": "ALICE",
        "action": "orion_product_pulse",
        "read_only": True,
        "source_record_sha256": expected_record_sha256,
        "observed_at": str(record.get("ts") or ""),
        "summary": (
            f"status={status}; service={service}; login_http={login_http}; "
            f"db_user={db_user}; failures={len(failures)}; "
            f"actions={len(actions)}; escalations={len(escalations)}"
        ),
        "checks": checks,
    }
