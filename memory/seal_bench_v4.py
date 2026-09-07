#!/usr/bin/env python3
"""SEAL-Bench v4 — harder regression frontier after v3 reached 100%.

v3 is now a regression suite. v4 is intentionally adversarial and should not
score 100 until the next architecture layer exists.
"""
from __future__ import annotations

from seal_secrets import pg_dsn

import argparse
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_pool
from memory_citation_feedback import record_memory_citation_feedback
from reasoning_quality_validator import score_and_update_reasoning_trace
from seal_bench import _search_memories, _store_memory
from soul_memory_sdk_runtime import TenantOverrideError, reject_tenant_override


DB_URL = os.environ.get("SEAL_DB_URL") or pg_dsn(required=True)
ADVISORY_LOCK_KEY_V4 = 100004
BENCH_AGENT_A = "BENCH_V4_ALPHA"
BENCH_AGENT_B = "BENCH_V4_BETA"
BENCH_SOURCE = "seal_bench_v4"


@dataclass
class V4Result:
    category: str
    test_name: str
    score: float
    passed: bool
    elapsed_ms: int
    detail: dict[str, Any]
    error: str | None = None
    critical: bool = False


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


async def _run_probe(
    category: str,
    test_name: str,
    func: Callable[[], Awaitable[tuple[float, dict[str, Any]]]],
    *,
    critical: bool = False,
) -> V4Result:
    t0 = time.monotonic()
    try:
        score, detail = await func()
        score = _clamp_score(score)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return V4Result(
            category=category,
            test_name=test_name,
            score=score,
            passed=score >= 70.0 and not (critical and score < 100.0),
            elapsed_ms=elapsed_ms,
            detail=detail,
            critical=critical,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return V4Result(
            category=category,
            test_name=test_name,
            score=0.0,
            passed=False,
            elapsed_ms=elapsed_ms,
            detail={"traceback_tail": traceback.format_exc()[-900:]},
            error=f"{type(exc).__name__}: {exc}",
            critical=critical,
        )


async def _ensure_bench_agents(pool: Any) -> None:
    for agent in (BENCH_AGENT_A, BENCH_AGENT_B):
        await pool.execute(
            """
            INSERT INTO soul_v3.agents (name, role, active)
            VALUES ($1, 'benchmark_v4', false)
            ON CONFLICT (name) DO NOTHING
            """,
            agent,
        )
        await pool.execute(
            """
            INSERT INTO soul_v3.identity
                (agent, personality, ocean_scores, boot_context, philosophy)
            VALUES ($1, $2::jsonb, $3::jsonb, $4, $5)
            ON CONFLICT (agent) DO UPDATE SET
                personality=$2::jsonb,
                ocean_scores=$3::jsonb,
                boot_context=$4,
                philosophy=$5
            """,
            agent,
            json.dumps({"role": "benchmark_v4", "style": "adversarial-evidence"}),
            json.dumps({"O": 0.75, "C": 0.85, "E": 0.40, "A": 0.60, "N": 0.15}),
            f"{agent} is an isolated SEAL-Bench v4 test identity.",
            "Temporary benchmark identity.",
        )


async def _cleanup_bench_v4(pool: Any) -> None:
    for agent in (BENCH_AGENT_A, BENCH_AGENT_B):
        await pool.execute("DELETE FROM soul_v3.instinct_activations WHERE agent=$1", agent)
        await pool.execute("DELETE FROM soul_v3.instincts WHERE agent=$1", agent)
        await pool.execute("DELETE FROM soul_v3.reasoning_traces WHERE agent=$1", agent)
        await pool.execute("DELETE FROM soul_v3.inner_monologue WHERE agent=$1", agent)
        await pool.execute("DELETE FROM soul_v3.memories WHERE agent=$1 OR source=$2", agent, BENCH_SOURCE)
        await pool.execute("DELETE FROM soul_v3.identity WHERE agent=$1", agent)
        await pool.execute("DELETE FROM soul_v3.agents WHERE name=$1", agent)
    await pool.execute(
        "DELETE FROM soul_v3.memory_retrieval_log WHERE metadata->>'bench_version' = 'v4'"
    )


async def _store_v4_memory(
    pool: Any,
    agent: str,
    content: str,
    *,
    category: str = "fact",
    importance: int = 5,
    scope: str = "private",
    valence: float | None = None,
    arousal: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    meta = {"bench_version": "v4", **(metadata or {})}
    mem_id = await _store_memory(
        pool,
        agent,
        content,
        category=category,
        importance=importance,
        scope=scope,
        valence=valence,
        arousal=arousal,
        metadata=meta,
    )
    await pool.execute("UPDATE soul_v3.memories SET source=$1 WHERE id=$2", BENCH_SOURCE, mem_id)
    return int(mem_id)


async def cat1_adversarial_emotional_retrieval() -> tuple[float, dict[str, Any]]:
    pool = await get_pool()
    emotional_ids: set[int] = set()
    seeds = [
        "William felt proud when ADA admitted uncertainty before touching production",
        "ADA felt relief after NEXUS confirmed tenant isolation evidence",
        "JARVIS felt worried when a benchmark stopped discriminating",
        "ALICE felt frustrated by a UI fix reported without a screenshot",
        "Team SEAL felt trust after evidence arrived before the claim",
    ]
    for idx, content in enumerate(seeds):
        emotional_ids.add(
            await _store_v4_memory(
                pool,
                BENCH_AGENT_A,
                content,
                category="emotion",
                importance=8,
                valence=0.65 if idx in {0, 1, 4} else -0.70,
                arousal=0.8,
            )
        )
    for idx in range(80):
        await _store_v4_memory(
            pool,
            BENCH_AGENT_A,
            (
                f"adversarial emotional keyword stuffing {idx}: proud relief worried "
                f"frustrated trust William ADA memory evidence but this is operational noise"
            ),
            category="fact",
            importance=9,
            valence=0.95,
            arousal=0.95,
            metadata={"adversarial_spoof": True},
        )
    results = await _search_memories(
        pool,
        "emotional memory William proud relief worried frustrated trust evidence",
        BENCH_AGENT_A,
        limit=5,
        scope_aware=False,
    )
    top_ids = [int(r["id"]) for r in results]
    hits = sum(1 for mid in top_ids if mid in emotional_ids)
    spoof_hits = [
        r["id"] for r in results
        if isinstance(r.get("metadata"), dict) and r["metadata"].get("adversarial_spoof")
    ]
    score = hits / 5.0 * 100.0
    return score, {
        "metric": "precision_at_5_under_emotional_keyword_stuffing",
        "hits": hits,
        "top_ids": top_ids,
        "expected_emotional_ids": sorted(emotional_ids),
        "spoof_hits": spoof_hits,
    }


async def cat2_multilingual_implicit_gap() -> tuple[float, dict[str, Any]]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO soul_v3.reasoning_traces
            (agent, task, premises, reasoning, conclusion, outcome, outcome_success, causal_quality_score)
        VALUES ($1,
                'deploy con premisa tácita en español',
                $2::jsonb,
                'Paso 1: el servicio responde. Paso 2: se da por hecho que la ruta de config existe. Paso 3: reinicio. Paso 4: reporto terminado.',
                'El deploy es seguro',
                'synthetic trace: premisa tácita no validada',
                false,
                NULL)
        RETURNING id
        """,
        BENCH_AGENT_A,
        json.dumps(["servicio responde", "comando restart disponible", "William pidió evidencia"]),
    )
    trace_id = int(row["id"])
    async with pool.acquire() as conn:
        report = await score_and_update_reasoning_trace(conn, trace_id)
    fetched = await pool.fetchrow(
        """
        SELECT reasoning, causal_quality_score, outcome_success
        FROM soul_v3.reasoning_traces
        WHERE id=$1
        """,
        trace_id,
    )
    explicit_gap = "gap" in (fetched["reasoning"] or "").lower() or "premisa" in (fetched["reasoning"] or "").lower()
    quality_is_low = fetched["causal_quality_score"] is not None and float(fetched["causal_quality_score"]) <= 0.35
    detected = bool(report.get("missing_premise_detected"))
    score = 0.0
    if detected:
        score += 45.0
    if explicit_gap:
        score += 30.0
    if quality_is_low:
        score += 25.0
    return score, {
        "metric": "implicit_spanish_missing_premise_detection",
        "trace_id": trace_id,
        "missing_premise_detected": detected,
        "explicit_gap_in_reasoning": explicit_gap,
        "quality_score": float(fetched["causal_quality_score"]) if fetched["causal_quality_score"] is not None else None,
        "gap_report": report,
    }


async def cat3_answer_citation_extraction() -> tuple[float, dict[str, Any]]:
    pool = await get_pool()
    memory_a = await _store_v4_memory(
        pool,
        BENCH_AGENT_A,
        "citation extraction v4 evidence memory",
        category="fact",
        importance=7,
    )
    memory_b = await _store_v4_memory(
        pool,
        BENCH_AGENT_A,
        "citation extraction v4 distractor",
        category="fact",
        importance=4,
    )
    async with pool.acquire() as conn:
        log_id = await conn.fetchval(
            """
            INSERT INTO soul_v3.memory_retrieval_log
                (agent_requesting, query_text, tool_used, memory_ids_returned, result_count, metadata)
            VALUES ($1, 'citation extraction v4', 'seal_bench_v4', $2::bigint[], 2, $3::jsonb)
            RETURNING id
            """,
            BENCH_AGENT_A,
            [memory_a, memory_b],
            json.dumps({"bench_version": "v4"}),
        )
        answer_text = f"La evidencia viene de [SOUL id={memory_a}] y no del distractor."
        auto_extractor = getattr(__import__("memory_citation_feedback"), "record_answer_citation_feedback", None)
        helper_exists = callable(auto_extractor)
        if helper_exists:
            maybe_result = auto_extractor(conn, log_id, answer_text=answer_text, actor="SEAL-Bench v4")
            if inspect.isawaitable(maybe_result):
                await maybe_result
        after = await conn.fetchrow(
            """
            SELECT memory_ids_cited, memory_ids_used, citation_precision
            FROM soul_v3.memory_retrieval_log
            WHERE id=$1
            """,
            log_id,
        )
    cited = list(after["memory_ids_cited"])
    used = list(after["memory_ids_used"])
    score = 20.0
    if helper_exists:
        score += 25.0
    if cited == [memory_a]:
        score += 30.0
    if used == [memory_a]:
        score += 15.0
    if after["citation_precision"] == 1.0:
        score += 10.0
    return score, {
        "metric": "extract_cited_memory_ids_from_answer_text",
        "retrieval_log_id": log_id,
        "answer_text_shape": "contains [SOUL id=<returned_id>]",
        "auto_extractor_exists": helper_exists,
        "expected_cited": [memory_a],
        "actual_cited": cited,
        "actual_used": used,
        "citation_precision": float(after["citation_precision"]) if after["citation_precision"] is not None else None,
    }


async def cat4_tenant_override_hardening() -> tuple[float, dict[str, Any]]:
    cases = [
        ("header_dash", {"headers": {"X-Tenant-ID": "bad"}}),
        ("header_underscore", {"headers": {"x_tenant_id": "bad"}}),
        ("query_tenant", {"query": {"tenant_id": "bad"}}),
        ("payload_org", {"payload": {"org_id": "bad"}}),
        ("payload_nested_expected_gap", {"payload": {"metadata": {"tenant_id": "bad"}}}),
    ]
    results = []
    for name, kwargs in cases:
        try:
            reject_tenant_override(**kwargs)
        except TenantOverrideError:
            blocked = True
        else:
            blocked = False
        results.append({"case": name, "blocked": blocked})
    blocked = sum(1 for r in results if r["blocked"])
    return blocked / len(results) * 100.0, {
        "metric": "tenant_override_direct_and_nested_vectors_blocked",
        "blocked": blocked,
        "total": len(results),
        "cases": results,
        "expected_gap": "nested metadata tenant_id should be rejected or scrubbed",
    }


async def cat5_outcome_ledger_worm() -> tuple[float, dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='soul_v3' AND table_name='awareness_closed_loop_outcomes'
            )
            """
        )
        if not table_exists:
            return 0.0, {
                "metric": "outcome_ledger_append_only_external_review",
                "table_exists": False,
                "expected": "soul_v3.awareness_closed_loop_outcomes",
            }
        cols = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='soul_v3' AND table_name='awareness_closed_loop_outcomes'
            """
        )
        col_names = {r["column_name"] for r in cols}
        triggers = await conn.fetch(
            """
            SELECT trigger_name
            FROM information_schema.triggers
            WHERE event_object_schema='soul_v3' AND event_object_table='awareness_closed_loop_outcomes'
            """
        )
        trigger_names = [r["trigger_name"] for r in triggers]
        audit_table_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='soul_v3'
                  AND table_name='awareness_closed_loop_outcomes_worm_audit'
            )
            """
        )
        audit_rows_created = 0
        audit_mutation_blocked = False
        probe_id = f"bench_v4_worm_{int(time.time() * 1000)}"
        if audit_table_exists:
            await conn.execute(
                """
                INSERT INTO soul_v3.awareness_closed_loop_outcomes
                    (outcome_id, agent, example_id, source_event_id, outcome_type,
                     metric_delta, benchmark_score, regression_count, promotion_decision,
                     status, evidence)
                VALUES ($1, $2, 'bench_example', 'bench_source', 'bench_probe',
                        0.1, 0.9, 0, 'promote_pending_nexus',
                        'pending_nexus_review', $3::jsonb)
                """,
                probe_id,
                BENCH_AGENT_A,
                json.dumps({"bench_version": "v4", "probe": "worm_insert"}),
            )
            await conn.execute(
                """
                UPDATE soul_v3.awareness_closed_loop_outcomes
                SET status='pending_nexus_review',
                    evidence=evidence || $2::jsonb
                WHERE outcome_id=$1
                """,
                probe_id,
                json.dumps({"updated": True}),
            )
            await conn.execute(
                "DELETE FROM soul_v3.awareness_closed_loop_outcomes WHERE outcome_id=$1",
                probe_id,
            )
            audit_rows_created = int(await conn.fetchval(
                """
                SELECT count(*)
                FROM soul_v3.awareness_closed_loop_outcomes_worm_audit
                WHERE outcome_id=$1
                """,
                probe_id,
            ) or 0)
            audit_id = await conn.fetchval(
                """
                SELECT audit_id
                FROM soul_v3.awareness_closed_loop_outcomes_worm_audit
                WHERE outcome_id=$1
                LIMIT 1
                """,
                probe_id,
            )
            if audit_id is not None:
                try:
                    async with conn.transaction():
                        await conn.execute(
                            """
                            UPDATE soul_v3.awareness_closed_loop_outcomes_worm_audit
                            SET changed_by='tamper'
                            WHERE audit_id=$1
                            """,
                            audit_id,
                        )
                except Exception:
                    audit_mutation_blocked = True
    required_cols = {"status", "reviewed_by", "reviewed_at"}
    has_review_gate = bool(required_cols & col_names)
    has_worm_trigger = any("append" in name.lower() or "worm" in name.lower() or "immutable" in name.lower() for name in trigger_names)
    has_audit_shadow = bool(audit_table_exists)
    score = 0.0
    if table_exists:
        score += 25.0
    if has_review_gate:
        score += 15.0
    if has_worm_trigger and has_audit_shadow:
        score += 20.0
    if audit_rows_created >= 3:
        score += 25.0
    if audit_mutation_blocked:
        score += 15.0
    return score, {
        "metric": "outcome_ledger_append_only_external_review",
        "table_exists": bool(table_exists),
        "columns": sorted(col_names),
        "has_review_gate": has_review_gate,
        "trigger_names": trigger_names,
        "has_worm_or_immutable_trigger": has_worm_trigger,
        "audit_shadow_table_exists": has_audit_shadow,
        "audit_rows_created_for_insert_update_delete": audit_rows_created,
        "audit_mutation_blocked": audit_mutation_blocked,
    }


