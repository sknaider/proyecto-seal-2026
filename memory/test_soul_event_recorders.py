import asyncpg
import pytest

from seal_secrets import pg_dsn
from soul_event_recorders import record_denial, record_skill_use


@pytest.mark.asyncio
async def test_record_denial_transactional():
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            row = await record_denial(
                conn,
                agent="ADA",
                denied_action="test.denied_action",
                denial_reason="transactional test",
                source="pytest",
                context={"test": True},
            )
            assert row["agent"] == "ADA"
            assert row["resolved"] is False
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_record_skill_use_transactional():
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        skill_id = await conn.fetchval("SELECT id FROM soul_v3.skills WHERE invalid_at IS NULL LIMIT 1")
        assert skill_id is not None
        tr = conn.transaction()
        await tr.start()
        try:
            row = await record_skill_use(
                conn,
                agent="ADA",
                skill_id=skill_id,
                params={"test": True},
                success=True,
                duration_ms=7,
            )
            assert row["skill_id"] == skill_id
            assert row["success"] is True
        finally:
            await tr.rollback()
    finally:
        await conn.close()
