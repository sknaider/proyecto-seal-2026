#!/usr/bin/env python3
"""
Regression Test — SOUL Token Savings
Verifica que el ratio de compresión de tokens se mantiene > 90% vs naive full-dump.

Baseline medido (8 abril 2026, sesión ADA):
  - active_recall:  98.5% ahorro
  - hybrid search:  96.2% ahorro

ALARMA si cualquier métrica cae por debajo del threshold.
Corre contra live PostgreSQL — no modifica datos.

Uso:
  python3 test_token_savings.py           # umbral default 90%
  python3 test_token_savings.py --strict  # umbral 95% (baseline -3%)
"""

import asyncio
import asyncpg
import sys
import argparse
import os
from datetime import datetime, timezone

import tiktoken

DB_URL = os.environ.get("SEAL_PG_DSN", "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")
AGENT = "ADA"
HYBRID_K = 5

# Umbrales de regresión
THRESHOLD_DEFAULT = 90.0   # 90% mínimo — alarma si baja de aquí
THRESHOLD_STRICT = 95.0    # 95% — baseline conservador

# Baseline histórico (para detectar drift)
BASELINE_RECALL = 98.5
BASELINE_HYBRID = 96.2

# Queries representativos de sesiones reales de agente
SAMPLE_QUERIES = [
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

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))


results = {"passed": 0, "failed": 0, "errors": []}


def report(name: str, passed: bool, detail: str = ""):
    icon = "✅" if passed else "❌"
    results["passed" if passed else "failed"] += 1
    msg = f"  {icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not passed:
        results["errors"].append(f"{name}: {detail}")


async def measure_naive_tokens(conn) -> tuple[int, int]:
    """Cuenta tokens del dump completo (naive baseline)."""
    rows = await conn.fetch("""
        SELECT content, category, importance
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
        ORDER BY importance DESC, created_at DESC
    """, AGENT)

    if not rows:
        return 0, 0

    dump = "\n".join(
        f"[{r['category']}|imp={r['importance']}] {r['content']}"
        for r in rows
    )
    return count_tokens(dump), len(rows)


async def measure_recall_tokens(conn) -> int:
    """Cuenta tokens del active_recall (contexto fijo por sesión)."""
    corrections = await conn.fetch("""
        SELECT content FROM memories
        WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
        ORDER BY importance DESC, created_at DESC LIMIT 5
    """, AGENT)

    instincts = await conn.fetch("""
        SELECT trigger_condition, action, strength
        FROM instincts
        WHERE agent = $1 AND invalid_at IS NULL AND strength >= 0.7
        ORDER BY strength DESC LIMIT 3
    """, AGENT)

    rules = await conn.fetch("""
        SELECT rule_key, content FROM rules
        WHERE active = true AND priority >= 8
        ORDER BY CASE WHEN priority = 10 THEN 0 ELSE 1 END,
                 created_at DESC LIMIT 5
    """)

    parts = []
    if corrections:
        parts.append("🔴 CORRECCIONES RECIENTES:\n" + "\n".join(
            f"  - {c['content'][:200]}" for c in corrections))
    if instincts:
        parts.append("⚡ INSTINTOS ACTIVOS:\n" + "\n".join(
            f"  - [{i['confidence']:.2f}] {i['trigger_condition'][:80]} → {i['response'][:100]}"
            for i in instincts))
    if rules:
        parts.append("📋 REGLAS ACTIVAS:\n" + "\n".join(
            f"  - {r['rule_key']}: {r['content'][:100]}" for r in rules))

    return count_tokens("\n".join(parts))


async def measure_hybrid_tokens(conn, recall_tokens: int) -> tuple[float, float]:
    """
    Mide tokens promedio de hybrid search por query.
    Incluye active_recall (siempre presente) + top-K por query.
    Retorna (avg_tokens, avg_memories_returned).
    """
    query_tokens = []
    memories_returned = []

    for query in SAMPLE_QUERIES:
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
            rows = await conn.fetch("""
                SELECT content, category, importance
                FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                ORDER BY importance DESC, created_at DESC LIMIT $2
            """, AGENT, HYBRID_K)

        context = "\n".join(f"[{r['category']}] {r['content']}" for r in rows)
        # Total tokens = recall (fijo) + hybrid context (por query)
        total = recall_tokens + count_tokens(context)
        query_tokens.append(total)
        memories_returned.append(len(rows))

    avg_tokens = sum(query_tokens) / len(query_tokens)
    avg_memories = sum(memories_returned) / len(memories_returned)
    return avg_tokens, avg_memories


