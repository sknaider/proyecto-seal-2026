from pathlib import Path

import asyncpg
import pytest

from seal_secrets import pg_dsn


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "memory/migrations/20260828_skill_telemetry_coverage_v1.sql"


def test_migration_makes_partial_coverage_explicit():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "skill_telemetry_coverage" in sql
    assert "skill_telemetry_expected_routes" in sql
    assert "unmeasurable_before_hook" in sql
    assert "partial_coverage_do_not_infer_zero" in sql
    assert "skill_usage_by_agent_measured" in sql
    assert "SessionStart" in sql


@pytest.mark.asyncio
async def test_aggregate_view_never_calls_partial_coverage_complete():
    conn = await asyncpg.connect(pg_dsn(required=True))
    row = await conn.fetchrow(
        """
        SELECT coverage_complete, measurement_status,
               expected_required_agents, covered_required_agents,
               common_measurement_start
        FROM soul_v3.skill_usage_measured
        LIMIT 1
        """
    )
    await conn.close()
    expected_complete = row["expected_required_agents"] == row["covered_required_agents"]
    assert row["coverage_complete"] is expected_complete
    if not row["coverage_complete"]:
        assert row["measurement_status"].startswith("partial_coverage_")
        assert row["common_measurement_start"] is None
