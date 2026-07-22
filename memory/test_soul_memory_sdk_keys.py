from __future__ import annotations

import sys
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_keys import (  # noqa: E402
    CREATE_CONFIRMATION,
    ROTATE_CONFIRMATION,
    KeyProvisioningError,
    key_preview,
    key_record,
    normalize_expiry,
    normalize_scopes,
    revoke_tenant_key,
    rotate_tenant_key,
)
from soul_memory_sdk_runtime import hash_api_key  # noqa: E402


class FakeConn:
    def __init__(self, fetchval_result: Any = 0, fetchrow_result: Any = None) -> None:
        self.fetchval_result = fetchval_result
        self.fetchrow_result = fetchrow_result
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        return self.fetchval_result

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.fetchrow_calls.append((sql, args))
        return self.fetchrow_result


def test_normalize_scopes_rejects_unknown_and_wildcard_mix() -> None:
    assert normalize_scopes(["read", "tenant:read", "read"]) == ("read", "tenant:read")
    with pytest.raises(KeyProvisioningError, match="unsupported_scopes:admin"):
        normalize_scopes(["admin"])
    with pytest.raises(KeyProvisioningError, match="wildcard_scope_must_stand_alone"):
        normalize_scopes(["*", "read"])


def test_key_record_stores_only_hash_and_metadata() -> None:
    record = key_record(
        "sk-soul-secret",
        scopes=["tenant:read"],
        label="william-prod",
        created_by="ADA",
    )

    assert record["sha256"] == hash_api_key("sk-soul-secret")
    assert record["scopes"] == ["tenant:read"]
    assert record["label"] == "william-prod"
    assert record["created_by"] == "ADA"
    assert "sk-soul-secret" not in repr(record)


def test_key_record_supports_canonical_future_expiry() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    record = key_record(
        "sk-soul-secret",
        scopes=["read"],
        label="short-lived",
        ttl_seconds=300,
        now=now,
    )

    assert record["created_at"] == now.isoformat()
    assert record["expires_at"] == (now + timedelta(seconds=300)).isoformat()


def test_normalize_expiry_rejects_unsafe_values() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    assert normalize_expiry(expires_at="2026-07-22T13:00:00Z", now=now) == "2026-07-22T13:00:00+00:00"
    with pytest.raises(KeyProvisioningError, match="mutually_exclusive"):
        normalize_expiry(expires_at="2026-07-22T13:00:00Z", ttl_seconds=60, now=now)
    with pytest.raises(KeyProvisioningError, match="ttl_seconds_must_be_positive_integer"):
        normalize_expiry(ttl_seconds=0, now=now)
    with pytest.raises(KeyProvisioningError, match="ttl_seconds_out_of_range"):
        normalize_expiry(ttl_seconds=10**30, now=now)
    with pytest.raises(KeyProvisioningError, match="expires_at_timezone_required"):
        normalize_expiry(expires_at="2026-07-22T13:00:00", now=now)
    with pytest.raises(KeyProvisioningError, match="expires_at_must_be_future"):
        normalize_expiry(expires_at="2026-07-22T11:00:00Z", now=now)


def test_key_preview_never_exposes_full_hash() -> None:
    digest = "a" * 64
    preview = key_preview({"sha256": digest, "label": "x", "scopes": ["read"]})

    assert preview == {
        "label": "x",
        "hash_prefix": "a" * 12,
        "scopes": ["read"],
        "created_at": "",
        "expires_at": "",
        "revoked_at": "",
        "rotated_to_hash_prefix": "",
    }
    assert digest not in repr(preview)


def test_key_preview_accepts_jsonb_returned_as_string() -> None:
    preview = key_preview('{"sha256":"bbbbbbbbbbbbcccc","label":"x","scopes":["tenant:read"]}')

    assert preview["hash_prefix"] == "bbbbbbbbbbbb"
    assert preview["label"] == "x"
    assert preview["scopes"] == ["tenant:read"]


