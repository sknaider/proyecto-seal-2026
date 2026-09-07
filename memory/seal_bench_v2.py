#!/usr/bin/env python3
"""SEAL-Bench v2 — Native integrated benchmark.

Improvements over v1 standalone:
- asyncio.gather(): 6 categories run in parallel
- Results persisted to soul_v3.bench_runs + bench_results
- Advisory lock: prevents concurrent runs
- MCP-compatible interface (return JSON dict)

Backwards-compatible CLI:
    python3 seal_bench_v2.py                # run all, persist
    python3 seal_bench_v2.py --category 1   # single category
    python3 seal_bench_v2.py --json         # JSON output
    python3 seal_bench_v2.py --dry-run      # no DB persist
    python3 seal_bench_v2.py --history      # show last 5 runs
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncpg
from db import get_pool
from seal_bench import (
    BenchReport, CategoryResult, TestResult,
    _setup_bench_identity, _cleanup_bench_data,
    cat1_personality_persistence,
    cat2_emotional_memory,
    cat3_instinct_formation,
    cat4_temporal_belief,
    cat5_agent_isolation,
    cat6_reasoning_traces,
)

ADVISORY_LOCK_KEY = 99999


async def _persist_run(conn: asyncpg.Connection, report: BenchReport,
                       elapsed_ms: int, triggered_by: str) -> int:
    git_commit = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            git_commit = result.stdout.strip()
    except Exception:
        pass

    total = sum(c.total for c in report.categories)
    passed = sum(c.passed for c in report.categories)
    failed = total - passed
    all_scores = [t.score for c in report.categories for t in c.tests]
    score_avg = sum(all_scores) / len(all_scores) if all_scores else 0.0

    run_id = await conn.fetchval("""
        INSERT INTO soul_v3.bench_runs
            (triggered_by, total_tests, passed, failed, score_avg, elapsed_ms, git_commit)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
    """, triggered_by, total, passed, failed, round(score_avg, 2), elapsed_ms, git_commit)

    rows = []
    for cat in report.categories:
        for t in cat.tests:
            rows.append((
                run_id, cat.name, t.name, t.passed,
                round(t.score, 1), int(t.elapsed_ms),
                t.detail[:500] if t.detail else None,
                t.error[:500] if t.error else None,
            ))

    await conn.executemany("""
        INSERT INTO soul_v3.bench_results
            (run_id, category, test_name, passed, score, elapsed_ms, detail, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, rows)

    return run_id


