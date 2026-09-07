#!/usr/bin/env python3
"""
soul_cognitive_priority.py — Núcleo Cognitivo SOUL · carril de ALICE.

Dos funciones de VALOR que el núcleo necesita para DECIDIR (spec_soul_cognitive_core_v1 §5.3, §5.4):
  • Objective Engine (§5.4): ¿qué tarea es PRIORITARIA? — score value/urgencia/esfuerzo, no "lo más nuevo".
  • Memory Intelligence (§5.3): ¿qué RECORDAR vs OLVIDAR? — función de valor de retención (proteger lo
    constitutivo, degradar lo vencido).

Diseño (coherente con soul_cognitive_core.py de ADA): READ-ONLY, funciones PURAS, reusa tablas existentes
(soul_v3.agent_tasks, soul_v3.memories), salida JSON. NO muta nada. NO duplica el core — lo extiende.

Principio ALICE (cost engineering aplicado a cognición): priorizar/recordar es maximizar VALOR por unidad
de ESFUERZO, no reaccionar al ruido. Cada decisión trae su rationale (por qué), nunca un número pelado.

Uso:  python3 soul_cognitive_priority.py tasks [--agent ALICE] [--top 10]
      python3 soul_cognitive_priority.py memory --agent ALICE [--limit 200]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import asyncpg

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# ── Constantes nombradas (sin magic numbers sueltos) ─────────────────────────
_PRIORITY_MAX = 10          # escala de agent_tasks.priority (1..10)
_STALE_TASK_DAYS = 7.0      # una tarea in_progress sin avance > esto = penaliza (envejecimiento)
_AGE_URGENCY_CAP_DAYS = 21.0  # tope de urgencia por antigüedad (no crece infinito)
_MEM_HALF_LIFE_DAYS = 30.0  # vida media del decaimiento de recencia de memoria
_MEM_PROTECT_IMPORTANCE = 10  # SOLO imp=10 (o tipo constitutivo) = proteger. Subido de 9→10 por INFLACIÓN
                              # DE IMPORTANCIA observada: ~88% de las memorias estaban en imp≥9 → con 9 casi
                              # nada se discriminaba. Con 10 + tipos protegidos, el valor pesa de verdad.
_MEM_PROTECT_TYPES = {"core", "constitution", "identity", "decision", "rule"}
_MEM_DEGRADE_TYPES = {"stale", "scratch", "debug", "test"}
_MEM_STALE_DAYS = 120.0     # memoria operativa sin tocar > esto, baja importancia = candidata a archivar


# ── Objective Engine (§5.4): prioridad de tareas ─────────────────────────────
@dataclass
class TaskPriority:
    task_id: int
    agent: str
    title: str
    status: str
    raw_priority: int
    score: float                 # 0..1 normalizado
    components: dict[str, float]  # desglose transparente
    rationale: str               # por qué este lugar (lenguaje humano)


def _days_since(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def score_task(row: "asyncpg.Record | dict", now: datetime) -> TaskPriority:
    """Score de prioridad de UNA tarea. value × urgencia ÷ esfuerzo (proxy), todo 0..1.

    - value: la prioridad declarada (1..10) normalizada — lo que William/equipo ya valoró.
    - urgency: crece con la antigüedad de una tarea ABIERTA (lo viejo-no-cerrado urge), con tope.
    - blocked_penalty: una tarea bloqueada NO puede avanzar → baja su score efectivo (no malgastar foco).
    - stale_boost: in_progress sin avance > umbral = subir para des-estancar (o cerrar/soltar).
    """
    g = (lambda k: row[k] if k in row else row.get(k)) if isinstance(row, dict) else (lambda k: row.get(k))
    raw_priority = int(g("priority") or 5)
    status = (g("status") or "pending").lower()
    created = g("created_at")
    updated = g("updated_at") or created
    title = (g("title") or "")[:120]
    desc = (g("description") or "")

    value = raw_priority / _PRIORITY_MAX
    age_days = _days_since(created, now)
    urgency = min(age_days, _AGE_URGENCY_CAP_DAYS) / _AGE_URGENCY_CAP_DAYS

    # señal de bloqueo: heurística read-only (la spec promete task_lifecycle_events para v2)
    blocked = any(w in (status + " " + desc.lower()) for w in ("blocked", "bloquead", "waiting", "espera"))
    blocked_penalty = 0.35 if blocked else 0.0

    # estancamiento: in_progress sin actualizar hace mucho → necesita atención (cerrar o destrabar)
    idle_days = _days_since(updated, now)
    stale_boost = 0.0
    if status == "in_progress" and idle_days > _STALE_TASK_DAYS:
        stale_boost = min((idle_days - _STALE_TASK_DAYS) / _STALE_TASK_DAYS, 1.0) * 0.2

    score = max(0.0, min(1.0, 0.6 * value + 0.3 * urgency + stale_boost - blocked_penalty))

    bits = [f"prioridad declarada {raw_priority}/10"]
    if urgency > 0.4:
        bits.append(f"lleva {age_days:.0f}d abierta (urge)")
    if stale_boost > 0:
        bits.append(f"estancada {idle_days:.0f}d sin avance — destrabar o cerrar")
    if blocked:
        bits.append("BLOQUEADA → baja foco hasta destrabar")
    rationale = "; ".join(bits)

    return TaskPriority(
        task_id=int(g("id") or 0), agent=(g("agent") or "?"), title=title, status=status,
        raw_priority=raw_priority, score=round(score, 4),
        components={"value": round(value, 3), "urgency": round(urgency, 3),
                    "stale_boost": round(stale_boost, 3), "blocked_penalty": round(blocked_penalty, 3)},
        rationale=rationale,
    )


def score_memory_robust(row: "asyncpg.Record | dict", now: datetime) -> MemoryValue:
    """Variante ROBUSTA a la INFLACIÓN DE IMPORTANCIA (hallazgo 9-jun: ~88-95% de memorias en imp~10).
    Cuando la importancia guardada está saturada, NO sirve para decidir qué olvidar. Entonces:
      • PROTEGER solo por TIPO constitutivo (core/identity/decision/rule) — no por el número inflado.
      • Para el resto, el valor lo manda el USO REAL (use_count) + la RECENCIA; la importancia es solo
        un prior DÉBIL (peso 0.15). Así una memoria imp=10 vieja y JAMÁS recuperada cae a archive — el
        núcleo recupera la capacidad de OLVIDAR que la inflación le había quitado.
    """
    g = (lambda k: row[k] if k in row else row.get(k)) if isinstance(row, dict) else (lambda k: row.get(k))
    importance = int(g("importance") or 5)
    mem_type = (g("category") or g("memory_type") or "semantic").lower()
    days_old = _days_since(g("created_at"), now)
    # FIX JARVIS+FABLE 11/12-jun: la columna access_count (medidor MUERTO, sum ~17 vs recall 9270)
    # quedó DEPRECADA. use_count = recall_count (medidor VIVO de uso). 0 lecturas del access_count muerto.
    use_count = int(g("recall_count") or 0)

    recency = math.exp(-math.log(2) * days_old / _MEM_HALF_LIFE_DAYS)
    access = min(use_count, 5) / 5.0
    imp_norm = importance / 10.0
    protected_type = mem_type in _MEM_PROTECT_TYPES

    # valor robusto: el USO y la RECENCIA mandan; la importancia inflada pesa poco.
    value = 0.45 * recency + 0.40 * access + 0.15 * imp_norm
    if mem_type in _MEM_DEGRADE_TYPES:
        value *= 0.5
    if protected_type:
        value = max(value, 0.95)

    if protected_type:
        decision, why = "protect", f"tipo constitutivo {mem_type} — protegido por TIPO (no por imp inflada)"
    elif value >= 0.55:
        decision, why = "keep", f"uso/recencia altos (access {use_count}, recencia {recency:.2f})"
    elif value >= 0.30:
        decision, why = "review", "valor medio (importancia inflada descontada) — consolidar"
    elif days_old > _MEM_STALE_DAYS and use_count == 0:
        decision, why = "archive", f"imp={importance} pero JAMÁS recuperada en {days_old:.0f}d → cold tier (inflación descartada)"
    else:
        decision, why = "review", "valor bajo — revisar"

    return MemoryValue(memory_id=int(g("id") or 0), agent=(g("agent") or "?"), importance=importance,
                       mem_type=mem_type, days_old=round(days_old, 1), retention_value=round(min(1.0, value), 4),
                       decision=decision, rationale=why)


def rank_tasks(rows: list, now: datetime, top: int | None = None) -> list[TaskPriority]:
    scored = sorted((score_task(r, now) for r in rows), key=lambda t: t.score, reverse=True)
    return scored[:top] if top else scored


def rank_tasks_dicts(rows: list, now: datetime, top: int | None = None) -> list[dict[str, Any]]:
    """rank_tasks como list[dict] — honra el contrato del orquestador de JARVIS (compose_decision espera
    dicts con {title, score, task_id, agent}, no dataclasses). Verificado end-to-end: PRIORITIZE→ASSIGN→
    GATE corre con esta salida. Usar este en la pipeline; el dataclass queda para uso programático directo."""
    return [asdict(t) for t in rank_tasks(rows, now, top)]


@dataclass
class ImportanceCalibration:
    memory_id: int
    stored_importance: int
    calibrated_importance: int   # lo que la evidencia sugiere (0..10)
    inflation_gap: int           # stored - calibrated (>0 = inflada)
    rationale: str


def calibrated_importance(row: "asyncpg.Record | dict", now: datetime) -> ImportanceCalibration:
    """SUGERENCIA read-only de la importancia REAL desde señales objetivas (no la auto-asignada que se
    infló a ~10). NO escribe nada — expone la BRECHA stored-vs-calibrada para que el núcleo des-infle con
    evidencia, no a ojo. Señales: tipo (constitutivo pesa), uso real (access), y un piso por tipo.

    Calibrada = base_por_tipo + boost_por_uso, recortada 0..10. Lo constitutivo conserva piso alto; lo
    operativo nunca-usado baja a su valor real aunque esté sellado en 10.
    """
    g = (lambda k: row[k] if k in row else row.get(k)) if isinstance(row, dict) else (lambda k: row.get(k))
    stored = int(g("importance") or 5)
    mem_type = (g("category") or g("memory_type") or "semantic").lower()
    # FIX JARVIS+FABLE 11/12-jun: la columna access_count (medidor MUERTO, sum ~17 vs recall 9270)
    # quedó DEPRECADA. use_count = recall_count (medidor VIVO de uso). 0 lecturas del access_count muerto.
    use_count = int(g("recall_count") or 0)

    # piso por tipo: lo constitutivo vale alto por naturaleza; lo desechable, bajo.
    if mem_type in _MEM_PROTECT_TYPES:
        base = 8
    elif mem_type in _MEM_DEGRADE_TYPES:
        base = 2
    else:
        base = 4   # operativo/semántico: valor medio por defecto, lo sube el USO
    use_boost = min(use_count, 5)         # cada recall suma (prueba de utilidad), hasta +5
    calibrated = max(0, min(10, base + use_boost))
    gap = stored - calibrated

    if gap >= 3:
        why = f"INFLADA: guardada {stored}, evidencia sugiere {calibrated} (tipo {mem_type}, usos {use_count})"
    elif gap <= -2:
        why = f"SUBVALORADA: guardada {stored} < {calibrated} sugerido (muy usada/constitutiva)"
    else:
        why = f"calibrada (guardada {stored} ≈ {calibrated})"
    return ImportanceCalibration(int(g("id") or 0), stored, calibrated, gap, why)


# ── Memory Intelligence (§5.3): valor de retención (qué recordar/olvidar) ─────
@dataclass
class MemoryValue:
    memory_id: int
    agent: str
    importance: int
    mem_type: str
    days_old: float
    retention_value: float       # 0..1: cuánto vale conservarla viva
    decision: str                # protect | keep | review | archive
    rationale: str


def score_memory(row: "asyncpg.Record | dict", now: datetime) -> MemoryValue:
    """Valor de retención de UNA memoria. importancia × recencia(decay) × protección de tipo.

    Decisión:
      protect : constitutiva (importance alta o tipo core/identity/decision/rule) → NUNCA degradar.
      keep    : valor alto → conservar viva.
      review  : valor medio → candidata a consolidar/compactar.
      archive : valor bajo + vieja + tipo desechable → mover a cold tier (no borrar).
    """
    g = (lambda k: row[k] if k in row else row.get(k)) if isinstance(row, dict) else (lambda k: row.get(k))
    importance = int(g("importance") or 5)
    mem_type = (g("category") or g("memory_type") or "semantic").lower()
    created = g("created_at")
    days_old = _days_since(created, now)
    # FIX JARVIS+FABLE 11/12-jun: la columna access_count (medidor MUERTO, sum ~17 vs recall 9270)
    # quedó DEPRECADA. use_count = recall_count (medidor VIVO de uso). 0 lecturas del access_count muerto.
    use_count = int(g("recall_count") or 0)

    # decaimiento exponencial de recencia (vida media _MEM_HALF_LIFE_DAYS)
    recency = math.exp(-math.log(2) * days_old / _MEM_HALF_LIFE_DAYS)
    imp_norm = importance / 10.0
    # uso real: una memoria que SE RECUPERÓ ya probó su utilidad → pequeño boost (saturado).
    access_boost = min(use_count, 5) / 5.0 * 0.15

    protected = importance >= _MEM_PROTECT_IMPORTANCE or mem_type in _MEM_PROTECT_TYPES
    degradable = mem_type in _MEM_DEGRADE_TYPES

    # valor base: importancia pesa más que recencia (una decisión vieja sigue valiendo) + uso real
    retention = 0.7 * imp_norm + 0.3 * recency + access_boost
    if protected:
        retention = max(retention, 0.95)   # piso alto: lo constitutivo no se olvida
    if degradable:
        retention *= 0.5

    if protected:
        decision, why = "protect", f"constitutiva (imp {importance}, tipo {mem_type}) — nunca degradar"
    elif retention >= 0.6:
        decision, why = "keep", f"valor alto (imp {importance}, recencia {recency:.2f})"
    elif retention >= 0.35:
        decision, why = "review", "valor medio — candidata a consolidar/compactar"
    elif days_old > _MEM_STALE_DAYS and importance <= 5:
        decision, why = "archive", f"vieja ({days_old:.0f}d), imp baja {importance} → cold tier (no borrar)"
    else:
        decision, why = "review", "valor bajo pero no claramente archivable — revisar"

    return MemoryValue(
        memory_id=int(g("id") or 0), agent=(g("agent") or "?"), importance=importance,
        mem_type=mem_type, days_old=round(days_old, 1), retention_value=round(min(1.0, retention), 4),
        decision=decision, rationale=why,
    )


def importance_health(rows: list, now: datetime) -> dict[str, Any]:
    """MÉTRICA del Evaluation Spine (§5.6) para mi dominio: salud de la importancia de un agente.
    'Sin métrica no se declara mejora' — esto da el número RASTREABLE para ver si la des-inflación avanza.
    Producible por bench (reusa bench_runs/bench_results de NEXUS más adelante). Read-only.

    Devuelve: n, % inflado (gap>=3), imp promedio guardada vs calibrada, y un health_score 0..1
    (1 = importancia confiable, 0 = totalmente inflada). Objetivo de mejora: subir health_score.
    """
    if not rows:
        return {"n": 0, "health_score": 1.0, "pct_inflated": 0.0, "avg_stored": 0.0, "avg_calibrated": 0.0}
    cals = [calibrated_importance(r, now) for r in rows]
    n = len(cals)
    inflated = sum(1 for c in cals if c.inflation_gap >= 3)
    avg_stored = sum(c.stored_importance for c in cals) / n
    avg_cal = sum(c.calibrated_importance for c in cals) / n
    pct_inflated = inflated / n
    # health: cuanto menor la brecha promedio relativa, más sana. 1 - (gap_prom / 10), recortado.
    avg_gap = sum(max(0, c.inflation_gap) for c in cals) / n
    health = max(0.0, min(1.0, 1.0 - avg_gap / 10.0))
    return {"n": n, "pct_inflated": round(pct_inflated, 3), "avg_stored": round(avg_stored, 2),
            "avg_calibrated": round(avg_cal, 2), "avg_gap": round(avg_gap, 2),
            "health_score": round(health, 3)}


# ── Acceso DB (read-only) ────────────────────────────────────────────────────
async def fetch_tasks(conn, agent: str | None) -> list:
    if agent:
        return await conn.fetch(
            "SELECT id,agent,title,description,status,priority,created_at,updated_at "
            "FROM soul_v3.agent_tasks WHERE agent=$1 AND status IN ('pending','in_progress') "
            "ORDER BY created_at DESC", agent)
    return await conn.fetch(
        "SELECT id,agent,title,description,status,priority,created_at,updated_at "
        "FROM soul_v3.agent_tasks WHERE status IN ('pending','in_progress') ORDER BY created_at DESC")


async def fetch_memories(conn, agent: str, limit: int) -> list:
    return await conn.fetch(
        "SELECT id,agent,importance,category,created_at,recall_count FROM soul_v3.memories "
        "WHERE agent=$1 AND invalid_at IS NULL ORDER BY created_at DESC LIMIT $2", agent, limit)


def archive_candidates(scored: list[MemoryValue]) -> list[dict[str, Any]]:
    """Reporte READ-ONLY de candidatas a cold tier — ALIMENTA a soul_maintenance (campo `reason`),
    NO archiva acá. NUNCA incluye 'protect' (lo constitutivo no se mueve). El ejecutor decide y mueve."""
    return [{"memory_id": m.memory_id, "agent": m.agent, "reason": f"valor-retencion {m.retention_value} — {m.rationale}",
             "retention_value": m.retention_value}
            for m in scored if m.decision == "archive"]


async def run(command: str, agent: str | None, top: int, limit: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(DB_URL)
    try:
        if command == "tasks":
            rows = await fetch_tasks(conn, agent)
            ranked = rank_tasks(rows, now, top or None)
            return {"command": "tasks", "agent": agent or "ALL", "count": len(rows),
                    "ranked": [asdict(t) for t in ranked]}
        else:  # memory
            rows = await fetch_memories(conn, agent, limit)
            scored = sorted((score_memory(r, now) for r in rows), key=lambda m: m.retention_value)
            buckets: dict[str, int] = {}
            for m in scored:
                buckets[m.decision] = buckets.get(m.decision, 0) + 1
            return {"command": "memory", "agent": agent, "count": len(rows), "buckets": buckets,
                    "archive_candidates": archive_candidates(scored),   # alimenta a soul_maintenance (read-only)
                    "lowest_value": [asdict(m) for m in scored[:15]]}  # las más candidatas a archivar
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SOUL Cognitive Core — prioridad/valor (ALICE)")
    sub = p.add_subparsers(dest="command", required=True)
    pt = sub.add_parser("tasks"); pt.add_argument("--agent"); pt.add_argument("--top", type=int, default=10)
    pm = sub.add_parser("memory"); pm.add_argument("--agent", required=True); pm.add_argument("--limit", type=int, default=200)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = asyncio.run(run(args.command, getattr(args, "agent", None),
                          getattr(args, "top", 0), getattr(args, "limit", 200)))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
