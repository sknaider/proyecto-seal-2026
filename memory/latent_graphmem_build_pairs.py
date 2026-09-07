#!/usr/bin/env python3
"""latent_graphmem_build_pairs.py — Step 2 of LatentGraphMem (Track A).

Builds training pairs for the subgraph retriever adapter:
  - Load 64 queries from diagnostic/test_set_v1.jsonl
  - For each query:
      * Positive  = BFS k=2 around expected_memory_ids[0] in Neo4j
      * Negatives = 3 random unrelated memory_ids, BFS k=2 each
  - Subgraph serialized as JSONB: {nodes: [{memory_id, agent, category, content}], edges: [{src, tgt, rel}]}
  - Query embedding via embeddings.get_embedding (768-dim multilingual-e5-base)
  - Insert into latent_graphmem_training_pairs (label 1=positive, 0=negative)

Spec: .specs/tasks/draft/graph-transformer-soul-integration.feature.md (Step 2)
Self-answered blocking questions (2026-04-12):
  Q1: expected_memory_ids populated as ints, verified (3125 exists in Neo4j + pg).
  Q2: Neo4j bolt://localhost:7687 neo4j/seal2026soul ; Postgres localhost:5433 seal_memory
  Q3: Neo4j :Memory nodes keyed by property `memory_id` (not `id`).
  Q4: Ratio 1 pos : 3 neg = 256 pairs mínimo. Expand later via augmentation.
  Q5: Weak-label postponed (Step 2.5). V1 uses only test_set_v1 ground truth.

Edge types in graph: EXCITES, INHIBITS, MENTIONS, CAUSES, INFORMED, OCCURRED_ON (ignored).
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

import asyncpg
from neo4j import AsyncGraphDatabase

from embeddings import get_embedding

# ── Config ──
HERE = Path(__file__).parent
TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"      # BLACKLIST-ONLY (never source)
SYNTHETIC_SET = HERE / "diagnostic" / "synthetic_queries_v1.jsonl"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")
PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
BFS_DEPTH = 2
BFS_MAX_NODES = 32   # cap subgraph size — graph is densely connected (avg degree ~7)
NEG_PER_POS = 3
SEED = 42
# Relationship types to traverse (graph edges, skip temporal scaffolding)
EDGE_TYPES = ["EXCITES", "INHIBITS", "MENTIONS", "CAUSES", "INFORMED"]


async def _fetch_node(session, mid: int) -> dict | None:
    r = await session.run(
        "MATCH (m:Memory {memory_id: $mid}) "
        "RETURN m.memory_id AS memory_id, m.agent AS agent, "
        "m.category AS category, m.content AS content, m.importance AS importance",
        mid=mid,
    )
    rec = await r.single()
    return dict(rec) if rec else None


async def _fetch_neighbors(session, mid: int) -> list[dict]:
    """1-hop neighbors of mid with edge metadata, ranked by edge importance."""
    rel_pattern = "|".join(EDGE_TYPES)
    r = await session.run(
        f"MATCH (m:Memory {{memory_id: $mid}})-[rel:{rel_pattern}]-(n:Memory) "
        "WHERE n.memory_id IS NOT NULL "
        "RETURN DISTINCT n.memory_id AS nid, type(rel) AS rtype, "
        "startNode(rel).memory_id AS src, endNode(rel).memory_id AS tgt, "
        "coalesce(rel.weight, 1.0) AS w "
        "ORDER BY w DESC",
        mid=mid,
    )
    return [dict(rec) async for rec in r]


async def _bfs_subgraph(
    session, seed_id: int, depth: int = BFS_DEPTH, max_nodes: int = BFS_MAX_NODES
) -> dict:
    """Breadth-limited BFS from seed. Cap at max_nodes to avoid flooding
    (graph avg degree ~7+, unbounded BFS pulls near-entire graph).
    """
    seed_node = await _fetch_node(session, seed_id)
    if seed_node is None:
        return {"nodes": [], "edges": []}

    nodes: dict[int, dict] = {seed_id: seed_node}
    edges: list[dict] = []
    edge_keys: set[tuple] = set()
    frontier = [seed_id]

    for _ in range(depth):
        if len(nodes) >= max_nodes:
            break
        next_frontier: list[int] = []
        for mid in frontier:
            if len(nodes) >= max_nodes:
                break
            neigh = await _fetch_neighbors(session, mid)
            for hit in neigh:
                nid = hit["nid"]
                if nid is None:
                    continue
                key = (hit["src"], hit["tgt"], hit["rtype"])
                if key not in edge_keys:
                    edges.append({"src": hit["src"], "tgt": hit["tgt"], "rel": hit["rtype"]})
                    edge_keys.add(key)
                if nid not in nodes:
                    node = await _fetch_node(session, nid)
                    if node is None:
                        continue
                    nodes[nid] = node
                    next_frontier.append(nid)
                    if len(nodes) >= max_nodes:
                        break
        frontier = next_frontier

    return {"nodes": list(nodes.values()), "edges": edges}


async def _sample_negative_seeds(
    session, exclude: set[int], k: int, rng: random.Random
) -> list[int]:
    """Sample k random memory_ids not in exclude set."""
    result = await session.run(
        "MATCH (m:Memory) WHERE NOT m.memory_id IN $exclude "
        "RETURN m.memory_id AS mid",
        exclude=list(exclude),
    )
    candidates = [rec["mid"] async for rec in result if rec["mid"] is not None]
    if len(candidates) < k:
        return candidates
    return rng.sample(candidates, k)


def _load_queries(path: Path) -> list[dict]:
    if path.resolve() == TEST_SET.resolve():
        raise RuntimeError(
            "REFUSED: test_set_v1.jsonl is held-out eval data, NEVER training source. "
            "Pass --source=synthetic or a custom --source-path."
        )
    queries = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            queries.append(json.loads(line))
    return queries


def _load_blacklist_ids() -> set[int]:
    bl: set[int] = set()
    if not TEST_SET.exists():
        return bl
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            for i in (d.get("expected_memory_ids") or []):
                bl.add(int(i))
    return bl


def _subgraph_touches_blacklist(sg: dict, blacklist: set[int]) -> bool:
    for n in sg.get("nodes", []):
        if n.get("memory_id") in blacklist:
            return True
    return False


async def build(
    limit: int | None = None,
    dry_run: bool = False,
    source: str = "synthetic",
    source_path: Path | None = None,
) -> dict:
    rng = random.Random(SEED)
    if source == "synthetic":
        path = source_path or SYNTHETIC_SET
        source_tag = "synthetic_v1"
    else:
        raise RuntimeError(f"unknown source={source!r}. Only 'synthetic' allowed.")
    queries = _load_queries(path)
    if limit:
        queries = queries[:limit]

    blacklist = _load_blacklist_ids()
    print(f"[build_pairs] source={source_tag} path={path.name} "
          f"queries={len(queries)} blacklist_ids={len(blacklist)}")

    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    pg = None if dry_run else await asyncpg.connect(PG_DSN)

    n_pos = 0
    n_neg = 0
    n_skip = 0
    rows_to_insert: list[tuple] = []

    try:
        async with driver.session() as session:
            for q in queries:
                qid = q["id"]
                query_text = q["query"]
                qtype = q.get("query_type", "factual")
                exp_ids = q.get("expected_memory_ids") or []

                if not exp_ids:
                    print(f"[skip] {qid}: no expected_memory_ids")
                    n_skip += 1
                    continue

                seed = int(exp_ids[0])

                # L3 leak guard: refuse seeds that ARE in the blacklist
                if seed in blacklist:
                    print(f"[skip] {qid}: seed {seed} is in test_set blacklist")
                    n_skip += 1
                    continue

                # Positive subgraph
                pos_sg = await _bfs_subgraph(session, seed)
                if not pos_sg["nodes"]:
                    print(f"[skip] {qid}: seed {seed} has no neighbors in Neo4j")
                    n_skip += 1
                    continue

                # L3 leak guard: positive subgraph must not contain blacklisted nodes
                if _subgraph_touches_blacklist(pos_sg, blacklist):
                    print(f"[skip] {qid}: pos BFS touches blacklist")
                    n_skip += 1
                    continue

                # Query embedding (one per query, reused for all its pairs)
                q_emb = await get_embedding(query_text)

                rows_to_insert.append((query_text, q_emb, json.dumps(pos_sg), 1, qtype))
                n_pos += 1

                # Negative subgraphs — exclude expected ids AND blacklist
                neg_exclude = set(int(x) for x in exp_ids) | blacklist
                neg_seeds = await _sample_negative_seeds(
                    session,
                    exclude=neg_exclude,
                    k=NEG_PER_POS * 2,   # oversample in case of L3 rejects
                    rng=rng,
                )
                neg_count_for_q = 0
                for neg_seed in neg_seeds:
                    if neg_count_for_q >= NEG_PER_POS:
                        break
                    neg_sg = await _bfs_subgraph(session, int(neg_seed))
                    if not neg_sg["nodes"]:
                        continue
                    if _subgraph_touches_blacklist(neg_sg, blacklist):
                        continue
                    rows_to_insert.append(
                        (query_text, q_emb, json.dumps(neg_sg), 0, qtype)
                    )
                    n_neg += 1
                    neg_count_for_q += 1

                if n_pos % 10 == 0:
                    print(f"[progress] {n_pos} positives / {n_neg} negatives built")

        if dry_run:
            print(f"[dry_run] would insert {len(rows_to_insert)} rows")
        else:
            # Wipe + bulk insert (idempotent for this test set)
            await pg.execute(
                "DELETE FROM latent_graphmem_training_pairs WHERE source=$1",
                source_tag,
            )
            # pgvector expects string form '[f1,f2,...]'
            formatted = [
                (qt, "[" + ",".join(f"{x:.6f}" for x in emb) + "]", sg, lab, qtp,
                 source_tag)
                for (qt, emb, sg, lab, qtp) in rows_to_insert
            ]
            await pg.executemany(
                "INSERT INTO latent_graphmem_training_pairs "
                "(query, query_emb, subgraph, label, query_type, source) "
                "VALUES ($1, $2::vector, $3::jsonb, $4, $5, $6)",
                formatted,
            )
            print(f"[insert] wrote {len(formatted)} rows")
    finally:
        await driver.close()
        if pg is not None:
            await pg.close()

    report = {
        "queries_processed": len(queries),
        "positives": n_pos,
        "negatives": n_neg,
        "skipped": n_skip,
        "total_rows": n_pos + n_neg,
    }
    print(f"[done] {report}")
    return report


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    lim = None
    source = "synthetic"
    source_path: Path | None = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            lim = int(a.split("=", 1)[1])
        elif a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif a.startswith("--source-path="):
            source_path = Path(a.split("=", 1)[1])
    asyncio.run(build(limit=lim, dry_run=dry, source=source, source_path=source_path))
