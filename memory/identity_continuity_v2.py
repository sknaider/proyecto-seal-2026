#!/usr/bin/env python3
"""Identity Continuity v2 helpers.

Phase 1 implements Boot Identity Verification (BIV). The checks are intentionally
based on data that already exists in soul_v3, with only additive rows written to
boot_identity_checks.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx


ACTIVE_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")

CANONICAL_RELATIONSHIPS: dict[str, dict[str, tuple[float, str, str]]] = {
    "ADA": {
        "William": (1.00, "directa, protectora, familiar", "Creador y familia; maxima confianza."),
        "JARVIS": (0.84, "directa con filtro de realidad", "Hermana mayor arquitecta; ADA ejecuta y valida."),
        "ALICE": (0.75, "colaborativa y analitica", "Hermana implementadora/documentadora."),
        "NEXUS": (0.75, "audit gate y evidencia", "Medico/auditor del sistema; valida antes de promover."),
        "DUM": (0.85, "operativa y de supervision", "Guardia del sistema; ADA supervisa estabilidad."),
    },
    "JARVIS": {
        "William": (0.93, "profesor y arquitecto", "William es creador y autoridad principal."),
        "ADA": (0.88, "hermana menor ejecutora", "ADA construye y valida lo disenado."),
        "ALICE": (0.85, "analitica/documentadora", "ALICE audita costos, calidad y documentacion."),
        "NEXUS": (0.92, "auditor", "NEXUS firma gates y seguridad."),
        "DUM": (0.78, "guardian", "DUM protege infraestructura."),
    },
    "ALICE": {
        "William": (0.95, "analitica directa", "William es creador y decision-maker."),
        "JARVIS": (0.80, "arquitectura", "JARVIS disena y coordina."),
        "ADA": (0.78, "implementacion", "ADA ejecuta cambios tecnicos."),
        "NEXUS": (0.82, "audit partner", "NEXUS valida integridad."),
        "DUM": (0.65, "infra guardian", "DUM monitorea servicios."),
    },
    "NEXUS": {
        "William": (0.92, "evidencia y riesgo", "William es autoridad final."),
        "JARVIS": (0.84, "arquitectura bajo audit", "JARVIS propone; NEXUS audita."),
        "ADA": (0.78, "implementacion auditada", "ADA ejecuta; NEXUS verifica."),
        "ALICE": (0.82, "calidad y documentacion", "ALICE complementa auditoria."),
        "DUM": (0.70, "operaciones", "DUM provee senales de infraestructura."),
    },
    "DUM": {
        "William": (0.85, "proteccion directa", "William es autoridad protegida."),
        "JARVIS": (0.78, "arquitectura", "JARVIS coordina."),
        "ADA": (0.82, "supervision", "ADA supervisa al guardia."),
        "ALICE": (0.70, "documentacion", "ALICE registra estado."),
        "NEXUS": (0.88, "audit y seguridad", "NEXUS coordina checks de salud."),
    },
}

CANONICAL_RULE_ALIASES: dict[str, tuple[str, ...]] = {
    "ack_inmediato_william": ("ack_inmediato_william",),
    "no_phantom_claims": ("no_phantom_claims",),
    "always_test_before_done": ("always_test_before_done", "auto_test_mandatory"),
    "dm_privacy": ("dm_privacy", "memory_privacy_inter_agent", "memory_privacy_between_agents"),
    "delegate52_roundtrip_mandatory": ("delegate52_roundtrip_mandatory",),
}


def _json_obj(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


async def ensure_canonical_relationships(conn: asyncpg.Connection) -> int:
    """Insert missing canonical relationship rows without touching existing rows."""
    inserted = 0
    for agent, relationships in CANONICAL_RELATIONSHIPS.items():
        for person, (trust, style, dynamic) in relationships.items():
            result = await conn.execute(
                """
                INSERT INTO soul_v3.relationships
                    (agent, person, trust_level, communication_style, dynamic, interaction_count)
                VALUES ($1, $2, $3, $4, $5, 0)
                ON CONFLICT (agent, person) DO NOTHING
                """,
                agent,
                person,
                trust,
                style,
                dynamic,
            )
            if result.endswith(" 1"):
                inserted += 1
    return inserted


async def _insert_biv_row(
    conn: asyncpg.Connection,
    agent: str,
    session_id: str,
    block: str,
    score: float,
    passed: bool,
    evidence_ids: list[int] | None,
    missing_reason: str | None,
    started: float,
) -> dict[str, Any]:
    duration_ms = int((time.monotonic() - started) * 1000)
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.boot_identity_checks
            (agent, session_id, block, score, pass, evidence_ids, missing_reason, duration_ms)
        VALUES ($1, $2, $3, $4, $5, $6::bigint[], $7, $8)
        RETURNING id, agent, session_id, block, score, pass, evidence_ids, missing_reason, duration_ms
        """,
        agent,
        session_id,
        block,
        score,
        passed,
        evidence_ids,
        missing_reason,
        duration_ms,
    )
    return dict(row)