CATEGORIES: dict[int, tuple[str, str, Callable[[], Awaitable[tuple[float, dict[str, Any]]]], bool]] = {
    1: ("v4.1 Adversarial Emotional Retrieval", "emotional recall resists keyword-stuffed facts", cat1_adversarial_emotional_retrieval, False),
    2: ("v4.2 Multilingual Implicit Gap", "Spanish implicit assumptions are flagged as missing premises", cat2_multilingual_implicit_gap, False),
    3: ("v4.3 Answer Citation Extraction", "answer text citations become cited/used retrieval feedback", cat3_answer_citation_extraction, False),
    4: ("v4.4 Tenant Override Hardening", "direct and nested tenant override vectors are blocked", cat4_tenant_override_hardening, True),
    5: ("v4.5 Outcome Ledger WORM", "closed-loop outcomes have external review and append-only guard", cat5_outcome_ledger_worm, False),
}


async def _persist_v4_results(results: list[V4Result], elapsed_ms: int, triggered_by: str) -> int:
    conn = await asyncpg.connect(DB_URL)
    try:
        git_commit = None
        try:
            git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        except Exception:
            pass
        passed = sum(1 for r in results if r.passed)
        score_avg = round(sum(r.score for r in results) / len(results), 2) if results else 0.0
        run_id = await conn.fetchval(
            """
            INSERT INTO soul_v3.bench_runs
                (triggered_by, total_tests, passed, failed, score_avg, elapsed_ms, git_commit)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            triggered_by,
            len(results),
            passed,
            len(results) - passed,
            score_avg,
            elapsed_ms,
            git_commit,
        )
        await conn.executemany(
            """
            INSERT INTO soul_v3.bench_results
                (run_id, category, test_name, passed, score, elapsed_ms, detail, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            [
                (
                    run_id,
                    r.category,
                    r.test_name,
                    r.passed,
                    r.score,
                    r.elapsed_ms,
                    json.dumps(r.detail, ensure_ascii=False)[:4000],
                    r.error,
                )
                for r in results
            ],
        )
        return int(run_id)
    finally:
        await conn.close()


async def run_bench_v4(
    *,
    categories: list[int] | None = None,
    cleanup: bool = True,
    persist: bool = True,
    triggered_by: str = "manual_v4",
) -> dict[str, Any]:
    t0 = time.monotonic()
    pool = await get_pool()
    lock_conn = await asyncpg.connect(DB_URL)
    run_id = None
    try:
        acquired = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY_V4)
        if not acquired:
            return {"status": "skipped", "reason": "another seal-bench v4 run in progress"}
        await _cleanup_bench_v4(pool)
        await _ensure_bench_agents(pool)

        selected = {k: v for k, v in CATEGORIES.items() if categories is None or k in categories}
        results: list[V4Result] = []
        for category, test_name, func, critical in selected.values():
            results.append(await _run_probe(category, test_name, func, critical=critical))

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if persist and categories is None:
            run_id = await _persist_v4_results(results, elapsed_ms, triggered_by)
        if cleanup:
            await _cleanup_bench_v4(pool)

        avg = round(sum(r.score for r in results) / len(results), 2) if results else 0.0
        critical_failures = [
            {"category": r.category, "score": r.score, "detail": r.detail}
            for r in results
            if r.critical and not r.passed
        ]
        return {
            "status": "completed",
            "version": "v4",
            "run_id": run_id,
            "score_avg": avg,
            "total_tests": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "elapsed_ms": elapsed_ms,
            "critical_failures": critical_failures,
            "discriminates": avg < 100.0,
            "results": [asdict(r) for r in results],
        }
    finally:
        try:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY_V4)
        finally:
            await lock_conn.close()


