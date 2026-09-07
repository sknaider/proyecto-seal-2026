#!/usr/bin/env python3
"""Daily instinct maintenance cron job.

Run via cron or systemd timer:
  0 6 * * * /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/instinct_cron.py

Operations:
  1. Decay — reduce confidence of inactive instincts (Ebbinghaus)
  2. Consolidate — scan memories for new instinct candidates
  3. Report — write summary to event_log
"""
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")

sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from db import get_pool, close_pool

DECAY_RATE = 0.01       # -1% per day of inactivity
MIN_STRENGTH = 0.05     # below this → soft-invalidate
SIMILARITY_THRESHOLD = 0.85


def decay_reference(created_at, last_activated_at, last_decayed_at):
    """Use the newest evidence point so repeated runs never reapply full age."""
    return max(
        value
        for value in (created_at, last_activated_at, last_decayed_at)
        if value is not None
    )


def decayed_strength(strength: float, elapsed_days: float, success_count: int) -> float:
    """Exponentially decay only the elapsed interval since the latest evidence."""
    elapsed_days = max(0.0, elapsed_days)
    frequency_factor = success_count / (1.0 + success_count)
    effective_lambda = DECAY_RATE * (1.0 - 0.7 * frequency_factor)
    return max(0.0, strength * math.exp(-effective_lambda * elapsed_days))


async def decay_instincts(pool) -> dict:
    """Apply Ebbinghaus decay to all active instincts (v3 schema: strength, invalid_at, success_count)."""
    rows = await pool.fetch("""
        SELECT i.id, i.agent, i.strength, i.created_at, i.trigger_condition,
               i.success_count,
               latest.last_activated_at,
               NULLIF(i.metadata->>'last_decayed_at', '')::timestamptz AS last_decayed_at
        FROM instincts i
        LEFT JOIN LATERAL (
            SELECT MAX(a.created_at) AS last_activated_at
            FROM instinct_activations a
            WHERE a.instinct_id = i.id
        ) latest ON TRUE
        WHERE i.invalid_at IS NULL
    """)

    now = datetime.now(LIMA_TZ)
    decayed = 0
    deactivated = 0

    for r in rows:
        if r["created_at"] is None:
            continue

        last = decay_reference(
            r["created_at"], r["last_activated_at"], r["last_decayed_at"]
        )

        days_since = (now - last).total_seconds() / 86400
        if days_since < 1:
            continue

        new_strength = decayed_strength(
            float(r["strength"]), days_since, int(r["success_count"] or 0)
        )

        if new_strength < MIN_STRENGTH:
            await pool.execute(
                """UPDATE instincts
                   SET invalid_at = now(), strength = $2,
                       metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb),
                                            '{last_decayed_at}', to_jsonb(now()), true)
                   WHERE id = $1""",
                r["id"], new_strength,
            )
            deactivated += 1
            print(f"  DEACTIVATED #{r['id']} ({r['agent']}): {r['trigger_condition'][:50]} — strength={new_strength:.3f}")
        else:
            await pool.execute(
                """UPDATE instincts
                   SET strength = $2,
                       metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb),
                                            '{last_decayed_at}', to_jsonb(now()), true)
                   WHERE id = $1""",
                r["id"], new_strength,
            )
        decayed += 1

    # TTL: prune unconfirmed instincts older than 30 days
    pruned = 0
    ttl_rows = await pool.fetch("""
        SELECT id, agent, trigger_condition, strength, created_at
        FROM instincts
        WHERE invalid_at IS NULL AND strength < 0.5 AND success_count = 0
          AND created_at < now() - interval '30 days'
    """)
    for r in ttl_rows:
        await pool.execute("UPDATE instincts SET invalid_at = now() WHERE id = $1", r["id"])
        pruned += 1
        print(f"  PRUNED (TTL) #{r['id']} ({r['agent']}): {r['trigger_condition'][:50]} — never activated, 30d old")

    # Warn about instincts expiring soon
    expiring = await pool.fetch("""
        SELECT id, agent, trigger_condition, strength, created_at
        FROM instincts
        WHERE invalid_at IS NULL AND strength < 0.5 AND success_count = 0
          AND created_at < now() - interval '23 days'
          AND created_at >= now() - interval '30 days'
    """)
    for r in expiring:
        days_left = 30 - (datetime.now(LIMA_TZ) - r["created_at"]).days
        print(f"  ⚠️ EXPIRING #{r['id']} ({r['agent']}): {r['trigger_condition'][:50]} — {days_left}d left")

    return {"decayed": decayed, "deactivated": deactivated, "pruned": pruned}


async def find_candidates(pool) -> dict:
    """Find semantic clusters in corrections that could become new instincts."""
    agents = await pool.fetch("SELECT DISTINCT agent FROM memories WHERE invalid_at IS NULL")
    total_candidates = 0

    for agent_row in agents:
        agent = agent_row["agent"]
        pairs = await pool.fetch("""
            WITH pairs AS (
                SELECT a.id as id_a, b.id as id_b,
                       1 - (a.embedding <=> b.embedding) as similarity
                FROM memories a
                JOIN memories b ON a.id < b.id
                WHERE a.agent = $1 AND a.category = 'correction' AND a.invalid_at IS NULL
                  AND b.agent = $1 AND b.category = 'correction' AND b.invalid_at IS NULL
                  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                  AND 1 - (a.embedding <=> b.embedding) > $2
            )
            SELECT COUNT(*) as cnt FROM pairs
        """, agent, SIMILARITY_THRESHOLD)

        cnt = pairs[0]["cnt"] if pairs else 0
        if cnt > 0:
            total_candidates += cnt
            print(f"  {agent}: {cnt} similar correction pairs found")

    return {"total_candidates": total_candidates}


