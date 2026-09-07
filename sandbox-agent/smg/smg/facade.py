"""MCP SSE facade — reverse proxy over the upstream MCP server.

Design goals (MVP):
  * clients (Claude Code) connect to SMG at /sse
  * SMG forwards GET /sse and POST /message to the upstream MCP
  * when upstream is unhealthy, return 503 with JSON-RPC error so clients
    see a clean failure and can retry, instead of a silent hang
  * every request is logged to structlog for later audit wiring
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import SMGConfig
from .health import HealthMonitor
from .metrics import REQUEST_COUNT, REQUEST_LATENCY, RATE_LIMITED
from .middleware.audit import AuditLogger
from .middleware.rate_limit import RateLimiter

LOG = logging.getLogger("smg.facade")


def _caller_agent(request: Request) -> str:
    # explicit header wins; else derive from User-Agent
    agent = request.headers.get("X-SEAL-Agent")
    if agent:
        return agent.upper()
    ua = request.headers.get("User-Agent", "")
    for known in ("NEXUS", "ADA", "JARVIS", "ALICE", "DUM"):
        if known.lower() in ua.lower():
            return known
    return "_anon"


def create_app(cfg: SMGConfig, health: HealthMonitor,
               audit: AuditLogger | None = None,
               ratelimiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="SEAL Memory Gateway", version="0.1.0")

    upstream_cfg = cfg.backends.get("primary_sse")
    if not upstream_cfg or not upstream_cfg.url:
        raise RuntimeError("primary_sse backend missing from config")
    upstream_base = upstream_cfg.url.rstrip("/").removesuffix("/sse")

    # shared client for outbound; long timeout for SSE streams
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await client.aclose()

    @app.get("/health")
    async def healthz() -> dict:
        snap = health.snapshot()
        primary_ok = snap.get("primary_sse", {}).get("healthy", False)
        status = "ok" if primary_ok else "degraded"
        return {"status": status, "backends": snap, "uptime_s": round(time.time() - _started_at, 1)}

    @app.get("/sse")
    async def sse_proxy(request: Request):
        if not health.is_healthy("primary_sse"):
            raise HTTPException(503, detail="primary MCP backend circuit open")

        upstream_url = f"{upstream_base}/sse"
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        async def iter_upstream():
            try:
                async with client.stream("GET", upstream_url, headers=headers) as r:
                    if r.status_code >= 500:
                        health.mark_failure("primary_sse")
                    async for chunk in r.aiter_raw():
                        yield chunk
            except httpx.HTTPError as e:
                health.mark_failure("primary_sse")
                LOG.warning("SSE stream failed: %s", e)
                return

        return StreamingResponse(
            iter_upstream(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    @app.post("/message")
    async def message_proxy(request: Request):
        started = time.monotonic()
        agent = _caller_agent(request)

        # rate limit
        if ratelimiter is not None:
            allowed, wait_s = ratelimiter.allow(agent)
            if not allowed:
                RATE_LIMITED.labels(agent=agent).inc()
                REQUEST_COUNT.labels(method="POST", path="/message", status="429", agent=agent).inc()
                if audit:
                    audit.record(
                        agent=agent, method="POST", path="/message",
                        status=429, latency_ms=int((time.monotonic() - started) * 1000),
                        backend="primary_sse", extra={"reason": "rate_limited", "retry_after_s": round(wait_s, 2)},
                    )
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(int(wait_s) + 1)},
                    content={"jsonrpc": "2.0", "error": {"code": -32005, "message": f"rate limit exceeded for agent={agent}"}, "id": None},
                )

        if not health.is_healthy("primary_sse"):
            if audit:
                audit.record(
                    agent=agent, method="POST", path="/message",
                    status=503, latency_ms=int((time.monotonic() - started) * 1000),
                    backend="primary_sse", extra={"reason": "circuit_open"},
                )
            return JSONResponse(
                status_code=503,
                content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "SMG: primary MCP backend unavailable",
                    },
                    "id": None,
                },
            )

        body = await request.body()
        upstream_url = f"{upstream_base}/message"
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length"}
        }
        qs = dict(request.query_params)
        try:
            r = await client.post(upstream_url, content=body, headers=headers, params=qs)
        except httpx.HTTPError as e:
            health.mark_failure("primary_sse")
            LOG.warning("POST /message failed: %s", e)
            if audit:
                audit.record(
                    agent=agent, method="POST", path="/message",
                    status=502, latency_ms=int((time.monotonic() - started) * 1000),
                    backend="primary_sse", extra={"error": str(e)[:200]},
                )
            return JSONResponse(
                status_code=502,
                content={"jsonrpc": "2.0", "error": {"code": -32001, "message": str(e)}, "id": None},
            )

        if r.status_code >= 500:
            health.mark_failure("primary_sse")
        else:
            health.mark_success("primary_sse")

        elapsed = time.monotonic() - started
        REQUEST_LATENCY.labels(method="POST", path="/message").observe(elapsed)
        REQUEST_COUNT.labels(method="POST", path="/message", status=str(r.status_code), agent=agent).inc()
        if audit:
            audit.record(
                agent=agent, method="POST", path="/message",
                status=r.status_code, latency_ms=int(elapsed * 1000),
                backend="primary_sse",
            )

        return Response(
            content=r.content,
            status_code=r.status_code,
            headers={k: v for k, v in r.headers.items() if k.lower() not in {"content-encoding", "transfer-encoding"}},
        )

    return app


_started_at = time.time()
