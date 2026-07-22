from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_keys import (  # noqa: E402
    CREATE_CONFIRMATION,
    KeyProvisioningError,
    key_preview,
    key_record,
    normalize_scopes,
    revoke_tenant_key,
)
from soul_memory_sdk_runtime import hash_api_key  # noqa: E402


class FakeConn:
    def __init__(self, fetchval_result: Any = 0) -> None:
        self.fetchval_result = fetchval_result
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        return self.fetchval_result


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


def test_key_preview_never_exposes_full_hash() -> None:
    digest = "a" * 64
    preview = key_preview({"sha256": digest, "label": "x", "scopes": ["read"]})

    assert preview == {
        "label": "x",
        "hash_prefix": "a" * 12,
        "scopes": ["read"],
        "created_at": "",
        "revoked_at": "",
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


def test_cli_confirmation_constant_is_explicit() -> None:
    assert CREATE_CONFIRMATION == "CREATE_TENANT_KEY"
