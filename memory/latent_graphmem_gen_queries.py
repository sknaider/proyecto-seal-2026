#!/usr/bin/env python3
"""latent_graphmem_gen_queries.py — Synthetic query generator (leak-fix V1.1).

Generates training queries from random SOUL memories via qwen2.5:7b (Ollama),
guaranteeing ZERO overlap with diagnostic/test_set_v1.jsonl.

Protocol:
  1. Load test_set_v1 as BLACKLIST (18 memory_ids + 64 query strings).
  2. Sample N random memories from postgres where id NOT IN blacklist AND
     BFS k=2 around the memory does not touch any blacklisted id (L3 check).
  3. For each memory, ask qwen2.5:7b for 2 natural-language questions whose
     answer lives in that memory.
  4. Filter generated questions:
        - lower-cased exact match vs any test query  → drop
        - token Jaccard >0.6 vs any test query       → drop
        - length <15 chars                           → drop
        - contains 'seal', 'test', 'placeholder'     → drop (model artifacts)
  5. Write diagnostic/synthetic_queries_v1.jsonl with schema:
        {id, query, expected_memory_ids:[src_id], query_type:"synthetic"}

Usage:
  python3 latent_graphmem_gen_queries.py --target 200 [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

import asyncpg
import httpx
from neo4j import AsyncGraphDatabase

HERE = Path(__file__).parent
TEST_SET = HERE / "diagnostic" / "test_set_v1.jsonl"
OUT_FILE = HERE / "diagnostic" / "synthetic_queries_v1.jsonl"
PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
BFS_DEPTH = 2
BFS_MAX_NODES = 32
EDGE_TYPES = ["EXCITES", "INHIBITS", "MENTIONS", "CAUSES", "INFORMED"]
SEED = 1337

GEN_PROMPT = """Tengo un fragmento de memoria de un sistema multi-agente. Genera exactamente 2 preguntas naturales en español que un humano haría cuyo contenido esté en este fragmento. Sin numeración, una pregunta por línea, sin comentarios, sin "Pregunta:", sin guiones.

Fragmento:
---
{content}
---

