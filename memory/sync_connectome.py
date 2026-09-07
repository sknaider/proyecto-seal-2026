#!/usr/bin/env python3
"""
sync_connectome.py — Sincronización completa PostgreSQL → Neo4j
Autor: JARVIS — Team SEAL
Fecha: 2026-03-31

Problema: Neo4j solo tenía 24,248 aristas de 150,204 en PostgreSQL (16.1%).
Este script sincroniza TODOS los tipos de conexión:
  - excitatory, inhibitory (ya parciales)
  - similar, corrected, caused, informed, contradicts (ausentes)

Uso:
  python3 sync_connectome.py              # sync incremental (solo faltantes)
  python3 sync_connectome.py --full       # rebuild completo
  python3 sync_connectome.py --stats      # solo estadísticas
"""

import asyncio
import sys
import time
from datetime import datetime

import asyncpg
from neo4j import AsyncGraphDatabase

# --- Config ---
PG_URL    = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")

BATCH_SIZE = 500  # aristas por batch

# Mapeo: tipo PostgreSQL → relación Neo4j
TYPE_MAP = {
    "excitatory":  "EXCITES",
    "inhibitory":  "INHIBITS",
    "similar":     "EXCITES",    # similitud = excitatorio
    "caused":      "EXCITES",    # causalidad = excitatorio
    "informed":    "EXCITES",    # informado = excitatorio
    "corrected":   "INHIBITS",   # corrección = inhibitorio
    "contradicts": "INHIBITS",   # contradicción = inhibitorio
}


