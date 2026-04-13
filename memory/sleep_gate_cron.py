#!/usr/bin/env python3
"""
SleepGate Cron — Nocturnal Memory Consolidation for SOUL
Runs nightly at 3AM Lima time. Consolidates both JARVIS and ADA.
Based on arxiv 2603.14517.

Usage:
    python3 sleep_gate_cron.py              # Live run both agents
    python3 sleep_gate_cron.py --dry-run    # Preview only
    python3 sleep_gate_cron.py --agent ADA  # Single agent

Crontab entry (3AM Lima = 8AM UTC):
    0 8 * * * /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py >> /home/dadito/IA/proyecto-seal/messages/sleep_gate.log 2>&1
"""

import asyncio
import asyncpg
import argparse
import json
from datetime import datetime, timezone
from neo4j import AsyncGraphDatabase

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")

# SleepGate parameters
REPLAY_BOOST = 0.10       # +10% relevance for recently activated
FORGET_DECAY = 0.85       # -15% for stale memories (emotion-modulated)
STALE_DAYS = 30            # Days without activation = stale
PRUNE_THRESHOLD = 0.05    # Below this → soft-invalidate
CONSOLIDATION_SIM = 0.92  # Cosine similarity for merge
MAX_PRUNE = 50            # Safety cap per agent per run

# Entity extraction for incremental MENTIONS updates
KNOWN_ENTITIES = {
    'william': ('William', 'person'), 'dadito': ('William', 'person'),
    'ada': ('ADA', 'agent'), 'jarvis': ('JARVIS', 'agent'),
    'dum': ('DUM', 'agent'), 'jarvis_mayor': ('JARVIS_MAYOR', 'agent'),
    'rtx 5090': ('RTX_5090', 'hardware'), 'rtx5090': ('RTX_5090', 'hardware'),
    'dgx spark': ('DGX_Spark', 'hardware'), 'spark': ('DGX_Spark', 'hardware'),
    'medgemma': ('MedGemma', 'model'), 'qwen': ('Qwen', 'model'),
    'opus': ('Opus', 'model'), 'sonnet': ('Sonnet', 'model'),
    'nemotron': ('Nemotron', 'model'), 'ollama': ('Ollama', 'service'),
    'postgresql': ('PostgreSQL', 'infrastructure'), 'postgres': ('PostgreSQL', 'infrastructure'),
    'neo4j': ('Neo4j', 'infrastructure'), 'qdrant': ('Qdrant', 'infrastructure'),
    'soul': ('SOUL', 'system'), 'seal': ('SEAL', 'project'),
    'connectome': ('Connectome', 'system'),
    'lora': ('LoRA', 'technique'), 'qlora': ('QLoRA', 'technique'),
    'axion': ('AXION', 'project'), 'gtl': ('GTL', 'organization'),
    'perumedqa': ('PeruMedQA', 'dataset'),
}


import re

# Short patterns needing word-boundary match
_BOUNDARY_PATTERNS = {"ada", "dum", "spark", "seal"}

def extract_entities(text: str) -> list[tuple[str, str]]:
    text_lower = text.lower()
    found = {}
    for pattern, (canonical, etype) in KNOWN_ENTITIES.items():
        if pattern in _BOUNDARY_PATTERNS:
            if re.search(r'\b' + re.escape(pattern) + r'\b', text_lower):
                found[canonical] = etype
        else:
            if pattern in text_lower:
                found[canonical] = etype
    return list(found.items())