Preguntas:"""


def _tokenize(s: str) -> set[str]:
    return set(re.findall(r"[a-záéíóúñü0-9]+", s.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _load_blacklist() -> tuple[set[int], list[str], list[set[str]]]:
    ids: set[int] = set()
    queries: list[str] = []
    with TEST_SET.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            queries.append(d["query"].strip().lower())
            for i in (d.get("expected_memory_ids") or []):
                ids.add(int(i))
    tokens = [_tokenize(q) for q in queries]
    return ids, queries, tokens


async def _bfs_touches_blacklist(session, mid: int, blacklist: set[int]) -> bool:
    """Return True if BFS k=BFS_DEPTH around mid touches any blacklisted id."""
    rel_pattern = "|".join(EDGE_TYPES)
    r = await session.run(
        f"MATCH (m:Memory {{memory_id: $mid}})-[:{rel_pattern}*1..{BFS_DEPTH}]-(n:Memory) "
        "WHERE n.memory_id IN $bl RETURN count(n) AS c",
        mid=mid, bl=list(blacklist),
    )
    rec = await r.single()
    return bool(rec and rec["c"] > 0)


async def _sample_candidates(pg: asyncpg.Connection, blacklist: set[int], n: int, rng: random.Random) -> list[dict]:
    rows = await pg.fetch(
        "SELECT id, content FROM memories "
        "WHERE content IS NOT NULL AND length(content) > 60 "
        "AND id <> ALL($1::int[])",
        list(blacklist),
    )
    rng.shuffle(rows)
    return [{"id": int(r["id"]), "content": r["content"]} for r in rows[:n]]


async def _gen_questions(content: str, client: httpx.AsyncClient) -> list[str]:
    prompt = GEN_PROMPT.format(content=content[:1500])
    try:
        r = await client.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.8, "num_predict": 180}},
            timeout=90.0,
        )
        r.raise_for_status()
        txt = r.json().get("response", "")
    except Exception as e:
        print(f"  [ollama err] {type(e).__name__}: {e}", file=sys.stderr)
        return []
    lines = [ln.strip(" -•*\t").strip() for ln in txt.splitlines()]
    out = [ln.rstrip("?") + "?" if not ln.endswith("?") else ln
           for ln in lines if len(ln) >= 15 and "?" in ln or (ln and len(ln) >= 15)]
    # Keep only lines ending with ? or long enough to be a question
    out = [ln for ln in out if ln.endswith("?")]
    return out[:2]


def _passes_filter(q: str, bl_queries: list[str], bl_tokens: list[set[str]]) -> bool:
    qs = q.strip().lower()
    if len(qs) < 15:
        return False
    bad_words = ("seal", "test", "placeholder", "fragmento", "contexto dado")
    if any(w in qs for w in bad_words):
        return False
    if qs in bl_queries:
        return False
    qt = _tokenize(qs)
    for bt in bl_tokens:
        if _jaccard(qt, bt) > 0.6:
            return False
    return True


async def main(target: int, dry_run: bool, oversample: int = 4) -> int:
    rng = random.Random(SEED)
    blacklist, bl_queries, bl_tokens = _load_blacklist()
    print(f"[blacklist] {len(blacklist)} memory_ids, {len(bl_queries)} queries")

    pg = await asyncpg.connect(PG_DSN)
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    need = target
    accepted: list[dict] = []
    rejected_overlap = 0
    rejected_l3 = 0
    rejected_format = 0

    sample_size = target * oversample
    candidates = await _sample_candidates(pg, blacklist, sample_size, rng)
    print(f"[sample] {len(candidates)} candidate memories pulled")

    # Pre-filter L3 with Neo4j
    print("[L3] checking BFS leak for each candidate...")
    clean: list[dict] = []
    async with driver.session() as session:
        for i, c in enumerate(candidates):
            if await _bfs_touches_blacklist(session, c["id"], blacklist):
                rejected_l3 += 1
            else:
                clean.append(c)
            if (i + 1) % 50 == 0:
                print(f"  [L3 progress] {i+1}/{len(candidates)} — clean={len(clean)} rej={rejected_l3}")
    print(f"[L3] {len(clean)} clean / {rejected_l3} leak-rejected")

    async with httpx.AsyncClient() as client:
        t0 = time.perf_counter()
        for i, mem in enumerate(clean):
            if len(accepted) >= need:
                break
            questions = await _gen_questions(mem["content"], client)
            if not questions:
                rejected_format += 1
                continue
            for q in questions:
                if len(accepted) >= need:
                    break
                if not _passes_filter(q, bl_queries, bl_tokens):
                    rejected_overlap += 1
                    continue
                qid = "syn_" + hashlib.sha1(
                    f"{mem['id']}::{q}".encode()
                ).hexdigest()[:10]
                accepted.append({
                    "id": qid,
                    "query": q,
                    "expected_memory_ids": [mem["id"]],
                    "query_type": "synthetic",
                })
            if (i + 1) % 20 == 0:
                dt = time.perf_counter() - t0
                print(f"  [gen] {i+1}/{len(clean)} mems · accepted={len(accepted)} · {dt:.0f}s")

    print(f"\n[result] accepted={len(accepted)} "
          f"rej_overlap={rejected_overlap} rej_l3={rejected_l3} rej_format={rejected_format}")

    if dry_run:
        print("[dry_run] not writing")
        for a in accepted[:5]:
            print(" ", a)
    else:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with OUT_FILE.open("w") as f:
            for a in accepted:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        print(f"[write] {OUT_FILE} ({len(accepted)} rows)")

    await driver.close()
    await pg.close()
    return 0 if accepted else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=200)
    ap.add_argument("--oversample", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.target, args.dry_run, args.oversample)))
