"""Cathedral II Layer 7 — two-pass structural retrieval.

Given an anchor set of chunk IDs with scores, walk cgraph_edges_chunk +
cgraph_edges_symbol up to MAX_WALK_DEPTH hops and collect structural
neighbors. Score = anchor_score * 1/(1+hop).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import asyncpg

MAX_WALK_DEPTH = 2
NEIGHBOR_CAP = 50


@dataclass
class ChunkScore:
    chunk_id: int
    score: float
    hop: int
    source: str = "anchor"  # "anchor" | "neighbor"


async def expand_anchors(
    conn: asyncpg.Connection,
    anchors: list[ChunkScore],
    walk_depth: int = 0,
    near_symbol: Optional[str] = None,
) -> list[ChunkScore]:
    depth = max(0, min(walk_depth, MAX_WALK_DEPTH))
    seen: dict[int, ChunkScore] = {}

    for a in anchors:
        seen[a.chunk_id] = ChunkScore(a.chunk_id, a.score, 0, "anchor")

    if near_symbol:
        rows = await conn.fetch(
            "SELECT id FROM cgraph_chunks WHERE symbol_name_qualified = $1 LIMIT 50",
            near_symbol,
        )
        base_score = anchors[0].score if anchors else 1.0
        for r in rows:
            if r["id"] not in seen:
                seen[r["id"]] = ChunkScore(r["id"], base_score, 0, "anchor")

    if depth == 0 and not near_symbol:
        return list(seen.values())

    frontier = [cid for cid, c in seen.items() if c.hop == 0]

    for hop in range(1, depth + 1):
        if not frontier:
            break
        next_frontier: set[int] = set()
        decay = 1.0 / (1 + hop)

        for chunk_id in frontier:
            current = seen.get(chunk_id)
            if current is None:
                continue

            # Resolved edges: both endpoints are known chunk IDs
            resolved = await conn.fetch(
                """
                SELECT to_chunk_id   AS neighbor_id FROM cgraph_edges_chunk WHERE from_chunk_id = $1
                UNION
                SELECT from_chunk_id AS neighbor_id FROM cgraph_edges_chunk WHERE to_chunk_id   = $1
                LIMIT $2
                """,
                chunk_id, NEIGHBOR_CAP,
            )
            direct_ids = [r["neighbor_id"] for r in resolved]

            # Unresolved edges: look up chunks by symbol_name_qualified
            unresolved = await conn.fetch(
                "SELECT to_symbol FROM cgraph_edges_symbol WHERE from_chunk_id = $1 LIMIT $2",
                chunk_id, NEIGHBOR_CAP,
            )
            if unresolved:
                targets = [r["to_symbol"] for r in unresolved]
                sym_rows = await conn.fetch(
                    "SELECT id FROM cgraph_chunks WHERE symbol_name_qualified = ANY($1::text[]) LIMIT $2",
                    targets, NEIGHBOR_CAP,
                )
                direct_ids.extend(r["id"] for r in sym_rows)

            for tid in direct_ids:
                if tid in seen:
                    continue
                nb_score = current.score * decay
                seen[tid] = ChunkScore(tid, nb_score, hop, "neighbor")
                next_frontier.add(tid)

        frontier = list(next_frontier)

    return list(seen.values())


async def hydrate_chunks(conn: asyncpg.Connection, chunk_ids: list[int]) -> list[dict]:
    if not chunk_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT
            cc.id AS chunk_id,
            cc.chunk_index,
            cc.chunk_text,
            cc.symbol_name,
            cc.symbol_name_qualified,
            cc.start_line,
            cc.end_line,
            cc.language,
            p.file_path,
            p.id AS page_id
        FROM cgraph_chunks cc
        JOIN cgraph_pages p ON p.id = cc.page_id
        WHERE cc.id = ANY($1::int[])
        """,
        chunk_ids,
    )
    return [dict(r) for r in rows]