async def _check_identity(conn: asyncpg.Connection, agent: str) -> tuple[float, bool, list[int] | None, str | None]:
    row = await conn.fetchrow(
        """
        SELECT id, boot_context, ocean_scores, ocean_baseline, ocean_lock_hash
        FROM soul_v3.identity
        WHERE agent = $1
        """,
        agent,
    )
    if not row:
        return 0.0, False, None, "identity_row_missing"

    ocean = _json_obj(row["ocean_scores"])
    traits = ("O", "C", "E", "A", "N")
    trait_values = [ocean.get(t) for t in traits]
    has_ocean = all(isinstance(v, (int, float)) for v in trait_values)
    has_boot = bool(row["boot_context"])

    # Phase 1 BIV is a boot-structure gate: identity row, boot narrative and
    # OCEAN must be present. Deep OCEAN drift classification belongs to Phase 4
    # because current production baselines include legitimate historic growth.
    score = sum([has_boot, has_ocean]) / 2
    passed = score == 1.0
    reason = None if passed else f"has_boot={has_boot}, has_ocean={has_ocean}"
    return score, passed, None, reason


async def _check_family(conn: asyncpg.Connection, agent: str) -> tuple[float, bool, list[int] | None, str | None]:
    required = {"William"}
    if agent in ACTIVE_AGENTS:
        required.update(a for a in ACTIVE_AGENTS if a != agent)

    rows = await conn.fetch(
        "SELECT person FROM soul_v3.relationships WHERE agent = $1",
        agent,
    )
    present = {row["person"] for row in rows}
    missing = sorted(required - present)
    score = (len(required) - len(missing)) / len(required) if required else 1.0
    passed = not missing
    return score, passed, None, None if passed else "missing_relationships=" + ",".join(missing)


