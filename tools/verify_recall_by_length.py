#!/usr/bin/env python3
"""
verify_recall_by_length.py — verificación reproducible del bug "memoria larga NO
vuelve, memoria corta SI" en el recall semantico de SOUL (pre/post fix JARVIS).

CONTEXTO (medido por NEXUS, baseline PRE-fix):
    query: "contador acumulado sube aunque el proceso este muerto, medir el
            escritor no lo escrito"
    scope=team, category=insight, importance=8

    #378169    110 chars   sim 0.92   VUELVE
    #378185    258 chars   sim 0.89   VUELVE
    #378158    670 chars              NO VUELVE
    #378205  2.151 chars              NO VUELVE

COMO CONSULTA (leido, no adivinado):
  memory/mcp_server_v4.py -> get_qdrant() devuelve un PgVectorAdapter
  (memory/soul_lite_adapter.py) sobre PostgreSQL: "Qdrant retirado, corre en
  pgvector". El patron de consulta real que SI esta 100% en SQL puro en este
  archivo es el bloque "Cold Archive" (mcp_server_v4.py ~L2836-2857):

      SELECT id, ..., 1 - (embedding <=> $1::vector) AS similarity
      FROM cold_archive
      WHERE embedding IS NOT NULL [AND agent=$k] [AND category=$k]
      ORDER BY embedding <=> $1::vector
      LIMIT $2

  Este script reproduce el MISMO criterio (cosine similarity via pgvector
  <=>, mismo filtro agent/category) pero contra la tabla activa `memories`
  (soul_v3.memories), que es donde viven las 4 fichas del baseline. NO pasa
  por el pipeline completo de memory_search() (H-MEM 4-layer pre-filter,
  TrieIndex, decayed_score, RL utility) porque ese pipeline vive detras de
  Qdrant-shim/asyncio/MCP y no es SQL puro — replicarlo entero excede el
  alcance de este script y NO fue verificado byte a byte. Lo que este script
  SI reproduce fielmente: si el embedding coseno del query contra la fila
  entra en el TOP-N por similitud, que es la condicion necesaria que separa
  "vuelve" de "no vuelve" en el bug reportado.

  Conexion reutiliza memory/db.py (get_pool) y memory/embeddings.py
  (get_query_embedding, get_embedding) del propio proyecto -> mismas
  credenciales/DSN/modelo que usa el server real, sin hardcodear nada.

USO:
    python3 tools/verify_recall_by_length.py
    python3 tools/verify_recall_by_length.py --agent NEXUS --top-n 20
    python3 tools/verify_recall_by_length.py --sembrar   # crea escalera nueva

CODIGOS DE SALIDA:
    0 ARREGLADO   — vuelve 2151 Y siguen volviendo las cortas (110, 258)
    1 REGRESION   — alguna corta (110 y/o 258) dejo de volver
    2 PARCIAL     — vuelve 670 pero NO 2151 (2151 sigue sin volver)
    3 SIN CAMBIOS — ni 670 ni 2151 vuelven (o resultado no concluyente,
                    ver detalle impreso)

REGLAS DURAS RESPETADAS:
  - No borra nada. No modifica archivos existentes. Solo lee y, con
    --sembrar explicito, hace UN INSERT de filas nuevas (no update/delete).
  - No hay rutas de borrado en este script; no aplica la regla de ruta
    literal para `rm` (no se usa).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Reusar el codigo real del proyecto (mismo DSN, mismo modelo de embeddings)
# en vez de hardcodear credenciales o adivinar el puerto del contenedor.
_MEMORY_DIR = "/home/dadito/IA/proyecto-seal/memory"
if _MEMORY_DIR not in sys.path:
    sys.path.insert(0, _MEMORY_DIR)

QUERY_TEXT = (
    "contador acumulado sube aunque el proceso este muerto, "
    "medir el escritor no lo escrito"
)

SCOPE = "team"
CATEGORY = "insight"
IMPORTANCE = 8

# IDs reales del baseline PRE-fix medido por NEXUS (constante, no adivinar).
BASELINE_IDS = {
    378169: 110,
    378185: 258,
    378158: 670,
    378205: 2151,
}

# Escalera para --sembrar: mismo orden de magnitud que las fichas reales
# (mediana 2.545, p90 7.237 chars en /home/dadito/.claude/.../memory/*.md).
SEED_SIZES = [110, 258, 670, 2151, 7237]

SHORT_IDS_KEY = (110, 258)   # control negativo: SIEMPRE deben volver
LONG_670_KEY = 670
LONG_2151_KEY = 2151

BASE_TEXT = (
    "El contador acumulado sube aunque el proceso este muerto: hay que medir "
    "el escritor, no lo escrito. Un valor que crece no prueba que el proceso "
    "que lo incrementa siga vivo -- puede ser un acumulador stale, un log "
    "residual, o un proceso zombie que ya no hace trabajo util. La forma "
    "correcta de verificar vida es observar el ESCRITOR (su PID, su ultimo "
    "timestamp de actividad, su heartbeat) en vez de solo leer el ULTIMO "
    "VALOR escrito, que puede quedar congelado o seguir moviendose por "
    "inercia de otro proceso. "
)


def _make_text(n_chars: int) -> str:
    """Repite BASE_TEXT hasta llegar a n_chars y recorta exacto."""
    reps = (n_chars // len(BASE_TEXT)) + 2
    return (BASE_TEXT * reps)[:n_chars]


async def seed(agent: str) -> list[int]:
    from embeddings import get_embedding
    from db import get_pool
    import json

    pool = await get_pool()
    created: list[int] = []
    async with pool.acquire() as conn:
        for size in SEED_SIZES:
            content = _make_text(size)
            emb = await get_embedding(content)
            row = await conn.fetchrow(
                """
                INSERT INTO memories
                    (tenant_id, agent, category, content, embedding,
                     importance, source, scope)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                "default",
                agent,
                CATEGORY,
                content,
                json.dumps(emb),
                IMPORTANCE,
                "verify_recall_by_length.py --sembrar",
                SCOPE,
            )
            created.append(row["id"])
            print(f"  sembrado id={row['id']} len={len(content)}")
    return created


