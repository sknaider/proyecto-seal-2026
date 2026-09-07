"""Durable, privacy-safe quota metering for the public SOUL Memory SDK.

The database function installed by migration 056 is the authority.  This
module deliberately contains no fallback to an in-memory allow decision: if
the durable counter cannot be reached or returns an invalid record, public
traffic fails closed.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4


API_KEY_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
OPERATIONS = {"create", "read", "recall"}


class QuotaUnavailable(RuntimeError):
    """Raised when a durable quota decision cannot be obtained safely."""


class InvalidIdempotencyKey(ValueError):
    """Raised when an external idempotency key is outside the public contract."""


@dataclass(frozen=True)
class DurableQuotaDecision:
    allowed: bool
    plan_id: str
    remaining: int
    retry_after: int
    reason: str
    duplicate: bool = False

    def headers(self) -> dict[str, str]:
        headers = {
            "X-SOUL-Quota-Plan": self.plan_id,
            "X-SOUL-Quota-Remaining": str(max(0, self.remaining)),
        }
        if not self.allowed:
            headers["Retry-After"] = str(max(1, self.retry_after))
        return headers


def new_request_id() -> str:
    """Return a server-generated correlation/idempotency identifier."""
    return str(uuid4())


def normalize_idempotency_key(value: str | None) -> str | None:
    """Validate the optional public retry key without accepting arbitrary data."""

    if value is None:
        return None
    if not IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise InvalidIdempotencyKey(
            "idempotency_key_must_be_8_to_128_safe_ascii_characters"
        )
    return value


def derive_request_id(
    *,
    tenant_id: str,
    api_key_hash: str,
    idempotency_key: str | None,
    operation: str,
    method: str,
    path: str,
    query_bytes: bytes = b"",
    body_bytes: bytes = b"",
) -> str:
    """Derive a stable opaque UUID for an identical authenticated retry.

    The tenant and API-key identity are server-derived before this function is
    called.  Query/body bytes are only HMACed in memory; neither they nor the
    external key are persisted.  Reusing a key with a different request yields
    a different UUID and therefore cannot bypass plan charging.
    """

    try:
        normalized_tenant = str(UUID(str(tenant_id)))
        hmac_key = bytes.fromhex(str(api_key_hash))
    except (TypeError, ValueError, AttributeError) as exc:
        raise QuotaUnavailable("invalid_quota_identity") from exc
    if not API_KEY_HASH_RE.fullmatch(str(api_key_hash or "")):
        raise QuotaUnavailable("invalid_api_key_fingerprint")
    client_key = normalize_idempotency_key(idempotency_key)
    if client_key is None:
        return new_request_id()

    normalized_operation = normalize_operation(operation)
    request_digest = hmac.new(hmac_key, digestmod=hashlib.sha256)
    for value in (
        normalized_tenant.encode("ascii"),
        normalized_operation.encode("ascii"),
        str(method).upper().encode("ascii"),
        str(path).encode("utf-8"),
        bytes(query_bytes),
        bytes(body_bytes),
        client_key.encode("ascii"),
    ):
        request_digest.update(len(value).to_bytes(8, "big"))
        request_digest.update(value)
    return str(UUID(bytes=request_digest.digest()[:16], version=5))


def normalize_operation(operation: str | None) -> str:
    normalized = {
        "list": "read",
        "get": "read",
        "create": "create",
        "read": "read",
        "recall": "recall",
    }.get(str(operation or "").strip().lower())
    if normalized is None:
        raise QuotaUnavailable("unsupported_metering_operation")
    return normalized


class DurableQuotaMeter:
    """Call the transaction-safe quota function; never allow on uncertainty."""

    _SQL = """
        SELECT allowed, plan_id, remaining, retry_after, reason, duplicate
        FROM soul_v3.sdk_consume_quota($1::uuid, $2, $3::uuid, $4, $5)
    """

    async def consume(
        self,
        pool: Any,
        *,
        tenant_id: str,
        api_key_hash: str,
        request_id: str,
        operation: str,
        units: int = 1,
    ) -> DurableQuotaDecision:
        try:
            normalized_tenant = str(UUID(str(tenant_id)))
            normalized_request = str(UUID(str(request_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise QuotaUnavailable("invalid_quota_identity") from exc
        if not API_KEY_HASH_RE.fullmatch(str(api_key_hash or "")):
            raise QuotaUnavailable("invalid_api_key_fingerprint")
        normalized_operation = normalize_operation(operation)
        if not isinstance(units, int) or isinstance(units, bool) or not 1 <= units <= 1_000:
            raise QuotaUnavailable("invalid_quota_units")

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    self._SQL,
                    normalized_tenant,
                    api_key_hash,
                    normalized_request,
                    normalized_operation,
                    units,
                )
        except Exception as exc:
            raise QuotaUnavailable("durable_quota_unavailable") from exc
        if row is None:
            raise QuotaUnavailable("durable_quota_no_decision")
        try:
            decision = DurableQuotaDecision(
                allowed=bool(row["allowed"]),
                plan_id=str(row["plan_id"]),
                remaining=max(0, int(row["remaining"])),
                retry_after=max(0, int(row["retry_after"])),
                reason=str(row["reason"]),
                duplicate=bool(row["duplicate"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QuotaUnavailable("durable_quota_invalid_decision") from exc
        if not decision.plan_id or not decision.reason:
            raise QuotaUnavailable("durable_quota_invalid_decision")
        return decision


class BasicSdkMetrics:
    """Aggregate process metrics with bounded labels and no tenant/user data."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._counts: dict[tuple[str, str], int] = {}
        self._duration_ms: dict[str, int] = {}
        self._lock = threading.Lock()

    def observe(self, *, operation: str, status: str, duration_ms: int) -> None:
        normalized_operation = normalize_operation(operation)
        normalized_status = "ok" if status == "ok" else "failed"
        duration = max(0, int(duration_ms))
        with self._lock:
            key = (normalized_operation, normalized_status)
            self._counts[key] = self._counts.get(key, 0) + 1
            self._duration_ms[normalized_operation] = (
                self._duration_ms.get(normalized_operation, 0) + duration
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": max(0, int(time.monotonic() - self._started)),
                "requests": {
                    f"{operation}:{status}": count
                    for (operation, status), count in sorted(self._counts.items())
                },
                "duration_ms": dict(sorted(self._duration_ms.items())),
            }

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()
            self._duration_ms.clear()
            self._started = time.monotonic()


quota_meter = DurableQuotaMeter()
sdk_metrics = BasicSdkMetrics()
