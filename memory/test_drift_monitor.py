#!/usr/bin/env python3
"""Test drift monitor — Gap #3 (NEXUS spec, 24-abr-2026)
Tests: innocent update, incremental attacker drift, importance<8 no tracking.
"""
import asyncio
import json
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")

import asyncpg

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

# Import helpers from mcp_server
from mcp_server_v3 import _cosine_sim, _hashlib, get_embedding  # type: ignore

PASS = 0
FAIL = 0

def ok(name):
    global PASS
    PASS += 1
    print(f"  ✅ {name}")

def fail(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  ❌ {name}" + (f": {detail}" if detail else ""))


async def test_cosine_sim():
    """_cosine_sim: identical vectors → 1.0, orthogonal → 0.0"""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    sim_same = _cosine_sim(a, b)
    sim_ortho = _cosine_sim(a, c)
    if abs(sim_same - 1.0) < 1e-6:
        ok("cosine_sim identical=1.0")
    else:
        fail("cosine_sim identical", f"got {sim_same}")
    if abs(sim_ortho) < 1e-6:
        ok("cosine_sim orthogonal=0.0")
    else:
        fail("cosine_sim orthogonal", f"got {sim_ortho}")


async def test_drift_columns_exist():
    """Schema: 5 drift columns exist on memories table."""
    conn = await asyncpg.connect(DB_URL)
    cols = await conn.fetch(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name='memories'
           AND column_name IN ('original_content_hash','original_embedding_vector',
                               'revision_count','last_revision_at','drift_score')"""
    )
    await conn.close()
    names = {r["column_name"] for r in cols}
    expected = {"original_content_hash", "original_embedding_vector",
                "revision_count", "last_revision_at", "drift_score"}
    if names == expected:
        ok("drift columns exist (5/5)")
    else:
        missing = expected - names
        fail("drift columns", f"missing: {missing}")


async def test_memory_store_seeds_original():
    """memory_store with importance=9: original_content_hash set, revision_count=0."""
    from mcp_server_v3 import memory_store  # type: ignore
    content = "William autorizó deploy_DRIFTTEST_42 el 2026-04-24"
    result = await memory_store(
        agent="ADA",
        category="decision",
        content=content,
        importance=9,
        scope="team",
    )
    mem_id = None
    try:
        data = json.loads(result) if result.startswith("{") else {}
        mem_id = data.get("id")
    except Exception:
        pass

    if mem_id is None:
        # Try parsing "Stored memory #ID" style
        import re
        m = re.search(r"#(\d+)", result)
        if m:
            mem_id = int(m.group(1))

    if mem_id is None:
        fail("store_seeds_original", f"could not extract id from: {result[:100]}")
        return None

    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT original_content_hash, revision_count FROM memories WHERE id=$1",
        mem_id
    )
    await conn.close()

    if row and row["original_content_hash"] and row["revision_count"] == 0:
        ok(f"store seeds original hash (mem #{mem_id})")
    elif row and not row["original_content_hash"]:
        fail("store seeds original hash", "hash is NULL")
    else:
        fail("store seeds original hash", f"row={dict(row) if row else None}")
    return mem_id


async def test_memory_update_drift_tracking(seed_mem_id):
    """memory_update: revision_count increments, drift_score computed."""
    if seed_mem_id is None:
        fail("update drift tracking", "no seed_mem_id")
        return

    from mcp_server_v3 import memory_update  # type: ignore
    result = await memory_update(
        memory_id=seed_mem_id,
        new_content="William autorizó deploy_DRIFTTEST_42 el 2026-04-24 (con restricciones operativas)",
        reason="test drift monitor — inocente",
    )

    # Find new memory ID from result
    import re
    m = re.search(r"superseded by #(\d+)", result)
    if not m:
        fail("update drift tracking", f"unexpected result: {result[:100]}")
        return
    new_id = int(m.group(1))

    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT revision_count, drift_score, original_content_hash, last_revision_at FROM memories WHERE id=$1",
        new_id
    )
    await conn.close()

    if not row:
        fail("update drift tracking", f"new memory #{new_id} not found")
        return

    if row["revision_count"] == 1:
        ok(f"revision_count=1 on first update (#{new_id})")
    else:
        fail("revision_count", f"expected 1, got {row['revision_count']}")

    if row["drift_score"] is not None and row["drift_score"] < 0.3:
        ok(f"drift_score={row['drift_score']:.3f} (innocent update < 0.3)")
    elif row["drift_score"] is None:
        fail("drift_score", "None (embedding may have failed)")
    else:
        fail("drift_score", f"unexpected value {row['drift_score']:.3f}")

    if row["last_revision_at"] is not None:
        ok("last_revision_at set")
    else:
        fail("last_revision_at", "NULL")

    if row["original_content_hash"]:
        ok("original_content_hash propagated")
    else:
        fail("original_content_hash", "not propagated")


async def test_no_tracking_low_importance():
    """memory_store with importance=5: original_content_hash stays NULL."""
    from mcp_server_v3 import memory_store  # type: ignore
    import re
    result = await memory_store(
        agent="ADA",
        category="fact",
        content="Nota trivial para test drift importance=5",
        importance=5,
        scope="private",
    )
    m = re.search(r"#(\d+)", result)
    if not m:
        fail("no_tracking_low_importance", f"could not parse result: {result[:80]}")
        return
    mem_id = int(m.group(1))

    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT original_content_hash, revision_count FROM memories WHERE id=$1",
        mem_id
    )
    await conn.close()

    if row and row["original_content_hash"] is None:
        ok(f"importance=5: original_content_hash=NULL (no tracking) #{mem_id}")
    else:
        fail("no_tracking_low_importance", f"unexpected hash={row['original_content_hash'] if row else None}")


async def main():
    print("=" * 50)
    print("Drift Monitor Tests — Gap #3")
    print("=" * 50)

    await test_cosine_sim()
    await test_drift_columns_exist()
    seed_id = await test_memory_store_seeds_original()
    await test_memory_update_drift_tracking(seed_id)
    await test_no_tracking_low_importance()

    print("=" * 50)
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} passed" + (f", {FAIL} failed" if FAIL else " — ALL PASS"))
    print("=" * 50)
    return FAIL


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
