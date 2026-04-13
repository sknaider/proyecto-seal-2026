#!/usr/bin/env python3
"""encode_subgraphs_batch.py — Offline encoder for latent_subgraph_cache.

Pre-encodes BFS subgraphs (k=2) around every memory.id using the current LoRA
adapter and upserts into latent_subgraph_cache with a MODEL_VERSION tag.

Root cause #2 of LatentGraphMem V1 rescue: serve-time _rerank does N live
forward passes per query (~3000ms). With this cache, query-time becomes
1 forward + pgvector ivfflat search (~20ms). 150x speedup.

Usage:
  python3 encode_subgraphs_batch.py --model-version e5-base-lora-v1-20260413-prereclass
  python3 encode_subgraphs_batch.py --model-version <v> --only-missing
  python3 encode_subgraphs_batch.py --model-version <v> --limit 100  # smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import asyncpg
from neo4j import AsyncGraphDatabase

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from latent_graphmem.data import serialize_subgraph  # noqa: E402
from latent_graphmem_build_pairs import _bfs_subgraph  # noqa: E402

PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")
ADAPTER_DIR = Path("/home/dadito/IA/modelos/latent-graphmem-soul-v1/best")
BATCH = 64


def _subgraph_hash(sg: dict) -> str:
    blob = json.dumps(sg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.blake2b(blob, digest_size=8).hexdigest()  # 16 hex chars


def _load_model():
    import torch
    from latent_graphmem.model import BiEncoder
    from peft import PeftModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = BiEncoder(apply_lora=False)
    if ADAPTER_DIR.exists():
        model.encoder = PeftModel.from_pretrained(model.encoder, str(ADAPTER_DIR))
        print(f"[encode] LoRA adapter loaded from {ADAPTER_DIR}")
    else:
        print(f"[encode] WARNING no adapter at {ADAPTER_DIR} — using base e5-base")
    model.to(device).eval()
    return model, device


async def _all_memory_ids(pg: asyncpg.Connection, limit: int | None) -> list[int]:
    q = "SELECT id FROM memories ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = await pg.fetch(q)
    return [r["id"] for r in rows]


async def _existing_ids(pg: asyncpg.Connection, model_version: str) -> set[int]:
    rows = await pg.fetch(
        "SELECT memory_id FROM latent_subgraph_cache WHERE model_version=$1",
        model_version,
    )
    return {r["memory_id"] for r in rows}


async def _build_subgraphs_batch(neo4j_session, mids: list[int]) -> list[dict]:
    # sequential BFS (Neo4j driver async but work is serialized inside)
    out = []
    for m in mids:
        sg = await _bfs_subgraph(neo4j_session, m)
        out.append(sg)
    return out


def _encode_batch(model, device, texts: list[str]):
    import torch
    with torch.no_grad():
        embs = model.encode(texts, device)  # (N, D), already L2-normalized
    return embs.cpu().numpy()


async def _upsert(pg, rows: list[tuple]):
    if not rows:
        return
    await pg.executemany(
        """
        INSERT INTO latent_subgraph_cache
          (memory_id, subgraph_hash, subgraph, serialized_text, embedding, model_version, encoded_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, now(), now())
        ON CONFLICT (memory_id) DO UPDATE SET
          subgraph_hash   = EXCLUDED.subgraph_hash,
          subgraph        = EXCLUDED.subgraph,
          serialized_text = EXCLUDED.serialized_text,
          embedding       = EXCLUDED.embedding,
          model_version   = EXCLUDED.model_version,
          updated_at      = now()
        """,
        rows,
    )


async def main(model_version: str, only_missing: bool, limit: int | None, dry_run: bool):
    t0 = time.perf_counter()
    model, device = _load_model() if not dry_run else (None, None)

    pg = await asyncpg.connect(PG_DSN)
    try:
        all_ids = await _all_memory_ids(pg, limit)
        print(f"[encode] loaded {len(all_ids)} memory ids from pg")

        if only_missing:
            have = await _existing_ids(pg, model_version)
            all_ids = [m for m in all_ids if m not in have]
            print(f"[encode] --only-missing: {len(all_ids)} ids remaining")

        if dry_run:
            print("[encode] DRY-RUN — no Neo4j, no encode, no upsert")
            return

        neo = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        total = 0
        empty = 0
        try:
            async with neo.session() as nsess:
                for i in range(0, len(all_ids), BATCH):
                    batch_ids = all_ids[i : i + BATCH]
                    sgs = await _build_subgraphs_batch(nsess, batch_ids)
                    pairs = [
                        (mid, sg)
                        for mid, sg in zip(batch_ids, sgs)
                        if sg.get("nodes")
                    ]
                    if not pairs:
                        empty += len(batch_ids)
                        continue
                    ids_b = [p[0] for p in pairs]
                    sgs_b = [p[1] for p in pairs]
                    texts = [serialize_subgraph(sg) for sg in sgs_b]
                    embs = _encode_batch(model, device, texts)
                    rows = []
                    for mid, sg, txt, emb in zip(ids_b, sgs_b, texts, embs):
                        emb_str = "[" + ",".join(f"{float(x):.6f}" for x in emb) + "]"
                        rows.append((
                            mid,
                            _subgraph_hash(sg),
                            json.dumps(sg, ensure_ascii=False),
                            txt,
                            emb_str,
                            model_version,
                        ))
                    await _upsert(pg, rows)
                    total += len(rows)
                    empty += len(batch_ids) - len(rows)
                    print(f"  batch {i//BATCH+1}: +{len(rows)} upserted  (total={total}  empty={empty})")
        finally:
            await neo.close()

        print(f"[encode] done total_cached={total}  empty_subgraphs={empty}  model_version={model_version}")
    finally:
        await pg.close()

    dt = time.perf_counter() - t0
    print(f"[encode] wall {dt:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-version", required=True)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.model_version, args.only_missing, args.limit, args.dry_run))
