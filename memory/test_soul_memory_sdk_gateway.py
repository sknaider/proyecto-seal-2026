from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_memory_sdk_gateway as gateway  # noqa: E402
from soul_memory_sdk_controls import SlidingWindowRateLimiter, meter  # noqa: E402


def _request(
    path: str,
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
    client: tuple[str, int] = ("127.0.0.1", 1),
) -> gateway.Request:
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return gateway.Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers or [],
            "client": client,
        },
        receive=receive,
    )


def test_gateway_routes_only_memory_surface_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOUL_GATEWAY_SDK_URL", "http://sdk.local")
    monkeypatch.setenv("SOUL_GATEWAY_LEGACY_URL", "http://legacy.local")

    sdk_paths = [
        "/v1/memories",
        "/v1/memories/123",
        "/v1/memory",
        "/v1/memory/123",
        "/v1/memory/search",
        "/v1/recall",
        "/v1/tenant/memories",
        "/v1/tenant/recall",
    ]
    legacy_paths = [
        "/health",
        "/install.ps1",
        "/sdk/docker-compose.yml",
        "/v1/auth/token",
        "/v1/soul/ADA",
        "/v1/widget/ADA/chat",
        "/v1/memories/123/extra",
    ]

    assert [gateway.select_upstream(path) for path in sdk_paths] == ["http://sdk.local"] * len(sdk_paths)
    assert [gateway.select_upstream(path) for path in legacy_paths] == ["http://legacy.local"] * len(legacy_paths)


def test_upstream_url_preserves_path_and_query() -> None:
    assert (
        gateway.upstream_url("http://127.0.0.1:8768/", "/v1/memories", "limit=2&agent=ADA")
        == "http://127.0.0.1:8768/v1/memories?limit=2&agent=ADA"
    )


@pytest.mark.asyncio
async def test_fetch_health_reports_json_status() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok", "service": "fake"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await gateway.fetch_health(client, "http://fake.local")

    assert result == {
        "ok": True,
        "status_code": 200,
        "body": {"status": "ok", "service": "fake"},
        "content_type": "application/json",
        "service_matches": True,
    }


@pytest.mark.asyncio
async def test_fetch_health_rejects_html_false_green() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>agent panel</html>", headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await gateway.fetch_health(
            client,
            "http://fake.local",
            expected_service="soul-memory-sdk-api",
        )

    assert result["ok"] is False
    assert result["content_type"] == "text/html"
    assert result["service_matches"] is False


@pytest.mark.asyncio
async def test_proxy_rejects_non_json_success_from_sdk() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>wrong service</html>", headers={"content-type": "text/html"})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    starlette_request = gateway.Request(
        {"type": "http", "method": "GET", "path": "/v1/memories", "query_string": b"", "headers": [], "client": ("127.0.0.1", 1)},
        receive=receive,
    )
    original_client = gateway.httpx.AsyncClient
    gateway.httpx.AsyncClient = lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
    try:
        response = await gateway.proxy_request(
            starlette_request,
            "http://sdk.local",
            "/v1/memories",
            require_json=True,
        )
    finally:
        gateway.httpx.AsyncClient = original_client

    assert response.status_code == 502
    assert b"invalid_sdk_upstream_response" in response.body


@pytest.mark.asyncio
async def test_fetch_health_reports_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await gateway.fetch_health(client, "http://down.local")

    assert result == {"ok": False, "error": "ConnectError"}


def test_validate_upstream_url_rejects_non_localhost() -> None:
    with pytest.raises(ValueError, match="must resolve to localhost"):
        gateway._validate_upstream_url("http://attacker.com/evil", "SDK")
    with pytest.raises(ValueError, match="must resolve to localhost"):
        gateway._validate_upstream_url("http://192.168.1.1:8768", "legacy")
    # localhost variants must pass
    gateway._validate_upstream_url("http://127.0.0.1:8768", "SDK")
    gateway._validate_upstream_url("http://localhost:8767", "legacy")


def test_sanitize_path_rejects_traversal() -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        gateway._sanitize_path("v1/memories/../../../etc/passwd")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "path_traversal_rejected"
    # clean paths must pass
    assert gateway._sanitize_path("v1/memories/123") == "v1/memories/123"
    assert gateway._sanitize_path("health") == "health"


def test_legacy_admin_surface_is_blocked_fail_closed() -> None:
    assert gateway.is_blocked_legacy_endpoint("/v1/admin/agents") is True
    assert gateway.is_blocked_legacy_endpoint("v1/admin/consolidate/ADA") is True
    assert gateway.is_blocked_legacy_endpoint("/v1/admin/agents/ADA/soul") is True
    assert gateway.is_blocked_legacy_endpoint("/v1/soul/ADA") is False
    assert gateway.is_blocked_legacy_endpoint("/install.ps1") is False


@pytest.mark.asyncio
async def test_gateway_never_proxies_legacy_admin() -> None:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = gateway.Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/v1/admin/agents/ADA",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
        },
        receive=receive,
    )
    response = await gateway.gateway("v1/admin/agents/ADA", request)
    assert response.status_code == 403
    assert b"legacy_admin_disabled" in response.body


