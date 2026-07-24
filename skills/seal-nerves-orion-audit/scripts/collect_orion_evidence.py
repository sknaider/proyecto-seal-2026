#!/usr/bin/env python3
"""Build canonical ALICE/ORION evidence from one admitted status record."""

from __future__ import annotations

import hashlib
import json
import re
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
FAILURE_PREFIXES = (
    "servicio orion-exam no activo (",
    "no pude consultar el servicio:",
    "/login devolvió ",
    "/login inaccesible:",
    "no hay snapshots de backup",
    "backup viejo (",
    "identidad ORION inválida:",
    "DSN del proceso vivo difiere del EnvironmentFile",
    "no pude verificar identidad viva:",
    "sesión DB autenticó como ",
    "baseline de conteos ausente (",
    "orion_exam.",
    "no pude verificar conteos restringidos:",
    "reinicié orion-exam pero ",
    "backup pendiente y ya se intentó ",
    "corrí el backup pero ",
)
IDENTITY_PATTERN = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]{0,62}|unknown|not_rechecked)$"
)
SERVICE_PATTERN = re.compile(r"^[a-z][a-z-]{0,31}$")


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


def _admit_failures(values: list[str]) -> tuple[list[str], int]:
    admitted = [
        value for value in values if value.startswith(FAILURE_PREFIXES)
    ]
    return admitted, len(values) - len(admitted)


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
    if (
        not isinstance(service, str)
        or SERVICE_PATTERN.fullmatch(service) is None
    ):
        raise OrionEvidenceError("service_invalid")
    login_http = record.get("login_http")
    if isinstance(login_http, bool) or not isinstance(login_http, int):
        raise OrionEvidenceError("login_http_invalid")
    db_user = record.get("db_user", "not_rechecked")
    if (
        not isinstance(db_user, str)
        or IDENTITY_PATTERN.fullmatch(db_user) is None
    ):
        raise OrionEvidenceError("db_user_invalid")
    raw_failures = _bounded_strings(
        record.get("fails", record.get("fails_original", [])),
        label="fails",
    )
    failures, rejected_failures = _admit_failures(raw_failures)
    escalations = record.get("escalations", [])
    if escalations:
        raw_escalations = _bounded_strings(
            escalations, label="escalations"
        )
        escalations, rejected_escalations = _admit_failures(
            raw_escalations
        )
    elif not isinstance(escalations, list):
        raise OrionEvidenceError("escalations_invalid")
    else:
        rejected_escalations = 0
    rejected_count = rejected_failures + rejected_escalations
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
            "evidence_id": "orion:source-data-integrity",
            "kind": "source_data_integrity",
            "ok": rejected_count == 0,
            "value": {
                "rejected_failures": rejected_failures,
                "rejected_escalations": rejected_escalations,
            },
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
            f"actions={len(actions)}; escalations={len(escalations)}; "
            f"rejected_entries={rejected_count}"
        ),
        "checks": checks,
    }
