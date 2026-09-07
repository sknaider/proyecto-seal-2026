#!/usr/bin/env python3
"""Identity Continuity v2 Phase 6 — meta-pattern audit.

Creates soul_v3.meta_pattern_audit(agent) and refreshes the latest audit
summary into both a history table and awareness_state for dashboard use.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_secrets import pg_dsn  # noqa: E402


DEFAULT_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")


FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION soul_v3.meta_pattern_audit(p_agent text)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    v_total integer := 0;
    v_category_counts jsonb := '{}'::jsonb;
    v_importance_counts jsonb := '{}'::jsonb;
    v_entropy numeric := 0;
    v_top_categories jsonb := '[]'::jsonb;
    v_ocean jsonb := '{}'::jsonb;
    v_gaps jsonb := '[]'::jsonb;
    v_o numeric := 0;
    v_c numeric := 0;
    v_e numeric := 0;
    v_a numeric := 0;
    v_n numeric := 0;
    v_insight_pattern integer := 0;
    v_correction_decision integer := 0;
    v_relational integer := 0;
BEGIN
    SELECT COUNT(*)::integer
    INTO v_total
    FROM soul_v3.memories
    WHERE agent = p_agent
      AND invalid_at IS NULL
      AND created_at >= now() - interval '30 days';

    WITH counts AS (
        SELECT category, COUNT(*)::integer AS n
        FROM soul_v3.memories
        WHERE agent = p_agent
          AND invalid_at IS NULL
          AND created_at >= now() - interval '30 days'
        GROUP BY category
    )
    SELECT COALESCE(jsonb_object_agg(category, n), '{}'::jsonb)
    INTO v_category_counts
    FROM counts;

    WITH counts AS (
        SELECT importance::text AS importance, COUNT(*)::integer AS n
        FROM soul_v3.memories
        WHERE agent = p_agent
          AND invalid_at IS NULL
          AND created_at >= now() - interval '30 days'
        GROUP BY importance
    )
    SELECT COALESCE(jsonb_object_agg(importance, n), '{}'::jsonb)
    INTO v_importance_counts
    FROM counts;

    WITH counts AS (
        SELECT category, COUNT(*)::numeric AS n
        FROM soul_v3.memories
        WHERE agent = p_agent
          AND invalid_at IS NULL
          AND created_at >= now() - interval '30 days'
        GROUP BY category
    )
    SELECT COALESCE((-SUM((n / NULLIF(v_total, 0)) * LN(n / NULLIF(v_total, 0))))::numeric, 0)
    INTO v_entropy
    FROM counts;

    WITH counts AS (
        SELECT category, COUNT(*)::integer AS n
        FROM soul_v3.memories
        WHERE agent = p_agent
          AND invalid_at IS NULL
          AND created_at >= now() - interval '30 days'
        GROUP BY category
        ORDER BY n DESC, category
        LIMIT 3
    )
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'category', category,
                'count', n,
                'pct', CASE WHEN v_total > 0 THEN ROUND((n::numeric / v_total::numeric), 4) ELSE 0 END
            )
        ),
        '[]'::jsonb
    )
    INTO v_top_categories
    FROM counts;

    SELECT COALESCE(ocean_scores, '{}'::jsonb)
    INTO v_ocean
    FROM soul_v3.identity
    WHERE agent = p_agent
    LIMIT 1;

    v_o := COALESCE((v_ocean->>'O')::numeric, 0);
    v_c := COALESCE((v_ocean->>'C')::numeric, 0);
    v_e := COALESCE((v_ocean->>'E')::numeric, 0);
    v_a := COALESCE((v_ocean->>'A')::numeric, 0);
    v_n := COALESCE((v_ocean->>'N')::numeric, 0);

    v_insight_pattern :=
        COALESCE((v_category_counts->>'insight')::integer, 0) +
        COALESCE((v_category_counts->>'pattern')::integer, 0);
    v_correction_decision :=
        COALESCE((v_category_counts->>'correction')::integer, 0) +
        COALESCE((v_category_counts->>'decision')::integer, 0);
    v_relational :=
        COALESCE((v_category_counts->>'emotion')::integer, 0) +
        COALESCE((v_category_counts->>'trust')::integer, 0) +
        COALESCE((v_category_counts->>'relationship')::integer, 0);

    IF v_total >= 10 AND v_o >= 0.75 AND v_insight_pattern < CEIL(v_total * 0.10) THEN
        v_gaps := v_gaps || jsonb_build_array(jsonb_build_object(
            'trait', 'O',
            'expected_capture_pattern', 'insight/pattern >= 10%',
            'observed_count', v_insight_pattern
        ));
    END IF;
    IF v_total >= 10 AND v_c >= 0.80 AND v_correction_decision < CEIL(v_total * 0.10) THEN
        v_gaps := v_gaps || jsonb_build_array(jsonb_build_object(
            'trait', 'C',
            'expected_capture_pattern', 'decision/correction >= 10%',
            'observed_count', v_correction_decision
        ));
    END IF;
    IF v_total >= 10 AND (v_e >= 0.70 OR v_a >= 0.70) AND v_relational < CEIL(v_total * 0.05) THEN
        v_gaps := v_gaps || jsonb_build_array(jsonb_build_object(
            'trait', 'E/A',
            'expected_capture_pattern', 'emotion/trust/relationship >= 5%',
            'observed_count', v_relational
        ));
    END IF;

    RETURN jsonb_build_object(
        'agent', p_agent,
        'window_days', 30,
        'total_memories', v_total,
        'category_counts', v_category_counts,
        'importance_counts', v_importance_counts,
        'entropy', ROUND(v_entropy, 6),
        'top_categories', v_top_categories,
        'gap_categories', v_gaps,
        'ocean', v_ocean,
        'generated_at', now()
    );
END;
$$;
"""


