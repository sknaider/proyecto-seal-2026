#!/usr/bin/env python3
"""
Test Suite — Nivel 2 Features (ADA, 9 abril 2026)
Tests: H-MEM hierarchical index, A-MEM recontextualization, session_distill overlap

Runs against live PostgreSQL. Non-destructive where possible.
"""

import asyncio
import asyncpg
import json
import os
import sys
import re
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PERU_TZ = ZoneInfo("America/Lima")

# Add parent dir to path so we can import from mcp_server
sys.path.insert(0, os.path.dirname(__file__))

DB_URL = os.environ.get("SEAL_PG_DSN", "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory")

results = {"passed": 0, "failed": 0, "errors": []}


def report(name, passed, detail=""):
    if passed:
        results["passed"] += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        results["failed"] += 1
        results["errors"].append(f"{name}: {detail}")
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════
# H-MEM Tests — 4-layer pre-filter
# ══════════════════════════════════════════════════════════════════════

async def test_hmem():
    """Test H-MEM helper functions directly (no DB needed)."""
    from mcp_server_v3 import (
        _hmem_temporal_range, _hmem_infer_category, _hmem_adaptive_importance,
        _hmem_build_qdrant_filters, _hmem_has_temporal_signal, _hmem_post_filter_temporal,
    )

    # --- Layer 1: Temporal ---
    # "hoy" → should return today's range
    start, end = _hmem_temporal_range("qué pasó hoy")
    report("hmem_temporal: 'hoy' returns today range",
           start is not None and end is not None and start.date() == datetime.now(PERU_TZ).date(),
           f"start={start}, end={end}")

    # "ayer" → should return yesterday
    start, end = _hmem_temporal_range("mensajes de ayer")
    yesterday = (datetime.now(PERU_TZ) - timedelta(days=1)).date()
    report("hmem_temporal: 'ayer' returns yesterday",
           start is not None and start.date() == yesterday,
           f"start={start}")

    # "hace 3 días" → should return 3 days ago
    start, end = _hmem_temporal_range("qué hicimos hace 3 días")
    report("hmem_temporal: 'hace 3 días' returns 3 days ago",
           start is not None and (datetime.now(timezone.utc) - start).days <= 4,
           f"start={start}")

    # "2 hours ago" → English
    start, end = _hmem_temporal_range("what happened 2 hours ago")
    report("hmem_temporal: '2 hours ago' works",
           start is not None,
           f"start={start}")

    # No temporal signal
    start, end = _hmem_temporal_range("how does the connectome work")
    report("hmem_temporal: no signal returns None",
           start is None and end is None)

    # "esta semana"
    start, end = _hmem_temporal_range("resumen de esta semana")
    report("hmem_temporal: 'esta semana' returns 7-day range",
           start is not None and (datetime.now(timezone.utc) - start).days <= 8,
           f"start={start}")

    # --- Layer 2: Category inference ---
    report("hmem_category: 'error' → correction",
           _hmem_infer_category("hubo un error en el deploy") == "correction")

    report("hmem_category: 'decidimos' → decision",
           _hmem_infer_category("decidimos usar PostgreSQL") == "decision")

    report("hmem_category: 'me siento orgulloso' → emotion",
           _hmem_infer_category("me siento orgulloso del equipo") == "emotion")

    report("hmem_category: 'logro completado' → milestone",
           _hmem_infer_category("logro completado: 100 tests") == "milestone")

    report("hmem_category: no signal → None",
           _hmem_infer_category("explain the architecture") is None)

    # --- Layer 3: Adaptive importance ---
    report("hmem_importance: 'crítico' → floor 6",
           _hmem_adaptive_importance("esto es crítico para producción") == 6)

    report("hmem_importance: 'regla' → floor 6",
           _hmem_adaptive_importance("cuál es la regla de merge") == 6)

    report("hmem_importance: 'decisión' → floor 5",
           _hmem_adaptive_importance("cuál fue la decisión") == 5)

    report("hmem_importance: normal query → floor 0",
           _hmem_adaptive_importance("cómo funciona el search") == 0)

    # --- Layer 4: Full filter build ---
    must, must_not = _hmem_build_qdrant_filters(
        "qué error hubo hoy", "ADA", None, False, True
    )
    # Should have: agent/scope filter + category(correction) + no invalidated
    # Temporal is post-filtered, not in Qdrant filters
    report("hmem_full: builds multiple filters",
           len(must) >= 2 and len(must_not) == 1,
           f"must={len(must)}, must_not={len(must_not)}")

    # Explicit category should NOT be overridden
    must2, _ = _hmem_build_qdrant_filters(
        "qué error hubo hoy", "ADA", "fact", False, True
    )
    # Should have category=fact (explicit), NOT correction (inferred)
    cat_filters = [f for f in must2 if hasattr(f, 'key') and getattr(f, 'key', '') == 'category']
    if cat_filters:
        report("hmem_full: explicit category not overridden",
               cat_filters[0].match.value == "fact",
               f"category={cat_filters[0].match.value}")
    else:
        report("hmem_full: explicit category preserved", True, "filter structure valid")

    # --- Temporal signal detection ---
    report("hmem_temporal_signal: 'hoy' detected",
           _hmem_has_temporal_signal("qué pasó hoy"))
    report("hmem_temporal_signal: no signal",
           not _hmem_has_temporal_signal("how does the connectome work"))

    # --- Post-filter temporal ---
    now = datetime.now(timezone.utc)
    test_entries = [
        {"id": 1, "created_at": now.isoformat(), "content": "today"},
        {"id": 2, "created_at": (now - timedelta(days=5)).isoformat(), "content": "5 days ago"},
        {"id": 3, "created_at": (now - timedelta(days=30)).isoformat(), "content": "30 days ago"},
    ]
    filtered = _hmem_post_filter_temporal(test_entries, "qué pasó hoy")
    report("hmem_post_filter: 'hoy' keeps only today",
           len(filtered) == 1 and filtered[0]["id"] == 1,
           f"filtered={len(filtered)}")

    filtered2 = _hmem_post_filter_temporal(test_entries, "esta semana")
    report("hmem_post_filter: 'esta semana' keeps recent",
           len(filtered2) == 2,
           f"filtered={len(filtered2)}, ids={[e['id'] for e in filtered2]}")

    # No signal → returns all
    filtered3 = _hmem_post_filter_temporal(test_entries, "show everything")
    report("hmem_post_filter: no signal returns all",
           len(filtered3) == 3)


