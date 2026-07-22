#!/usr/bin/env python3
"""Gateway for the SOUL Memory SDK cutover.

The gateway is intentionally conservative: only the memory SDK surface is
routed to the new tenant-safe API. Everything else falls back to the legacy
SOUL API so installer, widget, auth, and soul endpoints keep their behavior.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from soul_memory_sdk_controls import SlidingWindowRateLimiter, credential_fingerprint, meter


SDK_DEFAULT = "http://127.0.0.1:8768"
# The v1 contract is absorbed by the canonical SDK API.  Keep the historical
# variable/function names for unit-file compatibility, but never default back
# to the retired :8766 container.
LEGACY_DEFAULT = "http://127.0.0.1:8768"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_BODY_BYTES = int(os.environ.get("SOUL_SDK_MAX_BODY_BYTES", "1100000"))
RATE_LIMIT_REQUESTS = int(os.environ.get("SOUL_SDK_RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = float(os.environ.get("SOUL_SDK_RATE_LIMIT_WINDOW_SECONDS", "60"))
PEER_RATE_LIMIT_REQUESTS = int(os.environ.get("SOUL_SDK_PEER_RATE_LIMIT_REQUESTS", "300"))
_ALLOWED_UPSTREAM_HOSTS = {"127.0.0.1", "localhost"}

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

RESPONSE_DROP_HEADERS = HOP_BY_HOP_HEADERS | {"content-length", "server", "date"}
REQUEST_DROP_HEADERS = HOP_BY_HOP_HEADERS | {
    "host",
    "content-length",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}

_gateway_limiter = SlidingWindowRateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
_gateway_peer_limiter = SlidingWindowRateLimiter(PEER_RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)




@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_upstream_url(sdk_base_url(), "SDK")
    _validate_upstream_url(legacy_base_url(), "legacy")
    yield


app = FastAPI(
    title="SOUL Memory SDK Gateway",
    version="0.2.0",
    lifespan=_lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


def _validate_upstream_url(url: str, name: str) -> None:
    """Reject upstream URLs that point outside localhost — prevents SSRF via env vars.

    Called once at startup, not on every request, so test mocks are unaffected.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in _ALLOWED_UPSTREAM_HOSTS:
        raise ValueError(
            f"SOUL gateway {name} URL must resolve to localhost, got host={host!r}"
        )


def sdk_base_url() -> str:
    return os.environ.get("SOUL_GATEWAY_SDK_URL", SDK_DEFAULT).rstrip("/")


def legacy_base_url() -> str:
    return os.environ.get("SOUL_GATEWAY_LEGACY_URL", LEGACY_DEFAULT).rstrip("/")


def is_sdk_memory_endpoint(path: str) -> bool:
    normalized = "/" + path.strip("/")
    if normalized in {
        "/v1/memories",
        "/v1/memory",
        "/v1/recall",
        "/v1/memory/search",
        "/v1/tenant/memories",
        "/v1/tenant/recall",
    }:
        return True
    if normalized.startswith("/v1/memories/"):
        return len(normalized.removeprefix("/v1/memories/").split("/")) == 1
    if normalized.startswith("/v1/memory/"):
        suffix = normalized.removeprefix("/v1/memory/")
        return suffix != "search" and len(suffix.split("/")) == 1
    return False


def select_upstream(path: str) -> str:
    return sdk_base_url() if is_sdk_memory_endpoint(path) else legacy_base_url()


def is_blocked_legacy_endpoint(path: str) -> bool:
    """Fail closed for the legacy admin surface, which has no authentication.

    The historical API exposes create/suspend/reactivate/delete/consolidate
    mutations without an authorization dependency.  Never proxy those routes
    through the production/staging gateways while the compatibility layer is
    being absorbed into the scoped native API.
    """
    normalized = "/" + path.strip("/")
    return normalized == "/v1/admin" or normalized.startswith("/v1/admin/")


def _copy_request_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in REQUEST_DROP_HEADERS
    }


def _copy_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in RESPONSE_DROP_HEADERS
    }


