#!/usr/bin/env python3
"""Build one canonical ADA engineering evidence document from a pulse record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "seal.nerves.agent-evidence.v1"
ALLOWED_RECORD_KEYS = frozenset(
    {
        "ts",
        "agent",
        "action",
        "changed_python_checked",
        "diff_check_ok",
        "syntax_failures",
        "status",
    }
)


class EngineeringEvidenceError(ValueError):
    """Raised when a pulse cannot be admitted as ADA engineering evidence."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def record_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(dict(record))).hexdigest()


def build_evidence(
    mission_id: str,
    record: Mapping[str, Any],
    *,
    expected_record_sha256: str,
) -> dict[str, Any]:
    if set(record) - ALLOWED_RECORD_KEYS:
        raise EngineeringEvidenceError("record_contains_unadmitted_keys")
    if record.get("agent") != "ADA":
        raise EngineeringEvidenceError("record_agent_must_be_ADA")
    if record.get("action") != "engineering_pulse":
        raise EngineeringEvidenceError("record_action_must_be_engineering_pulse")
    if record.get("status") != "issue":
        raise EngineeringEvidenceError("record_status_must_be_issue")
    if record_sha256(record) != expected_record_sha256:
        raise EngineeringEvidenceError("record_sha256_mismatch")

    syntax_failures = record.get("syntax_failures")
    if not isinstance(syntax_failures, list) or any(
        not isinstance(path, str) or not path or len(path) > 500
        for path in syntax_failures
    ):
        raise EngineeringEvidenceError("syntax_failures_invalid")
    diff_check_ok = record.get("diff_check_ok")
    if not isinstance(diff_check_ok, bool):
        raise EngineeringEvidenceError("diff_check_ok_invalid")
    changed = record.get("changed_python_checked")
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise EngineeringEvidenceError("changed_python_checked_invalid")

    checks: list[dict[str, Any]] = [
        {
            "evidence_id": "engineering:git-diff-check",
            "kind": "git_diff_check",
            "ok": diff_check_ok,
            "value": "clean" if diff_check_ok else "whitespace_error",
        },
        {
            "evidence_id": "engineering:python-syntax",
            "kind": "python_syntax",
            "ok": not syntax_failures,
            "value": list(syntax_failures),
        },
        {
            "evidence_id": "engineering:changed-python-count",
            "kind": "changed_python_count",
            "ok": True,
            "value": changed,
        },
    ]
    return {
        "schema": SCHEMA,
        "mission_id": mission_id,
        "agent": "ADA",
        "action": "engineering_pulse",
        "read_only": True,
        "source_record_sha256": expected_record_sha256,
        "observed_at": str(record.get("ts") or ""),
        "summary": (
            f"diff_check_ok={diff_check_ok}; "
            f"syntax_failures={len(syntax_failures)}; "
            f"changed_python_checked={changed}"
        ),
        "checks": checks,
    }


def load_record(path: Path, expected_sha256: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EngineeringEvidenceError(
                f"artifact_invalid_json_line_{number}"
            ) from exc
        if isinstance(value, dict) and record_sha256(value) == expected_sha256:
            matches.append(value)
    if len(matches) != 1:
        raise EngineeringEvidenceError(
            f"record_match_count_must_be_one:{len(matches)}"
        )
    return matches[0]