async def get_pg_stats(pool) -> dict:
    """Estadísticas de aristas en PostgreSQL."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT connection_type, COUNT(*) as cnt "
            "FROM memory_connections "
            "GROUP BY connection_type ORDER BY cnt DESC"
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM memory_connections")
    return {"total": total, "by_type": {r["connection_type"]: r["cnt"] for r in rows}}


async def get_neo4j_stats(session) -> dict:
    """Estadísticas de aristas en Neo4j."""
    result = await session.run(
        "MATCH ()-[r]->() RETURN type(r) as t, COUNT(r) as cnt ORDER BY cnt DESC"
    )
    records = await result.data()
    total_r = await session.run("MATCH ()-[r]->() RETURN COUNT(r) as total")
    total_data = await total_r.data()
    total = total_data[0]["total"] if total_data else 0
    return {"total": total, "by_type": {r["t"]: r["cnt"] for r in records}}


async def ensure_nodes(pool, driver):
    """Asegura que todos los nodos Memory existan en Neo4j."""
    print("  Verificando nodos Memory en Neo4j...")
    async with pool.acquire() as conn:
        memories = await conn.fetch("SELECT id, agent, category FROM memories")

    async with driver.session() as session:
        # Verificar cuántos nodos existen
        result = await session.run("MATCH (m:Memory) RETURN COUNT(m) as cnt")
        data = await result.data()
        existing = data[0]["cnt"] if data else 0
        print(f"    Neo4j tiene {existing:,} nodos, PostgreSQL tiene {len(memories):,}")

        if existing < len(memories):
            missing = len(memories) - existing
            print(f"    Creando {missing:,} nodos faltantes...")
            # Batch create nodos
            batch = []
            for m in memories:
                batch.append({"id": m["id"], "agent": m["agent"], "category": m["category"]})
                if len(batch) >= BATCH_SIZE:
                    await session.run(
                        "UNWIND $batch AS row "
                        "MERGE (m:Memory {id: row.id}) "
                        "SET m.agent = row.agent, m.category = row.category",
                        batch=batch
                    )
                    batch = []
            if batch:
                await session.run(
                    "UNWIND $batch AS row "
                    "MERGE (m:Memory {id: row.id}) "
                    "SET m.agent = row.agent, m.category = row.category",
                    batch=batch
                )
            print(f"    ✅ Nodos sincronizados")
        else:
            print(f"    ✅ Nodos OK")


async def get_existing_neo4j_pairs(session, rel_type: str) -> set:
    """Obtiene pares (from_id, to_id) existentes en Neo4j para un tipo de relación."""
    result = await session.run(
        f"MATCH (a:Memory)-[r:{rel_type}]->(b:Memory) "
        f"RETURN a.id as from_id, b.id as to_id"
    )
    records = await result.data()
    return {(r["from_id"], r["to_id"]) for r in records}


async def sync_connection_type(pool, driver, pg_type: str, full: bool = False):
    """Sincroniza un tipo de conexión de PG a Neo4j."""
    neo4j_type = TYPE_MAP.get(pg_type, pg_type.upper())
    print(f"\n  [{pg_type}] → [{neo4j_type}]")

    # Obtener aristas de PostgreSQL
    async with pool.acquire() as conn:
        pg_rows = await conn.fetch(
            "SELECT source_id, target_id, weight "
            "FROM memory_connections WHERE connection_type = $1",
            pg_type
        )

    if not pg_rows:
        print(f"    Sin aristas en PostgreSQL, saltando.")
        return 0

    print(f"    PostgreSQL: {len(pg_rows):,} aristas")

    async with driver.session() as session:
        if full:
            # Modo full: eliminar todas las existentes y recrear
            del_result = await session.run(
                f"MATCH ()-[r:{neo4j_type}]->() DELETE r RETURN COUNT(r) as deleted"
            )
            del_data = await del_result.data()
            deleted = del_data[0]["deleted"] if del_data else 0
            if deleted > 0:
                print(f"    Eliminadas {deleted:,} aristas existentes")
            to_create = pg_rows
        else:
            # Modo incremental: solo crear las que faltan
            existing = await get_existing_neo4j_pairs(session, neo4j_type)
            print(f"    Neo4j existentes: {len(existing):,}")
            to_create = [r for r in pg_rows
                        if (r["source_id"], r["target_id"]) not in existing]
            print(f"    Faltantes a crear: {len(to_create):,}")

        if not to_create:
            print(f"    ✅ Ya sincronizado")
            return 0

        # Crear en batches
        created = 0
        batch = []
        for row in to_create:
            batch.append({
                "from_id": row["source_id"],
                "to_id":   row["target_id"],
                "weight":  float(row["weight"]) if row["weight"] else 0.5,
                "conf":    0.5,
            })
            if len(batch) >= BATCH_SIZE:
                result = await session.run(
                    f"UNWIND $batch AS row "
                    f"MATCH (a:Memory {{id: row.from_id}}) "
                    f"MATCH (b:Memory {{id: row.to_id}}) "
                    f"MERGE (a)-[r:{neo4j_type}]->(b) "
                    f"SET r.weight = row.weight, r.confidence = row.conf "
                    f"RETURN COUNT(r) as cnt",
                    batch=batch
                )
                data = await result.data()
                created += data[0]["cnt"] if data else 0
                batch = []
                print(f"    ... {created:,} creadas", end="\r")

        if batch:
            result = await session.run(
                f"UNWIND $batch AS row "
                f"MATCH (a:Memory {{id: row.from_id}}) "
                f"MATCH (b:Memory {{id: row.to_id}}) "
                f"MERGE (a)-[r:{neo4j_type}]->(b) "
                f"SET r.weight = row.weight, r.confidence = row.conf "
                f"RETURN COUNT(r) as cnt",
                batch=batch
            )
            data = await result.data()
            created += data[0]["cnt"] if data else 0

        print(f"    ✅ {created:,} aristas creadas")
        return created


async def print_summary(pool, driver):
    """Imprime resumen comparativo PG vs Neo4j."""
    async with driver.session() as session:
        pg_stats = await get_pg_stats(pool)
        neo4j_stats = await get_neo4j_stats(session)

    print("\n" + "━"*60)
    print("  RESUMEN: PostgreSQL vs Neo4j")
    print("━"*60)
    print(f"  {'Tipo':<15} {'PostgreSQL':>12} {'Neo4j':>12} {'Sync%':>8}")
    print(f"  {'-'*15} {'-'*12} {'-'*12} {'-'*8}")

    all_types = set(list(pg_stats["by_type"].keys()) + list(neo4j_stats["by_type"].keys()))
    for t in sorted(all_types):
        pg_cnt  = pg_stats["by_type"].get(t, 0)
        neo_key = TYPE_MAP.get(t, t.upper())
        neo_cnt = neo4j_stats["by_type"].get(neo_key, 0)
        pct = (neo_cnt / pg_cnt * 100) if pg_cnt > 0 else 0
        status = "✅" if pct >= 99 else "⚠️" if pct >= 50 else "❌"
        print(f"  {t:<15} {pg_cnt:>12,} {neo_cnt:>12,} {pct:>7.1f}% {status}")

    pg_total  = pg_stats["total"]
    neo_total = neo4j_stats["total"]
    pct_total = (neo_total / pg_total * 100) if pg_total > 0 else 0
    print(f"  {'TOTAL':<15} {pg_total:>12,} {neo_total:>12,} {pct_total:>7.1f}%")
    print("━"*60)
    return pct_total


async def main():
    full_mode  = "--full"  in sys.argv
    stats_mode = "--stats" in sys.argv

    print("━"*60)
    print("  sync_connectome.py — JARVIS, Team SEAL")
    print(f"  Modo: {'FULL REBUILD' if full_mode else 'STATS ONLY' if stats_mode else 'INCREMENTAL'}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("━"*60)

    # Conectar
    print("\nConectando a bases de datos...")
    pool   = await asyncpg.create_pool(PG_URL, min_size=1, max_size=5)
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    print("  ✅ PostgreSQL conectado")
    print("  ✅ Neo4j conectado")

    # Stats iniciales
    print("\nEstado inicial:")
    await print_summary(pool, driver)

    if stats_mode:
        await pool.close()
        await driver.close()
        return

    # Sincronización
    t0 = time.time()
    print("\nSincronizando...")

    await ensure_nodes(pool, driver)

    total_created = 0
    for pg_type in TYPE_MAP.keys():
        created = await sync_connection_type(pool, driver, pg_type, full=full_mode)
        total_created += created

    elapsed = time.time() - t0

    # Stats finales
    print(f"\nEstado final (después de {elapsed:.1f}s):")
    pct = await print_summary(pool, driver)

    print(f"\n  Total aristas creadas: {total_created:,}")
    print(f"  Sincronización: {pct:.1f}%")

    if pct >= 99:
        print("  🏆 CONNECTOME COMPLETAMENTE SINCRONIZADO")
    elif pct >= 90:
        print("  ✅ Sincronización mayor al 90%")
    else:
        print("  ⚠️  Sincronización parcial — revisar errores arriba")

    await pool.close()
    await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
