#!/usr/bin/env python3
"""Public REST router for SOUL Memory SDK Phase 2.

This module is deliberately separate from the internal MCP server. It exposes
the public SDK surface through tenant-aware transactions backed by the Phase 1
RLS schema.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from db import get_pool
from soul_memory_sdk_runtime import (
    TenantAuthError,
    TenantContext,
    TenantOverrideError,
    TenantScopeError,
    audit_query_hash,
    audit_user_read,
    create_memory,
    derive_tenant_from_api_key,
    extract_bearer_token,
    get_memory,
    list_memories,
    normalize_agent_id,
    normalize_user_id,
    recall_memories,
    reject_tenant_override,
    retrieve_memories,
    tenant_transaction,
)
from soul_memory_sdk_controls import SlidingWindowRateLimiter, credential_fingerprint, meter


_compat_module: Any | None = None
API_RATE_LIMIT_REQUESTS = int(os.environ.get("SOUL_SDK_RATE_LIMIT_REQUESTS", "120"))
API_RATE_LIMIT_WINDOW_SECONDS = float(os.environ.get("SOUL_SDK_RATE_LIMIT_WINDOW_SECONDS", "60"))
_api_limiter = SlidingWindowRateLimiter(API_RATE_LIMIT_REQUESTS, API_RATE_LIMIT_WINDOW_SECONDS)


class ContractModel(BaseModel):
    """Forward-compatible public contract without trusting unknown fields."""

    model_config = ConfigDict(extra="allow")


class MemoryCreateRequest(ContractModel):
    agent_id: str | None = None
    agent: str | None = None
    content: str = Field(min_length=1, max_length=1_000_000)
    category: str = "fact"
    importance: int = Field(default=5, ge=1, le=10)
    memory_type: str = "semantic"
    scope: str | None = None
    valid_at: datetime | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallRequest(ContractModel):
    query: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = None
    agent: str | None = None
    importance_gte: int = Field(default=1, ge=1, le=10)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    agent_id: str
    content: str
    category: str
    importance: int
    memory_type: str
    scope: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str | None = None
    content_hash: str | None = None


class MemoryResponse(BaseModel):
    ok: bool = True
    memory: MemoryModel


class MemoryListResponse(BaseModel):
    ok: bool = True
    memories: list[MemoryModel]


class TenantMemoryListResponse(MemoryListResponse):
    audit: dict[str, int]


class RecallResponse(BaseModel):
    memories: list[MemoryModel]
    total_hits: int
    latency_ms: int
    retrieval_mode: str


class HealthResponse(BaseModel):
    status: str
    service: str
    phase: str
    contract: str
    legacy_compat: str


class ErrorResponse(BaseModel):
    detail: str | list[dict[str, Any]]


AUTH_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing, invalid, expired, or revoked API key"},
    403: {"model": ErrorResponse, "description": "API key scope does not permit this operation"},
    429: {"model": ErrorResponse, "description": "API key quota exceeded; Retry-After is provided"},
    503: {"model": ErrorResponse, "description": "Fail-safe request control is unavailable"},
}


bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Tenant-scoped SOUL API key. The tenant identity is derived server-side.",
)


@asynccontextmanager
async def app_lifespan(application: FastAPI):
    if _compat_module is not None:
        await _compat_module.startup()
    try:
        yield
    finally:
        if _compat_module is not None:
            await _compat_module.shutdown()


app = FastAPI(
    title="SOUL Memory SDK API",
    version="0.2.0",
    description="Tenant-safe memory storage and recall. Tenant identity is never accepted from clients.",
    lifespan=app_lifespan,
)


def _operation_for_request(request: Request) -> str | None:
    path = request.url.path.rstrip("/")
    if request.method == "POST" and path in {"/v1/memory", "/v1/memories"}:
        return "create"
    if request.method == "GET" and path in {"/v1/memory", "/v1/memories", "/v1/tenant/memories"}:
        return "list"
    if request.method == "GET" and (
        path.startswith("/v1/memory/") or path.startswith("/v1/memories/")
    ) and path != "/v1/memory/search":
        return "get"
    if path in {"/v1/recall", "/v1/memory/search", "/v1/tenant/recall"}:
        return "recall"
    return None


@app.middleware("http")
async def audit_sdk_operations(request: Request, call_next: Any) -> Any:
    operation = _operation_for_request(request)
    if operation is None:
        return await call_next(request)
    started = time.monotonic()
    response = await call_next(request)
    for header, value in getattr(request.state, "rate_limit_headers", {}).items():
        response.headers[header] = value
    meter.emit(
        event="operation",
        operation=operation,
        status="ok" if response.status_code < 400 else "failed",
        status_code=response.status_code,
        tenant_id=getattr(request.state, "tenant_id", None),
        api_key_hash=getattr(request.state, "api_key_hash", "missing"),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return response


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, TenantOverrideError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, TenantScopeError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=500, detail=type(exc).__name__)


def _require_tenant_read(tenant: TenantContext) -> None:
    if not tenant.allows("tenant:read"):
        raise TenantScopeError("scope_required:tenant:read")


async def current_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    pool: Any = Depends(get_pool),
) -> TenantContext:
    presented_hash = credential_fingerprint(credentials.credentials if credentials is not None else None)
    request.state.api_key_hash = presented_hash
    try:
        reject_tenant_override(headers=request.headers, query=request.query_params)
        authorization = None
        if credentials is not None:
            authorization = f"{credentials.scheme} {credentials.credentials}"
        api_key = extract_bearer_token(authorization)
        async with pool.acquire() as conn:
            tenant = await derive_tenant_from_api_key(conn, api_key)
        request.state.api_key_hash = tenant.api_key_hash
        request.state.tenant_id = tenant.tenant_id
        try:
            decision = _api_limiter.check(f"{tenant.tenant_id}:{tenant.api_key_hash}")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="rate_limiter_unavailable") from exc
        request.state.rate_limit_headers = decision.headers()
        if not decision.allowed:
            meter.emit(
                event="rate_limit",
                operation=_operation_for_request(request),
                status="denied",
                status_code=429,
                tenant_id=tenant.tenant_id,
                api_key_hash=tenant.api_key_hash,
                reason="quota_exceeded",
            )
            raise HTTPException(status_code=429, detail="rate_limit_exceeded", headers=decision.headers())
        return tenant
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        if isinstance(exc, TenantAuthError):
            meter.emit(
                event="auth_failure",
                operation=_operation_for_request(request),
                status="denied",
                status_code=401,
                api_key_hash=presented_hash,
                reason="authentication_failed",
            )
        raise _http_error(exc) from exc


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "soul-memory-sdk-api",
        "phase": "2",
        "contract": "0.2.0",
        "legacy_compat": "native" if os.environ.get("SOUL_API_V1_COMPAT_ENABLED") == "1" else "disabled",
    }


@app.post(
    "/v1/memories",
    response_model=MemoryResponse,
    responses={**AUTH_RESPONSES, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["memory"],
)
@app.post("/v1/memory", response_model=MemoryResponse, include_in_schema=False)
async def post_memory(
    request: Request,
    payload: MemoryCreateRequest,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> MemoryResponse:
    try:
        payload_data = payload.model_dump(mode="json", exclude_none=True)
        reject_tenant_override(payload=payload_data)
        tenant.require("write")
        agent_id = normalize_agent_id(payload_data.get("agent_id") or payload_data.get("agent") or "default", required=True)
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            memory = await create_memory(conn, tenant, payload_data)
        return MemoryResponse(memory=MemoryModel.model_validate(memory))
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get(
    "/v1/memories",
    response_model=MemoryListResponse,
    responses=AUTH_RESPONSES,
    tags=["memory"],
)
@app.get("/v1/memory", response_model=MemoryListResponse, include_in_schema=False)
async def get_memories(
    request: Request,
    agent_id: str | None = None,
    category: str | None = None,
    importance_gte: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> MemoryListResponse:
    try:
        tenant.require("read")
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            memories = await list_memories(
                conn,
                query=request.query_params,
                agent_id=agent_id,
                category=category,
                importance_gte=importance_gte,
                limit=limit,
                offset=offset,
            )
        return MemoryListResponse(memories=[MemoryModel.model_validate(item) for item in memories])
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.post(
    "/v1/recall",
    response_model=RecallResponse,
    responses={**AUTH_RESPONSES, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["memory"],
)
async def post_recall(
    request: Request,
    payload: RecallRequest,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> RecallResponse:
    try:
        payload_data = payload.model_dump(mode="json", exclude_none=True)
        reject_tenant_override(payload=payload_data)
        query = str(payload_data.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query_required")
        agent_id = normalize_agent_id(payload_data.get("agent_id") or payload_data.get("agent"), required=False)
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            result = await recall_memories(
                conn,
                tenant,
                query_text=query,
                agent_id=agent_id,
                importance_gte=int(payload_data.get("importance_gte") or 1),
                limit=int(payload_data.get("limit") or 10),
            )
        return RecallResponse.model_validate(result)
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get("/v1/memory/search", response_model=RecallResponse, include_in_schema=False)
async def legacy_search(
    request: Request,
    agent_id: str | None = None,
    query: str = "",
    limit: int = Query(default=10, ge=1, le=50),
    importance_gte: int = Query(default=1, ge=1, le=10),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> RecallResponse:
    try:
        reject_tenant_override(query=request.query_params)
        if not query.strip():
            raise HTTPException(status_code=400, detail="query_required")
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            result = await recall_memories(
                conn,
                tenant,
                query_text=query,
                agent_id=agent_id,
                importance_gte=importance_gte,
                limit=limit,
            )
        return RecallResponse.model_validate(result)
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get(
    "/v1/memories/{memory_id}",
    response_model=MemoryResponse,
    responses={**AUTH_RESPONSES, 404: {"model": ErrorResponse}},
    tags=["memory"],
)
@app.get("/v1/memory/{memory_id}", response_model=MemoryResponse, include_in_schema=False)
async def get_memory_by_id(
    memory_id: int,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> MemoryResponse:
    try:
        tenant.require("read")
        async with tenant_transaction(pool, tenant) as conn:
            memory = await get_memory(conn, memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="memory_not_found")
        return MemoryResponse(memory=MemoryModel.model_validate(memory))
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get(
    "/v1/tenant/memories",
    response_model=TenantMemoryListResponse,
    responses=AUTH_RESPONSES,
    tags=["tenant"],
)
async def get_tenant_memories(
    request: Request,
    x_soul_user_id: str | None = Header(default=None),
    agent_id: str | None = None,
    category: str | None = None,
    importance_gte: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> TenantMemoryListResponse:
    """Tenant-owner read path. It is intentionally separate from agent reads."""
    try:
        _require_tenant_read(tenant)
        user_id = normalize_user_id(x_soul_user_id, required=True)
        endpoint = "/v1/tenant/memories"
        started = time.monotonic()
        query_hash = audit_query_hash(endpoint=endpoint, query=request.query_params)
        async with tenant_transaction(pool, tenant, viewer="user", user_id=user_id) as conn:
            memories = await list_memories(
                conn,
                query=request.query_params,
                agent_id=agent_id,
                category=category,
                importance_gte=importance_gte,
                limit=limit,
                offset=offset,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            await audit_user_read(
                conn,
                tenant,
                endpoint=endpoint,
                user_id=user_id or "",
                query_hash=query_hash,
                rows_read=len(memories),
                latency_ms=latency_ms,
                metadata={
                    "agent_id": agent_id,
                    "category": category,
                    "importance_gte": importance_gte,
                    "limit": limit,
                    "offset": offset,
                },
            )
        return TenantMemoryListResponse(
            memories=[MemoryModel.model_validate(item) for item in memories],
            audit={"rows_read": len(memories)},
        )
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.post(
    "/v1/tenant/recall",
    response_model=RecallResponse,
    responses={**AUTH_RESPONSES, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["tenant"],
)
async def post_tenant_recall(
    request: Request,
    payload: RecallRequest,
    x_soul_user_id: str | None = Header(default=None),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> RecallResponse:
    """Tenant-owner search path with mandatory user_read_audit."""
    try:
        _require_tenant_read(tenant)
        user_id = normalize_user_id(x_soul_user_id, required=True)
        payload_data = payload.model_dump(mode="json", exclude_none=True)
        reject_tenant_override(payload=payload_data)
        query = str(payload_data.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query_required")
        agent_id = normalize_agent_id(payload_data.get("agent_id") or payload_data.get("agent"), required=False)
        importance_gte = int(payload_data.get("importance_gte") or 1)
        limit = int(payload_data.get("limit") or 10)
        endpoint = "/v1/tenant/recall"
        started = time.monotonic()
        query_hash = audit_query_hash(endpoint=endpoint, payload=payload_data)
        async with tenant_transaction(pool, tenant, viewer="user", user_id=user_id) as conn:
            retrieval = await retrieve_memories(
                conn,
                query_text=query,
                agent_id=agent_id,
                importance_gte=importance_gte,
                limit=limit,
            )
            memories = retrieval.memories
            latency_ms = int((time.monotonic() - started) * 1000)
            await audit_user_read(
                conn,
                tenant,
                endpoint=endpoint,
                user_id=user_id or "",
                query_hash=query_hash,
                rows_read=len(memories),
                latency_ms=latency_ms,
                metadata={
                    "agent_id": agent_id,
                    "importance_gte": importance_gte,
                    "limit": limit,
                    "query_len": len(query),
                    "retrieval_mode": retrieval.mode,
                },
            )
        return RecallResponse(
            memories=[MemoryModel.model_validate(item) for item in memories],
            total_hits=len(memories),
            latency_ms=latency_ms,
            retrieval_mode=retrieval.mode,
        )
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


if os.environ.get("SOUL_API_V1_COMPAT_ENABLED") == "1":
    from fastapi.responses import JSONResponse
    from soul_api_v1_compat import load_module

    _compat_module = load_module()

    @app.api_route(
        "/v1/admin/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def block_legacy_admin(path: str) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": "legacy_admin_disabled",
                "detail": "administrative mutations require the native authenticated control plane",
            },
        )

    # Defined last: canonical tenant-safe memory routes always win; unmatched
    # v1 auth/soul/chat/widget/installer requests fall through to compat.
    app.mount("/", _compat_module.app)
