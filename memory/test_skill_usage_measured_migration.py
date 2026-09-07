from pathlib import Path

import asyncpg
import pytest

from seal_secrets import pg_dsn


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "memory/migrations/20260828_skill_usage_measured_v1.sql"


def test_migration_preserves_legacy_counters_and_declares_log_canonical():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "UPDATE soul_v3.skills" in sql
    assert "success_count = 0" not in sql
    assert "failure_count = 0" not in sql
    assert "CREATE OR REPLACE VIEW soul_v3.skill_usage_measured" in sql
    assert "'soul_v3.skill_use_log'::text AS canonical_usage_source" in sql


@pytest.mark.asyncio
async def test_measured_view_separates_ledger_from_legacy_aggregate():
    conn = await asyncpg.connect(pg_dsn(required=True))
    row = await conn.fetchrow(
        """
        SELECT measured_invocations, measured_successes,
               legacy_aggregate_success_count,
               aggregate_counter_is_legacy_or_unverified,
               canonical_usage_source
        FROM soul_v3.skill_usage_measured
        WHERE skill_id=166
        """
    )
    await conn.close()
    assert row["measured_invocations"] == 0
    assert row["measured_successes"] == 0
    assert row["legacy_aggregate_success_count"] == 339
    assert row["aggregate_counter_is_legacy_or_unverified"] is True
    assert row["canonical_usage_source"] == "soul_v3.skill_use_log"
