import asyncpg
import pytest

from agent_tasks_registry import AgentTaskRecord, ensure_schema, summary, upsert_agent_task
from seal_secrets import pg_dsn


@pytest.mark.asyncio
async def test_agent_tasks_registry_upsert_sets_completed_at_transactional():
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        await ensure_schema(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            row = await upsert_agent_task(
                conn,
                AgentTaskRecord(
                    agent="ADA",
                    title="test agent_tasks registry transactional",
                    status="pending",
                    priority=5,
                    source="test_agent_tasks_registry",
                    evidence={"phase": "pending"},
                ),
            )
            assert row["status"] == "pending"
            assert row["completed_at"] is None

            row2 = await upsert_agent_task(
                conn,
                AgentTaskRecord(
                    agent="ADA",
                    title="test agent_tasks registry transactional",
                    status="completed",
                    source="test_agent_tasks_registry",
                    evidence={"phase": "completed"},
                ),
            )
            assert row2["id"] == row["id"]
            assert row2["status"] == "completed"
            assert row2["completed_at"] is not None

            data = await summary(conn, limit=5)
            assert "team_summary" in data
            assert "open_work" in data
            assert "recent_completed" in data
        finally:
            await tr.rollback()
    finally:
        await conn.close()