def _bearer_fingerprint(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return credential_fingerprint(token if scheme.lower() == "bearer" else None)


def _rate_limit_key(request: Request) -> str:
    fingerprint = _bearer_fingerprint(request)
    if fingerprint != "missing":
        return f"key:{fingerprint}"
    return _peer_rate_limit_key(request)


def _peer_rate_limit_key(request: Request) -> str:
    # Do not retain a network address in state or logs. Forwarding headers are
    # attacker-controlled and intentionally never participate in this key.
    peer = request.client.host if request.client else "unknown"
    return f"peer:{credential_fingerprint(peer)}"


async def _read_limited_body(request: Request, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
            if declared_size < 0:
                raise ValueError("invalid_content_length")
            if declared_size > max_bytes:
                raise ValueError("request_body_too_large")
        except ValueError as exc:
            if str(exc) == "request_body_too_large":
                raise
            raise ValueError("invalid_content_length") from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("request_body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def upstream_url(base_url: str, path: str, query: str = "") -> str:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    return url


async def fetch_health(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    expected_service: str | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(f"{base_url.rstrip('/')}/health")
        content_type = response.headers.get("content-type", "").lower()
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text[:200]
        json_health = (
            "application/json" in content_type
            and isinstance(payload, dict)
            and (payload.get("status") == "ok" or payload.get("ok") is True)
        )
        service_matches = expected_service is None or (
            isinstance(payload, dict) and payload.get("service") == expected_service
        )
        return {
            "ok": 200 <= response.status_code < 300 and json_health and service_matches,
            "status_code": response.status_code,
            "body": payload,
            "content_type": content_type,
            "service_matches": service_matches,
        }
    except httpx.RequestError as exc:
        return {"ok": False, "error": type(exc).__name__}


@app.get("/health")
async def health() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        sdk = await fetch_health(
            client,
            sdk_base_url(),
            expected_service="soul-memory-sdk-api",
        )
        legacy = await fetch_health(client, legacy_base_url())
    return {
        "status": "ok" if sdk["ok"] and legacy["ok"] else "degraded",
        "service": "soul-memory-sdk-gateway",
        "phase": "2",
        "sdk": sdk,
        "legacy": legacy,
    }


@app.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> Response:
    """Publish the native typed contract instead of the gateway catch-all schema."""
    return await proxy_request(
        request,
        sdk_base_url(),
        "/openapi.json",
        require_json=True,
    )


async def proxy_request(
    request: Request,
    base_url: str,
    path: str,
    *,
    require_json: bool = False,
) -> Response:
    try:
        body = await _read_limited_body(request)
    except ValueError as exc:
        detail = str(exc)
        status_code = 413 if detail == "request_body_too_large" else 400
        return JSONResponse(status_code=status_code, content={"ok": False, "error": detail})
    target_url = upstream_url(base_url, path, request.url.query)
    headers = _copy_request_headers(request)
    client_ip = request.client.host if request.client else "unknown"
    # Replace, never append, client-supplied forwarding headers.  The immediate
    # socket peer is the only address this gateway can attest.
    headers["x-forwarded-for"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target_url,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "upstream_unavailable",
                "upstream": base_url,
                "detail": type(exc).__name__,
            },
        )
    if require_json and 200 <= upstream.status_code < 300:
        content_type = upstream.headers.get("content-type", "").lower()
        try:
            payload = upstream.json()
        except ValueError:
            payload = None
        if "application/json" not in content_type or not isinstance(payload, (dict, list)):
            return JSONResponse(
                status_code=502,
                content={
                    "ok": False,
                    "error": "invalid_sdk_upstream_response",
                    "detail": "memory SDK returned a non-JSON success response",
                },
            )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_copy_response_headers(upstream),
    )


def _sanitize_path(path: str) -> str:
    """Reject paths with traversal sequences before forwarding to upstream."""
    segments = path.split("/")
    if ".." in segments or "." in segments:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="path_traversal_rejected")
    return path


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def gateway(path: str, request: Request) -> Response:
    full_path = f"/{_sanitize_path(path)}"
    if is_blocked_legacy_endpoint(full_path):
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": "legacy_admin_disabled",
                "detail": "administrative mutations require the native authenticated control plane",
            },
        )
    sdk_route = is_sdk_memory_endpoint(full_path)
    if sdk_route:
        try:
            decisions = [_gateway_peer_limiter.check(_peer_rate_limit_key(request))]
            if _bearer_fingerprint(request) != "missing":
                decisions.append(_gateway_limiter.check(_rate_limit_key(request)))
            decision = next((item for item in decisions if not item.allowed), decisions[-1])
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "error": "rate_limiter_unavailable"},
            )
        if not decision.allowed:
            meter.emit(
                event="rate_limit",
                operation="gateway",
                status="denied",
                status_code=429,
                api_key_hash=_bearer_fingerprint(request),
                reason="quota_exceeded",
            )
            return JSONResponse(
                status_code=429,
                headers=decision.headers(),
                content={"ok": False, "error": "rate_limit_exceeded"},
            )
    response = await proxy_request(
        request,
        select_upstream(full_path),
        full_path,
        require_json=sdk_route,
    )
    if sdk_route:
        # Canonicalize the gateway quota headers, replacing any same-name
        # upstream values instead of emitting ambiguous duplicates.
        for header, value in decision.headers().items():
            response.headers[header] = value
    return response