async def run_bench_v2(
    categories: list[int] | None = None,
    cleanup: bool = True,
    persist: bool = True,
    triggered_by: str = "manual",
) -> dict:
    t0 = time.monotonic()
    report = BenchReport(timestamp=datetime.now(timezone.utc).isoformat())
    pool = await get_pool()

    bench_pool = pool

    try:
        async with bench_pool.acquire() as lock_conn:
            acquired = await lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY)
            if not acquired:
                return {"status": "skipped", "reason": "another bench run in progress"}
            try:
                return await _run_parallel(
                    pool, bench_pool, report, categories=categories,
                    cleanup=cleanup, persist=persist,
                    triggered_by=triggered_by, t0=t0)
            finally:
                await lock_conn.execute(
                    "SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
    finally:
        pass


async def _ensure_bench_agents(pool) -> None:
    """Insert BENCH_ALPHA/BETA into agents table (FK required by identity)."""
    for agent in ("BENCH_ALPHA", "BENCH_BETA"):
        await pool.execute("""
            INSERT INTO soul_v3.agents (name, role)
            VALUES ($1, 'benchmark')
            ON CONFLICT (name) DO NOTHING
        """, agent)


async def _run_parallel(pool, bench_pool, report: BenchReport, *,
                        categories, cleanup, persist, triggered_by, t0) -> dict:
    await _ensure_bench_agents(pool)
    await _setup_bench_identity(pool)

    all_funcs = {
        1: cat1_personality_persistence,
        2: cat2_emotional_memory,
        3: cat3_instinct_formation,
        4: cat4_temporal_belief,
        5: cat5_agent_isolation,
        6: cat6_reasoning_traces,
    }

    to_run = {k: v for k, v in all_funcs.items()
              if categories is None or k in categories}

    cat_results = await asyncio.gather(
        *[func() for func in to_run.values()],
        return_exceptions=True,
    )

    for cat_num, result in zip(to_run.keys(), cat_results):
        if isinstance(result, Exception):
            report.categories.append(CategoryResult(
                name=f"Category {cat_num} (ERROR)",
                tests=[TestResult(
                    name="Category failed", category=f"Category {cat_num}",
                    passed=False, score=0.0, elapsed_ms=0.0,
                    error=str(result),
                )],
            ))
        else:
            report.categories.append(result)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    run_id = None
    if persist and categories is None:
        async with bench_pool.acquire() as conn:
            run_id = await _persist_run(conn, report, elapsed_ms, triggered_by)

    if cleanup:
        await _cleanup_bench_data(pool)
        for agent in ("BENCH_ALPHA", "BENCH_BETA"):
            await pool.execute(
                "DELETE FROM soul_v3.agents WHERE name = $1", agent)

    total = sum(c.total for c in report.categories)
    passed = sum(c.passed for c in report.categories)
    all_scores = [t.score for c in report.categories for t in c.tests]
    score_avg = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

    return {
        "status": "completed",
        "run_id": run_id,
        "total_tests": total,
        "passed": passed,
        "failed": total - passed,
        "score_avg": score_avg,
        "total_score": round(report.total_score, 1),
        "grade": report.grade,
        "elapsed_ms": elapsed_ms,
        "categories": [
            {
                "name": c.name,
                "score": round(c.score, 1),
                "passed": c.passed,
                "total": c.total,
            }
            for c in report.categories
        ],
    }


async def get_bench_history(n: int = 5) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, run_at, total_tests, passed, failed, score_avg,
                   elapsed_ms, git_commit, triggered_by
            FROM soul_v3.bench_runs
            ORDER BY id DESC LIMIT $1
        """, n)
    return [dict(r) for r in rows]


async def compare_last_two() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = await conn.fetch("""
            SELECT id, run_at, score_avg, passed, failed FROM soul_v3.bench_runs
            ORDER BY id DESC LIMIT 2
        """)
        if len(runs) < 2:
            return {"error": "Need at least 2 runs to compare"}
        cur, prev = runs[0], runs[1]

        cur_cats = await conn.fetch("""
            SELECT category, AVG(score) as avg_score, COUNT(*) FILTER (WHERE passed) as passed
            FROM soul_v3.bench_results WHERE run_id = $1 GROUP BY category
        """, cur["id"])
        prev_cats = await conn.fetch("""
            SELECT category, AVG(score) as avg_score, COUNT(*) FILTER (WHERE passed) as passed
            FROM soul_v3.bench_results WHERE run_id = $1 GROUP BY category
        """, prev["id"])

    prev_map = {r["category"]: float(r["avg_score"]) for r in prev_cats}
    deltas = [
        {"category": r["category"],
         "current": round(float(r["avg_score"]), 2),
         "previous": round(prev_map.get(r["category"], 0), 2),
         "delta": round(float(r["avg_score"]) - prev_map.get(r["category"], 0), 2)}
        for r in cur_cats
    ]
    return {
        "current_run": dict(cur),
        "previous_run": dict(prev),
        "score_delta": round(float(cur["score_avg"]) - float(prev["score_avg"]), 2),
        "category_deltas": deltas,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL-Bench v2 — native integrated")
    parser.add_argument("--category", "-c", type=int, nargs="+")
    parser.add_argument("--json", "-j", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No persist to DB")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    if args.history:
        history = asyncio.run(get_bench_history())
        for r in history:
            print(f"run_id={r['id']} score={r['score_avg']} "
                  f"passed={r['passed']}/{r['total_tests']} "
                  f"elapsed={r['elapsed_ms']}ms")
        sys.exit(0)

    if args.compare:
        result = asyncio.run(compare_last_two())
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    result = asyncio.run(run_bench_v2(
        categories=args.category,
        cleanup=not args.no_cleanup,
        persist=not args.dry_run,
    ))

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        r = result
        print(f"\n{'='*60}")
        print(f"  SEAL-Bench v2 — RESULTS (elapsed {r.get('elapsed_ms',0)}ms)")
        print(f"{'='*60}")
        for c in r.get("categories", []):
            print(f"  {c['name']}: {c['score']}/100 ({c['passed']}/{c['total']} passed)")
        print(f"  {'─'*40}")
        print(f"  TOTAL: {r.get('total_score', 0)}/600")
        print(f"  GRADE: {r.get('grade', '?')}")
        print(f"  run_id: {r.get('run_id', 'N/A (dry-run)')}")
        print(f"{'='*60}\n")