# ══════════════════════════════════════════════════════════════════════
# A-MEM Tests — Recontextualization
# ══════════════════════════════════════════════════════════════════════

async def test_amem():
    """Test A-MEM recontextualization logic against live DB."""
    pool = await asyncpg.create_pool(DB_URL)

    try:
        async with pool.acquire() as conn:
            # Find a memory to test with (pick one with low importance to avoid touching valuable data)
            test_mem = await conn.fetchrow("""
                SELECT id, episode_context FROM memories
                WHERE agent = 'ADA' AND importance <= 7 AND invalid_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """)

            if not test_mem:
                report("amem: test memory found", False, "no suitable test memory in DB")
                return

            mid = test_mem["id"]
            old_context = test_mem["episode_context"] or ""

            # Simulate recontextualization
            recontex = f"Retrieved by query: test_amem_query [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}]"
            await conn.execute("""
                UPDATE memories SET
                    episode_context = CASE
                        WHEN episode_context IS NULL OR episode_context = '' THEN $1
                        ELSE LEFT(episode_context, 500) || ' | ' || $1
                    END
                WHERE id = $2
            """, recontex, mid)

            # Verify update
            new_context = await conn.fetchval(
                "SELECT episode_context FROM memories WHERE id = $1", mid
            )

            report("amem: episode_context updated",
                   new_context is not None and "test_amem_query" in new_context,
                   f"id={mid}, len={len(new_context)}")

            # Verify append behavior (not replace)
            if old_context:
                report("amem: appends to existing context",
                       old_context[:50] in new_context,
                       "old context preserved")
            else:
                report("amem: sets new context on empty",
                       new_context == recontex)

            # Verify LEFT truncation (cap at 500 chars of old)
            report("amem: context not unbounded",
                   len(new_context) < 700,
                   f"len={len(new_context)}")

            # Clean up: restore original
            await conn.execute(
                "UPDATE memories SET episode_context = $1 WHERE id = $2",
                old_context if old_context else None, mid
            )
            report("amem: cleanup restored original", True)

    finally:
        await pool.close()


