#!/usr/bin/env python3
"""SEAL Soul Maintenance — limpieza automática de Soul DB.

Ejecutar semanalmente via systemd timer o cron.
Idempotente: seguro de correr múltiples veces.

Políticas de retención:
  inner_monologue      → 30 días
  tool_observations    → 14 días
  nerves_metrics_log   → 7 días
  event_log            → 60 días
  instinct_activations → 90 días
  latent_subgraph_cache stale → 24 horas
  memories invalidadas → mover a archive inmediatamente
  memory_broadcasts huérfanos → eliminar
  chat_messages        → 180 días
"""

import asyncio
import asyncpg
import json
import logging
import sys
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
LOG_PATH = "/home/dadito/IA/proyecto-seal/memory/maintenance_log.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("seal_maintenance")


async def run_maintenance():
    conn = await asyncpg.connect(DB_URL)
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "steps": {}
    }

    try:
        # ── 1. Purge inner_monologue > 30 días ──
        n = int((await conn.execute(
            "DELETE FROM inner_monologue WHERE created_at < NOW() - INTERVAL '30 days'"
        )).split()[-1])
        report["steps"]["inner_monologue_purged"] = n
        log.info(f"inner_monologue: {n} filas eliminadas (>30d)")

        # ── 2. Purge tool_observations > 14 días ──
        n = int((await conn.execute(
            "DELETE FROM tool_observations WHERE created_at < NOW() - INTERVAL '14 days'"
        )).split()[-1])
        report["steps"]["tool_observations_purged"] = n
        log.info(f"tool_observations: {n} filas eliminadas (>14d)")

        # ── 3. Purge nerves_metrics_log > 7 días ──
        n = int((await conn.execute(
            "DELETE FROM nerves_metrics_log WHERE created_at < NOW() - INTERVAL '7 days'"
        )).split()[-1])
        report["steps"]["nerves_metrics_purged"] = n
        log.info(f"nerves_metrics_log: {n} filas eliminadas (>7d)")

        # ── 4. Purge event_log > 60 días (columna: created_at) ──
        n = int((await conn.execute(
            "DELETE FROM event_log WHERE created_at < NOW() - INTERVAL '60 days'"
        )).split()[-1])
        report["steps"]["event_log_purged"] = n
        log.info(f"event_log: {n} filas eliminadas (>60d)")

        # ── 5. Purge instinct_activations > 90 días ──
        n = int((await conn.execute(
            "DELETE FROM instinct_activations WHERE created_at < NOW() - INTERVAL '90 days'"
        )).split()[-1])
        report["steps"]["instinct_activations_purged"] = n
        log.info(f"instinct_activations: {n} filas eliminadas (>90d)")

        # ── 6. Purge latent_subgraph_cache stale > 24h ──
        # Clean FK refs first
        await conn.execute("""
            DELETE FROM latent_subgraph_cache
            WHERE memory_id IN (SELECT id FROM memories WHERE invalid_at IS NOT NULL)
        """)
        n = int((await conn.execute(
            "DELETE FROM latent_subgraph_cache WHERE updated_at < NOW() - INTERVAL '24 hours'"
        )).split()[-1])
        report["steps"]["latent_cache_purged"] = n
        log.info(f"latent_subgraph_cache: {n} filas stale eliminadas")

        # ── 7. Purge chat_messages > 180 días ──
        n = int((await conn.execute(
            "DELETE FROM chat_messages WHERE created_at < NOW() - INTERVAL '180 days'"
        )).split()[-1])
        report["steps"]["chat_messages_purged"] = n
        log.info(f"chat_messages: {n} filas eliminadas (>180d)")

        # ── 7b. Purge conversation_turn memories > 7 días ──
        n = int((await conn.execute(
            "DELETE FROM soul_v3.memories WHERE category = 'conversation_turn'"
            " AND created_at < NOW() - INTERVAL '7 days' AND invalid_at IS NULL"
        )).split()[-1])
        report["steps"]["conv_turn_purged"] = n
        log.info(f"conversation_turn: {n} memorias eliminadas (>7d)")

        # ── 8. Migrar memorias invalidadas a archive ──
        invalid_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE invalid_at IS NOT NULL"
        )
        if invalid_count > 0:
            # Clean FK refs
            await conn.execute("""
                DELETE FROM memory_broadcasts
                WHERE memory_id IN (SELECT id FROM memories WHERE invalid_at IS NOT NULL)
            """)
            await conn.execute("""
                DELETE FROM latent_subgraph_cache
                WHERE memory_id IN (SELECT id FROM memories WHERE invalid_at IS NOT NULL)
            """)
            # Archive
            await conn.execute("""
                INSERT INTO memories_archive
                    (id, agent, scope, category, content, importance, source_tier,
                     heat_score, access_count, created_at, archived_at, reason, metadata)
                SELECT id, agent, scope, category, content, importance, source_tier,
                       heat_score, COALESCE(access_count, query_count, recall_count, 0),
                       created_at, NOW(), 'auto_maintenance', metadata
                FROM memories WHERE invalid_at IS NOT NULL
                ON CONFLICT (id) DO NOTHING
            """)
            n = int((await conn.execute(
                "DELETE FROM memories WHERE invalid_at IS NOT NULL"
            )).split()[-1])
            report["steps"]["invalid_memories_archived"] = n
            log.info(f"memories invalidadas: {n} movidas a archive")

        # ── 9. Detectar inflación de importancia ──
        total = await conn.fetchval("SELECT COUNT(*) FROM memories")
        at_7 = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE importance = 7 AND invalid_at IS NULL"
        )
        if total > 0 and at_7 / total > 0.50:
            log.info(f"Inflación detectada: {at_7}/{total} en imp=7 ({100*at_7//total}%). Recalibrando...")
            await conn.execute("""
                UPDATE memories SET importance = new_imp
                FROM (
                    SELECT id,
                        CASE
                            WHEN pct >= 0.90 THEN 10
                            WHEN pct >= 0.75 THEN 9
                            WHEN pct >= 0.50 THEN 8
                            WHEN pct >= 0.25 THEN 7
                            WHEN pct >= 0.10 THEN 6
                            ELSE 5
                        END as new_imp
                    FROM (
                        SELECT id,
                            PERCENT_RANK() OVER (
                                PARTITION BY agent
                                ORDER BY (importance * 0.5 + COALESCE(recall_count, 0) * 0.3
                                         + COALESCE(utility_score, 5) * 0.2)
                            ) as pct
                        FROM memories WHERE invalid_at IS NULL
                    ) ranked
                ) recalc
                WHERE memories.id = recalc.id
            """)
            report["steps"]["importance_recalibrated"] = True
            log.info("Importancia recalibrada")
        else:
            report["steps"]["importance_recalibrated"] = False

        # ── 10. Purge soul_audit_log > 365 días ──
        n = int((await conn.execute(
            "DELETE FROM soul_audit_log WHERE ts < NOW() - INTERVAL '365 days'"
        )).split()[-1])
        report["steps"]["audit_log_purged"] = n
        log.info(f"soul_audit_log: {n} filas eliminadas (>365d)")

        # ── 11. Sincronizar Qdrant — purgar vectores de memorias archivadas ──
        try:
            qc = QdrantClient(host="localhost", port=6333)
            pg_active_ids = set(
                r["id"] for r in await conn.fetch("SELECT id FROM memories WHERE invalid_at IS NULL")
            )
            qdrant_ids = set()
            offset = None
            while True:
                results, next_offset = qc.scroll(
                    "soul_memories", limit=500, offset=offset,
                    with_payload=False, with_vectors=False
                )
                for p in results:
                    qdrant_ids.add(p.id)
                if next_offset is None:
                    break
                offset = next_offset
            orphan_ids = [qid for qid in qdrant_ids if qid not in pg_active_ids]
            if orphan_ids:
                for i in range(0, len(orphan_ids), 100):
                    qc.delete("soul_memories", points_selector=PointIdsList(points=orphan_ids[i:i+100]))
            report["steps"]["qdrant_orphans_purged"] = len(orphan_ids)
            log.info(f"Qdrant sync: {len(orphan_ids)} vectores huérfanos eliminados")
        except Exception as e:
            report["steps"]["qdrant_orphans_purged"] = f"error: {e}"
            log.warning(f"Qdrant sync skipped: {e}")

        # ── 12. VACUUM ANALYZE tablas críticas ──
        for table in ["memories", "inner_monologue", "memory_broadcasts",
                      "tool_observations", "nerves_metrics_log", "event_log",
                      "latent_subgraph_cache"]:
            await conn.execute(f"VACUUM ANALYZE {table}")
        log.info("VACUUM ANALYZE completado")
        report["steps"]["vacuum_done"] = True

        # ── 13. Stats finales ──
        report["stats"] = {
            "memories_active": await conn.fetchval("SELECT COUNT(*) FROM memories"),
            "memories_archive": await conn.fetchval("SELECT COUNT(*) FROM memories_archive"),
            "inner_monologue": await conn.fetchval("SELECT COUNT(*) FROM inner_monologue"),
            "event_log": await conn.fetchval("SELECT COUNT(*) FROM event_log"),
            "db_size_mb": await conn.fetchval(
                "SELECT ROUND(pg_database_size(current_database()) / 1048576.0, 1)"
            ),
        }
        log.info(f"Stats: {report['stats']}")

    except Exception as e:
        report["error"] = str(e)
        log.error(f"Error en mantenimiento: {e}")
        raise
    finally:
        await conn.close()

    # Append to log
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(report, default=str) + "\n")

    return report


if __name__ == "__main__":
    report = asyncio.run(run_maintenance())
    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if "error" not in report else 1)