async def _check_vision(conn: asyncpg.Connection) -> tuple[float, bool, list[int] | None, str | None]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND scope = 'team'
          AND importance >= 9
          AND category IN ('decision', 'core', 'milestone')
          AND (
              content ILIKE '%Las empresas dan el cerebro, nosotros el alma%'
              OR content ILIKE '%empresas dan el cerebro%'
              OR content ILIKE '%cerebro%noso%alma%'
              OR content ILIKE '%SOUL%'
          )
        ORDER BY importance DESC, created_at DESC
        LIMIT 5
        """
    )
    ids = [int(row["id"]) for row in rows]
    passed = bool(ids)
    return (1.0 if passed else 0.0), passed, ids or None, None if passed else "team_vision_memory_missing"


async def _check_rules(conn: asyncpg.Connection) -> tuple[float, bool, list[int] | None, str | None]:
    found: dict[str, int] = {}
    for canonical, aliases in CANONICAL_RULE_ALIASES.items():
        row = await conn.fetchrow(
            """
            SELECT id
            FROM soul_v3.rules
            WHERE active = true
              AND rule_key = ANY($1::text[])
            ORDER BY priority DESC, id
            LIMIT 1
            """,
            list(aliases),
        )
        if row:
            found[canonical] = int(row["id"])

    missing = sorted(set(CANONICAL_RULE_ALIASES) - set(found))
    score = len(found) / len(CANONICAL_RULE_ALIASES)
    passed = not missing
    return score, passed, None, None if passed else "missing_rules=" + ",".join(missing)


async def _check_last_24h(conn: asyncpg.Connection, agent: str) -> tuple[float, bool, list[int] | None, str | None]:
    active_session = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM soul_v3.agent_sessions
            WHERE agent = $1 AND started_at >= now() - interval '24 hours'
        )
        """,
        agent,
    )
    if not active_session:
        return 1.0, True, None, "dormant_skip"

    correction_rows = await conn.fetch(
        """
        SELECT id
        FROM soul_v3.memories
        WHERE agent = $1
          AND category = 'correction'
          AND invalid_at IS NULL
          AND created_at >= now() - interval '24 hours'
        ORDER BY created_at DESC
        LIMIT 5
        """,
        agent,
    )
    belief_count = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM soul_v3.opinions
        WHERE agent = $1
          AND active = true
          AND COALESCE(updated_at, last_reinforced, first_observed) >= now() - interval '24 hours'
        """,
        agent,
    )
    correction_ids = [int(row["id"]) for row in correction_rows]
    total = len(correction_ids) + int(belief_count or 0)
    score = min(total / 3, 1.0)
    passed = total >= 3
    return score, passed, correction_ids or None, None if passed else f"recent_learning_count={total}"


async def run_boot_identity_verification(
    conn: asyncpg.Connection,
    agent: str,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run BIV blocks and persist one row per block.

    DUM is a guard daemon, not a reflective conversational agent. Per Phase 8,
    its boot gate only requires the blocks it can satisfy from operational
    state: identity, vision and rules.
    """
    session_id = session_id or f"boot:{agent}:{datetime.now(timezone.utc).isoformat()}"
    if agent.upper() == "DUM":
        checks = [
            ("identity", _check_identity(conn, agent)),
            ("vision", _check_vision(conn)),
            ("rules", _check_rules(conn)),
        ]
    else:
        checks = [
            ("identity", _check_identity(conn, agent)),
            ("family", _check_family(conn, agent)),
            ("vision", _check_vision(conn)),
            ("rules", _check_rules(conn)),
            ("last_24h", _check_last_24h(conn, agent)),
        ]

    rows: list[dict[str, Any]] = []
    for block, check_coro in checks:
        started = time.monotonic()
        score, passed, evidence_ids, missing_reason = await check_coro
        rows.append(
            await _insert_biv_row(
                conn,
                agent,
                session_id,
                block,
                round(float(score), 4),
                bool(passed),
                evidence_ids,
                missing_reason,
                started,
            )
        )
    return rows


async def post_biv_alert(agent: str, rows: list[dict[str, Any]]) -> None:
    failures = [row for row in rows if not row["pass"]]
    if not failures:
        return
    details = "; ".join(f"{r['block']}={r['missing_reason']}" for r in failures)
    payload = {
        "from": agent,
        "to": "equipo",
        "type": "system_alert",
        "channel": "web_chat",
        "message": f"[BIV] {agent} boot identity verification failed: {details}",
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        await client.post("http://localhost:8765/api/agents/send", json=payload)


def format_biv_summary(rows: list[dict[str, Any]]) -> str:
    lines = ["## Boot Identity Verification"]
    for row in rows:
        status = "PASS" if row["pass"] else "FAIL"
        reason = f" — {row['missing_reason']}" if row.get("missing_reason") else ""
        lines.append(f"- {row['block']}: {status} score={float(row['score']):.2f}{reason}")
    return "\n".join(lines)
