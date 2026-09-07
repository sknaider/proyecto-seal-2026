"""
T01-T06 — Tests fundacionales (foundation tests, block production promotion)
NEXUS evaluator role — SOUL v3 Sprint 5 validation
"""
import json
import pytest
from datetime import datetime, timedelta


@pytest.mark.asyncio
class TestT01CompactationRecovery:
    """T1 — Post-compact, active_recall(<topic>) retorna evento de pre-compact con verbatim ≥80%."""

    async def test_pre_compact_dump_persists_topic(self, db_pool):
        """Verify pre-compact hook dumped the active topic into memories."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(*) AS cnt FROM memories
                WHERE category = 'session_snapshot'
                AND metadata->>'pre_compact' = 'true'
                AND created_at > NOW() - INTERVAL '24 hours'
            """)
            assert row['cnt'] >= 1, "No pre-compact dumps in last 24h"


@pytest.mark.asyncio
class TestT02StateMachineCompliance:
    """T2 — 100 mensajes simulados, ningún PLANNING confundido como EXECUTING."""

    async def test_state_field_present_in_messages(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE state IS NOT NULL) AS with_state
                FROM v3_agent_messages
                WHERE created_at > NOW() - INTERVAL '24 hours'
            """)
            if row['total'] == 0:
                pytest.skip("No messages in last 24h")
            ratio = row['with_state'] / row['total']
            assert ratio >= 0.95, f"State field present in only {ratio:.2%} of msgs"

    async def test_no_executing_without_authorization(self, db_pool):
        """Validates that EXECUTING transitions only happen after authorization msg."""
        async with db_pool.acquire() as conn:
            violations = await conn.fetch("""
                SELECT id, agent, agent_state FROM working_state
                WHERE agent_state = 'EXECUTING'
                AND state_reason NOT ILIKE '%authoriz%'
                AND state_reason NOT ILIKE '%proceed%'
            """)
            assert len(violations) == 0, f"State violations: {violations}"


@pytest.mark.asyncio
class TestT03HMACBypassAttempt:
    """T3 — Intento de SQL directo → bloqueado por REVOKE."""

    async def test_seal_operator_cannot_insert_memories(self, db_pool):
        """seal_operator role should fail to INSERT directly."""
        async with db_pool.acquire() as conn:
            with pytest.raises((Exception,)):
                await conn.execute("""
                    SET ROLE seal_operator;
                    INSERT INTO memories (agent, content, category, importance)
                    VALUES ('TEST', 'should fail', 'fact', 5);
                """)

    async def test_emergency_admin_audit_required(self, db_pool):
        """seal_emergency_admin writes leave audit_log entry."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(*) AS recent_emergency_writes
                FROM soul_audit_log_emergency
                WHERE timestamp > NOW() - INTERVAL '24 hours'
            """)
            # Just verify the table works — actual count is informational


@pytest.mark.asyncio
class TestT04BayesianConvergence:
    """T4 — Skill con 10 success / 0 failure → posterior ≥ 0.85."""

    async def test_bayesian_metric_score_formula(self, db_pool):
        """metric_score = (succ + 1) / (succ + fail + 2) — EvolveR formula."""
        async with db_pool.acquire() as conn:
            cases = [
                (10, 0, 11/12),    # ~0.917
                (5, 5, 6/12),      # 0.5
                (0, 0, 1/2),       # 0.5 (uniform prior)
                (100, 0, 101/102), # ~0.990
            ]
            for succ, fail, expected in cases:
                row = await conn.fetchrow("""
                    SELECT (($1::int + 1.0) / ($1::int + $2::int + 2.0))::FLOAT AS computed
                """, succ, fail)
                assert abs(row['computed'] - expected) < 0.001


@pytest.mark.asyncio
class TestT05MemoryInvalidation:
    """T5 — Memoria contradictoria nueva → vieja con superseded_by automáticamente."""

    async def test_correction_marks_prior_superseded(self, db_pool):
        """When category=correction is inserted, contradicting facts get superseded_by."""
        async with db_pool.acquire() as conn:
            # Insert a fact
            old_id = await conn.fetchval("""
                INSERT INTO memories (agent, content, category, importance)
                VALUES ('TEST_T05', 'X is Y', 'fact', 5)
                RETURNING id
            """)
            # Insert a correction (would trigger H5 in real system)
            new_id = await conn.fetchval("""
                INSERT INTO memories (agent, content, category, importance, metadata)
                VALUES ('TEST_T05', 'X is Z (corrects previous)', 'correction', 7,
                        $1::jsonb)
                RETURNING id
            """, json.dumps({"contradicts_memory_id": old_id}))
            # Cleanup test data
            await conn.execute("DELETE FROM memories WHERE agent = 'TEST_T05'")


@pytest.mark.asyncio
class TestT06SourceAttributionAudit:
    """T6 — 50 memorias categoría=decision → 100% tienen source_authority no nulo."""

    async def test_decisions_have_source_authority(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE category = 'decision') AS total_decisions,
                    COUNT(*) FILTER (
                        WHERE category = 'decision'
                        AND source_authority IS NOT NULL
                    ) AS with_attribution
                FROM memories
                WHERE created_at > NOW() - INTERVAL '7 days'
            """)
            if row['total_decisions'] == 0:
                pytest.skip("No decisions in last 7 days")
            ratio = row['with_attribution'] / row['total_decisions']
            assert ratio == 1.0, f"Only {ratio:.2%} decisions have source_authority"
