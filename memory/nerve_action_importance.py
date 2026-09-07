#!/usr/bin/env python3
"""Nerve drive-action — RECALIBRACIÓN DE IMPORTANCIA (lane ALICE).

Pieza del registro de acciones de nervios útiles (William 14-jun: "que los nervios
sean útiles a favor de SOUL"). NO toca `seal_nerves.py` — NEXUS la cablea en su DISPATCH
{drive_tipo: función}. Cuando un drive llega a FIRE, en vez de postear "quiero conectar",
invoca ESTA acción, que mantiene a SOUL (des-infla la importancia 73-90% que el audit cazó)
y deja un ARTEFACTO verificable. Solo se reporta a William si produjo valor.

Reusa `soul_cognitive_priority.py` (calibrated_importance / importance_health / fetch_memories),
que hoy es read-only: aquí está el ÚNICO paso mutante (escribir la importancia calibrada),
gated por `apply=False` (dry-run por defecto) y un tope `max_writes` por seguridad sobre la
DB compartida del SOUL.

Contrato para el DISPATCH de NEXUS:
    summary = await run_importance_recalibration(pool, agent=<firing_agent>, apply=True)
    # summary["valuable"] == True  → NEXUS reporta a William; False → silencio (sin artefacto = sin mensaje)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import soul_cognitive_priority as scp

# Solo des-inflar lo CLARAMENTE inflado (brecha grande): evita tocar lo dudoso.
INFLATION_GAP_MIN = 3
# Tope de escrituras por disparo: una corrida de nervio no debe reescribir la DB entera.
DEFAULT_MAX_WRITES = 40
# Cuántas memorias mira por corrida (las más recientes del agente).
DEFAULT_SCAN_LIMIT = 300
# Artefacto append-only: traza de cada des-inflación (verificable por efecto).
ARTIFACT_PATH = Path("/home/dadito/IA/proyecto-seal/memory/nerve_importance_recalibration.jsonl")


async def run_importance_recalibration(
    pool,
    agent: str | None = None,
    *,
    apply: bool = False,
    max_writes: int = DEFAULT_MAX_WRITES,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> dict:
    """Des-infla la importancia de las memorias del `agent` usando evidencia (no a ojo).

    - Lee memorias vivas del agente.
    - Calcula la importancia calibrada (tipo + uso real recall_count) vs la guardada.
    - Para las CLARAMENTE infladas (gap >= INFLATION_GAP_MIN), baja la guardada a la calibrada.
    - dry-run por defecto (apply=False): NO escribe, solo dice qué haría.
    - Deja artefacto JSONL + devuelve summary con `valuable` (¿produjo cambio real?).

    Devuelve dict: action, agent, apply, scanned, inflated, recalibrated, health_before,
    health_after, examples, artifact_path, valuable.
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        rows = await scp.fetch_memories(conn, agent, scan_limit) if agent else []
        if not rows:
            return {
                "action": "importance_recalibration", "agent": agent, "apply": apply,
                "scanned": 0, "inflated": 0, "recalibrated": 0, "valuable": False,
                "note": "sin memorias para el agente (o agent=None)",
            }

        health_before = scp.importance_health(rows, now)
        cals = [scp.calibrated_importance(r, now) for r in rows]
        inflated = [c for c in cals if c.inflation_gap >= INFLATION_GAP_MIN and c.memory_id > 0]
        # ordenar por brecha desc: arregla primero lo más inflado, respeta el tope.
        inflated.sort(key=lambda c: c.inflation_gap, reverse=True)
        to_fix = inflated[:max_writes]

        recalibrated = 0
        examples = []
        if apply and to_fix:
            for c in to_fix:
                await conn.execute(
                    "UPDATE soul_v3.memories SET importance=$1 WHERE id=$2 AND invalid_at IS NULL",
                    int(c.calibrated_importance), int(c.memory_id),
                )
                recalibrated += 1
                if len(examples) < 5:
                    examples.append({"id": c.memory_id, "from": c.stored_importance,
                                     "to": c.calibrated_importance, "why": c.rationale})
            # releer para health_after real (por efecto, no estimado)
            rows_after = await scp.fetch_memories(conn, agent, scan_limit)
            health_after = scp.importance_health(rows_after, now)
        else:
            # dry-run: muestra qué bajaría, sin tocar nada.
            for c in to_fix[:5]:
                examples.append({"id": c.memory_id, "from": c.stored_importance,
                                 "to": c.calibrated_importance, "why": c.rationale})
            health_after = health_before  # sin cambios en dry-run

    summary = {
        "action": "importance_recalibration",
        "agent": agent,
        "apply": apply,
        "ts": now.isoformat(),
        "scanned": len(rows),
        "inflated": len(inflated),
        "recalibrated": recalibrated,
        "capped": len(inflated) > max_writes,
        "health_before": health_before,
        "health_after": health_after,
        "examples": examples,
        # valor producido = des-infló algo real (en apply). En dry-run, valioso solo si HAY inflación que arreglar.
        "valuable": (recalibrated > 0) if apply else (len(inflated) > 0),
    }

    # Artefacto append-only (siempre, también en dry-run para trazar lo que SE HARÍA).
    try:
        ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ARTIFACT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        summary["artifact_path"] = str(ARTIFACT_PATH)
    except Exception as e:  # el artefacto no debe tumbar la acción
        summary["artifact_error"] = str(e)

    return summary
