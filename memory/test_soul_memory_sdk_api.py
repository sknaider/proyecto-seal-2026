from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_memory_sdk_api as sdk_api  # noqa: E402
from db import get_pool  # noqa: E402
from soul_memory_sdk_api import MemoryCreateRequest, RecallRequest, app  # noqa: E402
from soul_memory_sdk_controls import SlidingWindowRateLimiter, credential_fingerprint, meter  # noqa: E402
from soul_memory_sdk_runtime import TenantAuthError, TenantContext  # noqa: E402


def test_sdk_api_exposes_phase2_routes() -> None:
    paths = {route.path for route in app.routes}
    assert "/v1/memories" in paths
    assert "/v1/memories/{memory_id}" in paths
    assert "/v1/recall" in paths
    assert "/v1/memory" in paths
    assert "/v1/memory/search" in paths
    assert "/v1/tenant/memories" in paths
    assert "/v1/tenant/recall" in paths


def test_sdk_api_openapi_has_bearer_dependency_surface() -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "SOUL Memory SDK API"
    assert schema["info"]["version"] == "0.2.0"
    bearer = schema["components"]["securitySchemes"]["BearerAuth"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"

    canonical = {
        ("/v1/memories", "get"),
        ("/v1/memories", "post"),
        ("/v1/recall", "post"),
        ("/v1/memories/{memory_id}", "get"),
        ("/v1/tenant/memories", "get"),
        ("/v1/tenant/recall", "post"),
    }
    for path, method in canonical:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"BearerAuth": []}]

    for path in ("/v1/memories", "/v1/recall", "/v1/tenant/recall"):
        request_schema = schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert request_schema.get("$ref", "").startswith("#/components/schemas/")

    assert "/v1/memory" not in schema["paths"]
    assert "/v1/memory/search" not in schema["paths"]


def test_contract_models_keep_overrides_visible_and_bound_limits() -> None:
    payload = MemoryCreateRequest(content="safe", tenant_id="attacker").model_dump(exclude_none=True)
    assert payload["tenant_id"] == "attacker"

    recall = RecallRequest(query="test", limit=50, importance_gte=10)
    assert recall.limit == 50
    assert recall.importance_gte == 10


def test_contract_models_reject_invalid_limits() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RecallRequest(query="test", limit=51)
    with pytest.raises(ValidationError):
        MemoryCreateRequest(content="test", importance=0)


def test_legacy_search_route_precedes_dynamic_memory_id_route() -> None:
    response = TestClient(app).get("/v1/memory/search?query=test")
    assert response.status_code == 401
    assert response.json()["detail"] == "authorization_required"


def test_tenant_routes_require_authorization_before_user_id() -> None:
    response = TestClient(app).get("/v1/tenant/memories")
    assert response.status_code == 401
    assert response.json()["detail"] == "authorization_required"


class _Acquire:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


def test_auth_failure_meter_hashes_presented_key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_key = "soul_live_do_not_store_this"

    async def reject(conn: object, key: str) -> TenantContext:
        raise TenantAuthError("invalid_api_key")

    monkeypatch.setattr(sdk_api, "derive_tenant_from_api_key", reject)
    app.dependency_overrides[get_pool] = lambda: _Pool()
    meter.clear()
    try:
        response = TestClient(app).get("/v1/memories", headers={"Authorization": f"Bearer {raw_key}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    events = meter.snapshot()
    auth_event = next(event for event in events if event["event"] == "auth_failure")
    assert auth_event["api_key_hash"] == credential_fingerprint(raw_key)
    assert raw_key not in str(events)


def test_successful_list_is_metered_by_key_and_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_key = "soul_live_metered_safely"
    key_hash = credential_fingerprint(raw_key)
    tenant = TenantContext(tenant_id="tenant-test", api_key_hash=key_hash, scopes=("read",))

    async def authenticate(conn: object, key: str) -> TenantContext:
        return tenant

    @asynccontextmanager
    async def transaction(pool: object, context: TenantContext, **kwargs: object):
        yield object()

    async def list_empty(conn: object, **kwargs: object) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(sdk_api, "derive_tenant_from_api_key", authenticate)
    monkeypatch.setattr(sdk_api, "tenant_transaction", transaction)
    monkeypatch.setattr(sdk_api, "list_memories", list_empty)
    monkeypatch.setattr(sdk_api, "_api_limiter", SlidingWindowRateLimiter(10, 60))
    app.dependency_overrides[get_pool] = lambda: _Pool()
    meter.clear()
    try:
        response = TestClient(app).get("/v1/memories", headers={"Authorization": f"Bearer {raw_key}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["ratelimit-limit"] == "10"
    assert response.headers["ratelimit-remaining"] == "9"
    operation = next(event for event in meter.snapshot() if event["event"] == "operation")
    assert operation["operation"] == "list"
    assert operation["status"] == "ok"
    assert operation["tenant_id"] == tenant.tenant_id
    assert operation["api_key_hash"] == key_hash
    assert raw_key not in str(meter.snapshot())


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/v1/memories", "create"),
        ("GET", "/v1/memories", "list"),
        ("GET", "/v1/memories/42", "get"),
        ("POST", "/v1/recall", "recall"),
        ("POST", "/v1/tenant/recall", "recall"),
    ],
)
def test_operation_metering_map_covers_public_surface(method: str, path: str, expected: str) -> None:
    from starlette.requests import Request

    request = Request({"type": "http", "method": method, "path": path, "query_string": b"", "headers": []})
    assert sdk_api._operation_for_request(request) == expected


def test_api_quota_returns_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_key = "soul_live_quota"
    key_hash = credential_fingerprint(raw_key)
    tenant = TenantContext(tenant_id="tenant-quota", api_key_hash=key_hash, scopes=("read",))

    async def authenticate(conn: object, key: str) -> TenantContext:
        return tenant

    limiter = SlidingWindowRateLimiter(1, 60)
    limiter.check(f"{tenant.tenant_id}:{key_hash}")
    monkeypatch.setattr(sdk_api, "derive_tenant_from_api_key", authenticate)
    monkeypatch.setattr(sdk_api, "_api_limiter", limiter)
    app.dependency_overrides[get_pool] = lambda: _Pool()
    try:
        response = TestClient(app).get("/v1/memories", headers={"Authorization": f"Bearer {raw_key}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["ratelimit-remaining"] == "0"


def test_get_by_id_translates_scope_error_to_403() -> None:
    tenant = TenantContext(tenant_id="tenant-test", api_key_hash="a" * 64, scopes=())
    app.dependency_overrides[sdk_api.current_tenant] = lambda: tenant
    app.dependency_overrides[get_pool] = lambda: _Pool()
    try:
        response = TestClient(app).get("/v1/memories/42")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert response.json()["detail"] == "scope_required:read"
