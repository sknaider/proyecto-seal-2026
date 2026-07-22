from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_memory_sdk_gateway as gateway  # noqa: E402


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