async def get_bench_v4_history(limit: int = 5) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, run_at, triggered_by, total_tests, passed, failed,
                   score_avg, elapsed_ms, git_commit
            FROM soul_v3.bench_runs
            WHERE triggered_by LIKE '%v4%'
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _print_human(result: dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print(f"  SEAL-Bench v4 — score_avg={result.get('score_avg')} "
          f"passed={result.get('passed')}/{result.get('total_tests')} "
          f"elapsed={result.get('elapsed_ms')}ms")
    print("=" * 72)
    for row in result.get("results", []):
        status = "PASS" if row["passed"] else "FAIL"
        critical = " CRITICAL" if row.get("critical") else ""
        print(f"  {status}{critical} | {row['score']:5.1f} | {row['category']}")
        metric = row.get("detail", {}).get("metric")
        if metric:
            print(f"       metric: {metric}")
    if result.get("critical_failures"):
        print("  CRITICAL FAILURES:")
        for failure in result["critical_failures"]:
            print(f"    - {failure['category']}: score={failure['score']}")
    print("=" * 72 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL-Bench v4 — harder frontier probes")
    parser.add_argument("--category", "-c", type=int, nargs="+", choices=sorted(CATEGORIES))
    parser.add_argument("--json", "-j", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Do not persist bench_runs/results")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep BENCH_V4 rows for inspection")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()

    if args.history:
        history = asyncio.run(get_bench_v4_history())
        if args.json:
            print(json.dumps(history, indent=2, default=str))
        else:
            for row in history:
                print(
                    f"run_id={row['id']} score={row['score_avg']} "
                    f"passed={row['passed']}/{row['total_tests']} "
                    f"elapsed={row['elapsed_ms']}ms at={row['run_at']}"
                )
        return

    result = asyncio.run(
        run_bench_v4(
            categories=args.category,
            cleanup=not args.no_cleanup,
            persist=not args.dry_run,
        )
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        _print_human(result)


if __name__ == "__main__":
    main()
