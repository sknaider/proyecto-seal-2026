"""Cathedral II — hybrid search with RRF Fusion + two-pass expansion.

Pipeline:
  keyword FTS → RRF fusion ← vector cosine
       ↓
  two-pass structural walk (code_edges)
       ↓
  deduplicate + rank
"""

from __future__ import annotations
import math
from typing import Optional
import asyncpg

from .two_pass import ChunkScore, expand_anchors, hydrate_chunks

RRF_K = 60
COMPILED_TRUTH_BOOST = 2.0


def rrf_fusion(lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    scores: dict[int, dict] = {}
    for lst in lists:
        for rank, item in enumerate(lst):
            cid = item["chunk_id"]
            rrf = 1.0 / (k + rank)
            if cid in scores:
                scores[cid]["score"] += rrf
            else:
                scores[cid] = {**item, "score": rrf}

    items = list(scores.values())
    if not items:
        return []

    max_score = max(i["score"] for i in items)
    if max_score > 0:
        for i in items:
            i["score"] /= max_score

    return sorted(items, key=lambda x: x["score"], reverse=True)


async def keyword_search(conn: asyncpg.Connection, query: str,
                         limit: int = 40) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            cc.id AS chunk_id,
            cc.chunk_text,
            cc.symbol_name,
            cc.symbol_name_qualified,
            cc.start_line,
            cc.end_line,
            cc.language,
            p.file_path,
            p.id AS page_id,
            ts_rank_cd(cc.search_vector, plainto_tsquery('english', $1)) AS score
        FROM cgraph_chunks cc
        JOIN cgraph_pages p ON p.id = cc.page_id
        WHERE cc.search_vector @@ plainto_tsquery('english', $1)
        ORDER BY score DESC
        LIMIT $2
        """,
        query, limit,
    )
    return [dict(r) for r in rows]


async def vector_search(conn: asyncpg.Connection, embedding: list[float],
                        limit: int = 40) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            cc.id AS chunk_id,
            cc.chunk_text,
            cc.symbol_name,
            cc.symbol_name_qualified,
            cc.start_line,
            cc.end_line,
            cc.language,
            p.file_path,
            p.id AS page_id,
            1 - (cc.embedding <=> $1::vector) AS score
        FROM cgraph_chunks cc
        JOIN cgraph_pages p ON p.id = cc.page_id
        WHERE cc.embedding IS NOT NULL
        ORDER BY cc.embedding <=> $1::vector
        LIMIT $2
        """,
        embedding, limit,
    )
    return [dict(r) for r in rows]


async def hybrid_search(
    conn: asyncpg.Connection,
    query: str,
    embedding: Optional[list[float]] = None,
    limit: int = 20,
    walk_depth: int = 0,
    near_symbol: Optional[str] = None,
) -> list[dict]:
    kw = await keyword_search(conn, query, limit * 2)

    lists = [kw]
    if embedding:
        vec = await vector_search(conn, embedding, limit * 2)
        lists.append(vec)

    fused = rrf_fusion(lists)

    if walk_depth > 0 or near_symbol:
        anchors = [
            ChunkScore(chunk_id=r["chunk_id"], score=r["score"], hop=0)
            for r in fused[:max(10, limit)]
        ]
        expanded = await expand_anchors(conn, anchors, walk_depth, near_symbol)
        existing = {r["chunk_id"] for r in fused}
        new_ids = [e.chunk_id for e in expanded if e.chunk_id not in existing]
        if new_ids:
            hydrated = await hydrate_chunks(conn, new_ids)
            score_map = {e.chunk_id: e.score for e in expanded}
            for h in hydrated:
                h["score"] = score_map.get(h["chunk_id"], 0.01)
                fused.append(h)
            fused.sort(key=lambda x: x["score"], reverse=True)

    seen_pages: dict[int, int] = {}
    out = []
    for r in fused:
        pid = r["page_id"]
        seen_pages[pid] = seen_pages.get(pid, 0) + 1
        if seen_pages[pid] > (5 if walk_depth > 0 else 2):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out
