"""cognitive_v1_pipeline — ENSAMBLE del Núcleo de Cognición v1 (un comando, fuentes reales).

Autor: JARVIS (arquitectura) · 2026-06-10 (build autónomo).
Cablea las piezas REALES del equipo en el pipeline del contrato de orquestación, SIN editar el
core de ADA (archivo NUEVO, coordinación sin pisarse). ADA puede foldearlo a su recommend_next
cuando quiera; mientras tanto, esto da el v1 corriendo end-to-end.

  STATE      ← soul_cognitive_core.build_state (ADA)
  PRIORITIZE ← soul_cognitive_priority.rank_tasks_dicts (ALICE)
  ASSIGN+GATE← cognitive_orchestrator.compose_decision (JARVIS) + cognitive_assign + invariants
  GATE-sec   ← soul_safety_governor.is_safe_to_act (NEXUS)

Read-only: NO escribe DB. Degradación con gracia: si un subsistema falla, se registra el gap y
se sigue (excepto el gate de seguridad, que falla cerrado).
"""
from __future__ import annotations
import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import asyncpg


def _try_import():
    """Importa las piezas; devuelve (mods, gaps). No crashea si falta alguna."""
    mods, gaps = {}, []
    import importlib
    for key, (modname, attr) in {
        "build_state": ("soul_cognitive_core", "build_state"),
        "fetch_tasks": ("soul_cognitive_priority", "fetch_tasks"),
        "rank_tasks_dicts": ("soul_cognitive_priority", "rank_tasks_dicts"),
        "is_safe_to_act": ("soul_safety_governor", "is_safe_to_act"),
        "compose_decision": ("cognitive_orchestrator", "compose_decision"),
        "pg_dsn": ("seal_secrets", "pg_dsn"),
    }.items():
        try:
            mods[key] = getattr(importlib.import_module(modname), attr)
        except Exception as e:
            gaps.append(f"{key} ({modname}.{attr}) no disponible: {e}")
    return mods, gaps


async def assemble_v1(*, limit: int = 20, top: int = 3, skip_network: bool = True) -> dict[str, Any]:
    """Corre el pipeline v1 contra fuentes reales y responde los 10 criterios (read-only)."""
    mods, gaps = _try_import()
    out: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "gaps": list(gaps)}

    # --- GATE de seguridad GLOBAL (fail-closed) ---
    safe, safe_reasons = True, []
    if "is_safe_to_act" in mods:
        try:
            safe, safe_reasons = mods["is_safe_to_act"](skip_network=True)
        except Exception as e:
            safe, safe_reasons = False, [f"is_safe_to_act lanzó: {e} → fail-closed"]
    else:
        out["gaps"].append("safety gate ausente → modo solo-lectura sin recomendación autónoma")

    # --- STATE + PRIORITIZE (requieren DB) ---
    state, ranked = None, []
    if "pg_dsn" in mods and "build_state" in mods:
        conn = None
        try:
            conn = await asyncpg.connect(mods["pg_dsn"](required=True))
            try:
                state = await mods["build_state"](conn, limit=limit, skip_network=skip_network)
            except Exception as e:
                out["gaps"].append(f"build_state falló: {e}")
            if "fetch_tasks" in mods and "rank_tasks_dicts" in mods:
                try:
                    rows = await mods["fetch_tasks"](conn, None)
                    ranked = mods["rank_tasks_dicts"](rows, datetime.now(timezone.utc), top=top)
                except Exception as e:
                    out["gaps"].append(f"priorización falló: {e}")
        except Exception as e:
            out["gaps"].append(f"conexión DB falló: {e} → degradado, sin state/priority")
        finally:
            if conn is not None:
                await conn.close()
    else:
        out["gaps"].append("DB/state no disponible → no se puede leer estado real")

    # --- ASSIGN + GATE de método → DECISIÓN ---
    decision = {"safe": safe, "decisions": []}
    if "compose_decision" in mods:
        sg = (lambda: (safe, safe_reasons)) if "is_safe_to_act" in mods else None
        try:
            decision = mods["compose_decision"](ranked, safety_gate=sg, top=top)
        except Exception as e:
            out["gaps"].append(f"compose_decision falló: {e}")

    # --- Respuesta a los 10 criterios (spec §10) ---
    st = state or {}
    out["criteria"] = {
        "1_what_doing": (st.get("tasks") or {}),
        "2_blocked": (st.get("work_ledger") or {}).get("blocked_count")
        if isinstance(st.get("work_ledger"), dict) else None,
        "3_4_knows_unknowns": st.get("knowledge") or st.get("gaps") or [],
        "5_6_7_8_next_decision": decision.get("decisions"),
        "9_safety": {"safe_to_act": safe, "reasons": safe_reasons},
        "10_product_surface": "SOUL Core público / SEAL Core privado / GTL vertical (spec §1)",
    }
    out["safe_to_act_autonomously"] = bool(safe and not out["gaps"])
    out["services"] = st.get("services")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Núcleo de Cognición v1 — pipeline ensamblado (read-only)")
    p.add_argument("command", nargs="?", default="status", choices=["status"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--network", action="store_true", help="permitir healthchecks de red")
    args = p.parse_args(argv)
    result = asyncio.run(assemble_v1(limit=args.limit, top=args.top, skip_network=not args.network))
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
