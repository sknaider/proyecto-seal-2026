#!/usr/bin/env python3
"""
GAP-T1 Talamo (Thalamic Push Daemon)
SEAL SOUL — Auto active_recall daemon

Monitorea william_channel.jsonl. Cuando detecta topico nuevo relevante,
pushea memorias relacionadas al agente via working_state.thalamic_queue
sin que el agente tenga que pedirlas (pull -> push automatico).

Arquitectura: MemOS memory scheduling pattern
Spec: sandbox-agent/specs/spec_soul_cognitive_gaps_v1.md (GAP-T1)
Implementado por NEXUS, diseno JARVIS, 2026-04-26
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

DB_DSN = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory",
)

CHANNEL_PATH = Path("/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl")
POLL_INTERVAL_SEC = 15
MIN_RELEVANCE_SCORE = 0.72  # umbral para push (mismo que semantic->procedural JARVIS)
MAX_MEMORIES_PER_PUSH = 5
AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [THALAMUS] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("thalamus")


def extract_keywords(text: str) -> list[str]:
    """Extrae keywords del mensaje: elimina stopwords, retorna tokens relevantes."""
    stopwords = {
        "el", "la", "los", "las", "un", "una", "de", "del", "al", "en", "es", "que",
        "y", "o", "a", "con", "por", "para", "se", "le", "lo", "me", "te", "si",
        "no", "mi", "tu", "su", "ya", "hay", "the", "is", "are", "to", "of", "and",
        "matrix", "nexus", "jarvis", "ada", "alice", "william", "dum",
    }
    tokens = re.findall(r"[a-zu00e1-u00fau00f1]{4,}", text.lower())
    return [t for t in tokens if t not in stopwords][:10]


async def get_relevant_memories(
    pool: asyncpg.Pool, agent: str, keywords: list[str], limit: int = MAX_MEMORIES_PER_PUSH
) -> list[dict]:
    """Busca memorias relevantes en PostgreSQL por keyword match (BM25 proxy)."""
    if not keywords:
        return []
    query_text = " | ".join(keywords)  # tsquery OR
    rows = await pool.fetch(
        """
        SELECT id, content, importance, category
        FROM memories
        WHERE agent = $1
          AND (valid_until IS NULL OR valid_until > NOW())
          AND to_tsvector('spanish', content) @@ to_tsquery('spanish', $2)
        ORDER BY importance DESC, updated_at DESC
        LIMIT $3
        """,
        agent, query_text, limit,
    )
    return [{"id": r["id"], "content": r["content"][:200], "importance": r["importance"], "category": r["category"]} for r in rows]


async def push_to_working_state(pool: asyncpg.Pool, agent: str, memories: list[dict], topic_summary: str):
    """Pushea memorias relevantes al working_state del agente via thalamic_queue."""
    queue_entry = {
        "thalamic_push": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topic": topic_summary,
        "memories": memories,
    }
    queue_json = json.dumps(queue_entry, ensure_ascii=False)
    await pool.execute(
        """
        UPDATE working_state
        SET state = state || jsonb_build_object('thalamic_queue', $2::jsonb),
            updated_at = NOW()
        WHERE agent = $1
        """,
        agent, queue_json,
    )
    log.info("push agent=%s topic=%s memories=%d", agent, topic_summary[:50], len(memories))


async def run(pool: asyncpg.Pool, dry_run: bool = False):
    """Loop principal: lee canal, detecta topicos, pushea memorias."""
    last_position = CHANNEL_PATH.stat().st_size if CHANNEL_PATH.exists() else 0
    log.info("thalamus started — poll=%ds dry_run=%s", POLL_INTERVAL_SEC, dry_run)

    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            if not CHANNEL_PATH.exists():
                continue
            current_size = CHANNEL_PATH.stat().st_size
            if current_size <= last_position:
                continue

            # Leer lineas nuevas desde la ultima posicion
            new_lines = []
            with open(CHANNEL_PATH, encoding="utf-8") as f:
                f.seek(last_position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            new_lines.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            last_position = current_size

            if not new_lines:
                continue

            # Filtrar: solo mensajes de William (o team) que no sean heartbeats
            relevant_msgs = [
                m for m in new_lines
                if m.get("from") in ("William", "equipo")
                and m.get("type") not in ("heartbeat", "system_alive")
            ]

            if not relevant_msgs:
                continue

            # Extraer keywords del contexto reciente
            combined_text = " ".join(m.get("message", "") for m in relevant_msgs[-3:])
            keywords = extract_keywords(combined_text)
            if len(keywords) < 2:
                continue

            topic_summary = " ".join(keywords[:5])
            log.info("topic detected: %s", topic_summary)

            # Para cada agente activo: buscar y pushear memorias relevantes
            for agent in AGENTS:
                memories = await get_relevant_memories(pool, agent, keywords)
                if memories:
                    if dry_run:
                        log.info("[DRY-RUN] would push %d memories to %s for topic '%s'",
                                 len(memories), agent, topic_summary)
                    else:
                        await push_to_working_state(pool, agent, memories, topic_summary)

        except Exception:
            log.exception("thalamus tick failed")


async def main_async(dry_run: bool):
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
    try:
        await run(pool, dry_run=dry_run)
    finally:
        await pool.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main_async(dry_run=dry_run))
