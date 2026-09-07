from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_metering import (  # noqa: E402
    BasicSdkMetrics,
    DurableQuotaMeter,
    InvalidIdempotencyKey,
    QuotaUnavailable,
    derive_request_id,
    new_request_id,
    normalize_operation,
)


class _Acquire:
    def __init__(self, conn: object) -> None:
        self.conn = conn

    async def __aenter__(self) -> object:
        return self.conn

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: object) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


class _Conn:
    def __init__(self, row: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self.row = row
        self.error = error
        self.args: tuple[object, ...] = ()

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        if self.error is not None:
            raise self.error
        assert "sdk_consume_quota" in sql
        self.args = args
        return self.row


def test_request_ids_are_canonical_server_uuids() -> None:
    first = new_request_id()
    second = new_request_id()
    assert first != second
    assert str(UUID(first)) == first
    assert str(UUID(second)) == second


def test_external_idempotency_is_stable_and_bound_to_exact_authenticated_request() -> None:
    base = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "api_key_hash": "a" * 64,
        "idempotency_key": "retry.order-0001",
        "operation": "create",
        "method": "POST",
        "path": "/v1/memories",
        "query_bytes": b"",
        "body_bytes": b'{"content":"same"}',
    }
    first = derive_request_id(**base)
    assert derive_request_id(**base) == first
    assert str(UUID(first)) == first
    for field, changed in (
        ("tenant_id", "00000000-0000-0000-0000-000000000002"),
        ("api_key_hash", "b" * 64),
        ("idempotency_key", "retry.order-0002"),
        ("path", "/v1/recall"),
        ("body_bytes", b'{"content":"different"}'),
    ):
        variant = {**base, field: changed}
        assert derive_request_id(**variant) != first


def test_missing_idempotency_key_preserves_internal_compatibility_with_unique_ids() -> None:
    kwargs = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "api_key_hash": "a" * 64,
        "idempotency_key": None,
        "operation": "read",
        "method": "GET",
        "path": "/v1/memories",
    }
    assert derive_request_id(**kwargs) != derive_request_id(**kwargs)


@pytest.mark.parametrize("value", ["short", "contains space", "x" * 129, "\u2603" * 8])
def test_idempotency_key_contract_fails_closed(value: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        derive_request_id(
            tenant_id="00000000-0000-0000-0000-000000000001",
            api_key_hash="a" * 64,
            idempotency_key=value,
            operation="read",
            method="GET",
            path="/v1/memories",
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("create", "create"), ("list", "read"), ("get", "read"), ("read", "read"), ("recall", "recall")],
)
def test_operation_normalization_is_allowlisted(raw: str, expected: str) -> None:
    assert normalize_operation(raw) == expected


def test_operation_normalization_fails_closed() -> None:
    with pytest.raises(QuotaUnavailable, match="unsupported_metering_operation"):
        normalize_operation("admin")


@pytest.mark.asyncio
async def test_durable_meter_returns_validated_decision() -> None:
    conn = _Conn(
        {
            "allowed": True,
            "plan_id": "pro",
            "remaining": 41,
            "retry_after": 0,
            "reason": "allowed",
            "duplicate": False,
        }
    )
    request_id = new_request_id()
    decision = await DurableQuotaMeter().consume(
        _Pool(conn),
        tenant_id="00000000-0000-0000-0000-000000000000",
        api_key_hash="a" * 64,
        request_id=request_id,
        operation="list",
    )
    assert decision.allowed is True
    assert decision.plan_id == "pro"
    assert decision.remaining == 41
    assert conn.args[2] == request_id
    assert conn.args[3] == "read"


@pytest.mark.asyncio
async def test_durable_meter_never_allows_on_database_error() -> None:
    with pytest.raises(QuotaUnavailable, match="durable_quota_unavailable"):
        await DurableQuotaMeter().consume(
            _Pool(_Conn(error=RuntimeError("database down"))),
            tenant_id="00000000-0000-0000-0000-000000000000",
            api_key_hash="a" * 64,
            request_id=new_request_id(),
            operation="read",
        )


@pytest.mark.asyncio
async def test_durable_meter_rejects_malformed_identity_before_database() -> None:
    with pytest.raises(QuotaUnavailable, match="invalid_quota_identity"):
        await DurableQuotaMeter().consume(
            _Pool(_Conn()),
            tenant_id="attacker",
            api_key_hash="a" * 64,
            request_id=new_request_id(),
            operation="read",
        )
    with pytest.raises(QuotaUnavailable, match="invalid_api_key_fingerprint"):
        await DurableQuotaMeter().consume(
            _Pool(_Conn()),
            tenant_id="00000000-0000-0000-0000-000000000000",
            api_key_hash="raw-secret-is-not-a-fingerprint",
            request_id=new_request_id(),
            operation="read",
        )


def test_basic_metrics_are_aggregate_and_bounded() -> None:
    metrics = BasicSdkMetrics()
    metrics.observe(operation="list", status="ok", duration_ms=4)
    metrics.observe(operation="recall", status="failed", duration_ms=7)
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == {"read:ok": 1, "recall:failed": 1}
    assert snapshot["duration_ms"] == {"read": 4, "recall": 7}
    assert "tenant" not in str(snapshot).lower()
    assert "api_key" not in str(snapshot).lower()


def test_migration_is_durable_idempotent_and_privacy_safe() -> None:
    sql = (Path(__file__).parent / "migrations/056_soul_sdk_durable_metering.sql").read_text()
    required = [
        "CREATE TABLE IF NOT EXISTS soul_v3.sdk_usage_plans",
        "CREATE TABLE IF NOT EXISTS soul_v3.sdk_tenant_subscriptions",
        "CREATE TABLE IF NOT EXISTS soul_v3.sdk_usage_windows",
        "CREATE TABLE IF NOT EXISTS soul_v3.sdk_usage_events",
        "request_id uuid PRIMARY KEY",
        "PRIMARY KEY (tenant_id, plan_id, window_start)",
        "pg_advisory_xact_lock",
        "FOR SHARE OF subscription, plan",
        "SECURITY DEFINER",
        "SET search_path = ''",
        "session_user <> 'svc_soul_memory_sdk'",
        "OWNER TO soul_sdk_metering_owner",
        "CREATE ROLE soul_sdk_metering_owner NOLOGIN NOSUPERUSER",
        "CREATE ROLE soul_sdk_onboarding_owner NOLOGIN NOSUPERUSER",
        "CREATE ROLE soul_sdk_onboarding_operator NOLOGIN NOSUPERUSER",
        "sdk_provision_external_tenant_roles",
        "retained_until",
        "REVOKE ALL ON FUNCTION soul_v3.sdk_consume_quota",
        "REVOKE ALL ON soul_v3.sdk_usage_events FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION soul_v3.sdk_consume_quota",
    ]
    for contract in required:
        assert contract in sql
    lowered = sql.lower()
    for forbidden in ("raw_api_key", "query_text", "request_body", "user_id", "ip_address"):
        assert forbidden not in lowered
    windows_sql = sql.split("CREATE TABLE IF NOT EXISTS soul_v3.sdk_usage_windows", 1)[1].split(
        "CREATE TABLE IF NOT EXISTS soul_v3.sdk_usage_events", 1
    )[0]
    assert "api_key_hash" not in windows_sql
    function_sql = sql.split("CREATE OR REPLACE FUNCTION soul_v3.sdk_consume_quota", 1)[1]
    assert "tenant.quotas" not in function_sql
    for plan in ("'free'", "'pro'", "'enterprise'", "'internal'"):
        assert plan in sql