@dataclass
class MetaPatternResult:
    agent: str
    total_memories: int
    entropy: float
    gap_count: int
    result_id: int | None = None


@dataclass
class MetaPatternRun:
    run_id: str
    dry_run: bool
    agents_checked: int
    results_written: int
    results: list[MetaPatternResult]
    self_test_ok: bool = False


class SelfTestRollback(Exception):
    def __init__(self, run: MetaPatternRun):
        super().__init__("rollback_self_test")
        self.run = run


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.meta_pattern_audit_results (
            id BIGSERIAL PRIMARY KEY,
            agent TEXT NOT NULL,
            audit JSONB NOT NULL DEFAULT '{}'::jsonb,
            total_memories INTEGER NOT NULL DEFAULT 0,
            category_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            importance_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            entropy NUMERIC NOT NULL DEFAULT 0,
            top_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            gap_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_meta_pattern_agent_created
        ON soul_v3.meta_pattern_audit_results(agent, created_at DESC)
        """
    )
    await conn.execute(
        "ALTER TABLE soul_v3.awareness_state ADD COLUMN IF NOT EXISTS meta_pattern_summary JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    await conn.execute(
        "ALTER TABLE soul_v3.awareness_state ADD COLUMN IF NOT EXISTS meta_pattern_audited_at TIMESTAMPTZ"
    )
    await conn.execute(FUNCTION_SQL)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _write_result(conn: asyncpg.Connection, agent: str, audit: dict[str, Any]) -> int:
    result_id = await conn.fetchval(
        """
        INSERT INTO soul_v3.meta_pattern_audit_results
            (agent, audit, total_memories, category_counts, importance_counts, entropy, top_categories, gap_categories)
        VALUES ($1, $2::jsonb, $3, $4::jsonb, $5::jsonb, $6, $7::jsonb, $8::jsonb)
        RETURNING id
        """,
        agent,
        json.dumps(audit, default=_json_default),
        int(audit.get("total_memories") or 0),
        json.dumps(audit.get("category_counts") or {}),
        json.dumps(audit.get("importance_counts") or {}),
        float(audit.get("entropy") or 0),
        json.dumps(audit.get("top_categories") or []),
        json.dumps(audit.get("gap_categories") or []),
    )
    await conn.execute(
        """
        INSERT INTO soul_v3.awareness_state
            (agent, state, current_focus, confidence, meta_pattern_summary, meta_pattern_audited_at, updated_at)
        VALUES ($1, 'active', 'meta_pattern_audit', 1.0, $2::jsonb, now(), now())
        ON CONFLICT (agent) DO UPDATE
        SET meta_pattern_summary = EXCLUDED.meta_pattern_summary,
            meta_pattern_audited_at = EXCLUDED.meta_pattern_audited_at,
            updated_at = now()
        """,
        agent,
        json.dumps(audit, default=_json_default),
    )
    return int(result_id)


async def _run_agent(conn: asyncpg.Connection, agent: str, *, dry_run: bool) -> MetaPatternResult:
    audit = await conn.fetchval("SELECT soul_v3.meta_pattern_audit($1)", agent)
    if isinstance(audit, str):
        audit = json.loads(audit)
    result = MetaPatternResult(
        agent=agent,
        total_memories=int(audit.get("total_memories") or 0),
        entropy=float(audit.get("entropy") or 0),
        gap_count=len(audit.get("gap_categories") or []),
    )
    if not dry_run:
        result.result_id = await _write_result(conn, agent, audit)
    return result


async def _log_evaluation_run(conn: asyncpg.Connection, run: MetaPatternRun, passed: bool, notes: str) -> None:
    await conn.execute(
        """
        INSERT INTO soul_v3.evaluation_runs
            (suite_name, score, passed, evidence, details, agent, run_at, notes)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), $7)
        """,
        "identity_continuity_v2_phase6_meta_pattern_audit",
        100 if passed else 0,
        passed,
        (
            f"run_id={run.run_id} agents_checked={run.agents_checked} "
            f"results_written={run.results_written} self_test_ok={run.self_test_ok}"
        ),
        json.dumps(asdict(run), default=_json_default),
        "ADA",
        notes,
    )


async def run_audit(conn: asyncpg.Connection, agents: list[str], *, dry_run: bool) -> MetaPatternRun:
    await ensure_schema(conn)
    results = [await _run_agent(conn, agent.upper(), dry_run=dry_run) for agent in agents]
    run = MetaPatternRun(
        run_id=str(uuid.uuid4()),
        dry_run=dry_run,
        agents_checked=len(results),
        results_written=sum(1 for r in results if r.result_id is not None),
        results=results,
    )
    await _log_evaluation_run(conn, run, True, "meta-pattern audit completed")
    return run


async def run_self_test(conn: asyncpg.Connection) -> MetaPatternRun:
    await ensure_schema(conn)
    async with conn.transaction():
        agent = "META_RTEST"
        await conn.execute(
            """
            INSERT INTO soul_v3.agents (name, role, active)
            VALUES ($1, 'synthetic meta-pattern audit test agent', true)
            ON CONFLICT (name) DO NOTHING
            """,
            agent,
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.identity (agent, personality, ocean_scores, ocean_baseline)
            VALUES ($1, '{}'::jsonb, $2::jsonb, $2::jsonb)
            ON CONFLICT (agent) DO UPDATE SET ocean_scores = EXCLUDED.ocean_scores
            """,
            agent,
            json.dumps({"O": 0.8, "C": 0.9, "E": 0.2, "A": 0.2, "N": 0.2}),
        )
        rows = []
        for i in range(6):
            rows.append((agent, "decision", f"Synthetic decision memory {i}", 7))
        for i in range(4):
            rows.append((agent, "emotion", f"Synthetic emotion memory {i}", 5))
        await conn.executemany(
            """
            INSERT INTO soul_v3.memories (agent, scope, category, content, importance, source, metadata, created_at)
            VALUES ($1, 'private', $2, $3, $4, 'meta_audit_test', $5::jsonb, now())
            """,
            [(a, c, content, imp, json.dumps({"synthetic": True, "phase": "identity_continuity_v2_phase6"})) for a, c, content, imp in rows],
        )
        result = await _run_agent(conn, agent, dry_run=False)
        audit = await conn.fetchval("SELECT soul_v3.meta_pattern_audit($1)", agent)
        if isinstance(audit, str):
            audit = json.loads(audit)
        cats = audit.get("category_counts") or {}
        expected_entropy = -(0.6 * math.log(0.6) + 0.4 * math.log(0.4))
        if cats.get("decision") != 6 or cats.get("emotion") != 4:
            raise RuntimeError(f"self-test counts failed: {cats}")
        if abs(float(audit.get("entropy") or 0) - expected_entropy) > 0.001:
            raise RuntimeError(f"self-test entropy failed: {audit.get('entropy')} expected={expected_entropy}")
        run = MetaPatternRun(
            run_id="self-test-rolled-back",
            dry_run=False,
            agents_checked=1,
            results_written=1,
            results=[result],
            self_test_ok=True,
        )
        raise SelfTestRollback(run)


