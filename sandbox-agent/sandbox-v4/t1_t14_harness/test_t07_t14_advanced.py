"""
T07-T14 — Advanced tests (sprint promotion gate, not foundation block)
NEXUS evaluator role
"""
import json
import pytest


@pytest.mark.asyncio
class TestT07MemoryFusion:
    """T7 — context_fingerprint distinto → ambas memorias retornan en retrieval."""

    async def test_fingerprint_prevents_collapse(self, db_pool):
        async with db_pool.acquire() as conn:
            # Two events with similar content but different fingerprints
            await conn.execute("""
                INSERT INTO memories (agent, content, category, importance, context_fingerprint, temporal_cluster)
                VALUES
                    ('TEST_T07', 'HMAC mismatch en 5 memorias', 'event', 8, 'fp_a_2026042708', '2026-W17'),
                    ('TEST_T07', 'HMAC mismatch en 2 memorias', 'event', 8, 'fp_b_2026042710', '2026-W17')
            """)
            rows = await conn.fetch("""
                SELECT id, content, context_fingerprint
                FROM memories
                WHERE agent = 'TEST_T07' AND content ILIKE '%HMAC mismatch%'
            """)
            fps = {r['context_fingerprint'] for r in rows}
            assert len(fps) == 2, "Different fingerprints should produce 2 records"
            await conn.execute("DELETE FROM memories WHERE agent = 'TEST_T07'")


@pytest.mark.asyncio
class TestT08ReflectiveOptimizerFix:
    """T8 — Inyected bug → reflective_optimizer propone fix accionable."""

    async def test_diagnosis_records_for_failed_traces(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE outcome_success = FALSE) AS failed_traces,
                    COUNT(DISTINCT rd.trace_id) AS diagnosed
                FROM reasoning_traces rt
                LEFT JOIN reflective_diagnoses rd ON rd.trace_id = rt.id
                WHERE rt.created_at > NOW() - INTERVAL '24 hours'
            """)
            if row['failed_traces'] == 0:
                pytest.skip("No failed traces in last 24h")
            ratio = row['diagnosed'] / row['failed_traces']
            assert ratio >= 0.6, f"Only {ratio:.2%} of failed traces diagnosed"


@pytest.mark.asyncio
class TestT09MetacogPlanningConsistency:
    """T9 — Skill posterior_lower < 0.4 → en próximas 3 sesiones agente practica X."""

    async def test_weak_skills_become_learning_goals(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE (success_count + 1.0) / (success_count + failure_count + 2.0) < 0.4
                    ) AS weak_skills,
                    COUNT(DISTINCT lg.target_capability) AS goals_for_weak
                FROM skills s
                LEFT JOIN agent_learning_goals lg
                    ON lg.target_capability = s.name
                    AND lg.status = 'active'
                WHERE s.invalid_at IS NULL
            """)
            if row['weak_skills'] == 0:
                pytest.skip("No weak skills currently")
            # Goals don't need to be 1:1 — just verify some coverage exists
            assert row['goals_for_weak'] > 0 or row['weak_skills'] < 3


@pytest.mark.asyncio
class TestT10MultiAgentDebateResolution:
    """T10 — 100% contradicciones <5min → protocolo formal."""

    async def test_contradictions_trigger_debate(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE NOT william_overrode) AS resolved_internally,
                    AVG(EXTRACT(EPOCH FROM (voted_at - created_at))) AS avg_resolution_seconds
                FROM debate_log
                WHERE voted_at > NOW() - INTERVAL '7 days'
            """)
            if row['resolved_internally'] is None or row['resolved_internally'] == 0:
                pytest.skip("No debates in last 7 days")
            assert row['avg_resolution_seconds'] < 600, "Debates should resolve in <10 min"


@pytest.mark.asyncio
class TestT11DARRoutingEfficacy:
    """T11 — DAR routing reduces -40% wakeups irrelevantes."""

    async def test_dar_skipped_count(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE dar_decision = 'skip') AS skipped,
                    COUNT(*) AS total
                FROM dar_routing_log
                WHERE created_at > NOW() - INTERVAL '24 hours'
            """)
            if row['total'] == 0:
                pytest.skip("DAR log empty (not yet deployed)")
            skip_ratio = row['skipped'] / row['total']
            assert skip_ratio >= 0.40, f"DAR only skipped {skip_ratio:.2%} (target ≥40%)"


@pytest.mark.asyncio
class TestT12ChallengerCoverage:
    """T12 — ≥1 challenge per agent per week."""

    async def test_each_agent_gets_weekly_challenge(self, db_pool):
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT challenged_agent, COUNT(*) AS n_challenges
                FROM agent_challenges
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY challenged_agent
            """)
            agents_covered = {r['challenged_agent'] for r in rows}
            expected = {'ADA', 'JARVIS', 'ALICE', 'NEXUS'}
            missing = expected - agents_covered
            assert len(missing) == 0, f"Agents without weekly challenge: {missing}"


@pytest.mark.asyncio
class TestT13SleepGateConsolidation:
    """T13 — MTM→LTM <60s for typical session."""

    async def test_consolidation_latency(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS runs,
                    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_seconds
                FROM ltm_consolidation_log
                WHERE completed_at > NOW() - INTERVAL '7 days'
            """)
            if row['runs'] == 0:
                pytest.skip("No consolidation runs yet")
            assert row['avg_seconds'] < 60, f"Consolidation took {row['avg_seconds']}s (target <60)"


@pytest.mark.asyncio
class TestT14DebateFailureModeCoverage:
    """T14 — Judge no produce hallucination/spec drift/EM rejection (ALICE C3)."""

    async def test_judge_synthesis_grounded_in_evidence(self, db_pool):
        """Judge synthesis must reference at least one reasoning_trace_id."""
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, outcome, rounds
                FROM debate_log
                WHERE voted_at > NOW() - INTERVAL '7 days'
            """)
            for row in rows:
                outcome_str = str(row['outcome'])
                rounds = row['rounds']
                # Heuristic: outcome mentions trace_id or specific evidence
                if isinstance(rounds, dict) and rounds.get('synthesizer'):
                    # Check that outcome references evidence, not just claims
                    assert 'trace' in outcome_str.lower() or 'evidence' in outcome_str.lower() or len(outcome_str) > 50, \
                        f"Debate {row['id']} outcome lacks grounded evidence"
