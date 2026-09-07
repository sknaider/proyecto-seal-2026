from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consolidation_integrity import (
    GLOBAL_PROTECTED_CATEGORIES,
    assert_candidate_matches_locked_row,
    candidate_manifest_sha256,
    record_flight_event,
)


def _row(**overrides):
    content = overrides.pop("content", "same")
    base = {
        "id": 7,
        "agent": "ADA",
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "scope": "private",
        "category": "pattern",
        "content": content,
        "content_hash_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "importance": 5,
        "invalid_at": None,
        "superseded_by": None,
        "metadata": {},
        "updated_at": None,
    }
    base.update(overrides)
    return base


def test_manifest_is_order_independent_but_payload_sensitive():
    a = {"memory_id": 1, "content_hash": "a", "action": "x"}
    b = {"memory_id": 2, "content_hash": "b", "action": "x"}
    assert candidate_manifest_sha256([a, b]) == candidate_manifest_sha256([b, a])
    assert candidate_manifest_sha256([a, b]) != candidate_manifest_sha256([a, {**b, "content_hash": "c"}])


def test_global_policy_protects_operational_and_emotional_anchors():
    assert {"identity", "trust", "correction", "rule", "emotion", "emotional_anchor"} <= GLOBAL_PROTECTED_CATEGORIES


def test_locked_target_hash_and_snapshot_must_match():
    row = _row()
    candidate = {
        "memory_id": 7,
        "content_hash": row["content_hash_sha256"],
        "current": {"category": "pattern", "importance": 5, "scope": "private"},
    }
    assert_candidate_matches_locked_row(candidate, row)
    with pytest.raises(RuntimeError, match="target_content_hash_mismatch"):
        assert_candidate_matches_locked_row({**candidate, "content_hash": "bad"}, row)
    with pytest.raises(RuntimeError, match="target_snapshot_mismatch"):
        assert_candidate_matches_locked_row({**candidate, "current": {"importance": 9}}, row)


@pytest.mark.asyncio
async def test_flight_event_is_hash_chained_and_redacts_content():
    conn = AsyncMock()
    conn.fetchval.return_value = "prev"
    before = _row(content="private secret")
    event_hash = await record_flight_event(
        conn,
        agent="ADA",
        phase="apply",
        action="duplicate",
        rollback_token="tok",
        batch_id="batch",
        manifest_sha256="m" * 64,
        memory_id=7,
        before=before,
        after={**before, "invalid_at": "now"},
    )
    assert len(event_hash) == 64
    args = conn.execute.await_args.args
    assert "private secret" not in args[-1]
    assert '"previous_event_hash":"prev"' in args[-1]

