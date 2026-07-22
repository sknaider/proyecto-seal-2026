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
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

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
    search_memories,
    tenant_transaction,
)


_compat_module: Any | None = None


@asynccontextmanager
async def app_lifespan(application: FastAPI):
    if _compat_module is not None:
        await _compat_module.startup()
    try:
        yield
    finally:
        if _compat_module is not None:
            await _compat_module.shutdown()


app = FastAPI(title="SOUL Memory SDK API", version="0.1.0-phase2", lifespan=app_lifespan)


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
    authorization: str | None = Header(default=None),
    pool: Any = Depends(get_pool),
) -> TenantContext:
    try:
        reject_tenant_override(headers=request.headers, query=request.query_params)
        api_key = extract_bearer_token(authorization)
        async with pool.acquire() as conn:
            return await derive_tenant_from_api_key(conn, api_key)
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "soul-memory-sdk-api",
        "phase": "2",
        "legacy_compat": "native" if os.environ.get("SOUL_API_V1_COMPAT_ENABLED") == "1" else "disabled",
    }


@app.post("/v1/memories")
@app.post("/v1/memory")
async def post_memory(
    request: Request,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise TenantOverrideError("json_object_required")
        reject_tenant_override(payload=payload)
        tenant.require("write")
        agent_id = normalize_agent_id(payload.get("agent_id") or payload.get("agent") or "default", required=True)
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            memory = await create_memory(conn, tenant, payload)
        return {"ok": True, "memory": memory}
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get("/v1/memories")
@app.get("/v1/memory")
async def get_memories(
    request: Request,
    agent_id: str | None = None,
    category: str | None = None,
    importance_gte: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
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
        return {"ok": True, "memories": memories}
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.post("/v1/recall")
async def post_recall(
    request: Request,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise TenantOverrideError("json_object_required")
        reject_tenant_override(payload=payload)
        query = str(payload.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query_required")
        agent_id = normalize_agent_id(payload.get("agent_id") or payload.get("agent"), required=False)
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            return await recall_memories(
                conn,
                tenant,
                query_text=query,
                agent_id=agent_id,
                importance_gte=int(payload.get("importance_gte") or 1),
                limit=int(payload.get("limit") or 10),
            )
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get("/v1/memory/search")
async def legacy_search(
    request: Request,
    agent_id: str | None = None,
    query: str = "",
    limit: int = Query(default=10, ge=1, le=50),
    importance_gte: int = Query(default=1, ge=1, le=10),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
    try:
        reject_tenant_override(query=request.query_params)
        if not query.strip():
            raise HTTPException(status_code=400, detail="query_required")
        async with tenant_transaction(pool, tenant, agent_id=agent_id) as conn:
            return await recall_memories(
                conn,
                tenant,
                query_text=query,
                agent_id=agent_id,
                importance_gte=importance_gte,
                limit=limit,
            )
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.get("/v1/memories/{memory_id}")
@app.get("/v1/memory/{memory_id}")
async def get_memory_by_id(
    memory_id: int,
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
    tenant.require("read")
    async with tenant_transaction(pool, tenant) as conn:
        memory = await get_memory(conn, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory_not_found")
    return {"ok": True, "memory": memory}


@app.get("/v1/tenant/memories")
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
) -> dict[str, Any]:
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
        return {"ok": True, "memories": memories, "audit": {"rows_read": len(memories)}}
    except (TenantAuthError, TenantOverrideError, TenantScopeError) as exc:
        raise _http_error(exc) from exc


@app.post("/v1/tenant/recall")
async def post_tenant_recall(
    request: Request,
    x_soul_user_id: str | None = Header(default=None),
    tenant: TenantContext = Depends(current_tenant),
    pool: Any = Depends(get_pool),
) -> dict[str, Any]:
    """Tenant-owner search path with mandatory user_read_audit."""
    try:
        _require_tenant_read(tenant)
        user_id = normalize_user_id(x_soul_user_id, required=True)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise TenantOverrideError("json_object_required")
        reject_tenant_override(payload=payload)
        query = str(payload.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query_required")
        agent_id = normalize_agent_id(payload.get("agent_id") or payload.get("agent"), required=False)
        importance_gte = int(payload.get("importance_gte") or 1)
        limit = int(payload.get("limit") or 10)
        endpoint = "/v1/tenant/recall"
        started = time.monotonic()
        query_hash = audit_query_hash(endpoint=endpoint, payload=payload)
        async with tenant_transaction(pool, tenant, viewer="user", user_id=user_id) as conn:
            memories = await search_memories(
                conn,
                query_text=query,
                agent_id=agent_id,
                importance_gte=importance_gte,
                limit=limit,
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
                    "importance_gte": importance_gte,
                    "limit": limit,
                    "query_len": len(query),
                },
            )
        return {"ok": True, "memories": memories, "total_hits": len(memories), "latency_ms": latency_ms}
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
