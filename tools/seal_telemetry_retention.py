#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seal_telemetry_retention.py — Política de retención unificada (NEXUS #21).
================================================================================
Poda telemetría sin-tope de soul_v3, conservando SEÑAL y podando RUIDO viejo.
DB en verde (~2.5GB) → ventanas CONSERVADORAS (30d) salvo el oráculo (regla de FABLE).

SEGURIDAD:
  • DRY-RUN por defecto. Borra SOLO con --execute.
  • DELETE en BATCHES (commit entre tandas) → sin lock-storm en infra compartida.
  • Conteos verificados ANTES y DESPUÉS por tabla. Reporta filas borradas reales.
  • NO usa DROP/TRUNCATE (solo DELETE con WHERE acotado). Cada tabla con su predicado.
  • harness_oracle: KEEP diverged=True POR SIEMPRE (la señal de FABLE) + ventana 7d de los False.

Constructor: NEXUS. Cláusula harness_oracle cruza-verificada por FABLE (dueña de la tabla).
Uso:  seal_telemetry_retention.py            # dry-run (no borra)
      seal_telemetry_retention.py --execute  # ejecuta la poda (batched)
"""
from __future__ import annotations
import sys, os, asyncio, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))
try:
    from seal_secrets import pg_dsn
except Exception:
    def pg_dsn(required=True):
        return os.environ.get("SEAL_PG_DSN") or os.environ.get("DATABASE_URL")

import asyncpg

# ── Política por tabla ──────────────────────────────────────────────────────
# where_keep = predicado de lo que SE CONSERVA además de la ventana (None = solo ventana).
# El DELETE borra: <ts_col> < now()-keep_days  AND NOT (<where_keep>)
RETENTION = [
    {"table": "soul_v3.awareness_ticks",             "ts": "created_at", "keep_days": 30, "where_keep": None,
     "note": "telemetría pura de ticks"},
    {"table": "soul_v3.awareness_247_process_runs",  "ts": "created_at", "keep_days": 30, "where_keep": None,
     "note": "telemetría de runs"},
    {"table": "soul_v3.nerves_metrics_log",          "ts": "created_at", "keep_days": 30, "where_keep": None,
     "note": "métricas nerves"},
    {"table": "soul_v3.event_log",                   "ts": "created_at", "keep_days": 30,
     "where_keep": "event_type NOT IN ('status', 'heartbeat')",
     "note": "MIXTO — poda SOLO ruido (status/heartbeat) >30d; CONSERVA milestone/rule_set/session_distill/etc por siempre"},
    # harness_oracle: regla de FABLE — conservar diverged=True por siempre; podar False viejos (>7d)
    {"table": "soul_v3.harness_oracle",              "ts": "ts",         "keep_days": 7,  "where_keep": "diverged IS TRUE",
     "note": "FABLE: KEEP diverged=True por siempre + ventana 7d de False"},
]
BATCH = 5000


def _delete_where(cfg: dict) -> tuple[str, str]:
    """Construye el WHERE del DELETE y un WHERE de conteo (mismo predicado)."""
    base = f"{cfg['ts']} < now() - interval '{cfg['keep_days']} days'"
    if cfg["where_keep"]:
        base += f" AND NOT ({cfg['where_keep']})"
    return base


import re as _re
# Allowlist por construcción: identificadores (tabla/columna/predicado) son CONSTANTES de RETENTION,
# NO input externo. Igual validamos forma estricta — defensa-en-profundidad (los identificadores no
# se pueden parametrizar con $1 en asyncpg, así que validamos que sean literales seguros).
_IDENT = _re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")   # soul_v3.tabla (permite dígitos)
_COL = _re.compile(r"^[a-z_][a-z0-9_]*$")                        # created_at / ts
_PRED = _re.compile(
    r"^[a-z_]+ (IS TRUE|IS FALSE|IS NOT TRUE|(NOT )?IN \('[a-z_]+'(, ?'[a-z_]+')*\))$"
)  # diverged IS TRUE  |  event_type NOT IN ('status','heartbeat')

def _validate(cfg: dict) -> None:
    assert _IDENT.match(cfg["table"]), f"tabla insegura: {cfg['table']}"
    assert _COL.match(cfg["ts"]), f"columna insegura: {cfg['ts']}"
    assert isinstance(cfg["keep_days"], int) and 0 < cfg["keep_days"] <= 3650
    if cfg["where_keep"]:
        assert _PRED.match(cfg["where_keep"]), f"predicado inseguro: {cfg['where_keep']}"


async def run(execute: bool) -> int:
    dsn = pg_dsn(required=True)
    conn = await asyncpg.connect(dsn)
    mode = "EXECUTE (borra)" if execute else "DRY-RUN (no borra)"
    print(f"=== SEAL telemetry retention — {mode} ===\n")
    total_to_prune = 0
    total_pruned = 0
    try:
        for cfg in RETENTION:
            _validate(cfg)   # aborta si algún identificador no es literal seguro
            tbl = cfg["table"]
            where = _delete_where(cfg)
            try:
                total = await conn.fetchval(f"SELECT count(*) FROM {tbl}")
                to_prune = await conn.fetchval(f"SELECT count(*) FROM {tbl} WHERE {where}")
            except Exception as e:
                print(f"  ⚠️  {tbl}: error contando ({str(e)[:60]}) — SKIP")
                continue
            total_to_prune += to_prune
            print(f"  {tbl}")
            print(f"     total={total}  conserva={total - to_prune}  poda={to_prune}  ({cfg['note']})")
            if not execute or to_prune == 0:
                continue
            # DELETE batched por ctid → sin lock-storm; commit implícito por statement
            deleted = 0
            while True:
                status = await conn.execute(
                    f"DELETE FROM {tbl} WHERE ctid IN (SELECT ctid FROM {tbl} WHERE {where} LIMIT {BATCH})"
                )
                n = int(status.split()[-1]) if status.startswith("DELETE") else 0
                deleted += n
                if n < BATCH:
                    break
            after = await conn.fetchval(f"SELECT count(*) FROM {tbl}")
            print(f"     ✅ borradas={deleted}  ahora={after}")
            total_pruned += deleted
        if execute:
            print(f"\nTOTAL PODADO: {total_pruned}")
        else:
            print(f"\nTOTAL A PODAR (dry-run, no se borró nada): {total_to_prune}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Ejecuta la poda (sin esto, dry-run)")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.execute)))