async def run_regression_tests(threshold: float):
    print("=" * 65)
    print(f"  SEAL Token Savings — Regression Test")
    print(f"  Agent: {AGENT} | Threshold: {threshold:.0f}% | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 65)

    conn = await asyncpg.connect(DB_URL)

    # ── Medir naive ──
    naive_tokens, total_memories = await measure_naive_tokens(conn)
    if naive_tokens == 0:
        print("  ⚠️  Sin memorias en SOUL — no se puede medir. Skipping.")
        await conn.close()
        return

    # ── Medir recall ──
    recall_tokens = await measure_recall_tokens(conn)

    # ── Medir hybrid ──
    avg_hybrid_tokens, avg_memories = await measure_hybrid_tokens(conn, recall_tokens)

    await conn.close()

    # ── Calcular savings ──
    savings_recall = (naive_tokens - recall_tokens) / naive_tokens * 100
    savings_hybrid = (naive_tokens - avg_hybrid_tokens) / naive_tokens * 100

    # ── Mostrar métricas ──
    print(f"\n  Naive (full dump):    {naive_tokens:>8,} tokens  ({total_memories} memorias)")
    print(f"  SOUL active_recall:   {recall_tokens:>8,} tokens  ({savings_recall:.1f}% ahorro)")
    print(f"  SOUL hybrid avg:      {int(avg_hybrid_tokens):>8,} tokens  ({savings_hybrid:.1f}% ahorro, {avg_memories:.1f} mems/query)")
    print(f"\n  Baseline histórico:   recall={BASELINE_RECALL}%  hybrid={BASELINE_HYBRID}%")
    print()

    # ── Tests de regresión ──
    print("  Tests:")

    # Test 1: active_recall supera umbral
    report(
        "active_recall savings ≥ threshold",
        savings_recall >= threshold,
        f"{savings_recall:.1f}% (umbral {threshold:.0f}%)"
    )

    # Test 2: hybrid search supera umbral
    report(
        "hybrid_search savings ≥ threshold",
        savings_hybrid >= threshold,
        f"{savings_hybrid:.1f}% (umbral {threshold:.0f}%)"
    )

    # Test 3: No hay regresión > 5% vs baseline
    recall_drift = BASELINE_RECALL - savings_recall
    report(
        "active_recall drift ≤ 5% vs baseline",
        recall_drift <= 5.0,
        f"drift={recall_drift:+.1f}% (baseline {BASELINE_RECALL}%)"
    )

    hybrid_drift = BASELINE_HYBRID - savings_hybrid
    report(
        "hybrid_search drift ≤ 5% vs baseline",
        hybrid_drift <= 5.0,
        f"drift={hybrid_drift:+.1f}% (baseline {BASELINE_HYBRID}%)"
    )

    # Test 4: SOUL retorna menos memorias que el dump completo
    report(
        "hybrid returns < 20% of total memories",
        avg_memories < total_memories * 0.2,
        f"{avg_memories:.1f} avg vs {total_memories} total ({avg_memories/total_memories*100:.1f}%)"
    )

    # Test 5: naive_tokens es significativo (sanity check)
    report(
        "naive dump > 1,000 tokens (SOUL tiene datos reales)",
        naive_tokens > 1000,
        f"{naive_tokens:,} tokens"
    )

    # ── Resultado final ──
    print()
    total = results["passed"] + results["failed"]
    if results["failed"] == 0:
        print(f"  ✅ {results['passed']}/{total} tests pasados — Token efficiency OK")
    else:
        print(f"  ❌ {results['failed']}/{total} tests FALLARON")
        print()
        print("  🚨 ALARMA — El ratio de compresión cayó:")
        for e in results["errors"]:
            print(f"    • {e}")
        print()
        print("  Posibles causas:")
        print("    1. Se agregaron muchas memorias de baja calidad (padding)")
        print("    2. El active_recall está retornando más contexto del necesario")
        print("    3. Las memorias crecieron exponencialmente sin compactación")
        print("    4. El D-MEM gate está fallando y dejando pasar todo")

    print("=" * 65)

    return results["failed"] == 0


def main():
    parser = argparse.ArgumentParser(description="SEAL Token Savings Regression Test")
    parser.add_argument("--strict", action="store_true",
                        help=f"Usar umbral estricto de {THRESHOLD_STRICT}% en vez de {THRESHOLD_DEFAULT}%")
    args = parser.parse_args()

    threshold = THRESHOLD_STRICT if args.strict else THRESHOLD_DEFAULT
    passed = asyncio.run(run_regression_tests(threshold))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
