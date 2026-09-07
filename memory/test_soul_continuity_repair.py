#!/usr/bin/env python3
"""Regression checks for SOUL continuity repair.

Validates:
- distilled_exchanges are linked to candidate sessions
- distilled_exchanges have pgvector embeddings
- raw Matrix auto-rules are not active high-priority rules
- semantic search over distilled_exchanges works through pgvector
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import asyncio
import json

import asyncpg

from embeddings import get_embedding


DB_URL = pg_dsn(required=True)


async def main() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        missing_embeddings = await conn.fetchval("""
            SELECT count(*)
            FROM soul_v3.distilled_exchanges
            WHERE embedding IS NULL
              AND COALESCE(exchange_core, summary, specific_context) IS NOT NULL
        """)
        assert missing_embeddings == 0, f"distilled_exchanges missing embeddings: {missing_embeddings}"

        uncovered = await conn.fetch("""
            WITH session_stats AS (
              SELECT s.agent, s.id,
                     COUNT(DISTINCT de.id) AS distills,
                     COUNT(DISTINCT m.id) FILTER (
                       WHERE m.importance >= 6
                         AND m.created_at BETWEEN s.started_at AND COALESCE(s.ended_at, now())
                     ) AS candidates
              FROM soul_v3.sessions s
              LEFT JOIN soul_v3.distilled_exchanges de
                ON de.agent = s.agent AND de.session_id = s.id
              LEFT JOIN soul_v3.memories m ON m.agent = s.agent
              GROUP BY s.agent, s.id
            )
            SELECT agent, id, candidates
            FROM session_stats
            WHERE candidates > 0 AND distills = 0
        """)
        assert not uncovered, f"candidate sessions without distills: {[dict(r) for r in uncovered]}"

        noisy_rules = await conn.fetchval("""
            SELECT count(*)
            FROM soul_v3.rules
            WHERE active = true
              AND priority >= 8
              AND rule_key LIKE 'william_auto_%'
              AND content LIKE '[Matrix]%'
        """)
        assert noisy_rules == 0, f"noisy Matrix rules still high-priority: {noisy_rules}"

        canonical_rules = await conn.fetchval("""
            SELECT count(*)
            FROM soul_v3.rules
            WHERE active = true
              AND priority = 10
              AND rule_key LIKE 'william_canonical_%'
        """)
        assert canonical_rules >= 5, f"canonical rules missing: {canonical_rules}"

        query_vec = json.dumps(await get_embedding("SPECTRE launcher daemon checkpoint"))
        hit = await conn.fetchrow("""
            SELECT id, agent, 1 - (embedding <=> $1::vector) AS sim
            FROM soul_v3.distilled_exchanges
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT 1
        """, query_vec)
        assert hit is not None, "pgvector distilled_exchanges returned no hits"
        assert float(hit["sim"]) > 0.50, f"semantic distill hit too weak: {dict(hit)}"

        print("PASS soul continuity repair regression")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