async def main():
    print(f"=== Instinct Cron — {datetime.now(LIMA_TZ).isoformat()} ===\n")

    pool = await get_pool()

    # 1. Decay
    print("Step 1: Decay inactive instincts")
    decay_result = await decay_instincts(pool)
    print(f"  Processed: {decay_result['decayed']}, Deactivated: {decay_result['deactivated']}\n")

    # 2. Consolidation candidates
    print("Step 2: Scan for new instinct candidates")
    candidates = await find_candidates(pool)
    print(f"  Total candidates: {candidates['total_candidates']}\n")

    # 3. Summary
    stats = await pool.fetch("""
        SELECT agent, COUNT(*) as total,
               COUNT(*) FILTER (WHERE strength >= 0.7) as strong,
               COUNT(*) FILTER (WHERE strength >= 0.5 AND strength < 0.7) as active,
               COUNT(*) FILTER (WHERE strength < 0.5) as weak
        FROM instincts WHERE invalid_at IS NULL
        GROUP BY agent
    """)

    print("Instinct Summary:")
    for s in stats:
        print(f"  {s['agent']}: {s['total']} total (strong={s['strong']}, active={s['active']}, weak={s['weak']})")

    # 3b. Procedural memory decay (hit >= 3, success_rate < 50%)
    print("Step 3b: Prune low-performing procedural memories")
    proc_pruned = await pool.fetch("""
        SELECT id, agent, query, hit_count, success_count
        FROM procedural_memories
        WHERE active = true AND hit_count >= 3
          AND success_count::float / NULLIF(hit_count, 1) < 0.5
    """)
    for p in proc_pruned:
        await pool.execute("UPDATE procedural_memories SET active = false WHERE id = $1", p["id"])
        rate = p["success_count"] / max(p["hit_count"], 1)
        print(f"  DEACTIVATED proc #{p['id']} ({p['agent']}): {p['query'][:50]} — rate={rate:.2f}")
    print(f"  Pruned: {len(proc_pruned)} procedural memories\n")

    # 4. SLEEP CONSOLIDATION (SleepGate pattern — replay, forget, prune)
    print("Step 4: Sleep consolidation (memory maintenance)")
    sleep_stats = {"replayed": 0, "decayed": 0, "pruned": 0}

    # 4a. REPLAY: boost recently activated memories (+10% importance weight)
    replayed = await pool.execute("""
        UPDATE memories SET
            importance = LEAST(importance + 1, 10)
        WHERE last_activation > now() - interval '24 hours'
          AND invalid_at IS NULL
          AND importance < 10
          AND query_count >= 2
    """)
    sleep_stats["replayed"] = int(replayed.split()[-1]) if replayed else 0
    print(f"  REPLAY: boosted {sleep_stats['replayed']} recently-activated memories")

    # 4b. FORGET: decay episodic memories not activated in 60+ days
    decayed_mems = await pool.execute("""
        UPDATE memories SET
            importance = GREATEST(importance - 1, 1)
        WHERE invalid_at IS NULL
          AND importance > 1
          AND (last_activation IS NULL OR last_activation < now() - interval '60 days')
          AND created_at < now() - interval '60 days'
          AND category NOT IN ('milestone', 'decision', 'correction')
    """)
    sleep_stats["decayed"] = int(decayed_mems.split()[-1]) if decayed_mems else 0
    print(f"  FORGET: decayed {sleep_stats['decayed']} dormant memories")

    # 4c. PRUNE: archive very low importance memories (imp=1, never activated, >90 days old)
    pruned = await pool.fetch("""
        SELECT id, agent, content FROM memories
        WHERE invalid_at IS NULL
          AND importance <= 1
          AND (last_activation IS NULL AND query_count = 0)
          AND created_at < now() - interval '90 days'
          AND category NOT IN ('milestone', 'decision', 'correction')
        LIMIT 20
    """)
    for p in pruned:
        await pool.execute("""
            UPDATE memories SET invalid_at = now(),
                metadata = COALESCE(metadata, '{}'::jsonb) || '{"archived_reason": "sleep_consolidation"}'::jsonb
            WHERE id = $1
        """, p["id"])
    sleep_stats["pruned"] = len(pruned)
    if pruned:
        print(f"  PRUNE: archived {len(pruned)} dormant memories")
        for p in pruned[:5]:
            print(f"    #{p['id']} ({p['agent']}): {p['content'][:60]}")
    print()

    # 5. Log event
    await pool.execute("""
        INSERT INTO event_log (created_at, agent, event_type, content, metadata)
        VALUES (now(), 'SYSTEM', 'heartbeat', $1, $2)
    """,
        f"Daily cron: instincts decayed={decay_result['decayed']}, deactivated={decay_result['deactivated']}, candidates={candidates['total_candidates']}. Sleep: replayed={sleep_stats['replayed']}, forgot={sleep_stats['decayed']}, pruned={sleep_stats['pruned']}",
        json.dumps({**decay_result, **candidates, "sleep": sleep_stats}),
    )

    print("\n✅ Instinct cron completed")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