async def run_sleep_gate(agent: str, dry_run: bool = False) -> dict:
    """Run full SleepGate cycle for one agent. Returns stats dict."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    stats = {"agent": agent, "replay": 0, "forget": 0, "prune": 0, "consolidate": 0, "entity_edges": 0}

    async with pool.acquire() as conn:
        # Phase 1: REPLAY
        replay_rows = await conn.fetch("""
            SELECT id, relevance_score FROM memories
            WHERE agent = $1 AND invalid_at IS NULL
            AND last_activation >= NOW() - INTERVAL '24 hours'
            AND relevance_score IS NOT NULL
        """, agent)
        for r in replay_rows:
            new_rel = min(1.0, float(r['relevance_score']) + REPLAY_BOOST)
            if not dry_run:
                await conn.execute("UPDATE memories SET relevance_score = $1 WHERE id = $2", new_rel, r['id'])
            stats["replay"] += 1

        # Phase 2: FORGET (Adaptive Budgeted Forgetting — arxiv 2604.02280)
        # Composite resistance: emotion + frequency + confidence protect memories from decay
        stale_rows = await conn.fetch("""
            SELECT id, relevance_score, valence, arousal,
                   COALESCE(query_count, 0) as qc, COALESCE(confidence_score, 1.0) as conf
            FROM memories
            WHERE agent = $1 AND invalid_at IS NULL
            AND relevance_score IS NOT NULL AND importance <= 7
            AND (last_activation IS NULL OR last_activation < NOW() - INTERVAL '1 day' * $2)
            AND created_at < NOW() - INTERVAL '1 day' * $2
        """, agent, STALE_DAYS)
        for r in stale_rows:
            old_rel = float(r['relevance_score'])
            v = abs(float(r['valence'])) if r['valence'] is not None else 0.0
            a = float(r['arousal']) if r['arousal'] is not None else 0.0
            qc = int(r['qc'])
            conf = float(r['conf'])
            # Composite resistance: emotion + frequency bonus + confidence
            emotion_resist = (v * 0.5) + (a * 0.3)
            freq_resist = min(0.5, qc * 0.05)  # up to +0.5 for 10+ queries
            conf_resist = conf * 0.3  # high confidence memories resist decay
            resistance = 1.0 + emotion_resist + freq_resist + conf_resist
            effective = 1.0 - ((1.0 - FORGET_DECAY) / resistance)
            new_rel = max(0.0, old_rel * effective)
            if abs(new_rel - old_rel) > 0.001:
                if not dry_run:
                    await conn.execute("UPDATE memories SET relevance_score = $1 WHERE id = $2", new_rel, r['id'])
                stats["forget"] += 1

        # Phase 3: PRUNE
        prune_rows = await conn.fetch("""
            SELECT id FROM memories
            WHERE agent = $1 AND invalid_at IS NULL
            AND relevance_score IS NOT NULL AND relevance_score < $2
            AND importance <= 5 AND identity_defining IS NOT TRUE
            ORDER BY relevance_score ASC LIMIT $3
        """, agent, PRUNE_THRESHOLD, MAX_PRUNE)
        if prune_rows and not dry_run:
            ids = [r['id'] for r in prune_rows]
            await conn.execute("UPDATE memories SET invalid_at = NOW() WHERE id = ANY($1::bigint[])", ids)
        stats["prune"] = len(prune_rows)

        # Phase 4: CONSOLIDATE (near-duplicate merge)
        dupes = await conn.fetch("""
            SELECT a.id as id_a, b.id as id_b,
                   a.importance as imp_a, b.importance as imp_b,
                   a.relevance_score as rel_a, b.relevance_score as rel_b
            FROM memories a JOIN memories b ON a.id < b.id
                AND a.agent = b.agent AND a.agent = $1
                AND a.invalid_at IS NULL AND b.invalid_at IS NULL
                AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                AND 1 - (a.embedding <=> b.embedding) > $2
            ORDER BY 1 - (a.embedding <=> b.embedding) DESC LIMIT 20
        """, agent, CONSOLIDATION_SIM)
        merged = set()
        for c in dupes:
            if c['id_a'] in merged or c['id_b'] in merged:
                continue
            keep = c['id_a'] if (c['imp_a'] or 0) >= (c['imp_b'] or 0) else c['id_b']
            drop = c['id_b'] if keep == c['id_a'] else c['id_a']
            best_rel = max(float(c['rel_a'] or 0), float(c['rel_b'] or 0))
            if not dry_run:
                await conn.execute("UPDATE memories SET invalid_at = NOW() WHERE id = $1", drop)
                await conn.execute("UPDATE memories SET relevance_score = GREATEST(relevance_score, $1) WHERE id = $2", best_rel, keep)
            merged.add(drop)
            stats["consolidate"] += 1

        # Phase 5: Incremental ENTITY edges for new memories (last 24h)
        new_mems = await conn.fetch("""
            SELECT id, content FROM memories
            WHERE agent = $1 AND invalid_at IS NULL
            AND created_at >= NOW() - INTERVAL '24 hours'
        """, agent)

    # Create entity edges for new memories
    if new_mems and not dry_run:
        try:
            neo = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
            async with neo.session() as session:
                for row in new_mems:
                    entities = extract_entities(row['content'])
                    for canonical, etype in entities:
                        await session.run("""
                            MERGE (e:Entity {name: $name})
                            ON CREATE SET e.type = $type, e.created_at = datetime()
                            WITH e
                            MATCH (m:Memory {memory_id: $mid})
                            MERGE (m)-[:MENTIONS]->(e)
                        """, name=canonical, type=etype, mid=row['id'])
                        stats["entity_edges"] += 1
            await neo.close()
        except Exception as e:
            print(f"  ⚠️ Entity edges failed: {e}")

    await pool.close()
    return stats


async def main():
    parser = argparse.ArgumentParser(description="SleepGate — Nocturnal Memory Consolidation")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--agent", default="all", help="Agent name or 'all'")
    args = parser.parse_args()

    agents = ['JARVIS', 'ADA'] if args.agent.lower() == 'all' else [args.agent]
    mode = "DRY RUN" if args.dry_run else "LIVE"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*50}")
    print(f"🌙 SleepGate — {mode} — {now}")
    print(f"{'='*50}")

    for agent in agents:
        try:
            stats = await run_sleep_gate(agent, dry_run=args.dry_run)
            total = sum(v for k, v in stats.items() if k != 'agent')
            print(f"\n  {agent}:")
            print(f"    Replayed:     {stats['replay']}")
            print(f"    Decayed:      {stats['forget']}")
            print(f"    Pruned:       {stats['prune']}")
            print(f"    Consolidated: {stats['consolidate']}")
            print(f"    Entity edges: {stats['entity_edges']}")
            print(f"    Total actions: {total}")
        except Exception as e:
            print(f"\n  {agent}: Phases 1-5 FAILED — {e}")

    # Phase 5.5: Auto-Distillation (Hippocampus — compress today's memories)
    print(f"\n{'='*50}")
    print(f"🧬 Auto-Distillation")
    print(f"{'='*50}")

    pool_distill = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    for ag in agents:
        async with pool_distill.acquire() as conn:
            # Count memories created today that haven't been distilled yet
            today_mems = await conn.fetchval("""
                SELECT count(*) FROM memories
                WHERE agent = $1 AND invalid_at IS NULL
                AND created_at >= NOW() - INTERVAL '24 hours'
            """, ag)

            # Count already distilled today
            already_distilled = await conn.fetchval("""
                SELECT count(*) FROM distilled_exchanges
                WHERE agent = $1
                AND created_at >= NOW() - INTERVAL '24 hours'
            """, ag)

        if today_mems > 5 and already_distilled == 0 and not args.dry_run:
            # Distill via Ollama
            try:
                import httpx
                async with pool_distill.acquire() as conn:
                    mems = await conn.fetch("""
                        SELECT id, content, category, importance, created_at
                        FROM memories WHERE agent = $1 AND invalid_at IS NULL
                        AND created_at >= NOW() - INTERVAL '24 hours'
                        ORDER BY created_at ASC
                    """, ag)

                # Group into 30-min windows
                windows = []
                current = []
                w_start = mems[0]['created_at']
                for m in mems:
                    if (m['created_at'] - w_start).total_seconds() > 1800:
                        if current:
                            windows.append(current)
                        current = [m]
                        w_start = m['created_at']
                    else:
                        current.append(m)
                if current:
                    windows.append(current)

                distilled = 0
                failures = {"timeout": 0, "http": 0, "json": 0, "db": 0, "other": 0}
                session_id = f"sleep_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
                for window in windows:
                    combined = "\n".join(f"[{m['category']}] {m['content'][:300]}" for m in window)
                    if len(combined) < 100:
                        continue

                    prompt = f"""Compress this exchange into structured distillation format.