@pytest.mark.asyncio
async def test_revoke_tenant_key_requires_specific_selector() -> None:
    conn = FakeConn()
    with pytest.raises(KeyProvisioningError, match="label_or_hash_prefix_required"):
        await revoke_tenant_key(conn, tenant_id="00000000-0000-0000-0000-000000000000", label=None, hash_prefix=None)
    with pytest.raises(KeyProvisioningError, match="hash_prefix_min_12_chars"):
        await revoke_tenant_key(conn, tenant_id="00000000-0000-0000-0000-000000000000", label=None, hash_prefix="abc")


@pytest.mark.asyncio
async def test_revoke_tenant_key_updates_matching_active_rows() -> None:
    conn = FakeConn(fetchval_result=1)
    revoked = await revoke_tenant_key(
        conn,
        tenant_id="00000000-0000-0000-0000-000000000000",
        label="william-prod",
        hash_prefix="a" * 12,
    )
    sql, args = conn.fetchval_calls[0]

    assert revoked == 1
    assert "jsonb_set" in sql
    assert "revoked_at" in sql
    assert args[1] == "william-prod"
    assert args[2] == "a" * 12


@pytest.mark.asyncio
async def test_rotate_tenant_key_is_one_atomic_update_and_never_stores_raw_key() -> None:
    conn = FakeConn(fetchrow_result={"match_count": 1, "api_keys_len": 3})
    result = await rotate_tenant_key(
        conn,
        tenant_id="00000000-0000-0000-0000-000000000000",
        current_label="william-prod",
        current_hash_prefix="a" * 12,
        scopes=["tenant:read"],
        new_label="william-prod",
        ttl_seconds=3600,
    )
    sql, args = conn.fetchrow_calls[0]
    stored_record = json.loads(args[5])[0]

    assert result["rotated"] == 1
    assert result["api_keys_len"] == 3
    assert result["raw_key"].startswith("sk-soul-")
    assert sql.count("UPDATE soul_v3.tenants") == 1
    assert "FOR UPDATE" in sql
    assert "match_count=1" in sql
    assert "revoked_at" in sql
    assert "rotated_to_sha256" in sql
    assert stored_record["sha256"] == hash_api_key(result["raw_key"])
    assert stored_record["expires_at"]
    assert result["raw_key"] not in repr(args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "error"),
    [
        ({"match_count": -1, "api_keys_len": None}, "tenant_not_found"),
        ({"match_count": 0, "api_keys_len": None}, "rotation_source_not_found"),
        ({"match_count": 2, "api_keys_len": None}, "rotation_source_ambiguous"),
    ],
)
async def test_rotate_tenant_key_fails_closed_without_exactly_one_source(row: dict[str, Any], error: str) -> None:
    conn = FakeConn(fetchrow_result=row)
    with pytest.raises(KeyProvisioningError, match=error):
        await rotate_tenant_key(
            conn,
            tenant_id="00000000-0000-0000-0000-000000000000",
            current_label="duplicate",
            current_hash_prefix=None,
            scopes=["read"],
            new_label="replacement",
        )


@pytest.mark.asyncio
async def test_rotate_tenant_key_rejects_ambiguous_selector_input_before_sql() -> None:
    conn = FakeConn()
    with pytest.raises(KeyProvisioningError, match="current_label_or_hash_prefix_required"):
        await rotate_tenant_key(
            conn,
            tenant_id="00000000-0000-0000-0000-000000000000",
            current_label=None,
            current_hash_prefix=None,
            scopes=["read"],
            new_label="replacement",
        )
    assert conn.fetchrow_calls == []


def test_cli_confirmation_constant_is_explicit() -> None:
    assert CREATE_CONFIRMATION == "CREATE_TENANT_KEY"
    assert ROTATE_CONFIRMATION == "ROTATE_TENANT_KEY"
