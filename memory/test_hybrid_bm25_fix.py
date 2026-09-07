#!/usr/bin/env python3
"""Regression test for memory_hybrid_search lexical recall.

Live, non-destructive test:
- verifies memories uses embedding_bm25, not search_vector
- exercises memory_hybrid_search with keyword weighting
"""
from seal_secrets import pg_dsn

import asyncio
import json
import os
import sys

import asyncpg


DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)


async def main() -> int:
    sys.path.insert(0, os.path.dirname(__file__))
    os.environ["SEAL_OPERATOR"] = "William"

    conn = await asyncpg.connect(DB_URL)
    try:
        cols = set(await conn.fetchval(
            """
            SELECT array_agg(column_name::text)
            FROM information_schema.columns
            WHERE table_schema = 'soul_v3'
              AND table_name = 'memories'
            """
        ) or [])
        assert "embedding_bm25" in cols, "memories.embedding_bm25 is missing"
        assert "search_vector" not in cols, "unexpected memories.search_vector column present"

        sample = await conn.fetchrow(
            """
            SELECT content
            FROM soul_v3.memories
            WHERE agent = 'ADA'
              AND invalid_at IS NULL
              AND content ILIKE '%William%'
            ORDER BY importance DESC, created_at DESC
            LIMIT 1
            """
        )
        assert sample, "no ADA memory containing William found for regression query"
    finally:
        await conn.close()

    import db
    import mcp_server_v4

    db._pool = None
    mcp_server_v4._pool = None
    mcp_server_v4._qdrant = None

    result = await mcp_server_v4.memory_hybrid_search(
        "William",
        agent="ADA",
        limit=5,
        semantic_weight=0.0,
        keyword_weight=1.0,
    )
    assert "Keyword search failed" not in result, result
    assert "No memories found matching query." not in result, result

    data = json.loads(result)
    assert isinstance(data, list) and data, result
    assert any(item.get("keyword_score", 0) > 0 for item in data), result
    print(f"PASS hybrid BM25 lexical recall returned {len(data)} result(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
