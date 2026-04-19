#!/usr/bin/env python3
"""
SEAL Token Efficiency Benchmark
Measures token savings from SOUL's active_recall vs naive full-memory dump.

Methodology:
  - Naive: dump ALL memories for agent into context (what you'd do without SOUL)
  - SOUL active_recall: top corrections + instincts + rules (what we actually inject)
  - SOUL hybrid_search: top-K semantically relevant memories per query

Metric: tokens saved = (naive - SOUL) / naive * 100%
"""

import asyncio
import asyncpg
import json
import os
import sys
import time
import tiktoken

from config import settings

DB_URL = settings.pg_dsn

# Use cl100k_base (Claude/GPT-4 tokenizer)
enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))


# ── Representative queries from real agent sessions ──
BENCHMARK_QUERIES = [
    "¿Cuál es el estado del sistema SOUL?",
    "Necesito implementar una nueva feature en el MCP server",
    "¿Cómo va el entrenamiento de MedGemma?",
    "William me preguntó sobre el progreso del connectome",
    "Hay un bug en la búsqueda híbrida, necesito debuggear",
    "¿Cuál es nuestra estrategia de medical AI?",
    "Revisa el estado de los servicios y reporta",
    "JARVIS necesita feedback sobre el diseño de rate limiting",
    "¿Qué decisiones importantes tomamos la semana pasada?",
    "Implementa la invalidación automática de facts obsoletos",
]

AGENT = "ADA"
HYBRID_K = 5  # memories returned by hybrid search per query


async def run_benchmark():
    conn = await asyncpg.connect(DB_URL)

    print("=" * 65)
    print(f"SEAL Token Efficiency Benchmark — {AGENT}")
    print("=" * 65)

    # ── 1. Naive: all memories for agent ──
    all_memories = await conn.fetch("""
        SELECT content, category, importance
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
        ORDER BY importance DESC, created_at DESC
    """, AGENT)

    naive_dump = "\n".join(
        f"[{r['category']}|imp={r['importance']}] {r['content']}"
        for r in all_memories
    )
    naive_tokens = count_tokens(naive_dump)
    print(f"\n📦 Naive dump: {len(all_memories)} memories = {naive_tokens:,} tokens")

    # ── 2. SOUL active_recall output (fixed per session) ──
    # Simulate what active_recall returns
    corrections = await conn.fetch("""
        SELECT content FROM memories
        WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
        ORDER BY importance DESC, created_at DESC LIMIT 5
    """, AGENT)

    instincts = await conn.fetch("""
        SELECT trigger_pattern, response, confidence
        FROM instincts
        WHERE agent = $1 AND active = true AND confidence >= 0.7
        ORDER BY confidence DESC LIMIT 3
    """, AGENT)

    rules = await conn.fetch("""
        SELECT rule_key, content FROM rules
        WHERE active = true AND LOWER(priority) IN ('critical', 'high')
        ORDER BY CASE LOWER(priority) WHEN 'critical' THEN 0 ELSE 1 END,
                 created_at DESC LIMIT 5
    """)

    recall_parts = []
    if corrections:
        recall_parts.append("🔴 CORRECCIONES RECIENTES:\n" + "\n".join(
            f"  - {c['content'][:200]}" for c in corrections))
    if instincts:
        recall_parts.append("⚡ INSTINTOS ACTIVOS:\n" + "\n".join(
            f"  - [{i['confidence']:.2f}] {i['trigger_pattern'][:80]} → {i['response'][:100]}"
            for i in instincts))
    if rules:
        recall_parts.append("📋 REGLAS ACTIVAS:\n" + "\n".join(
            f"  - {r['rule_key']}: {r['content'][:100]}" for r in rules))

    recall_text = "\n".join(recall_parts)
    recall_tokens = count_tokens(recall_text)

    # ── 3. Hybrid search: per-query top-K ──
    # Use PG full-text as proxy (no Qdrant needed for token counting)
    print(f"\n🔍 Running hybrid search for {len(BENCHMARK_QUERIES)} queries...")

    query_results = []
    for query in BENCHMARK_QUERIES:
        rows = await conn.fetch("""
            SELECT content, category, importance,
                   ts_rank(to_tsvector('spanish', content),
                           plainto_tsquery('spanish', $2)) AS rank
            FROM memories
            WHERE agent = $1 AND invalid_at IS NULL
              AND to_tsvector('spanish', content) @@ plainto_tsquery('spanish', $2)
            ORDER BY rank DESC, importance DESC
            LIMIT $3
        """, AGENT, query, HYBRID_K)

        if not rows:
            # Fallback: recency
            rows = await conn.fetch("""
                SELECT content, category, importance
                FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                ORDER BY importance DESC, created_at DESC LIMIT $2
            """, AGENT, HYBRID_K)

        context = "\n".join(
            f"[{r['category']}] {r['content']}" for r in rows
        )
        query_results.append({
            "query": query,
            "memories_returned": len(rows),
            "tokens": count_tokens(recall_text + "\n" + context)
        })

    avg_hybrid_tokens = sum(r["tokens"] for r in query_results) / len(query_results)
    avg_memories = sum(r["memories_returned"] for r in query_results) / len(query_results)

    await conn.close()

    # ── Results ──
    savings_recall = (naive_tokens - recall_tokens) / naive_tokens * 100
    savings_hybrid = (naive_tokens - avg_hybrid_tokens) / naive_tokens * 100

    print(f"\n{'─' * 65}")
    print(f"{'Mode':<30} {'Tokens':>10} {'Savings':>10} {'Memories':>10}")
    print(f"{'─' * 65}")
    print(f"{'Naive (full dump)':<30} {naive_tokens:>10,} {'—':>10} {len(all_memories):>10}")
    print(f"{'SOUL active_recall (fixed)':<30} {recall_tokens:>10,} {savings_recall:>9.1f}% {'corrections+rules':>10}")
    print(f"{'SOUL hybrid (query-aware)':<30} {int(avg_hybrid_tokens):>10,} {savings_hybrid:>9.1f}% {avg_memories:>10.1f}")
    print(f"{'─' * 65}")

    print(f"\n📊 Summary:")
    print(f"  • Total memories in SOUL: {len(all_memories):,}")
    print(f"  • Naive context window cost: {naive_tokens:,} tokens/query")
    print(f"  • SOUL active_recall: {recall_tokens:,} tokens (always-on context)")
    print(f"  • SOUL hybrid search: ~{int(avg_hybrid_tokens):,} tokens avg/query")
    print(f"  • Token reduction vs naive: {savings_hybrid:.0f}% (hybrid) / {savings_recall:.0f}% (recall only)")
    print(f"  • At $15/1M tokens (Claude Sonnet): saves ~${naive_tokens * 0.000015:.4f}/query")
    print(f"    → At 1,000 queries/day: ~${naive_tokens * 0.000015 * 1000:.2f}/day saved")
    print(f"    → At 30,000 queries/month: ~${naive_tokens * 0.000015 * 30000:.2f}/month saved")

    print(f"\n{'=' * 65}")

    return {
        "naive_tokens": naive_tokens,
        "recall_tokens": recall_tokens,
        "avg_hybrid_tokens": int(avg_hybrid_tokens),
        "savings_recall_pct": round(savings_recall, 1),
        "savings_hybrid_pct": round(savings_hybrid, 1),
        "total_memories": len(all_memories),
    }


if __name__ == "__main__":
    results = asyncio.run(run_benchmark())
