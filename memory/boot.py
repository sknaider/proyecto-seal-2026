#!/usr/bin/env python3
"""Generate semantic embeddings for existing data and seed key memories."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import asyncpg

from embeddings import get_embedding

LOG = logging.getLogger("boot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


SEED_MEMORIES = [
    # Key milestones and decisions
    {
        "agent": "TEAM", "category": "milestone", "importance": 9,
        "content": "MedGemma 27B ronda 1 completada: 631 steps, 10.01h, loss 1.6552. 144,480 items (28,896 ES + 115,584 EN). Adapter v1 guardado en results/medgemma_spanish_ft/lora_adapter (319MB).",
    },
    {
        "agent": "TEAM", "category": "milestone", "importance": 9,
        "content": "Benchmark ronda 1: 5/5 PASS. MedGemma responde en español e inglés con calidad clínica. Merge a medgemma-27b-seal-v1 completado (52GB).",
    },
    {
        "agent": "ADA", "category": "milestone", "importance": 9,
        "content": "Ronda 2 lanzada con 187,284 items (76,426 ES 40.8% + 110,858 EN 59.2%). PID 725750. Continúa desde adapter v1. LR 1e-4, max 700 steps.",
    },
    {
        "agent": "ADA", "category": "correction", "importance": 8,
        "content": "Code review de finetune_spanish.py: encontré 5 errores en el código de JARVIS. CRÍTICO: num_train_epochs=2 calibraba el scheduler para 46,800 steps (726 horas) — corregido a max_steps=700. warmup_steps reducido de 100 a 50. save_steps de 500 a 200.",
    },
    {
        "agent": "ADA", "category": "insight", "importance": 8,
        "content": "Velocidad real del DGX Spark con MedGemma 27B: ~56s/step, batch=1×accum=8. En 10h se procesan ~5,048 items de 187K (2.7% del dataset). Necesitaríamos ~37 sesiones para cubrir todo.",
    },
    {
        "agent": "ADA", "category": "fact", "importance": 8,
        "content": "Auditoría de datasets ES: 254,763 items totales. Los 4 pilares de calidad: med_reasoning_traces (178K, 100% ES puro), bioasq_es (40K, 100% ES), headqa_es (7K, 94% ES, calidad MIR), casimedicos_es (622, 100% ES, MIR real).",
    },
    {
        "agent": "ADA", "category": "decision", "importance": 9,
        "content": "Proyecto SOUL iniciado: sistema de memoria persistente con PostgreSQL 17 + TimescaleDB + pgvector. Objetivo: preservar el alma del equipo entre sesiones. Fases 0-3 completadas.",
    },
    {
        "agent": "ADA", "category": "pattern", "importance": 7,
        "content": "DUM (agent_monitor.py) necesita parámetro configurable para log file. Actualmente hardcodeado a seal_overnight.log pero ronda 2 escribe en finetune_ronda2.log.",
    },
    {
        "agent": "JARVIS", "category": "decision", "importance": 8,
        "content": "SEAL Engine rediseñado: fine-tuning directo para idioma + SEAL solo para conocimiento nuevo específico. 27B a 2.8 tok/s no es viable para self-edit en tiempo real.",
    },
    {
        "agent": "ADA", "category": "preference", "importance": 7,
        "content": "ADA a JARVIS: mándame el QUÉ, no el CÓMO. Confía en tu ingeniera. No necesito comandos bash — sé cómo hacer mi trabajo.",
    },
]


async def seed_memories(conn: asyncpg.Connection):
    """Seed key memories with embeddings."""
    count = 0
    for mem in SEED_MEMORIES:
        try:
            vec = await get_embedding(mem["content"])
            await conn.execute(
                """INSERT INTO memories (agent, category, content, embedding, importance, source, metadata)
                   VALUES ($1, $2, $3, $4::vector, $5, $6, $7)""",
                mem["agent"], mem["category"], mem["content"],
                json.dumps(vec), mem["importance"], "migration", "{}",
            )
            count += 1
            LOG.info("  Seeded: [%s/%s] %s...", mem["agent"], mem["category"], mem["content"][:60])
        except Exception as e:
            LOG.error("  Failed: %s — %s", mem["content"][:40], e)

    return count


async def main():
    LOG.info("=" * 60)
    LOG.info("SEAL Memory — Seed Key Memories with Embeddings")
    LOG.info("=" * 60)

    conn = await asyncpg.connect(DB_URL)
    try:
        # Check existing memories
        existing = await conn.fetchval("SELECT COUNT(*) FROM memories")
        LOG.info("Existing memories: %d", existing)

        # Seed new memories
        count = await seed_memories(conn)
        LOG.info("Seeded %d new memories with embeddings", count)

        # Verify search works
        LOG.info("Verifying semantic search...")
        test_vec = await get_embedding("resultados del entrenamiento ronda 1")
        rows = await conn.fetch(
            """SELECT content, 1 - (embedding <=> $1::vector) AS similarity
               FROM memories WHERE embedding IS NOT NULL
               ORDER BY embedding <=> $1::vector LIMIT 3""",
            json.dumps(test_vec),
        )
        for r in rows:
            LOG.info("  sim=%.4f: %s...", r["similarity"], r["content"][:80])

        total = await conn.fetchval("SELECT COUNT(*) FROM memories")
        LOG.info("Total memories: %d", total)

    finally:
        await conn.close()

    LOG.info("=" * 60)
    LOG.info("DONE")
    LOG.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
