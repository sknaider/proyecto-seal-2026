"""
TrieIndex — Árbol de prefijos sobre memorias SOUL para retrieval eficiente.

Propuesto por: William (15 abril 2026, 12:51 Lima)
Implementado por: ALICE (15 abril 2026)
Spec: agents/SPEC_context_trie_distill.md

Uso:
  python3 trie_index.py --build ALICE
  python3 trie_index.py --query ALICE "context_guard"
  python3 trie_index.py --stats ALICE
  python3 trie_index.py --build-all
"""

import os
import re
import sys
import json
import asyncio
import argparse
import asyncpg
from datetime import datetime
from typing import List, Optional

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

STOPWORDS = {
    "de", "la", "el", "en", "que", "y", "a", "los", "del", "las", "un", "por",
    "con", "una", "es", "se", "no", "te", "lo", "le", "da", "su", "al", "para",
    "the", "and", "or", "is", "in", "of", "to", "a", "an", "it", "be", "as",
    "at", "so", "we", "he", "by", "do", "on", "if", "up", "an", "my", "go",
}


async def get_conn():
    return await asyncpg.connect(DB_URL)


async def ensure_schema(conn):
    """Crea la tabla memory_trie si no existe."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_trie (
            id          SERIAL PRIMARY KEY,
            agent       VARCHAR(50) NOT NULL,
            prefix      VARCHAR(500) NOT NULL,
            memory_ids  INTEGER[] NOT NULL DEFAULT '{}',
            depth       INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trie_agent_prefix
        ON memory_trie(agent, prefix)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trie_agent
        ON memory_trie(agent)
    """)
    print("[TRIE] Schema OK")


def extract_keywords(text: str) -> List[str]:
    """Extrae tokens significativos del texto (min 3 chars, no stopwords)."""
    if not text:
        return []
    text = text.lower()
    tokens = re.findall(r'[a-záéíóúüñ_][a-záéíóúüñ0-9_]*', text)
    result = []
    for token in tokens:
        if len(token) >= 3 and token not in STOPWORDS:
            result.append(token)
    return list(set(result))


async def build_trie_for_agent(agent: str, verbose: bool = False) -> dict:
    """Indexa todas las memorias de un agente en la tabla memory_trie."""
    conn = await get_conn()
    stats = {"memories_processed": 0, "nodes_created": 0}

    try:
        await ensure_schema(conn)

        rows = await conn.fetch("""
            SELECT id, content, category
            FROM memories
            WHERE agent = $1 AND content IS NOT NULL
            ORDER BY created_at DESC
        """, agent)

        if not rows:
            print(f"[TRIE] No memories found for {agent}")
            return stats

        print(f"[TRIE] Building trie for {agent}: {len(rows)} memories...")

        prefix_map: dict = {}
        for row in rows:
            mem_id, content, category = row['id'], row['content'], row['category']
            keywords = extract_keywords(content or "")
            if category:
                keywords.extend(extract_keywords(category))

            for token in keywords:
                for i in range(3, len(token) + 1):
                    prefix = token[:i]
                    if prefix not in prefix_map:
                        prefix_map[prefix] = set()
                    prefix_map[prefix].add(mem_id)

            stats["memories_processed"] += 1

        # Limpiar trie anterior
        await conn.execute("DELETE FROM memory_trie WHERE agent = $1", agent)

        # Insertar en batch
        batch = [
            (agent, prefix, list(ids), len(prefix))
            for prefix, ids in prefix_map.items()
        ]

        await conn.executemany("""
            INSERT INTO memory_trie (agent, prefix, memory_ids, depth, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (agent, prefix) DO UPDATE SET
                memory_ids = EXCLUDED.memory_ids,
                updated_at = NOW()
        """, batch)

        stats["nodes_created"] = len(prefix_map)
        print(f"[TRIE] Built: {stats['memories_processed']} memories → {stats['nodes_created']} prefix nodes")
        return stats

    finally:
        await conn.close()


