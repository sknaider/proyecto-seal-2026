#!/usr/bin/env python3
"""Tests de soul_cognitive_priority.py — funciones puras de prioridad/valor (ALICE, núcleo cognitivo).
Sin DB: validan la LÓGICA de score (la parte que decide), no el acceso a Postgres."""
from datetime import datetime, timezone, timedelta

import soul_cognitive_priority as P

NOW = datetime(2026, 6, 9, tzinfo=timezone.utc)


def _task(**kw):
    base = {"id": 1, "agent": "ALICE", "title": "t", "description": "", "status": "pending",
            "priority": 5, "created_at": NOW, "updated_at": NOW}
    base.update(kw)
    return base


def _mem(**kw):
    base = {"id": 1, "agent": "ALICE", "importance": 5, "category": "semantic", "created_at": NOW}
    base.update(kw)
    return base


def run():
    fails = []
    def ok(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # ── Objective Engine ──
    hi = P.score_task(_task(priority=10), NOW)
    lo = P.score_task(_task(priority=1), NOW)
    ok("T1 prioridad alta > baja", hi.score > lo.score)

    old = P.score_task(_task(priority=5, created_at=NOW - timedelta(days=20)), NOW)
    new = P.score_task(_task(priority=5, created_at=NOW), NOW)
    ok("T2 tarea vieja-abierta urge más que nueva (igual prioridad)", old.score > new.score)

    blk = P.score_task(_task(priority=8, status="in_progress", description="blocked waiting on NEXUS"), NOW)
    free = P.score_task(_task(priority=8, status="in_progress"), NOW)
    ok("T3 bloqueada baja foco vs libre", blk.score < free.score)

    stale = P.score_task(_task(priority=5, status="in_progress",
                               created_at=NOW - timedelta(days=15), updated_at=NOW - timedelta(days=15)), NOW)
    ok("T4 estancada in_progress recibe stale_boost", stale.components["stale_boost"] > 0)

    ranked = P.rank_tasks([_task(id=1, priority=2), _task(id=2, priority=9), _task(id=3, priority=5)], NOW)
    ok("T5 rank ordena desc por score (id2 primero)", ranked[0].task_id == 2)
    ok("T6 score normalizado 0..1", all(0.0 <= t.score <= 1.0 for t in ranked))
    ok("T7 cada tarea trae rationale (por qué)", all(t.rationale for t in ranked))

    # ── Memory Intelligence ──
    core = P.score_memory(_mem(importance=10, category="core"), NOW)
    ok("M1 memoria core/imp-alta -> protect", core.decision == "protect")
    ok("M2 protegida retención >= 0.95 (no se olvida)", core.retention_value >= 0.95)

    fresh = P.score_memory(_mem(importance=8, created_at=NOW), NOW)
    ok("M3 imp alta reciente -> keep", fresh.decision == "keep")

    old_low = P.score_memory(_mem(importance=3, category="semantic", created_at=NOW - timedelta(days=200)), NOW)
    ok("M4 vieja + imp baja -> archive (cold tier)", old_low.decision == "archive")

    decision_old = P.score_memory(_mem(importance=10, category="decision", created_at=NOW - timedelta(days=300)), NOW)
    ok("M5 decisión vieja sigue protegida (imp manda sobre recencia)", decision_old.decision == "protect")

    deg = P.score_memory(_mem(importance=6, category="debug", created_at=NOW), NOW)
    keep = P.score_memory(_mem(importance=6, category="semantic", created_at=NOW), NOW)
    ok("M6 tipo desechable (debug) vale menos que semantic igual imp", deg.retention_value < keep.retention_value)

    used = P.score_memory(_mem(importance=6, recall_count=5, created_at=NOW), NOW)
    unused = P.score_memory(_mem(importance=6, recall_count=0, created_at=NOW), NOW)
    ok("M7 memoria recuperada (access>0) vale más que nunca-usada (uso real)", used.retention_value > unused.retention_value)

    # archive_candidates: read-only, nunca incluye protect
    scored = [P.score_memory(_mem(id=1, importance=3, category="semantic", created_at=NOW - timedelta(days=200)), NOW),
              P.score_memory(_mem(id=2, importance=10, category="core", created_at=NOW - timedelta(days=300)), NOW)]
    cands = P.archive_candidates(scored)
    ok("M8 archive_candidates incluye la vieja-baja", any(c["memory_id"] == 1 for c in cands))
    ok("M9 archive_candidates NUNCA incluye protegida (id2 core)", all(c["memory_id"] != 2 for c in cands))
    ok("M10 cada candidata trae reason (para soul_maintenance)", all(c.get("reason") for c in cands))

    # ── Variante ROBUSTA a inflación de importancia (entregable 3) ──
    # memoria imp=10 vieja y nunca usada: el modo estándar la protege (inflación); el robusto la archiva.
    inflated_old = _mem(importance=10, category="semantic", recall_count=0, created_at=NOW - timedelta(days=200))
    std = P.score_memory(inflated_old, NOW)
    rob = P.score_memory_robust(inflated_old, NOW)
    ok("R1 estándar protege imp=10 inflada", std.decision == "protect")
    ok("R2 robusto SÍ la archiva (recupera el olvido)", rob.decision == "archive")

    # tipo constitutivo: robusto lo protege aunque sea viejo (protección por TIPO, no por número)
    const_old = _mem(importance=10, category="decision", recall_count=0, created_at=NOW - timedelta(days=300))
    ok("R3 robusto protege tipo constitutivo (decision) por TIPO", P.score_memory_robust(const_old, NOW).decision == "protect")

    # uso real manda: imp baja pero muy accedida → keep en robusto
    used_lowimp = _mem(importance=4, category="semantic", recall_count=5, created_at=NOW)
    ok("R4 robusto: uso real (access=5) sostiene aunque imp baja", P.score_memory_robust(used_lowimp, NOW).decision == "keep")

    # ── Importancia calibrada (entregable 4) ──
    inflated = P.calibrated_importance(_mem(importance=10, category="semantic", recall_count=0), NOW)
    ok("C1 imp=10 semantic nunca-usada -> calibrada baja (inflación detectada)", inflated.calibrated_importance < 10 and inflated.inflation_gap > 0)

    constit = P.calibrated_importance(_mem(importance=10, category="decision", recall_count=0), NOW)
    ok("C2 tipo constitutivo conserva piso alto (no se des-infla a la fuerza)", constit.calibrated_importance >= 8)

    used = P.calibrated_importance(_mem(importance=4, category="semantic", recall_count=5), NOW)
    ok("C3 muy usada sube su calibrada por uso real", used.calibrated_importance > 4)

    ok("C4 calibrada acotada 0..10", 0 <= inflated.calibrated_importance <= 10)

    # ── Importance health metric (Evaluation Spine §5.6) ──
    inflated_set = [_mem(id=i, importance=10, category="semantic", recall_count=0) for i in range(10)]
    h_bad = P.importance_health(inflated_set, NOW)
    ok("H1 set 100% inflado -> health_score bajo", h_bad["health_score"] < 0.6 and h_bad["pct_inflated"] == 1.0)

    healthy_set = [_mem(id=i, importance=4, category="semantic", recall_count=0) for i in range(10)]
    h_ok = P.importance_health(healthy_set, NOW)
    ok("H2 set bien calibrado -> health_score alto", h_ok["health_score"] > 0.9)
    ok("H3 health vacío no crashea", P.importance_health([], NOW)["n"] == 0)

    total = 7 + 10 + 4 + 4 + 3
    print(f"\n== {total-len(fails)} PASS / {len(fails)} FAIL ==")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
