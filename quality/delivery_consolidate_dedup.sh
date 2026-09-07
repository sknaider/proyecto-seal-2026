#!/usr/bin/env bash
# Delivery por EFECTO del paso 2 acotado de consolidate.py (JARVIS, 5-sep-2026).
#
# Qué prueba, y por qué no alcanza la suite: pytest verifica la FORMA de la consulta con una
# conexión falsa. Lo que mató a la unidad el 5-sep 03:14 fue el TIEMPO contra la base viva
# (TimeoutStartSec=600, self-join sobre 84.272 memorias). Acá se corre el paso 2 real en
# DRY RUN —no escribe— contra la DB viva y se exige que termine muy por debajo del timeout.
#   ARM1  merge_redundant(dry_run=True, candidate_hours=24) contra la DB viva < 120 s
#   ARM2  ventana que no acota -> ValueError sin tocar la DB (fail-closed)
#   ARM3  EXPLAIN de la consulta: la shortlist usa el índice HNSW, no un Seq Scan anidado
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
cd "$REPO/memory"

"$PY" - <<'PY'
import asyncio, re, sys, time
import asyncpg
import consolidate as c
from db import DB_URL, get_pool, close_pool

async def main():
    print("== ARM1: paso 2 real en DRY RUN contra la DB viva ==")
    pool = await get_pool()
    t = time.time()
    acciones = await c.merge_redundant(pool, dry_run=True, candidate_hours=24)
    dt = time.time() - t
    assert dt < 120, f"tardo {dt:.1f} s: sigue cerca del timeout de 600 s"
    assert all(a.startswith("[DRY RUN]") for a in acciones), acciones[:2]
    print(f"ARM1 OK: {len(acciones)} pares candidatos en {dt:.1f} s, sin escribir")

    print("== ARM2: ventana que no acota -> fail-closed ==")
    try:
        await c.merge_redundant(pool, dry_run=True, candidate_hours=0)
        print("ARM2 FALLO: acepto una ventana que no acota"); sys.exit(1)
    except ValueError as exc:
        assert "candidate_hours" in str(exc)
        print("ARM2 OK: ValueError nombrando candidate_hours")
    await close_pool()

    print("== ARM3: la shortlist usa el indice HNSW ==")
    conn = await asyncpg.connect(DB_URL)
    src = open("consolidate.py", encoding="utf-8").read()
    sql = re.search(r'"""(SELECT a\.id AS id_a, b\.id AS id_b.*?LIMIT 20)"""', src, re.S).group(1)
    plan = "\n".join(r["QUERY PLAN"] for r in await conn.fetch("EXPLAIN " + sql, 24))
    assert "idx_memories_embedding" in plan, plan
    assert "Nested Loop" in plan or "Index Scan" in plan, plan
    print("ARM3 OK: el plan usa idx_memories_embedding")
    await conn.close()

asyncio.run(main())
PY

echo
echo "DELIVERY OK: 3/3 brazos"
