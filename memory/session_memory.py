"""Session Memory — Resumen automático de sesión para SOUL.

Inspirado en sessionMemory.ts de Claude Code v2.1.88.
Genera y mantiene un resumen acumulativo de la sesión actual
que sobrevive a la compactación del contexto.

Creado por JARVIS para Team SEAL — basado en investigación de Claude Code.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from db import get_pool

LOG = logging.getLogger("seal-session-memory")

# ── Esquema PostgreSQL ──
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_memory (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent           TEXT NOT NULL,
    turn_number     INT NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL,
    key_decisions   JSONB DEFAULT '[]'::jsonb,
    active_tasks    JSONB DEFAULT '[]'::jsonb,
    pending_items   JSONB DEFAULT '[]'::jsonb,
    errors_active   JSONB DEFAULT '[]'::jsonb,
    services_state  JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, agent)
);
"""


async def ensure_table():
    """Crea la tabla session_memory si no existe."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)


async def save_session_memory(
    agent: str,
    session_id: str,
    summary: str,
    turn_number: int = 0,
    key_decisions: Optional[list[str]] = None,
    active_tasks: Optional[list[dict]] = None,
    pending_items: Optional[list[str]] = None,
    errors_active: Optional[list[str]] = None,
    services_state: Optional[dict] = None,
) -> dict:
    """Guarda o actualiza el resumen de sesión.

    Usa UPSERT: si ya existe un registro para (session_id, agent), lo actualiza.
    Esto permite llamar save_session_memory repetidamente durante la sesión.

    Args:
        agent: ADA, JARVIS, DUM
        session_id: ID único de sesión (ej: 'jarvis_20260402_0215')
        summary: Resumen narrativo de lo que ha pasado en la sesión
        turn_number: Número de turno actual
        key_decisions: Lista de decisiones importantes tomadas
        active_tasks: Lista de tareas activas [{id, description, status}]
        pending_items: Lista de items pendientes de procesar
        errors_active: Lista de errores activos sin resolver
        services_state: Estado de servicios {pg: ok, neo4j: ok, qdrant: ok}

    Returns:
        Dict con id, session_id, agent, updated_at
    """
    await ensure_table()
    pool = await get_pool()

    decisions_json = json.dumps(key_decisions or [], ensure_ascii=False)
    tasks_json = json.dumps(active_tasks or [], ensure_ascii=False)
    pending_json = json.dumps(pending_items or [], ensure_ascii=False)
    errors_json = json.dumps(errors_active or [], ensure_ascii=False)
    services_json = json.dumps(services_state or {}, ensure_ascii=False)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO session_memory
               (session_id, agent, turn_number, summary, key_decisions,
                active_tasks, pending_items, errors_active, services_state)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, $9::jsonb)
               ON CONFLICT (session_id, agent) DO UPDATE SET
                   turn_number = EXCLUDED.turn_number,
                   summary = EXCLUDED.summary,
                   key_decisions = EXCLUDED.key_decisions,
                   active_tasks = EXCLUDED.active_tasks,
                   pending_items = EXCLUDED.pending_items,
                   errors_active = EXCLUDED.errors_active,
                   services_state = EXCLUDED.services_state,
                   updated_at = NOW()
               RETURNING id, session_id, agent, updated_at""",
            session_id, agent, turn_number, summary,
            decisions_json, tasks_json, pending_json, errors_json, services_json,
        )

    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "agent": row["agent"],
        "updated_at": row["updated_at"].isoformat(),
    }


async def get_session_memory(
    agent: str,
    session_id: Optional[str] = None,
) -> Optional[dict]:
    """Recupera el último resumen de sesión para un agente.

    Si session_id es None, retorna el más reciente.
    """
    await ensure_table()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if session_id:
            row = await conn.fetchrow(
                "SELECT * FROM session_memory WHERE session_id = $1 AND agent = $2",
                session_id, agent,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM session_memory WHERE agent = $1 ORDER BY updated_at DESC LIMIT 1",
                agent,
            )

    if not row:
        return None

    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "agent": row["agent"],
        "turn_number": row["turn_number"],
        "summary": row["summary"],
        "key_decisions": json.loads(row["key_decisions"]) if isinstance(row["key_decisions"], str) else row["key_decisions"],
        "active_tasks": json.loads(row["active_tasks"]) if isinstance(row["active_tasks"], str) else row["active_tasks"],
        "pending_items": json.loads(row["pending_items"]) if isinstance(row["pending_items"], str) else row["pending_items"],
        "errors_active": json.loads(row["errors_active"]) if isinstance(row["errors_active"], str) else row["errors_active"],
        "services_state": json.loads(row["services_state"]) if isinstance(row["services_state"], str) else row["services_state"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def list_recent_sessions(
    agent: str,
    limit: int = 5,
) -> list[dict]:
    """Lista las últimas N sesiones de un agente con resumen breve."""
    await ensure_table()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT session_id, turn_number, summary, updated_at
               FROM session_memory
               WHERE agent = $1
               ORDER BY updated_at DESC
               LIMIT $2""",
            agent, limit,
        )

    return [
        {
            "session_id": r["session_id"],
            "turn_number": r["turn_number"],
            "summary": r["summary"][:200] + "..." if len(r["summary"]) > 200 else r["summary"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


def generate_session_id(agent: str) -> str:
    """Genera un session_id único basado en agente + timestamp."""
    now = datetime.now(timezone.utc)
    return f"{agent.lower()}_{now.strftime('%Y%m%d_%H%M')}"