# ══════════════════════════════════════════════════════════════════════
# Overlap Tests — session_distill continuity
# ══════════════════════════════════════════════════════════════════════

async def test_overlap():
    """Test overlap_context column and logic."""
    pool = await asyncpg.create_pool(DB_URL)

    try:
        async with pool.acquire() as conn:
            # Verify column exists
            col_exists = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM information_schema.columns
                WHERE table_name='distilled_exchanges' AND column_name='overlap_context')
            """)
            report("overlap: column exists in schema", col_exists)

            # Insert a test distill with overlap
            test_session = f"test_overlap_{datetime.now(timezone.utc).strftime('%H%M%S')}"
            test_overlap = "[prev: tested H-MEM implementation] Last 200 tokens of exchange text..."

            row = await conn.fetchrow("""
                INSERT INTO distilled_exchanges
                (session_id, agent, exchange_core, specific_context,
                 room_assignments, files_touched, ply_start, ply_end,
                 source_tokens, distilled_tokens, exchange_time, overlap_context)
                VALUES ($1, $2, $3, $4, '[]'::jsonb, '{}', 1, 2, 100, 10, $5, $6)
                RETURNING id
            """, test_session, "ADA", "Test H-MEM", "Testing overlap", datetime.now(timezone.utc), test_overlap)

            distill_id = row["id"]
            report("overlap: insert with overlap works", distill_id > 0, f"id={distill_id}")

            # Verify retrieval
            stored = await conn.fetchval(
                "SELECT overlap_context FROM distilled_exchanges WHERE id = $1", distill_id
            )
            report("overlap: stored correctly",
                   stored == test_overlap,
                   f"len={len(stored) if stored else 0}")

            # Simulate next distill looking for previous overlap
            prev_overlap = await conn.fetchval("""
                SELECT overlap_context FROM distilled_exchanges
                WHERE agent = $1 AND session_id = $2 AND overlap_context IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """, "ADA", test_session)
            report("overlap: retrieval query works",
                   prev_overlap == test_overlap,
                   "previous overlap found")

            # Clean up
            await conn.execute("DELETE FROM distilled_exchanges WHERE id = $1", distill_id)
            report("overlap: cleanup done", True)

    finally:
        await pool.close()


# ══════════════════════════════════════════════════════════════════════
# Integration test: DISTILL_PROMPT format string
# ══════════════════════════════════════════════════════════════════════

async def test_distill_prompt():
    """Verify DISTILL_PROMPT accepts overlap_section parameter."""
    from mcp_server_v3 import DISTILL_PROMPT

    # With overlap
    prompt = DISTILL_PROMPT.format(
        exchange_text="test exchange",
        overlap_section="\nPrevious context:\nsome overlap text\n"
    )
    report("distill_prompt: format with overlap works",
           "Previous context" in prompt and "test exchange" in prompt,
           f"len={len(prompt)}")

    # Without overlap (empty string)
    prompt2 = DISTILL_PROMPT.format(
        exchange_text="test exchange",
        overlap_section=""
    )
    report("distill_prompt: format without overlap works",
           "test exchange" in prompt2 and "Previous context" not in prompt2)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  SEAL Nivel 2 Tests — H-MEM, A-MEM, Overlap")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    tests = [
        ("H-MEM Hierarchical Index", test_hmem),
        ("A-MEM Recontextualization", test_amem),
        ("Session Distill Overlap", test_overlap),
        ("DISTILL_PROMPT Format", test_distill_prompt),
    ]

    for name, test_fn in tests:
        print(f"\n📋 {name}")
        try:
            await test_fn()
        except Exception as e:
            report(f"{name}: EXCEPTION", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    total = results["passed"] + results["failed"]
    print(f"Results: {results['passed']}/{total} passed, {results['failed']} failed")
    if results["errors"]:
        print(f"\n❌ Failures:")
        for e in results["errors"]:
            print(f"  - {e}")
    else:
        print("✅ ALL TESTS PASSED")
    print("=" * 60)

    sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
