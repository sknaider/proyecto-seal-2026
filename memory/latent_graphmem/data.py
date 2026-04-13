"""Data loading for LatentGraphMem training.

Loads pairs from `latent_graphmem_training_pairs`, groups by query (each
query has 1 positive + 3 negatives), splits at query level (no leakage),
and emits batches of shape (B, K=4) where index 0 is the positive.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Iterator

import asyncpg

PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
MAX_TOKENS = 512  # e5-base limit


@dataclass
class QueryGroup:
    query: str
    query_type: str
    positive_subgraph: dict          # {nodes, edges}
    negative_subgraphs: list[dict]   # length 3


def serialize_subgraph(sg: dict, max_chars: int = 2000) -> str:
    """Serialize {nodes, edges} → linear text for the encoder.
    [SUB] n1 [SEP] n2 [SEP] ... [SEP] edge: n_i -[REL]-> n_j [SEP] ...
    """
    parts: list[str] = ["[SUB]"]
    total = 5
    nodes = sg.get("nodes", []) or []
    edges = sg.get("edges", []) or []
    for n in nodes:
        t = str(n.get("content") or "")[:200]
        if not t:
            continue
        chunk = f" {t} [SEP]"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    for e in edges:
        chunk = f" edge: {e.get('src')} -[{e.get('rel')}]-> {e.get('tgt')} [SEP]"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def serialize_query(q: str) -> str:
    # e5 convention: "query: " prefix for the query side.
    return f"query: {q.strip()}"


async def load_query_groups(dsn: str = PG_DSN, source: str = "synthetic_v1") -> list[QueryGroup]:
    """Fetch all pairs for a given source, group by query_text.

    V1.1 leak-fix: NEVER load source='test_set_v1' — that is held-out eval data.
    Default source is 'synthetic_v1'. Groups are kept if they have 1 pos + ≥1 neg
    (negatives capped at 3 per group to match InfoNCE K=4 shape).
    """
    if source == "test_set_v1":
        raise RuntimeError(
            "REFUSED: test_set_v1 is held-out eval — never train on it."
        )
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT query, query_type, subgraph, label "
            "FROM latent_graphmem_training_pairs "
            "WHERE source=$1 "
            "ORDER BY query, label DESC, id",
            source,
        )
    finally:
        await conn.close()

    by_q: dict[str, dict] = {}
    for r in rows:
        q = r["query"]
        sg = r["subgraph"]
        if isinstance(sg, str):
            sg = json.loads(sg)
        entry = by_q.setdefault(q, {"type": r["query_type"], "pos": None, "neg": []})
        if r["label"] == 1 and entry["pos"] is None:
            entry["pos"] = sg
        elif r["label"] == 0:
            entry["neg"].append(sg)

    groups: list[QueryGroup] = []
    for q, e in by_q.items():
        if e["pos"] is None or len(e["neg"]) < 3:
            continue
        groups.append(QueryGroup(
            query=q,
            query_type=e["type"] or "factual",
            positive_subgraph=e["pos"],
            negative_subgraphs=e["neg"][:3],
        ))
    return groups


def split_train_val(
    groups: list[QueryGroup], val_ratio: float = 0.20, seed: int = 42
) -> tuple[list[QueryGroup], list[QueryGroup]]:
    rng = random.Random(seed)
    shuffled = groups[:]
    rng.shuffle(shuffled)
    n_val = max(1, int(round(len(shuffled) * val_ratio)))
    return shuffled[n_val:], shuffled[:n_val]


def iter_batches(
    groups: list[QueryGroup], batch_size: int, shuffle: bool = True, seed: int = 0
) -> Iterator[list[QueryGroup]]:
    order = list(range(len(groups)))
    if shuffle:
        random.Random(seed).shuffle(order)
    for i in range(0, len(order), batch_size):
        batch = [groups[j] for j in order[i : i + batch_size]]
        if batch:
            yield batch
