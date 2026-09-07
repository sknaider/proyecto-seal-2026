import asyncpg
import pytest

from agent_work_ledger import WorkItem, ensure_schema, summary, upsert_work_item
from seal_secrets import pg_dsn


@pytest.mark.asyncio
async def test_agent_work_ledger_upsert_and_summary_transactional():
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        await ensure_schema(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            row = await upsert_work_item(
                conn,
                WorkItem(
                    agent="ADA",
                    title="test ledger transactional",
                    description="created by pytest and rolled back",
                    status="pending",
                    source="test_agent_work_ledger",
                    evidence={"test": True},
                ),
            )
            assert row["agent"] == "ADA"
            assert row["status"] == "pending"

            row2 = await upsert_work_item(
                conn,
                WorkItem(
                    agent="ADA",
                    title="test ledger transactional",
                    description="created by pytest and rolled back",
                    status="completed",
                    source="test_agent_work_ledger",
                    evidence={"test": True, "phase": "complete"},
                ),
            )
            assert row2["id"] == row["id"]
            assert row2["status"] == "completed"
            assert row2["completed_at"] is not None

            data = await summary(conn, limit=5)
            assert data["items"]
            assert data["counts"]
        finally:
            await tr.rollback()
    finally:
        await conn.close()
