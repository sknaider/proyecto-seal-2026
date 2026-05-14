import pytest
from companion_core.db import init_db, close_db, get_db


@pytest.mark.asyncio
async def test_db_creates_tables():
    await init_db()
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') ORDER BY name"
    )
    names = {r[0] for r in rows}
    assert "memories" in names
    assert "conversations" in names
    assert "companion_settings" in names
    assert "integrations" in names
    assert "memories_ai" in names
    assert "memories_au" in names
    assert "memories_ad" in names
    await close_db()


@pytest.mark.asyncio
async def test_fts5_search():
    await init_db()
    db = get_db()
    await db.execute(
        "INSERT INTO memories (agent, category, content) VALUES (?,?,?)",
        ("ALICE", "test", "quantum entanglement is fascinating")
    )
    await db.execute(
        "INSERT INTO memories (agent, category, content) VALUES (?,?,?)",
        ("JARVIS", "test", "neural networks process information")
    )
    await db.commit()
    rows = await db.execute_fetchall(
        "SELECT m.id, m.content FROM memories m "
        "JOIN memories_fts f ON f.rowid = m.id "
        "WHERE memories_fts MATCH ? ORDER BY rank",
        ("quantum",)
    )
    assert len(rows) == 1
    assert "quantum" in rows[0][1]
    await close_db()


@pytest.mark.asyncio
async def test_trigger_keeps_fts_in_sync():
    await init_db()
    db = get_db()
    cur = await db.execute(
        "INSERT INTO memories (agent, content) VALUES (?,?)",
        ("NEXUS", "original content here")
    )
    mem_id = cur.lastrowid
    await db.commit()
    # Update content
    await db.execute("UPDATE memories SET content=? WHERE id=?", ("updated content xyz", mem_id))
    await db.commit()
    # FTS should find updated, not original
    rows = await db.execute_fetchall(
        "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?", ("xyz",)
    )
    assert len(rows) == 1
    rows_old = await db.execute_fetchall(
        "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?", ("original",)
    )
    assert len(rows_old) == 0
    await close_db()
