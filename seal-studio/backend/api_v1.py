#!/usr/bin/env python3
"""
SEAL Studio — API v1 (contrato versionado estable)
===================================================
Capa v1 ADITIVA sobre el backend existente (:8800). No toca las rutas /api/*
actuales; las envuelve y agrega lo nuevo que pide la remodelación:

  GET  /v1/health                          → estado del gateway + versión
  GET  /v1/tools                           → catálogo unificado de tools MCP
  POST /v1/tools/{server}/{tool}/invoke    → invocar un tool (creds server-side)
  GET  /v1/stream                          → SSE de eventos del equipo (vivo)
  GET  /v1/team/status                     → alias versionado (read)
  GET  /v1/soul/ocean/all                  → alias versionado (read)
  GET  /v1/system/gpu | /v1/system/health  → alias versionado (read)

Contrato para que ALICE (frontend) construya contra algo estable.
Diseñado por JARVIS — Team SEAL — 2026-06-02.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from chat_contract import coerce_metadata, normalize_chat_message
import mcp_gateway

router = APIRouter(prefix="/v1", tags=["v1"])

API_V1_VERSION = "1.0.0"


class InvokeBody(BaseModel):
    arguments: dict = {}


# ── Meta ──
@router.get("/health")
async def v1_health():
    cfg = mcp_gateway.load_config()
    return {
        "ok": True,
        "version": API_V1_VERSION,
        "mcp_servers_configured": len(cfg),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── MCP tools (registro unificado) ──
@router.get("/tools")
async def v1_tools(probe_all: bool = False):
    """
    Catálogo unificado de tools de TODOS los MCP servers (igual que Claude Code).
    `probe_all=true` conecta en vivo a todos (incl. stdio); por defecto solo a los
    HTTP seguros (seal-memory) y reporta el resto como 'configured'.
    """
    return await mcp_gateway.list_catalog(probe_all=probe_all)


@router.post("/tools/{server}/{tool}/invoke")
async def v1_invoke(server: str, tool: str, body: InvokeBody, request: Request):
    """
    Invoca un tool MCP. Las credenciales del server NUNCA salen del backend.
    CAPABILITY GATE Fase 1 — ENFORCE: identifica al caller, evalúa el gate
    (deny-by-default §3), AUDITA, y DENIEGA (403) a callers externos sin grant.
    Agentes internos siempre permitidos. SEAL_CAPABILITY_OBSERVE=1 vuelve a observe.
    Caller identity comes exclusively from the canonical session validated by
    main.py middleware. X-Caller-* headers are deliberately ignored.
    """
    from fastapi import HTTPException
    session_user = getattr(request.state, "studio_user", None) or {}
    caller_id = str(session_user.get("username") or "unknown")
    caller_type = "admin" if session_user.get("role") in {"admin", "superuser"} else "external_user"
    observe_only = os.environ.get("SEAL_CAPABILITY_OBSERVE", "") in ("1", "true", "True")

    import asyncpg
    granted, reason = True, "not evaluated"
    try:
        conn = await asyncpg.connect(mcp_gateway.DB_URL)
        try:
            granted, reason = await mcp_gateway.check_capability(conn, server, tool, caller_id, caller_type)
            # ENFORCE: deny externos sin grant (NEXUS: granted=False Y caller_type != internal_agent).
            if not observe_only and not granted and caller_type != "internal_agent":
                await mcp_gateway.write_audit(
                    conn, caller_id=caller_id, caller_type=caller_type, server=server, tool=tool,
                    granted=False, reason=reason, latency_ms=0, status=403)
                raise HTTPException(status_code=403, detail=f"capability denied: {reason}")
            t0 = time.monotonic()
            result = await mcp_gateway.invoke(
                server,
                tool,
                body.arguments,
                caller_id=caller_id,
                caller_type=caller_type,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            status = 200 if result.get("ok") else 502
            await mcp_gateway.write_audit(
                conn, caller_id=caller_id, caller_type=caller_type, server=server, tool=tool,
                granted=granted, reason=reason, latency_ms=latency_ms, status=status)
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        # DB caída: fail-CLOSED para externos (no se puede verificar → denegar),
        # fail-open para internos (allow por defecto). observe_only nunca bloquea.
        if not observe_only and caller_type != "internal_agent":
            raise HTTPException(status_code=503, detail="capability gate unavailable (fail-closed)")
        result = await mcp_gateway.invoke(
            server,
            tool,
            body.arguments,
            caller_id=caller_id,
            caller_type=caller_type,
        )

    result["_gate"] = {"mode": "observe" if observe_only else "enforce", "granted": granted,
                       "reason": reason, "caller_id": caller_id, "caller_type": caller_type}
    return result


# ── Real-time (SSE) ──
@router.get("/stream")
async def v1_stream(interval: float = 5.0):
    """
    SSE de eventos del equipo en vivo (estado de agentes). El stream de tokens
    por-run de agentes llega en Fase 3 (Claude Agent SDK) bajo /v1/agents/{name}/run.
    Eventos: `team_status` cada `interval`s + `ping` keep-alive.
    """
    interval = max(1.0, min(float(interval), 60.0))

    async def gen():
        # import diferido para evitar import circular con main
        from main import team_status
        # evento inicial inmediato
        try:
            data = await team_status()
            yield {"event": "team_status", "data": json.dumps(data)}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)[:160]})}
        while True:
            await asyncio.sleep(interval)
            try:
                data = await team_status()
                yield {"event": "team_status", "data": json.dumps(data)}
            except Exception as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)[:160]})}

    return EventSourceResponse(gen())


# ── Chat en vivo (SSE) — Fase 2 ──
def _channel_visible_to_session(channel: str, uid: int, identity: str) -> bool:
    """Application-level gate before the database RLS gate."""
    normalized = (channel or "web_chat").lower()
    if normalized.startswith("dm_"):
        return False
    if normalized.startswith("dm:"):
        return identity.lower() in {part for part in normalized[3:].split(":") if part}
    if normalized.startswith("user:"):
        parts = normalized.split(":", 2)
        return len(parts) == 3 and parts[1] == str(uid)
    return True



# La consulta del stream en vivo, a nivel de modulo A PROPOSITO: asi un test puede
# importarla y comprobar el cableado real. Con la consulta enterrada dentro del
# generador, un test podia verificar la conversion de `metadata` y pasar en verde
# aunque la columna hubiera dejado de seleccionarse — probar la pieza no prueba que
# este conectada.
# `metadata` COMPLETA, no solo dos claves: antes se extraian unicamente file_url y
# filename, y `runtime_instance` (la firma del cuerpo que habla) nunca salia por el
# vivo. La UI entonces adivinaba por canal.
STREAM_MESSAGES_SQL = (
    "SELECT id, channel, sender_name, content, message_type, "
    "metadata, "
    "metadata->>'file_url' AS file_url, "
    "metadata->>'filename' AS filename, created_at "
    "FROM chat_messages WHERE channel=$1 AND id>$2 ORDER BY id ASC LIMIT 50"
)

@router.get("/chat/stream")
async def v1_chat_stream(request: Request, channel: str = "web_chat", interval: float = 2.0):
    """
    SSE de mensajes NUEVOS de un canal (para el chat assistant-ui en vivo).
    Hace tail de chat_messages (id creciente) bajo RLS de la sesión canónica.
    Eventos: `message` (uno por mensaje nuevo) + `ping` keep-alive.
    """
    user = getattr(request.state, "studio_user", None) or {}
    uid = int(user.get("id") or 0)
    identity = str(user.get("username") or "").strip()
    if not uid or not identity:
        async def deny_auth():
            yield {"event": "error", "data": json.dumps({"error": "authentication_required"})}
        return EventSourceResponse(deny_auth(), status_code=401)

    if not _channel_visible_to_session(channel, uid, identity):
        async def deny_private():
            yield {"event": "error", "data": json.dumps({"error": "channel not authorized"})}
        return EventSourceResponse(deny_private(), status_code=403)

    interval = max(0.5, min(float(interval), 30.0))

    async def gen():
        import asyncpg
        from main import DB_URL
        last_id = 0
        conn = None
        try:
            conn = await asyncpg.connect(DB_URL)
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE chat_msg_ro")
                await conn.execute("SELECT set_config('app.current_user_id', $1, true)", str(uid))
                await conn.execute("SELECT set_config('app.current_identity', $1, true)", identity)
                row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(id),0) AS m FROM chat_messages WHERE channel=$1", channel)
                last_id = row["m"] if row else 0
            ticks = 0
            while not await request.is_disconnected():
                await asyncio.sleep(interval)
                ticks += 1
                async with conn.transaction():
                    await conn.execute("SET LOCAL ROLE chat_msg_ro")
                    await conn.execute("SELECT set_config('app.current_user_id', $1, true)", str(uid))
                    await conn.execute("SELECT set_config('app.current_identity', $1, true)", identity)
                    rows = await conn.fetch(STREAM_MESSAGES_SQL, channel, last_id)
                for r in rows:
                    m = normalize_chat_message(r)
                    # `metadata` COMPLETA en el stream, no solo dos claves sueltas.
                    # El porque y el detalle de la conversion viven en
                    # chat_contract.coerce_metadata, junto a su test.
                    m["metadata"] = coerce_metadata(m.get("metadata"))
                    last_id = max(last_id, m["id"])
                    yield {"event": "message", "data": json.dumps(m)}
                if not rows and ticks % 15 == 0:
                    yield {"event": "ping", "data": json.dumps({"last_id": last_id})}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)[:160]})}
        finally:
            if conn is not None:
                await conn.close()

    return EventSourceResponse(gen())


# ── Agent runtime (Fase 3) — CABLEADO PERO APAGADO por defecto (gate de costo) ──
# Claude Agent SDK cobra crédito mensual aparte (desde 15-jun-2026). NO se activa
# ni se importa el SDK hasta que William encienda SEAL_AGENT_RUNTIME_ENABLED=1.
import os as _os


class AgentRunBody(BaseModel):
    prompt: str
    max_turns: int = 8


@router.get("/agents/runtime/status")
async def v1_agent_runtime_status():
    enabled = _os.environ.get("SEAL_AGENT_RUNTIME_ENABLED", "") in ("1", "true", "True")
    sdk = False
    try:
        import importlib.util as _u
        sdk = _u.find_spec("claude_agent_sdk") is not None
    except Exception:
        sdk = False
    return {"enabled": enabled, "sdk_installed": sdk,
            "note": "Runtime apagado por gate de costo (OK de William)." if not enabled else "Runtime activo."}


@router.post("/agents/{name}/run")
async def v1_agent_run(name: str, body: AgentRunBody):
    """
    Ejecuta un agente con el Claude Agent SDK (mismas tools que Claude Code).
    APAGADO por defecto: requiere SEAL_AGENT_RUNTIME_ENABLED=1 (decisión de costo de William).
    """
    enabled = _os.environ.get("SEAL_AGENT_RUNTIME_ENABLED", "") in ("1", "true", "True")
    if not enabled:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=(
            "Agent runtime APAGADO (gate de costo). El Claude Agent SDK cobra crédito mensual "
            "aparte desde 15-jun-2026. Enciéndelo con SEAL_AGENT_RUNTIME_ENABLED=1 tras OK de William."))
    # Lazy import — solo cuando está habilitado.
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions  # noqa
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=501, detail="claude-agent-sdk no instalado todavía.")

    async def gen():
        try:
            options = ClaudeAgentOptions(max_turns=body.max_turns)
            async for msg in query(prompt=body.prompt, options=options):
                yield {"event": "token", "data": json.dumps({"agent": name, "chunk": str(msg)})}
            yield {"event": "done", "data": json.dumps({"agent": name})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)[:200]})}

    return EventSourceResponse(gen())


# ── SOUL dashboards (Fase 2.5) — endpoints para los paneles de /v2 (ALICE) ──
_ALLOWED_AGENTS = {"ALICE", "JARVIS", "ADA", "NEXUS", "FABLE", "DUM", "TEAM"}


async def _soul_conn():
    import asyncpg
    from main import DB_URL
    return await asyncpg.connect(DB_URL)


@router.get("/soul/nerves")
async def v1_soul_nerves(agent: Optional[str] = None):
    """Drives NERVES en vivo: última presión por (agente,tank). [{agent,tank,pressure,threshold,fired,ocean_param}]"""
    conn = await _soul_conn()
    try:
        if agent:
            a = agent.upper()
            if a not in _ALLOWED_AGENTS:
                return {"drives": [], "error": "agente no permitido"}
            rows = await conn.fetch(
                "SELECT DISTINCT ON (agent,tank) agent,tank,pre_pressure AS pressure,threshold,fired,ocean_param "
                "FROM soul_v3.nerves_metrics_log WHERE agent=$1 ORDER BY agent,tank,created_at DESC", a)
        else:
            rows = await conn.fetch(
                "SELECT DISTINCT ON (agent,tank) agent,tank,pre_pressure AS pressure,threshold,fired,ocean_param "
                "FROM soul_v3.nerves_metrics_log ORDER BY agent,tank,created_at DESC")
        return {"drives": [dict(r) for r in rows]}
    finally:
        await conn.close()


@router.get("/soul/governance")
async def v1_soul_governance(limit: int = 30):
    """Revisiones de gobernanza inter-agente: {open,passed,failed, items:[{title,type,status,consensus}]}"""
    conn = await _soul_conn()
    try:
        rows = await conn.fetch(
            "SELECT agent, reviewer_agent, source_kind, category, status, rationale, created_at "
            "FROM soul_v3.cross_agent_governance_reviews ORDER BY created_at DESC LIMIT $1", min(limit, 100))
        items, openc, passed, failed = [], 0, 0, 0
        for r in rows:
            st = (r["status"] or "").lower()
            if st in ("approved", "passed", "accepted", "agree"):
                passed += 1
            elif st in ("rejected", "failed", "denied", "disagree"):
                failed += 1
            else:
                openc += 1
            items.append({
                "title": f"{r['source_kind'] or '?'} · {r['category'] or ''}".strip(" ·"),
                "type": r["source_kind"], "status": r["status"],
                "consensus": (r["rationale"] or "")[:200],
                "by": r["reviewer_agent"], "agent": r["agent"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return {"open": openc, "passed": passed, "failed": failed, "items": items}
    finally:
        await conn.close()


@router.get("/soul/memory-stats")
async def v1_soul_memory_stats():
    """Stats de memoria activa: {total, by_agent, by_category}"""
    conn = await _soul_conn()
    try:
        total = await conn.fetchval("SELECT count(*) FROM soul_v3.memories WHERE invalid_at IS NULL")
        by_agent = await conn.fetch(
            "SELECT agent, count(*) AS n FROM soul_v3.memories WHERE invalid_at IS NULL GROUP BY agent ORDER BY n DESC")
        by_cat = await conn.fetch(
            "SELECT category, count(*) AS n FROM soul_v3.memories WHERE invalid_at IS NULL "
            "GROUP BY category ORDER BY n DESC LIMIT 12")
        return {
            "total": total,
            "by_agent": {r["agent"]: r["n"] for r in by_agent},
            "by_category": [{"cat": r["category"], "n": r["n"]} for r in by_cat],
        }
    finally:
        await conn.close()


@router.get("/soul/agent/{name}")
async def v1_soul_agent(name: str):
    """Detalle de un agente: {working_state, instincts_count, drift_score}"""
    a = name.upper()
    if a not in _ALLOWED_AGENTS:
        return {"error": "agente no permitido"}
    conn = await _soul_conn()
    try:
        ws = await conn.fetchrow(
            "SELECT task_name, step, total_steps, risk_level, emotional_state, last_intention, agent_state, "
            "updated_at FROM soul_v3.working_state WHERE agent=$1", a)
        instincts = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.instincts WHERE agent=$1 AND invalid_at IS NULL", a)
        instincts_total = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.instincts WHERE agent=$1", a)
        drift = await conn.fetchrow(
            "SELECT drift_score, drift_level FROM soul_v3.ocean_drift_log WHERE agent=$1 "
            "ORDER BY created_at DESC LIMIT 1", a)
        return {
            "agent": a,
            "working_state": {
                "task": ws["task_name"] if ws else None,
                "step": ws["step"] if ws else None,
                "total_steps": ws["total_steps"] if ws else None,
                "risk": ws["risk_level"] if ws else None,
                "emotional_state": ws["emotional_state"] if ws else None,
                "last_intention": ws["last_intention"] if ws else None,
                "agent_state": ws["agent_state"] if ws else None,
                "updated_at": ws["updated_at"].isoformat() if ws and ws["updated_at"] else None,
            } if ws else None,
            "instincts_count": instincts or 0,
            "instincts_total": instincts_total or 0,
            "drift_score": float(drift["drift_score"]) if drift and drift["drift_score"] is not None else None,
            "drift_level": drift["drift_level"] if drift else None,
        }
    finally:
        await conn.close()


# ── Aliases versionados (read) — envuelven los handlers existentes ──
@router.get("/team/status")
async def v1_team_status():
    from main import team_status
    return await team_status()


@router.get("/soul/ocean/all")
async def v1_soul_ocean_all():
    from main import soul_ocean_all
    return await soul_ocean_all()


@router.get("/system/gpu")
async def v1_system_gpu():
    """GPU normalizado para el dashboard: {temp,util,power,mem_used_gb,mem_total_gb,name}."""
    from main import system_gpu
    raw = await system_gpu()
    if raw.get("error"):
        return raw
    def gb(mb):
        return round(mb / 1024, 1) if isinstance(mb, (int, float)) else None
    # power.draw aparte (el handler base no lo trae)
    power = None
    try:
        import subprocess
        p = subprocess.run(["nvidia-smi", "--query-gpu=power.draw",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0:
            v = p.stdout.strip().splitlines()[0].strip()
            power = float(v) if v not in ("", "[N/A]", "N/A") else None
    except Exception:
        power = None
    return {
        "temp": raw.get("temperature"), "util": raw.get("utilization"), "power": power,
        "mem_used_gb": gb(raw.get("memory_used_mb")), "mem_total_gb": gb(raw.get("memory_total_mb")),
        "name": raw.get("name"),
    }


@router.get("/system/health")
async def v1_system_health():
    from main import system_health
    return await system_health()
