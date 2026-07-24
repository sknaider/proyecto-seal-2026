"""Live PostgreSQL differential test; every fixture and DDL change is rolled back."""

import asyncio
from pathlib import Path

import asyncpg


DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260724_high_importance_delete_shadow_ADA.sql"
)


async def insert_memory(
    conn: asyncpg.Connection, *, agent: str, content: str, importance: int
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO soul_v3.memories (agent, content, category, importance)
        VALUES ($1, $2, 'test', $3)
        RETURNING id
        """,
        agent,
        content,
        importance,
    )


async def log_row(conn: asyncpg.Connection, memory_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT tenant_id, agent, had_exact_live_twin, had_exact_archive,
               would_block
        FROM soul_v3.memory_delete_shadow_log
        WHERE memory_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        memory_id,
    )


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    checks = 0
    try:
        await conn.execute(MIGRATION.read_text())
        await conn.execute(
            "SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111'"
        )

        unique = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST unique high",
            importance=10,
        )
        old_tenant = await conn.fetchval(
            "SELECT tenant_id FROM soul_v3.memories WHERE id=$1", unique
        )
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", unique)
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM soul_v3.memories WHERE id=$1)", unique
        )
        row = await log_row(conn, unique)
        assert row is not None and row["would_block"] is True
        assert row["tenant_id"] == old_tenant
        checks += 3

        low = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST low",
            importance=3,
        )
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", low)
        assert await log_row(conn, low) is None
        checks += 1

        twin_a = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST same owner twin",
            importance=9,
        )
        await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST same owner twin",
            importance=9,
        )
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", twin_a)
        row = await log_row(conn, twin_a)
        assert row is not None
        assert row["had_exact_live_twin"] is True and row["would_block"] is False
        checks += 2

        cross_agent = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST cross agent is not twin",
            importance=9,
        )
        await insert_memory(
            conn,
            agent="NEXUS",
            content="DELETE_SHADOW_TEST cross agent is not twin",
            importance=9,
        )
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", cross_agent)
        row = await log_row(conn, cross_agent)
        assert row is not None
        assert row["had_exact_live_twin"] is False and row["would_block"] is True
        checks += 2

        archived = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST archived exact row",
            importance=10,
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.memories_archive
                (id, agent, content, importance, reason, tenant_id)
            SELECT id, agent, content, importance, 'shadow_test', tenant_id
            FROM soul_v3.memories
            WHERE id=$1
            """,
            archived,
        )
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", archived)
        row = await log_row(conn, archived)
        assert row is not None
        assert row["had_exact_archive"] is True and row["would_block"] is False
        checks += 2

        columns = {
            r["column_name"]
            for r in await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='soul_v3'
                  AND table_name='memory_delete_shadow_log'
                """
            )
        }
        assert "content" not in columns and "content_head" not in columns
        checks += 1

        # A broken telemetry sink must not become accidental enforcement.
        sink_failure = await insert_memory(
            conn,
            agent="ADA",
            content="DELETE_SHADOW_TEST audit sink failure",
            importance=10,
        )
        await conn.execute("DROP TABLE soul_v3.memory_delete_shadow_log")
        await conn.execute("DELETE FROM soul_v3.memories WHERE id=$1", sink_failure)
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM soul_v3.memories WHERE id=$1)", sink_failure
        )
        checks += 1

        print(f"{checks} differential checks OK; transaction will ROLLBACK")
    finally:
        await tx.rollback()
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