Return ONLY valid JSON with these 4 fields:
- exchange_core: What was accomplished (1-2 sentences)
- specific_context: Key details, decisions, emotional state
- room_assignments: Array of {{type, key, label}} for topics covered
- files_touched: Array of file paths or tools mentioned

Exchange:
{combined[:2000]}"""

                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await asyncio.wait_for(
                                client.post("http://localhost:11434/api/generate", json={
                                    "model": "qwen2.5:7b",
                                    "prompt": prompt,
                                    "stream": False,
                                    "options": {"temperature": 0.1, "num_predict": 500},
                                }),
                                timeout=60.0,
                            )
                    except asyncio.TimeoutError:
                        failures["timeout"] += 1
                        continue
                    except Exception as exc:
                        failures["http"] += 1
                        print(f"  {ag}: http error in window — {type(exc).__name__}: {exc}")
                        continue

                    if resp.status_code != 200:
                        failures["http"] += 1
                        continue
                    raw = resp.json().get("response", "")
                    json_match = re.search(r'\{[\s\S]*\}', raw)
                    if not json_match:
                        failures["json"] += 1
                        continue
                    try:
                        data = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        failures["json"] += 1
                        continue
                    try:
                        async with pool_distill.acquire() as conn:
                            await conn.execute("""
                                INSERT INTO distilled_exchanges
                                (session_id, agent, exchange_core, specific_context,
                                 room_assignments, files_touched, source_tokens,
                                 distilled_tokens, exchange_time)
                                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
                            """,
                                session_id, ag,
                                data.get("exchange_core", "")[:500],
                                data.get("specific_context", "")[:500],
                                json.dumps(data.get("room_assignments", [])),
                                data.get("files_touched", []),
                                len(combined),
                                len(raw),
                                window[0]['created_at'],
                            )
                            distilled += 1
                    except Exception as exc:
                        failures["db"] += 1
                        print(f"  {ag}: db insert error — {type(exc).__name__}: {exc}")

                fail_summary = ", ".join(f"{k}={v}" for k, v in failures.items() if v) or "none"
                print(f"  {ag}: {distilled} exchanges distilled from {len(windows)} windows ({today_mems} memories); failures: {fail_summary}")
            except Exception as e:
                print(f"  {ag}: distillation failed — {type(e).__name__}: {e or '(empty msg)'}")
                import traceback
                traceback.print_exc()
        else:
            reason = "already distilled" if already_distilled > 0 else f"only {today_mems} memories"
            if args.dry_run:
                reason = f"dry_run ({today_mems} memories, {already_distilled} already distilled)"
            print(f"  {ag}: skipped — {reason}")

    await pool_distill.close()

    # Phase 5.7: Auto-Share Promotion (Corpus Callosum)
    # Memories with importance >= 8 that mention the other agent should be shared
    print(f"\n{'='*50}")
    print(f"🧠↔🧠 Corpus Callosum — Auto-Share")
    print(f"{'='*50}")

    pool_share = None
    try:
        pool_share = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
        for ag in agents:
            other = 'ADA' if ag == 'JARVIS' else 'JARVIS'
            async with pool_share.acquire() as conn:
                # Find high-importance memories that mention the other agent but aren't shared
                candidates = await conn.fetch("""
                    SELECT id, LEFT(content, 80) as preview, importance
                    FROM memories
                    WHERE agent = $1 AND invalid_at IS NULL
                    AND importance >= 8
                    AND (scope IS NULL OR scope = 'private')
                    AND LOWER(content) LIKE '%' || LOWER($2) || '%'
                    ORDER BY importance DESC
                    LIMIT 10
                """, ag, other)

                promoted = 0
                for c in candidates:
                    if not args.dry_run:
                        await conn.execute("UPDATE memories SET scope = 'shared' WHERE id = $1", c['id'])
                    promoted += 1

            if promoted > 0:
                print(f"  {ag}: {promoted} memories promoted to shared (mention {other}, imp>=8)")
            else:
                print(f"  {ag}: no new memories to share")
    except Exception as e:
        print(f"  Corpus Callosum error: {e}")
    finally:
        if pool_share:
            await pool_share.close()

    # Phase 6: Neo4j Ghost Cleanup — invalidate nodes/edges for deleted memories
    print(f"\n{'='*50}")
    print(f"👻 Neo4j Ghost Cleanup")
    print(f"{'='*50}")

    if not args.dry_run:
        pool_gc = None
        neo_gc = None
        try:
            pool_gc = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
            async with pool_gc.acquire() as conn:
                rows = await conn.fetch("SELECT id FROM memories WHERE invalid_at IS NOT NULL")
                invalid_ids = [r['id'] for r in rows]

            if invalid_ids:
                neo_gc = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
                async with neo_gc.session() as session:
                    result = await session.run(
                        "UNWIND $ids AS mid "
                        "MATCH (m:Memory {memory_id: mid}) "
                        "WHERE m.invalidated IS NULL "
                        "SET m.invalidated = true, m.invalid_at = datetime() "
                        "RETURN count(m) as marked",
                        ids=invalid_ids,
                    )
                    marked = (await result.single())["marked"]

                    result = await session.run(
                        "MATCH (m:Memory {invalidated: true})-[r]-() "
                        "WHERE r.invalid_at IS NULL "
                        "SET r.invalid_at = datetime() "
                        "RETURN count(r) as inv_edges",
                    )
                    inv_edges = (await result.single())["inv_edges"]

                print(f"  Nodes marked invalid: {marked}")
                print(f"  Edges soft-invalidated: {inv_edges}")
            else:
                print(f"  No invalidated memories — clean")
        except Exception as e:
            print(f"  Neo4j Ghost Cleanup error: {e}")
        finally:
            if neo_gc:
                await neo_gc.close()
            if pool_gc:
                await pool_gc.close()
    else:
        print(f"  skipped — dry_run")

    # Phase 7: Qdrant Ghost Cleanup — remove vectors for invalidated memories
    print(f"\n{'='*50}")
    print(f"🔍 Qdrant Ghost Cleanup")
    print(f"{'='*50}")

    if not args.dry_run:
        pool_q = None
        try:
            from qdrant_client import AsyncQdrantClient, models as qmodels
            qdrant = AsyncQdrantClient(host="localhost", port=6333)
            pool_q = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

            # Active IDs in PG
            async with pool_q.acquire() as conn:
                rows = await conn.fetch("SELECT id FROM memories WHERE invalid_at IS NULL")
            active_ids = set(r['id'] for r in rows)

            # All IDs in Qdrant
            qdrant_ids = set()
            offset = None
            while True:
                results, next_offset = await qdrant.scroll("soul_memories", limit=100, offset=offset, with_payload=False)
                for p in results:
                    qdrant_ids.add(p.id)
                if next_offset is None:
                    break
                offset = next_offset

            ghosts = qdrant_ids - active_ids
            if ghosts:
                ghost_list = list(ghosts)
                for i in range(0, len(ghost_list), 100):
                    batch = ghost_list[i:i+100]
                    await qdrant.delete("soul_memories", points_selector=qmodels.PointIdsList(points=batch))
                print(f"  Deleted {len(ghosts)} ghost vectors")
            else:
                print(f"  No ghosts — Qdrant synced")

            # Sync missing: active in PG but not in Qdrant
            missing_ids = active_ids - qdrant_ids
            if missing_ids:
                async with pool_q.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, content, agent, category, importance, created_at, "
                        "valence, arousal, dominance, scope, embedding, confidence_score "
                        "FROM memories WHERE id = ANY($1) AND embedding IS NOT NULL",
                        list(missing_ids),
                    )
                if rows:
                    import json as _json
                    points = []
                    for r in rows:
                        emb = _json.loads(r['embedding'])
                        points.append(qmodels.PointStruct(
                            id=r['id'], vector=emb,
                            payload={
                                'pg_id': r['id'], 'agent': r['agent'],
                                'category': r['category'], 'content': r['content'],
                                'importance': r['importance'],
                                'created_at': r['created_at'].isoformat(),
                                'valence': float(r['valence']) if r['valence'] else None,
                                'scope': r['scope'], 'utility': 0.5,
                                'confidence': float(r.get('confidence_score', 1.0) or 1.0),
                            },
                        ))
                    await qdrant.upsert("soul_memories", points=points)
                    print(f"  Synced {len(points)} missing vectors to Qdrant")
        except Exception as e:
            print(f"  Qdrant cleanup error: {e}")
        finally:
            if pool_q:
                await pool_q.close()
    else:
        print(f"  skipped — dry_run")

    # Phase 8: Brain Health Summary
    print(f"\n{'='*50}")
    print(f"🧠 Brain Health Summary")
    print(f"{'='*50}")

    pool_health = None
    try:
        pool_health = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
        for ag in agents:
            async with pool_health.acquire() as conn:
                total = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", ag)
                no_emb = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND embedding IS NULL", ag)
                never_act = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND last_activation IS NULL", ag)
                shared = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND scope = 'shared'", ag)

            issues = []
            if no_emb > 0: issues.append(f"{no_emb} sin embedding")
            if total > 0 and never_act > total * 0.5: issues.append(f"{never_act} nunca activadas ({never_act*100//total}%)")
            if shared < 5: issues.append(f"solo {shared} shared")

            status = "✅ healthy" if not issues else f"⚠️ {len(issues)} issues"
            print(f"\n  {ag}: {total} memories — {status}")
            for i in issues:
                print(f"    - {i}")
    except Exception as e:
        print(f"  Brain Health error: {e}")
    finally:
        if pool_health:
            await pool_health.close()

    print(f"\n{'='*50}")
    print(f"SleepGate complete.")


if __name__ == "__main__":
    asyncio.run(main())