async def run_check(agent: str, top_n: int, ids: dict[int, int]) -> dict[int, tuple[bool, float | None, int]]:
    """Devuelve {id: (volvio, similitud|None, longitud_real_chars)}."""
    from embeddings import get_query_embedding
    from db import get_pool
    import json

    pool = await get_pool()
    qvec = await get_query_embedding(QUERY_TEXT)
    qvec_str = json.dumps(qvec)

    result: dict[int, tuple[bool, float | None, int]] = {}
    async with pool.acquire() as conn:
        # Longitud real actual de cada fila (por si difiere del baseline anotado)
        len_rows = await conn.fetch(
            "SELECT id, LENGTH(content) AS len FROM memories WHERE id = ANY($1::bigint[])",
            list(ids.keys()),
        )
        real_len = {r["id"]: r["len"] for r in len_rows}
        missing_ids = [i for i in ids if i not in real_len]

        # TOP-N por similitud coseno, mismo filtro scope/category que el baseline,
        # mismo operador pgvector <=> que usa el bloque cold_archive real.
        top_rows = await conn.fetch(
            f"""
            SELECT id, 1 - (embedding <=> $1::vector) AS similarity
            FROM memories
            WHERE embedding IS NOT NULL
              AND scope = $2
              AND category = $3
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,
            qvec_str, SCOPE, CATEGORY, top_n,
        )
        top_sim = {r["id"]: float(r["similarity"]) for r in top_rows}

    for mid, baseline_len in ids.items():
        if mid in missing_ids:
            result[mid] = (False, None, -1)  # -1 = fila no existe hoy: DECIRLO, no inventar
            continue
        vuelve = mid in top_sim
        result[mid] = (vuelve, top_sim.get(mid), real_len[mid])
    return result


def print_table(rows: dict[int, tuple[bool, float | None, int]], baseline_len: dict[int, int]) -> None:
    print(f"\n{'id':>10} {'len(baseline)':>14} {'len(real hoy)':>14} {'volvio':>8} {'similitud':>10}")
    for mid in sorted(rows, key=lambda k: baseline_len.get(k, 0)):
        vuelve, sim, real_len = rows[mid]
        real_len_s = "NO_EXISTE" if real_len == -1 else str(real_len)
        sim_s = f"{sim:.4f}" if sim is not None else "-"
        print(f"{mid:>10} {baseline_len.get(mid, '-'):>14} {real_len_s:>14} {str(vuelve):>8} {sim_s:>10}")


async def main_async(args: argparse.Namespace) -> int:
    ids = dict(BASELINE_IDS)

    if args.sembrar:
        print(f"--sembrar: creando escalera nueva {SEED_SIZES} para agent={args.agent} ...")
        new_ids = await seed(args.agent)
        print(f"IDs creados: {new_ids}")
        # Mapear los nuevos ids a sus tamanios por orden (SEED_SIZES es el orden de insercion)
        ids = dict(zip(new_ids, SEED_SIZES))

    baseline_len = dict(BASELINE_IDS) if not args.sembrar else ids
    rows = await run_check(args.agent, args.top_n, ids)
    print_table(rows, baseline_len)

    # Localizar por longitud (funciona tanto con BASELINE_IDS como con --sembrar)
    def by_len(target_len: int):
        for mid, (vuelve, sim, real_len) in rows.items():
            bl = baseline_len.get(mid)
            if bl == target_len:
                return mid, vuelve, sim, real_len
        return None

    short_results = [by_len(l) for l in SHORT_IDS_KEY]
    r670 = by_len(LONG_670_KEY)
    r2151 = by_len(LONG_2151_KEY)

    if any(r is None for r in short_results) or r670 is None or r2151 is None:
        print("\nNO CONCLUYENTE: falta algun id del baseline en la tabla (ver NO_EXISTE arriba). "
              "No invento un resultado sobre datos que no están.")
        return 3

    shorts_ok = all(r[1] for r in short_results)
    ok_670 = r670[1]
    ok_2151 = r2151[1]

    print()
    if not shorts_ok:
        rotas = [str(baseline_len[r[0]]) for r in short_results if not r[1]]
        print(f"REGRESION: el fix rompio lo que ya andaba (dejaron de volver: {', '.join(rotas)} chars)")
        return 1

    if ok_2151:
        print("ARREGLADO: vuelve la de 2.151 chars y las cortas (110, 258) siguen volviendo")
        return 0

    if ok_670:
        print("PARCIAL: 670 es un cuarto de la mediana real, el problema sigue "
              "(2.151 -- mediana real de las fichas -- sigue sin volver)")
        return 2

    print("SIN CAMBIOS: ni 670 ni 2.151 vuelven; las cortas (110, 258) siguen volviendo (sin regresion)")
    return 3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="NEXUS", help="agent para filtrar/crear filas (default NEXUS)")
    ap.add_argument("--top-n", type=int, default=20, help="tamano del top-N de similitud a inspeccionar (default 20)")
    ap.add_argument("--sembrar", action="store_true",
                     help="crea una escalera nueva de tamanios (110,258,670,2151,7237) en vez de usar BASELINE_IDS")
    args = ap.parse_args()

    try:
        code = asyncio.run(main_async(args))
    except Exception as exc:
        print(f"ERROR no manejado: {exc!r}", file=sys.stderr)
        print("No pude medir -- no invento un resultado.", file=sys.stderr)
        sys.exit(4)

    sys.exit(code)


if __name__ == "__main__":
    main()