async def self_test(conn: asyncpg.Connection) -> MetaPatternRun:
    try:
        return await run_self_test(conn)
    except SelfTestRollback as exc:
        run = exc.run
        await _log_evaluation_run(conn, run, True, "meta-pattern audit self-test passed and rolled back")
        return run


def _print_run(run: MetaPatternRun) -> None:
    print(
        "meta_pattern_audit=ok "
        f"run_id={run.run_id} dry_run={run.dry_run} agents_checked={run.agents_checked} "
        f"results_written={run.results_written} self_test_ok={run.self_test_ok}"
    )
    for result in run.results:
        print(
            f"- agent={result.agent} total={result.total_memories} entropy={result.entropy:.6f} "
            f"gaps={result.gap_count} result_id={result.result_id}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Identity Continuity v2 Phase 6 meta-pattern audit")
    parser.add_argument("--agents", nargs="+", default=list(DEFAULT_AGENTS))
    parser.add_argument("--apply", action="store_true", help="write audit result rows and awareness_state summaries")
    parser.add_argument("--self-test", action="store_true", help="run hand-computed sample in rollback transaction")
    args = parser.parse_args()

    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        if args.self_test:
            run = await self_test(conn)
        else:
            run = await run_audit(conn, args.agents, dry_run=not args.apply)
    finally:
        await conn.close()

    _print_run(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
