"""Shared integrity primitives for reversible SOUL memory consolidation.

This module deliberately contains no autonomous delete/invalidate operation.
It provides deterministic manifests, locked-row validation and an append-only
flight record written in the same PostgreSQL transaction as an approved change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence


GLOBAL_PROTECTED_CATEGORIES = frozenset(
    {
        "identity",
        "core",
        "core_belief",
        "trust",
        "correction",
        "rule",
        "decision",
        "milestone",
        "emotion",
        "emotional_anchor",
        "relationship",
        "diary",
    }
)

MUTABLE_SNAPSHOT_FIELDS = (
    "id",
    "agent",
    "tenant_id",
    "scope",
    "category",
    "content_hash_sha256",
    "importance",
    "invalid_at",
    "superseded_by",
    "metadata",
    "updated_at",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_text(canonical_json(value))


def candidate_manifest_sha256(candidates: Sequence[Mapping[str, Any]]) -> str:
    """Bind approval to the full ordered candidate payload, not only count."""
    normalized = sorted((dict(c) for c in candidates), key=lambda c: int(c["memory_id"]))
    return sha256_value(normalized)


def snapshot_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in MUTABLE_SNAPSHOT_FIELDS}


def content_sha256(row: Mapping[str, Any]) -> str:
    stored = str(row.get("content_hash_sha256") or "").strip()
    if stored:
        return stored
    return sha256_text(str(row.get("content") or ""))


def assert_candidate_matches_locked_row(candidate: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    memory_id = int(candidate.get("memory_id") or 0)
    if memory_id != int(row.get("id") or 0):
        raise RuntimeError(f"target_id_mismatch candidate={memory_id} locked={row.get('id')}")
    expected_hash = str(candidate.get("content_hash") or "").strip()
    actual_hash = content_sha256(row)
    if not expected_hash or expected_hash != actual_hash:
        raise RuntimeError(
            f"target_content_hash_mismatch memory_id={memory_id} "
            f"expected={expected_hash or '(missing)'} actual={actual_hash}"
        )
    current = candidate.get("current") or {}
    for field in ("category", "importance", "scope"):
        if field in current and current[field] != row.get(field):
            raise RuntimeError(
                f"target_snapshot_mismatch memory_id={memory_id} field={field} "
                f"expected={current[field]!r} actual={row.get(field)!r}"
            )


async def record_flight_event(
    conn: Any,
    *,
    agent: str,
    phase: str,
    action: str,
    rollback_token: str,
    batch_id: str,
    manifest_sha256: str,
    memory_id: int | None = None,
    archive_id: int | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    outcome: str = "ok",
) -> str:
    """Append one hash-chained flight event without storing raw memory content."""
    previous_hash = await conn.fetchval(
        """SELECT metadata->>'event_hash'
           FROM soul_v3.event_log
           WHERE event_type='memory_consolidation_audit'
             AND metadata->>'rollback_token'=$1
           ORDER BY id DESC LIMIT 1""",
        rollback_token,
    )
    payload = {
        "version": 1,
        "agent": agent.upper(),
        "phase": phase,
        "action": action,
        "rollback_token": rollback_token,
        "batch_id": batch_id,
        "manifest_sha256": manifest_sha256,
        "memory_id": memory_id,
        "archive_id": archive_id,
        "before_sha256": sha256_value(snapshot_row(before)) if before else None,
        "after_sha256": sha256_value(snapshot_row(after)) if after else None,
        "previous_event_hash": previous_hash,
        "outcome": outcome,
    }
    event_hash = sha256_text(f"{previous_hash or ''}:{canonical_json(payload)}")
    payload["event_hash"] = event_hash
    await conn.execute(
        """INSERT INTO soul_v3.event_log(agent,event_type,content,metadata)
           VALUES($1,'memory_consolidation_audit',$2,$3::jsonb)""",
        agent.upper(),
        f"{phase} {action} memory_id={memory_id} outcome={outcome}",
        canonical_json(payload),
    )
    return event_hash