@pytest.mark.asyncio
async def test_public_openapi_proxies_native_schema() -> None:
    native = {
        "openapi": "3.1.0",
        "info": {"title": "SOUL Memory SDK API", "version": "0.2.0"},
        "paths": {"/v1/memories": {}},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi.json"
        return httpx.Response(200, json=native)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = gateway.Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/openapi.json",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
        },
        receive=receive,
    )
    original_client = gateway.httpx.AsyncClient
    gateway.httpx.AsyncClient = lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
    try:
        response = await gateway.public_openapi(request)
    finally:
        gateway.httpx.AsyncClient = original_client

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.body == httpx.Response(200, json=native).content


def test_gateway_has_explicit_schema_route_without_generic_openapi() -> None:
    paths = [route.path for route in gateway.app.routes]
    assert paths.count("/openapi.json") == 1
    assert gateway.app.openapi_url is None


@pytest.mark.asyncio
async def test_body_limit_rejects_declared_size_before_reading_or_proxying() -> None:
    request = _request(
        "/v1/memories",
        method="POST",
        headers=[(b"content-length", str(gateway.MAX_BODY_BYTES + 1).encode())],
        body=b"this body must not be loaded",
    )
    response = await gateway.proxy_request(request, "http://sdk.local", "/v1/memories")
    assert response.status_code == 413
    assert b"request_body_too_large" in response.body


@pytest.mark.asyncio
async def test_body_limit_rejects_chunked_body_without_content_length() -> None:
    request = _request("/v1/memories", method="POST", body=b"12345")
    with pytest.raises(ValueError, match="request_body_too_large"):
        await gateway._read_limited_body(request, max_bytes=4)


@pytest.mark.asyncio
async def test_gateway_overwrites_untrusted_forwarding_headers() -> None:
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    request = _request(
        "/v1/memories",
        headers=[
            (b"x-forwarded-for", b"203.0.113.7"),
            (b"x-real-ip", b"203.0.113.8"),
            (b"forwarded", b"for=203.0.113.9"),
        ],
        client=("10.0.0.7", 42),
    )
    original_client = gateway.httpx.AsyncClient
    gateway.httpx.AsyncClient = lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
    try:
        response = await gateway.proxy_request(request, "http://sdk.local", "/v1/memories")
    finally:
        gateway.httpx.AsyncClient = original_client

    assert response.status_code == 200
    assert observed["x-forwarded-for"] == "10.0.0.7"
    assert "x-real-ip" not in observed
    assert "forwarded" not in observed


@pytest.mark.asyncio
async def test_gateway_rate_limit_returns_429_retry_after_without_raw_key(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = SlidingWindowRateLimiter(1, 30)
    monkeypatch.setattr(gateway, "_gateway_limiter", limiter)
    monkeypatch.setattr(gateway, "_gateway_peer_limiter", SlidingWindowRateLimiter(10, 30))
    meter.clear()
    token = "soul_live_raw_secret_must_never_be_metered"
    headers = [(b"authorization", f"Bearer {token}".encode())]
    first = _request("/v1/memories", headers=headers)
    limiter.check(gateway._rate_limit_key(first))

    response = await gateway.gateway("v1/memories", _request("/v1/memories", headers=headers))
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["ratelimit-remaining"] == "0"
    serialized = str(meter.snapshot())
    assert token not in serialized
    assert gateway._bearer_fingerprint(first) in serialized


@pytest.mark.asyncio
async def test_gateway_rate_limiter_failure_is_fail_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenLimiter:
        def check(self, key: str) -> None:
            raise RuntimeError("broken")

    monkeypatch.setattr(gateway, "_gateway_limiter", BrokenLimiter())
    monkeypatch.setattr(gateway, "_gateway_peer_limiter", SlidingWindowRateLimiter(10, 30))
    response = await gateway.gateway(
        "v1/memories",
        _request("/v1/memories", headers=[(b"authorization", b"Bearer test")]),
    )
    assert response.status_code == 503
    assert b"rate_limiter_unavailable" in response.body


@pytest.mark.asyncio
async def test_peer_quota_cannot_be_bypassed_by_rotating_keys_or_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    peer_limiter = SlidingWindowRateLimiter(1, 30)
    monkeypatch.setattr(gateway, "_gateway_peer_limiter", peer_limiter)
    monkeypatch.setattr(gateway, "_gateway_limiter", SlidingWindowRateLimiter(10, 30))
    first = _request(
        "/v1/memories",
        headers=[(b"authorization", b"Bearer first"), (b"x-forwarded-for", b"198.51.100.1")],
    )
    peer_limiter.check(gateway._peer_rate_limit_key(first))

    second = _request(
        "/v1/memories",
        headers=[(b"authorization", b"Bearer rotated"), (b"x-forwarded-for", b"203.0.113.2")],
    )
    response = await gateway.gateway("v1/memories", second)
    assert response.status_code == 429
    assert gateway._peer_rate_limit_key(first) == gateway._peer_rate_limit_key(second)


def test_response_headers_drop_gateway_duplicates() -> None:
    upstream = httpx.Response(
        200,
        headers={"content-length": "2", "server": "upstream", "date": "yesterday", "x-safe": "yes"},
        content=b"{}",
    )
    assert gateway._copy_response_headers(upstream) == {"x-safe": "yes"}
