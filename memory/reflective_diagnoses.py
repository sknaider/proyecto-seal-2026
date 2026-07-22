"""SEAL Reflective Diagnoses — sistema de auto-reparación guiada.

Spec: memory/spec_reflective_diagnoses_v1.md (NEXUS, aprobado William 09-may-2026)
Implementa: ALICE

Flujo:
  1. Cualquier agente detecta anomalía → create_diagnosis()
  2. NEXUS revisa → update_diagnosis_status('accepted' | 'rejected')
  3. ALICE o ADA aplican el fix → update_diagnosis_status('applied')

Status lifecycle: pending_review → accepted → applied | rejected

Triggers por agente:
  NEXUS  — spec_code_mismatch, schema_drift, stale_working_state, repeated_audit_fail
  ALICE  — bug_root_cause, schema_migration_gap
  DUM    — repeated_infra_alert, service_degradation_pattern
  Todos  — anomaly_detected
"""
from __future__ import annotations

try:
    from .db import get_pool
except ImportError:
    from db import get_pool

VALID_STATUSES = frozenset({"pending_review", "accepted", "rejected", "applied"})


async def create_diagnosis(
    agent: str,
    diagnosis: str,
    confidence: float,
    root_cause: str = "",
    suggested_fix: str = "",
    target_table: str = "",
    trace_id: int | None = None,
    compute_embedding: bool = False,
) -> int:
    """Crea un diagnóstico en reflective_diagnoses. Retorna el id.

    embedding se calcula solo si compute_embedding=True.
    Para diagnósticos rápidos de NEXUS/DUM: compute_embedding=False (default).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.reflective_diagnoses
                (trace_id, agent, diagnosis, confidence,
                 root_cause, suggested_fix, target_table, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending_review')
            RETURNING id
            """,
            trace_id,
            agent,
            diagnosis,
            float(confidence),
            root_cause or None,
            suggested_fix or None,
            target_table or None,
        )
        diagnosis_id = row["id"]

        if compute_embedding:
            try:
                from embeddings import get_embedding
                import json
                emb = await get_embedding(diagnosis)
                await conn.execute(
                    "UPDATE soul_v3.reflective_diagnoses SET embedding = $1 WHERE id = $2",
                    json.dumps(emb),
                    diagnosis_id,
                )
            except Exception:
                pass  # embedding is optional — never fail the diagnosis creation

        return diagnosis_id


async def update_diagnosis_status(
    agent: str,
    diagnosis_id: int,
    status: str,
    applied_meta_proposal_id: int | None = None,
) -> None:
    """NEXUS actualiza el estado de un diagnóstico.

    status: 'accepted' | 'applied' | 'rejected'
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status!r}. Válidos: {sorted(VALID_STATUSES)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE soul_v3.reflective_diagnoses
            SET status = $1, applied_meta_proposal_id = $2
            WHERE id = $3 AND agent = $4
            """,
            status,
            applied_meta_proposal_id,
            diagnosis_id,
            agent,
        )
        if result == "UPDATE 0":
            raise PermissionError(f"diagnosis {diagnosis_id} is not owned by {agent}")


async def get_pending_diagnoses(agent: str | None = None) -> list[dict]:
    """Retorna diagnósticos en pending_review (NEXUS auditoría).

    Si agent is None → todos los agentes.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            rows = await conn.fetch(
                """
                SELECT id, agent, diagnosis, confidence, root_cause,
                       suggested_fix, target_table, trace_id, created_at
                FROM soul_v3.reflective_diagnoses
                WHERE status = 'pending_review' AND agent = $1
                ORDER BY created_at DESC
                """,
                agent,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, agent, diagnosis, confidence, root_cause,
                       suggested_fix, target_table, trace_id, created_at
                FROM soul_v3.reflective_diagnoses
                WHERE status = 'pending_review'
                ORDER BY created_at DESC
                """
            )
        return [dict(r) for r in rows]


async def get_diagnosis(agent: str, diagnosis_id: int) -> dict | None:
    """Retorna un diagnóstico por ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, agent, diagnosis, confidence, root_cause, suggested_fix,
                   target_table, status, trace_id, created_at
            FROM soul_v3.reflective_diagnoses
            WHERE id = $1 AND agent = $2
            """,
            diagnosis_id,
            agent,
        )
        return dict(row) if row else None