async def trie_search(agent: str, query: str, limit: int = 10) -> List[dict]:
    """Busca memorias por prefijo. O(m) donde m = len(query)."""
    conn = await get_conn()
    try:
        prefix = query.lower().strip()
        if len(prefix) < 2:
            print(f"[TRIE] Query too short: '{prefix}' (min 2 chars)")
            return []

        row = await conn.fetchrow("""
            SELECT memory_ids FROM memory_trie
            WHERE agent = $1 AND prefix = $2
        """, agent, prefix)

        if not row:
            # Fallback: prefijo más corto
            for length in range(len(prefix) - 1, 2, -1):
                shorter = prefix[:length]
                row = await conn.fetchrow("""
                    SELECT memory_ids FROM memory_trie
                    WHERE agent = $1 AND prefix = $2
                """, agent, shorter)
                if row:
                    print(f"[TRIE] Fallback prefix: '{prefix}' → '{shorter}'")
                    break

        if not row or not row['memory_ids']:
            print(f"[TRIE] No matches for '{prefix}'")
            return []

        memory_ids = row['memory_ids'][:limit * 2]

        rows = await conn.fetch("""
            SELECT id, agent, category, content, importance, created_at
            FROM memories
            WHERE id = ANY($1) AND content IS NOT NULL
            ORDER BY importance DESC, created_at DESC
            LIMIT $2
        """, memory_ids, limit)

        return [
            {
                "id": r['id'],
                "agent": r['agent'],
                "category": r['category'],
                "content": (r['content'] or "")[:300],
                "importance": r['importance'],
                "created_at": str(r['created_at'])[:16] if r['created_at'] else ""
            }
            for r in rows
        ]

    finally:
        await conn.close()


async def get_stats(agent: str) -> dict:
    """Retorna estadísticas del trie del agente."""
    conn = await get_conn()
    try:
        row = await conn.fetchrow("""
            SELECT
                COUNT(*)::int as total_nodes,
                MAX(depth)::int as max_depth,
                AVG(array_length(memory_ids, 1))::float as avg_memories_per_node,
                MAX(updated_at) as last_build
            FROM memory_trie
            WHERE agent = $1
        """, agent)
        if row:
            return {
                "agent": agent,
                "total_nodes": row['total_nodes'],
                "max_depth": row['max_depth'],
                "avg_memories_per_node": float(row['avg_memories_per_node']) if row['avg_memories_per_node'] else 0,
                "last_build": str(row['last_build'])[:16] if row['last_build'] else "never"
            }
        return {"agent": agent, "total_nodes": 0, "last_build": "never"}
    finally:
        await conn.close()


async def _main_async(args):
    if args.build:
        stats = await build_trie_for_agent(args.build, verbose=True)
        print(json.dumps(stats, indent=2))

    elif args.build_all:
        for agent in ["ALICE", "ADA", "JARVIS", "DUM", "NEXUS"]:
            stats = await build_trie_for_agent(agent)
            print(f"  {agent}: {stats['nodes_created']} nodes")

    elif args.query:
        agent, query = args.query
        results = await trie_search(agent, query)
        if results:
            print(f"\n[TRIE] {len(results)} results for '{query}' ({agent}):\n")
            for r in results:
                print(f"  [{r['id']}] {r['created_at']} imp={r['importance']} cat={r['category']}")
                print(f"       {r['content'][:120]}...")
                print()
        else:
            print(f"[TRIE] No results for '{query}'")

    elif args.stats:
        stats = await get_stats(args.stats)
        print(json.dumps(stats, indent=2))


def main():
    parser = argparse.ArgumentParser(description="SEAL TrieIndex — árbol de prefijos sobre memorias SOUL")
    parser.add_argument("--build", metavar="AGENT", help="Construir trie para el agente")
    parser.add_argument("--query", nargs=2, metavar=("AGENT", "QUERY"), help="Buscar por prefijo")
    parser.add_argument("--stats", metavar="AGENT", help="Estadísticas del trie")
    parser.add_argument("--build-all", action="store_true", help="Construir trie para todos los agentes")
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
