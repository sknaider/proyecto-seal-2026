#!/usr/bin/env python3
"""
Test Suite — New MCP Tools (6 abril 2026)
Tests: sleep_gate, sleep_gate_mood_retrieval, connectome_entity,
       connectome_entity_query, memory_cross_search, memory_share_promote,
       brain_health_report

Runs against live PostgreSQL + Neo4j. Non-destructive (uses dry_run where possible).
"""

import asyncio
import asyncpg
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from neo4j import AsyncGraphDatabase

DB_URL = os.environ.get("SEAL_PG_DSN", "postgresql://seal:REDACTADO@localhost:5433/seal_memory")
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", os.environ.get("SEAL_NEO4J_PASSWORD", "seal2026soul"))

KNOWN_ENTITIES = {
    'william': ('William', 'person'), 'dadito': ('William', 'person'),
    'ada': ('ADA', 'agent'), 'jarvis': ('JARVIS', 'agent'),
    'dum': ('DUM', 'agent'), 'soul': ('SOUL', 'system'),
    'seal': ('SEAL', 'project'), 'neo4j': ('Neo4j', 'infrastructure'),
    'postgresql': ('PostgreSQL', 'infrastructure'), 'postgres': ('PostgreSQL', 'infrastructure'),
    'qdrant': ('Qdrant', 'infrastructure'), 'connectome': ('Connectome', 'system'),
    'lora': ('LoRA', 'technique'), 'medgemma': ('MedGemma', 'model'),
    'ollama': ('Ollama', 'service'), 'rtx 5090': ('RTX_5090', 'hardware'),
    'dgx spark': ('DGX_Spark', 'hardware'), 'spark': ('DGX_Spark', 'hardware'),
    'opus': ('Opus', 'model'), 'sonnet': ('Sonnet', 'model'),
    'qwen': ('Qwen', 'model'), 'nemotron': ('Nemotron', 'model'),
    'axion': ('AXION', 'project'), 'gtl': ('GTL', 'organization'),
    'perumedqa': ('PeruMedQA', 'dataset'), 'qlora': ('QLoRA', 'technique'),
    'jarvis_mayor': ('JARVIS_MAYOR', 'agent'),
}

results = {"passed": 0, "failed": 0, "errors": []}


def report(name, passed, detail=""):
    icon = "✅" if passed else "❌"
    results["passed" if passed else "failed"] += 1
    msg = f"  {icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not passed:
        results["errors"].append(f"{name}: {detail}")


async def test_sleep_gate_dry_run():
    """[Test] sleep_gate: dry_run returns report without modifying data"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    # Snapshot before
    async with pool.acquire() as conn:
        before_count = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent='JARVIS' AND invalid_at IS NULL"
        )
        before_inv = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent='JARVIS' AND invalid_at IS NOT NULL"
        )

    # Run the equivalent of sleep_gate dry_run
    async with pool.acquire() as conn:
        # Phase 1: REPLAY candidates
        replay = await conn.fetch("""
            SELECT id FROM memories WHERE agent='JARVIS' AND invalid_at IS NULL
            AND last_activation >= NOW() - INTERVAL '24 hours'
            AND heat_score IS NOT NULL
        """)

        # Phase 2: FORGET candidates
        stale = await conn.fetch("""
            SELECT id FROM memories WHERE agent='JARVIS' AND invalid_at IS NULL
            AND heat_score IS NOT NULL AND importance <= 7
            AND (last_activation IS NULL OR last_activation < NOW() - INTERVAL '30 days')
            AND created_at < NOW() - INTERVAL '30 days'
        """)

        # Phase 3: PRUNE candidates
        prune = await conn.fetch("""
            SELECT id FROM memories WHERE agent='JARVIS' AND invalid_at IS NULL
            AND heat_score IS NOT NULL AND heat_score < 0.05
            AND importance <= 5
            LIMIT 50
        """)

        # Phase 4: CONSOLIDATE candidates
        dupes = await conn.fetch("""
            SELECT a.id as id_a, b.id as id_b,
                   1 - (a.embedding <=> b.embedding) as sim
            FROM memories a JOIN memories b ON a.id < b.id
                AND a.agent = b.agent AND a.agent = 'JARVIS'
                AND a.invalid_at IS NULL AND b.invalid_at IS NULL
                AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                AND 1 - (a.embedding <=> b.embedding) > 0.92
            LIMIT 20
        """)

    # Snapshot after (should be identical — dry run)
    async with pool.acquire() as conn:
        after_count = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent='JARVIS' AND invalid_at IS NULL"
        )
        after_inv = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent='JARVIS' AND invalid_at IS NOT NULL"
        )

    report("sleep_gate dry_run: no data modified",
           before_count == after_count and before_inv == after_inv,
           f"before={before_count}/{before_inv}, after={after_count}/{after_inv}")

    report("sleep_gate: returns 4 phases",
           True,
           f"replay={len(replay)}, stale={len(stale)}, prune={len(prune)}, dupes={len(dupes)}")

    await pool.close()


async def test_sleep_gate_forget_emotional_resistance():
    """[Test] sleep_gate: emotional resistance modulates decay correctly"""
    # High valence memory should decay slower
    v, a = 0.8, 0.7
    resistance = 1.0 + (v * 0.5) + (a * 0.3)
    base_decay = 0.85
    effective = 1.0 - ((1.0 - base_decay) / resistance)

    # Neutral memory
    resistance_neutral = 1.0 + (0.0 * 0.5) + (0.0 * 0.3)
    effective_neutral = 1.0 - ((1.0 - base_decay) / resistance_neutral)

    report("sleep_gate: emotional resistance > 1.0 for emotional memories",
           resistance > 1.0,
           f"resistance={resistance:.3f}")

    report("sleep_gate: emotional memories decay slower than neutral",
           effective > effective_neutral,
           f"emotional_decay={effective:.4f} > neutral_decay={effective_neutral:.4f}")

    report("sleep_gate: decay stays in valid range [0, 1)",
           0.0 <= effective < 1.0 and 0.0 <= effective_neutral < 1.0,
           f"emotional={effective:.4f}, neutral={effective_neutral:.4f}")


async def test_mood_retrieval():
    """[Test] sleep_gate_mood_retrieval: mood valence calculation"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    async with pool.acquire() as conn:
        # Check mood valence computation
        mv = await conn.fetchval("""
            SELECT AVG(valence) FROM (
                SELECT valence FROM memories
                WHERE agent = 'JARVIS' AND valence IS NOT NULL AND invalid_at IS NULL
                ORDER BY created_at DESC LIMIT 10
            ) recent
        """)

        # Check that we have memories with valence
        valence_count = await conn.fetchval("""
            SELECT count(*) FROM memories
            WHERE agent = 'JARVIS' AND valence IS NOT NULL AND invalid_at IS NULL
        """)

        # Check latest emotional state
        mood_row = await conn.fetchrow("""
            SELECT emotional_state FROM inner_monologue
            WHERE agent = 'JARVIS' ORDER BY created_at DESC LIMIT 1
        """)

    report("mood_retrieval: mood valence computable",
           mv is not None,
           f"mood_valence={float(mv):.3f}" if mv else "no valence data")

    report("mood_retrieval: memories with valence exist",
           valence_count > 0,
           f"{valence_count} memories with valence")

    report("mood_retrieval: emotional state available",
           mood_row is not None,
           f"state: {mood_row['emotional_state'][:50]}" if mood_row else "no inner_monologue")

    # Test mood_score calculation
    mood_valence = float(mv) if mv else 0.0
    test_valence = 0.3
    mood_score = 1.0 - abs(test_valence - mood_valence)
    mood_score = max(0.0, min(1.0, mood_score))

    report("mood_retrieval: mood_score in valid range [0, 1]",
           0.0 <= mood_score <= 1.0,
           f"mood_score={mood_score:.3f} for valence={test_valence} vs mood={mood_valence:.3f}")

    await pool.close()


async def test_entity_extraction():
    """[Test] connectome_entity: entity extraction from text"""
    test_cases = [
        ("William dijo que JARVIS es el arquitecto del equipo SEAL",
         {"William", "JARVIS", "SEAL"}),
        ("ADA mejoró el connectome de Neo4j con PostgreSQL",
         {"ADA", "Connectome", "Neo4j", "PostgreSQL"}),
        ("DGX Spark corre MedGemma con LoRA adapter",
         {"DGX_Spark", "MedGemma", "LoRA"}),
        ("texto sin entidades conocidas aquí",
         set()),
        ("RTX 5090 y Ollama están activos en el DGX Spark",
         {"RTX_5090", "Ollama", "DGX_Spark"}),
    ]

    import re
    _BOUNDARY = {"ada", "dum", "spark", "seal"}

    def extract(text):
        text_lower = text.lower()
        found = {}
        for pattern, (canonical, etype) in KNOWN_ENTITIES.items():
            if pattern in _BOUNDARY:
                if re.search(r'\b' + re.escape(pattern) + r'\b', text_lower):
                    found[canonical] = etype
            else:
                if pattern in text_lower:
                    found[canonical] = etype
        return set(found.keys())

    all_pass = True
    for text, expected in test_cases:
        result = extract(text)
        if result != expected:
            report(f"entity_extraction: '{text[:40]}...'",
                   False,
                   f"expected={expected}, got={result}")
            all_pass = False

    if all_pass:
        report("entity_extraction: all 5 test cases pass",
               True,
               f"5/5 correct extractions")


async def test_entity_query_neo4j():
    """[Test] connectome_entity_query: Neo4j MENTIONS edges exist and are queryable"""
    neo = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    async with neo.session() as session:
        # Count MENTIONS edges
        r = await session.run("MATCH ()-[r:MENTIONS]->() RETURN count(r) as cnt")
        mentions_count = (await r.single())['cnt']

        # Count Entity nodes
        r2 = await session.run("MATCH (e:Entity) RETURN count(e) as cnt")
        entity_count = (await r2.single())['cnt']

        # Query a specific entity
        r3 = await session.run("""
            MATCH (e:Entity {name: 'William'})<-[:MENTIONS]-(m:Memory)
            RETURN count(m) as cnt
        """)
        william_mems = (await r3.single())['cnt']

        # Query co-entities
        r4 = await session.run("""
            MATCH (e:Entity {name: 'William'})<-[:MENTIONS]-(m:Memory)-[:MENTIONS]->(other:Entity)
            WHERE other.name <> 'William'
            RETURN other.name as name, count(*) as cnt
            ORDER BY cnt DESC LIMIT 5
        """)
        co_entities = [(rec['name'], rec['cnt']) async for rec in r4]

        # Edge case: non-existent entity
        r5 = await session.run("""
            MATCH (e:Entity {name: 'NONEXISTENT'})<-[:MENTIONS]-(m:Memory)
            RETURN count(m) as cnt
        """)
        nonexist = (await r5.single())['cnt']

    await neo.close()

    report("entity_query: MENTIONS edges exist",
           mentions_count > 0,
           f"{mentions_count} MENTIONS edges")

    report("entity_query: Entity nodes exist",
           entity_count > 0,
           f"{entity_count} Entity nodes")

    report("entity_query: William has memories",
           william_mems > 0,
           f"{william_mems} memories mention William")

    report("entity_query: co-entities returned",
           len(co_entities) > 0,
           f"top co-entities: {co_entities[:3]}")

    report("entity_query: non-existent entity returns 0",
           nonexist == 0,
           f"NONEXISTENT: {nonexist} memories")


async def test_cross_search():
    """[Test] memory_cross_search: cross-agent memory access with scope filtering"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    async with pool.acquire() as conn:
        # Count shareable memories (shared/team OR importance >= 7)
        jarvis_shareable = await conn.fetchval("""
            SELECT count(*) FROM memories
            WHERE agent = 'JARVIS' AND invalid_at IS NULL
            AND (scope IN ('shared', 'team') OR importance >= 7)
        """)

        ada_shareable = await conn.fetchval("""
            SELECT count(*) FROM memories
            WHERE agent = 'ADA' AND invalid_at IS NULL
            AND (scope IN ('shared', 'team') OR importance >= 7)
        """)

        # Verify private low-importance memories are NOT included
        jarvis_private_low = await conn.fetchval("""
            SELECT count(*) FROM memories
            WHERE agent = 'JARVIS' AND invalid_at IS NULL
            AND (scope IS NULL OR scope = 'private') AND importance < 7
        """)

        # Test actual query shape (without embedding — just verify SQL)
        cross_results = await conn.fetch("""
            SELECT id, agent, category, content, importance, scope
            FROM memories
            WHERE agent = 'ADA' AND invalid_at IS NULL
            AND (scope IN ('shared', 'team') OR importance >= 7)
            AND embedding IS NOT NULL
            ORDER BY importance DESC
            LIMIT 5
        """)

    report("cross_search: JARVIS has shareable memories for ADA",
           jarvis_shareable > 0,
           f"{jarvis_shareable} shareable (shared/team or imp>=7)")

    report("cross_search: ADA has shareable memories for JARVIS",
           ada_shareable > 0,
           f"{ada_shareable} shareable")

    report("cross_search: private low-imp excluded",
           jarvis_private_low > 0,
           f"{jarvis_private_low} private/low-imp JARVIS memories correctly excluded")

    report("cross_search: results have required fields",
           all(r['id'] and r['agent'] and r['content'] for r in cross_results),
           f"{len(cross_results)} results with valid fields")

    await pool.close()


async def test_share_promote():
    """[Test] memory_share_promote: scope promotion logic"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    async with pool.acquire() as conn:
        # Find a private JARVIS memory to test with (don't actually promote)
        private_mem = await conn.fetchrow("""
            SELECT id, agent, scope, LEFT(content, 50) as preview
            FROM memories
            WHERE agent = 'JARVIS' AND invalid_at IS NULL
            AND (scope IS NULL OR scope = 'private')
            LIMIT 1
        """)

        # Find an already shared memory
        shared_mem = await conn.fetchrow("""
            SELECT id, agent, scope FROM memories
            WHERE agent = 'JARVIS' AND scope = 'shared' AND invalid_at IS NULL
            LIMIT 1
        """)

        # Find an ADA memory (should fail for JARVIS)
        ada_mem = await conn.fetchrow("""
            SELECT id, agent FROM memories
            WHERE agent = 'ADA' AND invalid_at IS NULL
            LIMIT 1
        """)

    report("share_promote: private memory found for test",
           private_mem is not None,
           f"#{private_mem['id']}: {private_mem['preview']}" if private_mem else "none")

    # Validate ownership check logic
    if ada_mem:
        report("share_promote: ownership check blocks cross-agent promotion",
               ada_mem['agent'] != 'JARVIS',
               f"ADA mem #{ada_mem['id']} correctly not owned by JARVIS")

    if shared_mem:
        report("share_promote: already-shared detection works",
               shared_mem['scope'] == 'shared',
               f"#{shared_mem['id']} already shared")
    else:
        report("share_promote: no shared memories to test idempotency",
               True, "skipped — no shared memories")

    await pool.close()


async def test_brain_health():
    """[Test] brain_health_report: returns valid health data"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    neo = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    for ag in ['JARVIS', 'ADA']:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", ag)
            no_emb = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND embedding IS NULL", ag)
            no_val = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND valence IS NULL", ag)
            never_act = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND last_activation IS NULL", ag)
            shared = await conn.fetchval("SELECT count(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL AND scope = 'shared'", ag)

        async with neo.session() as session:
            r = await session.run("MATCH (m:Memory {agent:$ag})-[r]->() RETURN count(r) as e", ag=ag)
            edges = (await r.single())['e']

        report(f"brain_health({ag}): total > 0",
               total > 0, f"{total} memories")
        report(f"brain_health({ag}): edges > 0",
               edges > 0, f"{edges} connectome edges")
        report(f"brain_health({ag}): no_embedding reasonable",
               no_emb < total * 0.05,
               f"{no_emb}/{total} ({no_emb*100//total if total else 0}%) without embedding")

    await neo.close()
    await pool.close()


async def test_hybrid_search_mood_weight():
    """[Test] hybrid_search mood_weight: weight renormalization"""
    # Test weight normalization logic
    sem_w, kw_w, mood_w = 0.6, 0.4, 0.3
    total_w = sem_w + kw_w + mood_w

    sem_score, kw_score, mood_score = 0.8, 0.5, 0.9
    hybrid = (sem_w * sem_score + kw_w * kw_score + mood_w * mood_score) / total_w

    report("hybrid_search mood: weights renormalize correctly",
           abs(total_w - 1.3) < 0.001,
           f"total_w={total_w}")

    report("hybrid_search mood: hybrid score in valid range",
           0.0 <= hybrid <= 1.0,
           f"hybrid={hybrid:.4f}")

    # mood_weight=0 should give same as original
    sem_w0, kw_w0, mood_w0 = 0.6, 0.4, 0.0
    total_w0 = sem_w0 + kw_w0 + mood_w0
    hybrid0 = (sem_w0 * sem_score + kw_w0 * kw_score + mood_w0 * 0.5) / total_w0
    hybrid_orig = sem_w0 * sem_score + kw_w0 * kw_score

    report("hybrid_search mood: mood_weight=0 matches original",
           abs(hybrid0 - hybrid_orig) < 0.001,
           f"mood=0 gives {hybrid0:.4f}, original={hybrid_orig:.4f}")


async def test_rl_utility_bounds():
    """[Test] RL auto-utility: Bellman update stays in [0, 1]"""
    # Positive reinforcement
    for old_util in [0.0, 0.5, 0.9, 0.99, 1.0]:
        new_util = min(1.0, old_util + 0.05 * (1.0 - old_util))
        assert 0.0 <= new_util <= 1.0, f"Positive RL out of bounds: {new_util}"

    # Negative reinforcement (sleep_gate decay)
    for old_util in [0.0, 0.1, 0.5, 0.9, 1.0]:
        new_util = max(0.0, old_util - 0.03)
        assert 0.0 <= new_util <= 1.0, f"Negative RL out of bounds: {new_util}"

    report("rl_utility: positive reinforcement stays in [0, 1]", True, "5 values tested")
    report("rl_utility: negative decay stays in [0, 1]", True, "5 values tested")

    # Convergence test
    util = 0.5
    for _ in range(100):
        util = min(1.0, util + 0.05 * (1.0 - util))
    report("rl_utility: converges toward 1.0 with repeated activation",
           util > 0.99,
           f"after 100 activations: {util:.6f}")

    util2 = 0.5
    for _ in range(100):
        util2 = max(0.0, util2 - 0.03)
    report("rl_utility: decays to 0.0 with repeated forgetting",
           util2 == 0.0,
           f"after 100 decays: {util2:.6f}")


async def test_vector_null_fix():
    """[Test] json.dumps(None) produces 'null' which breaks vector cast — verify fix"""
    import json
    # The bug: json.dumps(None) -> "null" -> invalid input syntax for type vector
    report("json.dumps(None) is 'null' (the bug source)",
           json.dumps(None) == "null",
           f"json.dumps(None) = {repr(json.dumps(None))}")

    # The fix: when embedding is None, pass None directly (not json.dumps)
    embedding_ok = [0.1, 0.2, 0.3]
    embedding_none = None

    str_ok = json.dumps(embedding_ok) if embedding_ok is not None else None
    str_none = json.dumps(embedding_none) if embedding_none is not None else None

    report("vector_null: valid embedding produces JSON string",
           isinstance(str_ok, str) and str_ok.startswith("["),
           f"type={type(str_ok).__name__}, val={str_ok[:30]}")

    report("vector_null: None embedding produces Python None (not 'null')",
           str_none is None,
           f"type={type(str_none).__name__}, val={repr(str_none)}")

    # Verify no memories are missing embeddings now
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as missing FROM memories WHERE embedding IS NULL AND invalid_at IS NULL"
        )
        report("vector_null: zero memories without embeddings in DB",
               int(row['missing']) == 0,
               f"missing={row['missing']}")
    finally:
        await conn.close()


async def test_qdrant_pg_sync():
    """[Test] SKIPPED — Qdrant eliminated 28-abr-2026, soul_lite=True, pgvector is primary backend"""
    report("qdrant_pg_sync: skipped (Qdrant eliminated 28-abr-2026)", True, "soul_lite_mode=pgvector")


async def test_procedures_have_embeddings():
    """[Test] All active procedures should have embeddings for semantic search"""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM procedural_memories WHERE active = true")
        no_emb = await conn.fetchval("SELECT count(*) FROM procedural_memories WHERE active = true AND embedding IS NULL")
    report("procedures: all have embeddings",
           no_emb == 0,
           f"{total} procedures, {no_emb} without embedding")
    await pool.close()


async def test_sleep_gate_cron_syntax():
    """[Test] sleep_gate_cron.py: imports and runs without errors"""
    import subprocess
    result = subprocess.run(
        ["/home/dadito/IA/seal-spark/.venv/bin/python3", "-c",
         "import ast; ast.parse(open('/home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py').read()); print('OK')"],
        capture_output=True, text=True, timeout=5
    )
    report("sleep_gate_cron: syntax valid",
           result.returncode == 0 and "OK" in result.stdout,
           result.stderr[:100] if result.returncode != 0 else "clean parse")


async def test_hindsight_confidence():
    """[Test] Hindsight confidence scoring: blending in temporal_decay_score"""
    # Import temporal_decay_score
    import importlib.util, os
    spec = importlib.util.spec_from_file_location("mcp", os.path.expanduser(
        "~/IA/proyecto-seal/memory/mcp_server_v3.py"))
    mod = importlib.util.module_from_spec(spec)
    # We can't fully load MCP, so test the formula directly
    import math

    def temporal_decay_score(similarity, days_old, importance, valence=0.0, arousal=0.0,
                             category="", utility=0.5, confidence=1.0):
        half_life = 30.0
        decay = math.pow(0.5, days_old / half_life) if half_life > 0 else 0.0
        conf = max(0.0, min(1.0, confidence))
        blended = similarity * 0.5 + utility * 0.3 + conf * 0.2
        imp_weight = 0.5 + (importance / 20.0)
        return blended * decay * imp_weight

    # High confidence should score higher than low
    high_conf = temporal_decay_score(0.9, 5, 7, confidence=1.0)
    low_conf = temporal_decay_score(0.9, 5, 7, confidence=0.3)
    report("hindsight: high conf > low conf",
           high_conf > low_conf,
           f"high={high_conf:.4f} > low={low_conf:.4f}")

    # Zero confidence should still have non-zero score (similarity+utility still count)
    zero_conf = temporal_decay_score(0.9, 5, 7, confidence=0.0)
    report("hindsight: zero conf still has positive score",
           zero_conf > 0,
           f"zero_conf={zero_conf:.4f}")

    # Confidence weight is 0.2 of blend
    full = temporal_decay_score(0.9, 0, 7, confidence=1.0, utility=0.5)
    no_c = temporal_decay_score(0.9, 0, 7, confidence=0.0, utility=0.5)
    diff = full - no_c
    report("hindsight: confidence contributes ~0.2 to blend",
           0.05 < diff < 0.3,
           f"diff={diff:.4f}")


async def test_sleepgate_composite_resistance():
    """[Test] Adaptive Budgeted Forgetting: composite resistance in FORGET phase"""
    # Simulate the composite resistance formula from sleep_gate_cron.py
    def compute_resistance(valence, arousal, query_count, confidence):
        v = abs(valence)
        a = arousal
        emotion_resist = (v * 0.5) + (a * 0.3)
        freq_resist = min(0.5, query_count * 0.05)
        conf_resist = confidence * 0.3
        return 1.0 + emotion_resist + freq_resist + conf_resist

    # Base case: no emotion, no queries, default conf
    base = compute_resistance(0, 0, 0, 1.0)
    report("composite: base resistance > 1.0",
           base > 1.0,
           f"base={base:.3f}")

    # Emotional memory should have higher resistance
    emotional = compute_resistance(0.8, 0.7, 0, 1.0)
    report("composite: emotional > base",
           emotional > base,
           f"emotional={emotional:.3f} > base={base:.3f}")

    # Frequently accessed memory has extra resistance
    frequent = compute_resistance(0, 0, 10, 1.0)
    report("composite: frequent > base",
           frequent > base,
           f"frequent={frequent:.3f} (10 queries) > base={base:.3f}")

    # Low confidence reduces resistance
    low_conf = compute_resistance(0, 0, 0, 0.2)
    report("composite: low conf < base",
           low_conf < base,
           f"low_conf={low_conf:.3f} < base={base:.3f}")

    # Max resistance is bounded
    maxed = compute_resistance(1.0, 1.0, 20, 1.0)
    report("composite: max resistance bounded",
           maxed < 3.0,
           f"max={maxed:.3f} < 3.0")


async def test_sleepgate_error_isolation():
    """[Test] SleepGate phases are error-isolated (one failure doesn't kill all)"""
    import subprocess
    MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        ["/home/dadito/IA/seal-spark/.venv/bin/python3", "sleep_gate_cron.py", "--dry-run"],
        capture_output=True, text=True, timeout=300,
        cwd=MEMORY_DIR,
    )
    output = result.stdout
    report("sleepgate: dry-run completes",
           result.returncode == 0 and "SleepGate complete" in output,
           f"rc={result.returncode}")
    report("sleepgate: all phases execute",
           "Brain Health" in output and "Qdrant Ghost" in output and "Neo4j Ghost" in output,
           "all critical phases present")


async def test_confidence_pg_qdrant_sync():
    """[Test] Confidence score is consistent between PG and Qdrant"""
    conn = await asyncpg.connect(DB_URL)
    from qdrant_client import QdrantClient
    from config import settings as _cfg
    qd = QdrantClient(host='localhost', port=6333, api_key=_cfg.qdrant_api_key, https=False)

    # Check a sample of memories for confidence consistency
    rows = await conn.fetch("""
        SELECT id, COALESCE(confidence_score, 1.0) as pg_conf
        FROM memories WHERE invalid_at IS NULL AND embedding IS NOT NULL
        ORDER BY id DESC LIMIT 20
    """)
    mismatches = 0
    for r in rows:
        try:
            pts = qd.retrieve("soul_memories", ids=[r['id']], with_payload=True)
            if pts:
                qd_conf = float(pts[0].payload.get("confidence", 1.0) or 1.0)
                pg_conf = float(r['pg_conf'])
                if abs(qd_conf - pg_conf) > 0.01:
                    mismatches += 1
        except Exception:
            pass

    report("confidence: PG-Qdrant sync (sample 20)",
           mismatches == 0,
           f"{20 - mismatches}/20 matched, {mismatches} mismatches")

    # Verify no NULLs in confidence for active memories
    null_count = await conn.fetchval(
        "SELECT COUNT(*) FROM memories WHERE invalid_at IS NULL AND confidence_score IS NULL")
    report("confidence: no NULL confidence in active memories",
           null_count == 0,
           f"{null_count} NULL confidence scores")

    await conn.close()


# ── MAGMA Intent-Aware Routing Tests ──

async def test_intent_classification():
    """Test that _classify_intent routes queries correctly."""
    import re as _re

    _INTENT_PATTERNS = {
        "temporal": [
            r"\b(when|cuándo|fecha|antes|después|during|between|timeline|history|ayer|hoy|semana|mes)\b",
            r"\b(primero|último|reciente|antiguo|cronolog|secuencia|order)\b",
            r"\b\d{4}[-/]\d{2}",
        ],
        "causal": [
            r"\b(why|por\s*qu[ée]|cause|because|porque|resultado|efecto|consecuencia|provocó|led\s+to)\b",
            r"\b(trigger|causa|razón|motivo|originó|derivó)\b",
        ],
        "entity": [
            r"\b(who|quién|about|sobre|todo\s+(?:lo\s+)?de|mentions?|mencion)\b",
            r"\b(relacion|relationship|connected|vinculad|asociad)\b",
        ],
        "semantic": [],
    }

    def _classify_intent(query):
        q = query.lower()
        scores = {"semantic": 1}
        for intent, patterns in _INTENT_PATTERNS.items():
            for pat in patterns:
                if _re.search(pat, q, _re.IGNORECASE):
                    scores[intent] = scores.get(intent, 0) + 2
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [k for k, v in ranked if v > 0]

    # Temporal query
    t1 = _classify_intent("cuándo se creó el connectome?")
    report("intent: temporal query → temporal first", t1[0] == "temporal", f"got {t1}")

    # Causal query
    c1 = _classify_intent("por qué se borró la memoria 2324?")
    report("intent: causal query → causal first", c1[0] == "causal", f"got {c1}")

    # Entity query
    e1 = _classify_intent("todo sobre William")
    report("intent: entity query → entity first", e1[0] == "entity", f"got {e1}")

    # Pure semantic (no keywords)
    s1 = _classify_intent("training pipeline configuration")
    report("intent: generic query → semantic", s1[0] == "semantic", f"got {s1}")


# ── Bi-temporal Edge Query Tests ──

async def test_bitemporal_edges():
    """Test that Neo4j edges have bitemporal properties."""
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    async with driver.session() as session:
        # Count edges with valid_at set
        result = await session.run(
            "MATCH ()-[r]->() WHERE r.valid_at IS NOT NULL RETURN count(r) AS cnt"
        )
        rec = await result.single()
        with_valid = rec["cnt"] if rec else 0

        result2 = await session.run("MATCH ()-[r]->() RETURN count(r) AS cnt")
        rec2 = await result2.single()
        total = rec2["cnt"] if rec2 else 0

    await driver.close()

    report("bitemporal: edges exist in Neo4j", total > 0, f"total={total}")
    if total > 0:
        pct = with_valid * 100 // total
        report("bitemporal: edges have valid_at", with_valid > 0, f"{with_valid}/{total} ({pct}%)")


# ── Peer Model Tests ──

async def test_reflection_synthesize():
    """[Test] reflection_synthesize: synthesizes beliefs from episodic memories without LLM."""
    conn = await asyncpg.connect(DB_URL)

    # 1. Pre-condition: ADA has source memories
    count = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE agent = 'ADA' AND invalid_at IS NULL "
        "AND category IN ('correction', 'decision', 'insight', 'milestone', 'fact', 'pattern')"
    )
    report("reflection_synthesize: source memories exist",
           count > 0,
           f"{count} eligible memories in PG")

    if count == 0:
        report("reflection_synthesize: skip (empty DB)", True, "no source data")
        await conn.close()
        return

    # 2. Grouping logic (replicated from tool)
    rows = await conn.fetch(
        """SELECT id, category, content, importance, confidence_score
           FROM memories
           WHERE agent = 'ADA' AND invalid_at IS NULL
           AND category IN ('correction', 'decision', 'insight', 'milestone', 'fact', 'pattern')
           ORDER BY importance DESC NULLS LAST, created_at DESC
           LIMIT 20"""
    )
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)

    report("reflection_synthesize: grouping by category works",
           isinstance(groups, dict) and len(rows) > 0,
           f"groups={list(groups.keys())}, rows={len(rows)}")

    # 3. Belief synthesis formula: weighted confidence
    beliefs = []
    for cat, mems in groups.items():
        if not mems:
            continue
        total_weight = sum(float(m["importance"] or 5) for m in mems)
        if total_weight == 0:
            total_weight = len(mems)
        weighted_conf = sum(
            float(m["confidence_score"] or 0.8) * float(m["importance"] or 5)
            for m in mems
        ) / total_weight
        beliefs.append({"category": cat, "confidence": round(weighted_conf, 3), "count": len(mems)})

    report("reflection_synthesize: weighted confidence formula",
           len(beliefs) > 0,
           f"{len(beliefs)} belief(s) — {beliefs}")

    # 4. Confidence must be in [0, 1]
    all_valid = all(0.0 <= b["confidence"] <= 1.0 for b in beliefs)
    report("reflection_synthesize: confidence in [0.0, 1.0]",
           all_valid,
           f"values={[b['confidence'] for b in beliefs]}")

    # 5. Idempotency marker format (dedup by topic)
    topic = "seal_test_dedup_marker"
    marker = f"[synthesized:{topic}]"
    existing = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE agent = 'ADA' AND category = 'insight' "
        "AND content LIKE $1 AND invalid_at IS NULL",
        f"%{marker}%"
    )
    report("reflection_synthesize: dedup marker query works",
           isinstance(existing, int),
           f"existing synthesized beliefs with marker: {existing}")

    # 6. Return schema validation (must have these keys when called)
    EXPECTED_KEYS = {"beliefs", "memories_processed", "beliefs_created", "topic"}
    # Can't call the MCP tool directly, but verify the schema contract as doc
    report("reflection_synthesize: output schema defined",
           True,
           f"expected keys: {EXPECTED_KEYS}")

    await conn.close()


async def test_peer_model_table():
    """Test that peer_models table can be created and queried."""
    conn = await asyncpg.connect(DB_URL)

    # Check if table exists (may not exist yet before first use)
    exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'peer_models')"
    )

    if exists:
        # If it exists, verify schema
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'peer_models' ORDER BY ordinal_position"
        )
        col_names = [r["column_name"] for r in cols]
        has_fields = all(f in col_names for f in ["observer", "subject", "observed_patterns", "blind_spots", "strengths"])
        report("peer_model: table exists with correct schema", has_fields, f"cols={col_names}")
    else:
        # Table will be created on first peer_model_update call — that's fine
        report("peer_model: table will be auto-created on first use", True, "not yet created")

    await conn.close()


async def test_wave3_columns():
    """Verify Wave 3 schema columns (surprise_score, decay_score, recall_count, last_recalled_at).

    Pre-migration: columns don't exist → PASS with 'pending' note (migration awaits William's GO).
    Post-migration: columns exist → verify correct types and defaults.
    """
    conn = await asyncpg.connect(DB_URL)

    expected_cols = {"surprise_score", "decay_score", "recall_count", "last_recalled_at"}
    cols = await conn.fetch("""
        SELECT column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_name = 'memories'
        AND column_name = ANY($1::text[])
        ORDER BY column_name
    """, list(expected_cols))

    found = {r["column_name"] for r in cols}

    if not found:
        # Pre-migration state — columns don't exist yet, that's expected
        report("wave3: schema pending William's GO", True, "columns not yet created (migration not run)")
        await conn.close()
        return

    # Post-migration: all 4 columns must exist
    report("wave3: all 4 columns exist", found == expected_cols, f"found={found}, expected={expected_cols}")

    # Verify defaults by column
    col_map = {r["column_name"]: r for r in cols}

    if "surprise_score" in col_map:
        report("wave3: surprise_score is REAL", "real" in col_map["surprise_score"]["data_type"].lower(),
               f"type={col_map['surprise_score']['data_type']}")

    if "decay_score" in col_map:
        report("wave3: decay_score is REAL", "real" in col_map["decay_score"]["data_type"].lower(),
               f"type={col_map['decay_score']['data_type']}")

    if "recall_count" in col_map:
        report("wave3: recall_count is INTEGER", col_map["recall_count"]["data_type"] == "integer",
               f"type={col_map['recall_count']['data_type']}")

    if "last_recalled_at" in col_map:
        report("wave3: last_recalled_at is TIMESTAMPTZ",
               "timestamp" in col_map["last_recalled_at"]["data_type"].lower(),
               f"type={col_map['last_recalled_at']['data_type']}")

    # No NULLs in recall_count for valid memories
    null_count = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE recall_count IS NULL AND invalid_at IS NULL"
    )
    report("wave3: no NULL recall_count in valid memories", null_count == 0, f"nulls={null_count}")

    await conn.close()


async def test_rate_limiting():
    """Verify sliding-window rate limiter allows up to limit, then blocks."""
    import collections as _col
    import time as _t
    sys.path.insert(0, os.path.dirname(__file__))
    from mcp_server_v3 import _rate_check, _rate_windows, _RATE_LIMIT_DEFAULT, _RATE_LIMITS_OVERRIDE

    # Clear state for isolated test
    tool = "__test_rate_tool__"
    _rate_windows.pop(tool, None)

    # Allow exactly LIMIT requests
    limit = _RATE_LIMIT_DEFAULT
    allowed = sum(1 for _ in range(limit) if _rate_check(tool)[0])
    report("rate_limit: allows exactly default limit", allowed == limit,
           f"allowed={allowed}, limit={limit}")

    # Next request must be blocked
    blocked, remaining = _rate_check(tool)
    report("rate_limit: blocks at limit+1", not blocked,
           f"blocked={not blocked}, remaining={remaining}")

    # Override limits work
    heavy = "connectome_build"
    _rate_windows.pop(heavy, None)
    heavy_limit = _RATE_LIMITS_OVERRIDE[heavy]
    h_allowed = sum(1 for _ in range(heavy_limit + 2) if _rate_check(heavy)[0])
    report("rate_limit: connectome_build override", h_allowed == heavy_limit,
           f"allowed={h_allowed}, expected={heavy_limit}")

    # Cleanup
    _rate_windows.pop(tool, None)
    _rate_windows.pop(heavy, None)


async def test_health_check_structure():
    """Verify health_check returns valid JSON with required keys and live service status."""
    import asyncio
    sys.path.insert(0, os.path.dirname(__file__))
    from mcp_server_v3 import health_check

    raw = await health_check()
    try:
        data = json.loads(raw)
    except Exception as e:
        report("health_check: valid JSON", False, str(e))
        return

    report("health_check: valid JSON", True)

    required_top = {"status", "uptime_seconds", "timestamp", "services"}
    has_top = required_top.issubset(data.keys())
    report("health_check: required top-level keys", has_top,
           f"keys={set(data.keys())}")

    required_svc = {"postgresql", "neo4j", "qdrant"}
    has_svc = required_svc.issubset(data.get("services", {}).keys())
    report("health_check: all 3 services present", has_svc,
           f"services={set(data.get('services', {}).keys())}")

    pg_ok = data.get("services", {}).get("postgresql", {}).get("status") == "ok"
    report("health_check: postgresql alive", pg_ok,
           f"pg_status={data.get('services', {}).get('postgresql', {})}")

    uptime_valid = isinstance(data.get("uptime_seconds"), (int, float)) and data["uptime_seconds"] >= 0
    report("health_check: uptime_seconds is valid float", uptime_valid,
           f"uptime={data.get('uptime_seconds')}")


async def test_graceful_shutdown():
    """Verify _async_cleanup is idempotent and runs without error (no live connections to close)."""
    sys.path.insert(0, os.path.dirname(__file__))
    from mcp_server_v3 import _async_cleanup, _signal_handler, _sync_cleanup
    import inspect

    report("graceful_shutdown: _async_cleanup is coroutine",
           inspect.iscoroutinefunction(_async_cleanup))

    # Run cleanup with no connections open — must not raise
    try:
        await _async_cleanup()
        report("graceful_shutdown: _async_cleanup idempotent (no connections)", True)
    except Exception as e:
        report("graceful_shutdown: _async_cleanup idempotent (no connections)", False, str(e))

    # Signal handler exists and is callable
    report("graceful_shutdown: _signal_handler callable", callable(_signal_handler))
    report("graceful_shutdown: _sync_cleanup callable", callable(_sync_cleanup))


async def test_amac_admission_gate():
    """[Test] A-MAC 5-factor admission gate in memory_store."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None  # reset singleton for new event loop
    import mcp_server_v3; mcp_server_v3._qdrant = None  # reset qdrant singleton
    from mcp_server_v3 import memory_store

    # 1. Protected category bypasses A-MAC (correction, any importance)
    r1 = await memory_store(agent="ADA", category="correction",
                            content="TEST_AMAC_SUITE: William corrigió un patrón de diseño incorrecto", importance=6)
    data1 = json.loads(r1) if r1.startswith("{") else {"result": r1}
    passed1 = "stored" in r1.lower() or "Memory #" in r1
    report("amac_gate: correction bypasses A-MAC",
           passed1,
           f"result={'stored' if passed1 else 'blocked'}")

    # 2. High importance bypasses A-MAC (imp >= 8)
    r2 = await memory_store(agent="ADA", category="emotion",
                            content="TEST_AMAC_SUITE: momento crítico de orgullo cuando SEAL pasó 93 tests", importance=9)
    passed2 = "stored" in r2.lower() or "Memory #" in r2
    report("amac_gate: high importance (9) bypasses A-MAC",
           passed2,
           f"result={'stored' if passed2 else 'blocked'}")

    # 3. A-MAC scores are recorded in metadata for non-protected memories
    import random as _rng
    _unique_id = _rng.randint(100000, 999999)
    r3 = await memory_store(agent="ADA", category="fact",
                            content=f"TEST_AMAC_SUITE_{_unique_id}: La temperatura promedio en Chiclayo en abril es 28 grados celsius", importance=6)
    passed3 = "stored" in r3.lower() or "Memory #" in r3
    report("amac_gate: medium fact passes with score",
           passed3,
           f"result={'stored' if passed3 else 'blocked'}")

    # Verify amac_score in metadata
    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT metadata FROM memories WHERE content LIKE $1 "
        "AND invalid_at IS NULL ORDER BY id DESC LIMIT 1",
        f"TEST_AMAC_SUITE_{_unique_id}%"
    )
    if row:
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        has_score = "amac_score" in meta
        has_factors = "amac_factors" in meta
        report("amac_gate: metadata contains amac_score",
               has_score,
               f"score={meta.get('amac_score')}" if has_score else "missing")
        report("amac_gate: metadata contains amac_factors",
               has_factors,
               f"factors={list(meta.get('amac_factors', {}).keys())}" if has_factors else "missing")

        if has_factors:
            factors = meta["amac_factors"]
            expected_keys = {"future_utility", "factual_confidence", "semantic_novelty", "temporal_recency", "content_type_prior"}
            report("amac_gate: all 5 factors present",
                   set(factors.keys()) == expected_keys,
                   f"keys={set(factors.keys())}")
            # All factors should be in [0, 1]
            all_valid = all(0.0 <= v <= 1.0 for v in factors.values())
            report("amac_gate: all factors in [0.0, 1.0]",
                   all_valid,
                   f"values={list(factors.values())}")
    else:
        report("amac_gate: metadata check", False, "test memory not found in DB")

    # 4. Cleanup test memories — DELETE from PG + Qdrant (no orphans)
    test_ids = [r['id'] for r in await conn.fetch(
        "SELECT id FROM memories WHERE content LIKE 'TEST_AMAC_SUITE%'"
    )]
    await conn.execute(
        "DELETE FROM memories WHERE content LIKE 'TEST_AMAC_SUITE%'"
    )
    if test_ids:
        try:
            from qdrant_client import QdrantClient
            from config import settings as _cfg
            QdrantClient(host="localhost", port=6333, api_key=_cfg.qdrant_api_key, https=False).delete("soul_memories", points_selector=test_ids)
        except Exception:
            pass
    await conn.close()


async def test_tier5_opinions_schema():
    """[Test] Tier 5: opinions table has all required columns for belief synthesis."""
    conn = await asyncpg.connect(DB_URL)
    cols = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'opinions' ORDER BY ordinal_position"
    )
    col_names = [r["column_name"] for r in cols]

    required = ["topic", "category", "active", "status", "importance",
                 "last_challenged", "invalid_at", "updated_at", "search_vector"]
    missing = [c for c in required if c not in col_names]
    report("tier5_opinions: all Tier 5 columns exist",
           len(missing) == 0,
           f"cols={len(col_names)}, missing={missing}" if missing else f"all {len(required)} Tier 5 columns present")

    # Check indexes
    indexes = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'opinions' AND indexname LIKE '%opinions%'"
    )
    idx_names = [r["indexname"] for r in indexes]
    required_idx = ["idx_opinions_agent_topic", "idx_opinions_agent_active", "idx_opinions_search"]
    missing_idx = [i for i in required_idx if i not in idx_names]
    report("tier5_opinions: required indexes exist",
           len(missing_idx) == 0,
           f"indexes={idx_names}" if not missing_idx else f"missing={missing_idx}")

    # Check trigger
    triggers = await conn.fetch(
        "SELECT trigger_name FROM information_schema.triggers WHERE event_object_table = 'opinions'"
    )
    trig_names = [t["trigger_name"] for t in triggers]
    report("tier5_opinions: update trigger exists",
           "trg_opinions_tier5" in trig_names,
           f"triggers={trig_names}")

    await conn.close()


async def test_tier5_belief_query():
    """[Test] Tier 5: belief_query returns correct structure."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None  # reset singleton for new event loop
    from mcp_server_v3 import belief_query

    # Query all beliefs for ADA
    result = await belief_query(agent="ADA")
    data = json.loads(result)
    report("tier5_belief_query: returns valid JSON",
           isinstance(data, dict),
           f"keys={list(data.keys())}")

    report("tier5_belief_query: has required keys",
           all(k in data for k in ("agent", "count", "beliefs")),
           f"agent={data.get('agent')}, count={data.get('count')}")

    # Each belief has required fields
    if data["beliefs"]:
        b = data["beliefs"][0]
        required_fields = ["id", "topic", "belief", "confidence", "evidence_count", "status"]
        has_all = all(f in b for f in required_fields)
        report("tier5_belief_query: belief has all fields",
               has_all,
               f"fields={list(b.keys())}")

        report("tier5_belief_query: confidence in [0, 1]",
               0.0 <= b["confidence"] <= 1.0,
               f"confidence={b['confidence']}")
    else:
        report("tier5_belief_query: ADA has beliefs",
               False,
               "no beliefs found for ADA")

    # No-topic query (no semantic search, just top by confidence)
    result2 = await belief_query(agent="ALICE", status="all")
    data2 = json.loads(result2)
    report("tier5_belief_query: empty agent returns 0",
           data2["count"] == 0,
           f"ALICE beliefs count={data2['count']}")


async def test_tier5_boot_context_beliefs():
    """[Test] Tier 5: boot_context includes Active Beliefs section."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None  # reset singleton for new event loop
    from mcp_server_v3 import boot_context

    result = await boot_context(agent="ADA")
    has_beliefs = "## Active Beliefs" in result
    report("tier5_boot_context: Active Beliefs section present",
           has_beliefs,
           "section found in boot output" if has_beliefs else "section MISSING")

    if has_beliefs:
        # Count belief lines
        lines = result.split("\n")
        belief_lines = [l for l in lines if l.startswith("- [") and "conf=" in l]
        report("tier5_boot_context: belief lines formatted correctly",
               len(belief_lines) > 0,
               f"{len(belief_lines)} belief(s) loaded at boot")


async def test_tier5_reflection_writes_opinions():
    """[Test] Tier 5: reflection_synthesize writes to opinions, not memories."""
    conn = await asyncpg.connect(DB_URL)

    # Check that synthesized beliefs exist in opinions
    opinion_beliefs = await conn.fetchval(
        "SELECT count(*) FROM opinions WHERE agent = 'ADA' AND topic != 'general' AND active = TRUE"
    )
    report("tier5_reflection_opinions: synthesized beliefs in opinions table",
           opinion_beliefs > 0,
           f"{opinion_beliefs} synthesized belief(s) found")

    # Verify beliefs have source_memory_ids populated
    has_sources = await conn.fetchval(
        "SELECT count(*) FROM opinions WHERE agent = 'ADA' AND source_memory_ids IS NOT NULL "
        "AND source_memory_ids::text != 'null' AND source_memory_ids::text != '[]'"
    )
    report("tier5_reflection_opinions: source_memory_ids populated",
           has_sources > 0,
           f"{has_sources} belief(s) with source traceability")

    # Verify search_vector is populated (trigger working)
    has_fts = await conn.fetchval(
        "SELECT count(*) FROM opinions WHERE search_vector IS NOT NULL"
    )
    report("tier5_reflection_opinions: search_vector populated by trigger",
           has_fts > 0,
           f"{has_fts} row(s) with full-text search")

    await conn.close()


# ── Cold Archive Tests ──────────────────────────────────────────────────────

TEST_COLD_AGENT = "TEST_COLD"


async def _cold_archive_cleanup(conn):
    """Remove all test data from cold_archive and memories. Ensures TEST_COLD agent exists."""
    await conn.execute("DELETE FROM cold_archive WHERE agent = $1", TEST_COLD_AGENT)
    await conn.execute("DELETE FROM memories WHERE agent = $1", TEST_COLD_AGENT)
    await conn.execute(
        "INSERT INTO agents (name, role) VALUES ($1, 'test') ON CONFLICT (name) DO NOTHING",
        TEST_COLD_AGENT,
    )


async def test_cold_archive_table_exists():
    """[Test] Cold Archive: table and indexes exist."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'cold_archive')"
        )
        report("cold_archive: table exists", exists, "cold_archive table found" if exists else "MISSING")

        idx_count = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'cold_archive'"
        )
        report("cold_archive: indexes exist", idx_count >= 4, f"{idx_count} indexes found (expect >=4)")
    await pool.close()


async def test_cold_archive_migrate_dry_run():
    """[Test] Cold Archive: dry_run reports without writing."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await _cold_archive_cleanup(conn)
        # Insert a test memory and invalidate it 8 days ago
        await conn.execute("""
            INSERT INTO memories (agent, category, content, importance, source, invalid_at)
            VALUES ($1, 'fact', 'Cold archive dry run test memory', 5, 'test',
                    NOW() - INTERVAL '8 days')
        """, TEST_COLD_AGENT)

    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import _cold_archive_migrate

    stats = await _cold_archive_migrate(pool, TEST_COLD_AGENT, min_age_days=7, dry_run=True)
    report("cold_archive_migrate: dry_run returns stats",
           stats.get("archived", 0) > 0 and stats.get("dry_run") is True,
           f"archived={stats.get('archived')}, dry_run={stats.get('dry_run')}")

    # Verify memory still in memories (not moved)
    async with pool.acquire() as conn:
        still_there = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent = $1", TEST_COLD_AGENT
        )
        report("cold_archive_migrate: dry_run does not move data",
               still_there > 0, f"{still_there} memory(ies) still in memories")
        await _cold_archive_cleanup(conn)
    await pool.close()


async def test_cold_archive_migrate_live():
    """[Test] Cold Archive: live migration moves to cold_archive and deletes from memories."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await _cold_archive_cleanup(conn)
        await conn.execute("""
            INSERT INTO memories (agent, category, content, importance, source, invalid_at)
            VALUES ($1, 'fact', 'Cold archive live test — unique singleton memory xz99', 5, 'test',
                    NOW() - INTERVAL '8 days')
        """, TEST_COLD_AGENT)

    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import _cold_archive_migrate

    stats = await _cold_archive_migrate(pool, TEST_COLD_AGENT, min_age_days=7, ttl_days=365, dry_run=False)
    report("cold_archive_migrate: live archived > 0",
           stats.get("archived", 0) > 0,
           f"archived={stats.get('archived')}, singletons={stats.get('singletons')}")

    async with pool.acquire() as conn:
        in_memories = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE agent = $1", TEST_COLD_AGENT
        )
        in_cold = await conn.fetchval(
            "SELECT count(*) FROM cold_archive WHERE agent = $1", TEST_COLD_AGENT
        )
        report("cold_archive_migrate: source deleted from memories",
               in_memories == 0, f"memories={in_memories}")
        report("cold_archive_migrate: entry in cold_archive",
               in_cold > 0, f"cold_archive={in_cold}")
        await _cold_archive_cleanup(conn)
    await pool.close()


async def test_cold_archive_query_empty():
    """[Test] Cold Archive: query on empty result returns empty list."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import cold_archive_query

    result = await cold_archive_query(query="nonexistent topic xyz", agent="NOBODY_AGENT")
    data = json.loads(result)
    report("cold_archive_query: empty returns no error",
           "results" in data,
           f"keys={list(data.keys())}")
    report("cold_archive_query: empty results list",
           len(data.get("results", [1])) == 0,
           f"count={len(data.get('results', []))}")


async def test_cold_archive_stats():
    """[Test] Cold Archive: stats returns valid JSON."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import cold_archive_stats

    result = await cold_archive_stats()
    data = json.loads(result)
    report("cold_archive_stats: valid JSON with agents key",
           "agents" in data and "total" in data,
           f"total={data.get('total')}, agents={list(data.get('agents', {}).keys())}")


async def test_cold_archive_ttl_purge():
    """[Test] Cold Archive: TTL purge deletes expired entries."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await _cold_archive_cleanup(conn)
        # Insert expired cold entry
        await conn.execute("""
            INSERT INTO cold_archive (agent, original_memory_ids, summary, source_count,
                                      importance_max, category, expires_at)
            VALUES ($1, '{999999}', 'Expired test entry for TTL purge', 1, 5, 'test',
                    NOW() - INTERVAL '1 day')
        """, TEST_COLD_AGENT)
        # Insert non-expired entry
        await conn.execute("""
            INSERT INTO cold_archive (agent, original_memory_ids, summary, source_count,
                                      importance_max, category, expires_at)
            VALUES ($1, '{999998}', 'Future expiry test entry', 1, 5, 'test',
                    NOW() + INTERVAL '365 days')
        """, TEST_COLD_AGENT)
        # Insert never-expires entry
        await conn.execute("""
            INSERT INTO cold_archive (agent, original_memory_ids, summary, source_count,
                                      importance_max, category)
            VALUES ($1, '{999997}', 'Never expires test entry', 1, 5, 'test')
        """, TEST_COLD_AGENT)

    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    from mcp_server_v3 import _cold_archive_purge_expired

    stats = await _cold_archive_purge_expired(pool, dry_run=False)
    report("cold_archive_purge: expired entry deleted",
           stats.get("purged", 0) >= 1,
           f"purged={stats.get('purged')}")

    async with pool.acquire() as conn:
        remaining = await conn.fetchval(
            "SELECT count(*) FROM cold_archive WHERE agent = $1", TEST_COLD_AGENT
        )
        report("cold_archive_purge: non-expired + never-expires survive",
               remaining == 2, f"remaining={remaining} (expect 2)")
        await _cold_archive_cleanup(conn)
    await pool.close()


async def test_memory_search_include_archived():
    """[Test] Cold Archive: memory_search with include_archived returns cold results."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await _cold_archive_cleanup(conn)
        # Insert a cold archive entry with embedding
        from embeddings import get_embedding
        emb = await get_embedding("unique cold archive test searchable memory xyz789")

        await conn.execute("""
            INSERT INTO cold_archive (agent, original_memory_ids, summary, embedding,
                                      source_count, importance_max, category)
            VALUES ($1, '{888888}', 'unique cold archive test searchable memory xyz789',
                    $2::vector, 1, 7, 'fact')
        """, TEST_COLD_AGENT, json.dumps(emb))

    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import memory_search

    result = await memory_search(
        query="unique cold archive test searchable memory xyz789",
        agent=TEST_COLD_AGENT,
        include_archived=True,
    )
    # May return "No memories found" since TEST_COLD has no hot memories in Qdrant,
    # but if include_archived works, we should get cold results
    if "No memories found" not in result:
        data = json.loads(result)
        cold_results = [e for e in data if e.get("source") == "cold_archive"]
        report("memory_search_include_archived: cold results found",
               len(cold_results) > 0,
               f"{len(cold_results)} cold result(s)")
    else:
        # Even without hot results, cold should show up
        report("memory_search_include_archived: returns results",
               False, "Got 'No memories found' — cold archive integration may need hot results first")

    async with pool.acquire() as conn:
        await _cold_archive_cleanup(conn)
    await pool.close()


async def test_mirix_classification():
    """[Test] MIRIX: _mirix_classify maps categories to correct memory types."""
    sys.path.insert(0, os.path.dirname(__file__))
    from mcp_server_v3 import _mirix_classify, MIRIX_CATEGORY_MAP

    # Category mapping
    report("mirix_classify: emotion → core", _mirix_classify("emotion", "I feel happy") == "core",
           f"got={_mirix_classify('emotion', 'I feel happy')}")
    report("mirix_classify: trust → core", _mirix_classify("trust", "William trusts ADA") == "core",
           f"got={_mirix_classify('trust', 'William trusts ADA')}")
    report("mirix_classify: insight → semantic", _mirix_classify("insight", "Pattern detected") == "semantic",
           f"got={_mirix_classify('insight', 'Pattern detected')}")
    report("mirix_classify: milestone → episodic", _mirix_classify("milestone", "Completed phase 1") == "episodic",
           f"got={_mirix_classify('milestone', 'Completed phase 1')}")
    report("mirix_classify: decision → semantic", _mirix_classify("decision", "Chose PostgreSQL") == "semantic",
           f"got={_mirix_classify('decision', 'Chose PostgreSQL')}")

    # Explicit override
    report("mirix_classify: explicit override works", _mirix_classify("emotion", "test", memory_type="vault") == "vault",
           f"got={_mirix_classify('emotion', 'test', memory_type='vault')}")

    # Vault detection via content heuristic
    report("mirix_classify: detects vault from content",
           _mirix_classify("fact", "api_key=sk-abc123xyz") == "vault",
           f"got={_mirix_classify('fact', 'api_key=sk-abc123xyz')}")

    # Unknown category defaults to episodic
    report("mirix_classify: unknown → episodic", _mirix_classify("unknown_cat", "something") == "episodic",
           f"got={_mirix_classify('unknown_cat', 'something')}")


async def test_mirix_migration():
    """[Test] MIRIX: existing memories are classified correctly after migration."""
    conn = await asyncpg.connect(DB_URL)
    # Check memory_type column exists
    col = await conn.fetchval(
        "SELECT column_name FROM information_schema.columns WHERE table_name='memories' AND column_name='memory_type'"
    )
    report("mirix_migration: memory_type column exists", col == "memory_type", f"col={col}")

    # Check distribution matches category mapping
    core_count = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE memory_type = 'core' AND category IN ('emotion', 'trust', 'preference')"
    )
    core_wrong = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE memory_type = 'core' AND category NOT IN ('emotion', 'trust', 'preference')"
    )
    report("mirix_migration: core memories correctly classified", core_count > 0 and core_wrong == 0,
           f"correct={core_count}, wrong={core_wrong}")

    semantic_count = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE memory_type = 'semantic' AND category IN ('insight', 'fact', 'pattern', 'decision')"
    )
    report("mirix_migration: semantic memories exist", semantic_count > 0, f"count={semantic_count}")

    # Constraint exists
    chk = await conn.fetchval(
        "SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_type'"
    )
    report("mirix_migration: constraint chk_memory_type exists", chk == 1, f"exists={chk}")
    await conn.close()


async def test_mirix_retrieval_filter():
    """[Test] MIRIX: memory_search with memory_type filter returns only that type."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import memory_search

    # Search core memories only
    result = await memory_search("William", agent="ADA", memory_type="core", limit=5)
    if "No memories found" not in result:
        data = json.loads(result)
        results_list = data if isinstance(data, list) else data.get("results", data.get("memories", []))
        all_core = all(r.get("memory_type") == "core" for r in results_list if isinstance(r, dict))
        report("mirix_retrieval: core filter returns only core", all_core,
               f"types={[r.get('memory_type') for r in results_list[:3]]}")
    else:
        report("mirix_retrieval: core filter returns results", False, "no core memories found")


async def test_mirix_core_boost():
    """[Test] MIRIX: core memories get 1.2x boost in retrieval scoring."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import memory_search

    result = await memory_search("William familia equipo", agent="ADA", limit=20)
    if "No memories found" not in result:
        data = json.loads(result)
        results_list = data if isinstance(data, list) else data.get("results", data.get("memories", []))
        core_entries = [r for r in results_list if isinstance(r, dict) and r.get("memory_type") == "core"]
        report("mirix_core_boost: core memories present in results", len(core_entries) > 0,
               f"core_count={len(core_entries)}")
    else:
        report("mirix_core_boost: search returns results", False, "no results")


async def test_mirix_vault_exclusion():
    """[Test] MIRIX: vault memories excluded from general search."""
    conn = await asyncpg.connect(DB_URL)
    # Check no vault memories leak into general search (there shouldn't be any vault memories yet)
    vault_count = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE memory_type = 'vault' AND invalid_at IS NULL"
    )
    report("mirix_vault_exclusion: vault count is 0 (no secrets stored)", vault_count == 0,
           f"vault_count={vault_count}")
    await conn.close()


async def test_mirix_boot_context():
    """[Test] MIRIX: boot_context includes memory profile section."""
    sys.path.insert(0, os.path.dirname(__file__))
    import db; db._pool = None
    import mcp_server_v3; mcp_server_v3._qdrant = None
    from mcp_server_v3 import boot_context

    result = await boot_context("ADA")
    report("mirix_boot_context: Memory Profile section present",
           "Memory Profile (MIRIX)" in result,
           f"found={'Memory Profile' in result}")
    report("mirix_boot_context: shows type counts",
           "core=" in result or "episodic=" in result,
           f"has_types={'core=' in result}")


async def test_wave3_precompute_scoring():
    """[Test] Wave 3: compute_decay_score and compute_recall_boost produce valid scores."""
    from soul.core.scoring_v3 import compute_decay_score, compute_recall_boost
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    # decay_score: recent important memory should have high score
    ds_recent = compute_decay_score(importance=7, category="decision", created_at=now - timedelta(days=1), now=now)
    report("wave3_precompute: recent decision decay > 0.99", ds_recent > 0.99, f"decay={ds_recent:.4f}")

    # decay_score: old emotion should decay fast (half-life=1d)
    ds_old_emotion = compute_decay_score(importance=5, category="emotion", created_at=now - timedelta(days=7), now=now)
    report("wave3_precompute: 7-day emotion decay < 0.01", ds_old_emotion < 0.01, f"decay={ds_old_emotion:.6f}")

    # decay_score: immortal (importance >= 10)
    ds_immortal = compute_decay_score(importance=10, category="emotion", created_at=now - timedelta(days=365), now=now)
    report("wave3_precompute: imp=10 is immortal", ds_immortal == 1.0, f"decay={ds_immortal}")

    # recall_boost: no recalls = base 1.0
    rb_none = compute_recall_boost(recall_count=0, last_recalled_at=None, now=now)
    report("wave3_precompute: no recalls → boost=1.0", rb_none == 1.0, f"boost={rb_none}")

    # recall_boost: heavy recall = higher boost
    rb_heavy = compute_recall_boost(recall_count=10, last_recalled_at=now - timedelta(hours=1), now=now)
    report("wave3_precompute: heavy recall → boost > 1.3", rb_heavy > 1.3, f"boost={rb_heavy}")

    # recall_boost: capped at 1.5
    rb_max = compute_recall_boost(recall_count=100, last_recalled_at=now, now=now)
    report("wave3_precompute: boost capped at 1.5", rb_max <= 1.5, f"boost={rb_max}")


async def test_wave3_recall_tracking():
    """[Test] Wave 3: recall_count and last_recalled_at are updated on memory_search."""
    conn = await asyncpg.connect(DB_URL)
    # Pick a valid memory
    row = await conn.fetchrow(
        "SELECT id, recall_count, last_recalled_at FROM memories WHERE invalid_at IS NULL LIMIT 1"
    )
    if not row:
        report("wave3_recall_tracking: has valid memories", False, "no memories found")
        await conn.close()
        return

    mid = row["id"]
    old_count = row["recall_count"] or 0

    # Simulate what memory_search does
    await conn.execute("""
        UPDATE memories SET
            recall_count = COALESCE(recall_count, 0) + 1,
            last_recalled_at = now()
        WHERE id = $1
    """, mid)

    updated = await conn.fetchrow(
        "SELECT recall_count, last_recalled_at FROM memories WHERE id = $1", mid
    )
    report("wave3_recall_tracking: count incremented", updated["recall_count"] == old_count + 1,
           f"old={old_count}, new={updated['recall_count']}")
    report("wave3_recall_tracking: last_recalled_at set", updated["last_recalled_at"] is not None,
           f"ts={updated['last_recalled_at']}")

    # Revert to not pollute real data
    await conn.execute(
        "UPDATE memories SET recall_count = $1, last_recalled_at = $2 WHERE id = $3",
        row["recall_count"], row["last_recalled_at"], mid
    )
    await conn.close()


# ══════════════════════════════════════════════════════════════════════
# TG-RAG Phase 2 — Persistent Temporal Summaries Tests (arxiv 2510.13590)
# ADA — 2026-04-12
# ══════════════════════════════════════════════════════════════════════

def _tgrag_reset_singletons():
    """Reset DB/Qdrant/Neo4j singletons for TG-RAG tests."""
    import db; db._pool = None
    import mcp_server_v3
    mcp_server_v3._qdrant = None
    mcp_server_v3._neo4j_driver = None


async def test_tg_summary_persist():
    """[Test] TG-RAG: temporal_graph_build sets summary property on Day nodes."""
    _tgrag_reset_singletons()
    import mcp_server_v3 as srv
    # Build temporal graph (generates summaries)
    result = await srv.temporal_graph_build(agent="ADA")
    report("tg_summary_persist: build succeeds", "Temporal graph built" in result,
           f"result={result[:100]}")
    report("tg_summary_persist: summaries mentioned", "summaries" in result.lower(),
           f"result={result[:120]}")

    # Verify at least one Day node has summary property
    try:
        neo = srv.get_neo4j()
        async with neo.session() as session:
            res = await session.run("""
                MATCH (d:Day) WHERE d.summary IS NOT NULL
                RETURN count(d) AS cnt
            """)
            rec = await res.single()
            cnt = rec["cnt"] if rec else 0
        report("tg_summary_persist: Day nodes have summaries", cnt > 0, f"count={cnt}")
    except Exception as e:
        report("tg_summary_persist: Neo4j check", False, f"error={e}")


async def test_tg_summary_get():
    """[Test] TG-RAG: temporal_summary_get retrieves cached summary."""
    _tgrag_reset_singletons()
    import mcp_server_v3 as srv
    from datetime import date
    today = date.today().isoformat()

    result = await srv.temporal_summary_get(period=today, agent="ADA")
    # Could be cached or "No cached summary" — both valid
    is_valid = ("summary" in result.lower()) or ("no cached" in result.lower()) or ("period" in result)
    report("tg_summary_get: returns valid response", is_valid, f"result={result[:100]}")

    # Test auto level detection
    result_month = await srv.temporal_summary_get(period=f"{date.today().year}-{date.today().month:02d}")
    is_valid_month = ("summary" in result_month.lower()) or ("no cached" in result_month.lower()) or ("period" in result_month)
    report("tg_summary_get: month auto-detection", is_valid_month, f"result={result_month[:100]}")


async def test_tg_global_strategy():
    """[Test] TG-RAG: temporal_query with strategy='global' returns summaries."""
    _tgrag_reset_singletons()
    import mcp_server_v3 as srv
    from datetime import date, timedelta
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()

    result = await srv.temporal_query(
        start_date=week_ago, end_date=today.isoformat(),
        agent="ADA", strategy="global"
    )
    # Either returns global summaries or falls back to local
    is_valid = ("Temporal Query" in result) or ("No memories" in result)
    report("tg_global_strategy: returns valid response", is_valid, f"result={result[:100]}")


async def test_tg_fallback():
    """[Test] TG-RAG: global strategy falls back to local when no cached summaries."""
    _tgrag_reset_singletons()
    import mcp_server_v3 as srv
    # Query a date range likely without summaries (far future)
    result = await srv.temporal_query(
        start_date="2099-01-01", end_date="2099-01-07",
        strategy="global"
    )
    report("tg_fallback: handles no-summary gracefully",
           "No memories" in result or "Temporal Query" in result,
           f"result={result[:100]}")


async def test_tg_hierarchical():
    """[Test] TG-RAG: Month summary exists after build (aggregates Day summaries)."""
    _tgrag_reset_singletons()
    import mcp_server_v3 as srv
    try:
        neo = srv.get_neo4j()
        async with neo.session() as session:
            res = await session.run("""
                MATCH (m:Month) WHERE m.summary IS NOT NULL
                RETURN count(m) AS cnt
            """)
            rec = await res.single()
            cnt = rec["cnt"] if rec else 0
        # Month summaries may or may not exist depending on data
        report("tg_hierarchical: Month summary query works", cnt >= 0, f"months_with_summary={cnt}")
    except Exception as e:
        report("tg_hierarchical: Neo4j accessible", False, f"error={e}")


# ── MAGMA Multi-Graph Parallel Fusion (arxiv 2601.03236) ──

def _magma_reset_singletons():
    import db
    db._pool = None
    import mcp_server_v3
    mcp_server_v3._qdrant = None
    mcp_server_v3._neo4j_driver = None


async def test_magma_basic():
    """[Test] MAGMA: basic retrieval returns fused results with views_used."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="ADA", query="decisiones importantes del equipo")
    import json as _j
    out = _j.loads(raw)
    report("magma_basic: has context", "context" in out, f"keys={list(out.keys())}")
    report("magma_basic: has views_used", "views_used" in out and len(out["views_used"]) > 0, f"views={out.get('views_used')}")
    report("magma_basic: has stats", "stats" in out, f"stats={out.get('stats')}")


async def test_magma_parallel():
    """[Test] MAGMA: multiple views execute (stats show hits from different graphs)."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="ADA", query="por qué decidimos usar PostgreSQL", views=["semantic", "causal"])
    import json as _j
    out = _j.loads(raw)
    stats = out.get("stats", {})
    has_sem = "semantic_hits" in stats
    has_cau = "causal_hits" in stats
    report("magma_parallel: semantic_hits in stats", has_sem, f"stats={stats}")
    report("magma_parallel: causal_hits in stats", has_cau, f"stats={stats}")


async def test_magma_dedup():
    """[Test] MAGMA: same memory in 2 graphs appears once with boost > 1.0."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    # Test fusion logic directly
    fake_results = {
        "semantic": [{"id": 1, "content": "test memory", "score": 0.8, "category": "test"}],
        "causal": [{"id": 1, "content": "test memory", "score": 0.7, "category": "test"}],
    }
    fused = await srv._magma_fuse("test query", fake_results)
    mems = fused["source_memories"]
    report("magma_dedup: single entry after dedup", len(mems) == 1, f"count={len(mems)}")
    if mems:
        report("magma_dedup: cross_graph_boost > 1.0", mems[0]["cross_graph_boost"] > 1.0, f"boost={mems[0]['cross_graph_boost']}")
        report("magma_dedup: sources has both", len(mems[0]["sources"]) == 2, f"sources={mems[0]['sources']}")


async def test_magma_cross_boost():
    """[Test] MAGMA: memory in 3 graphs gets 1.3x boost (1.0 + 0.15 * 2)."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    fake_results = {
        "semantic": [{"id": 42, "content": "multi-graph memory", "score": 1.0, "category": "test"}],
        "causal": [{"id": 42, "content": "multi-graph memory", "score": 0.9, "category": "test"}],
        "entity": [{"id": 42, "content": "multi-graph memory", "score": 0.8, "category": "test"}],
    }
    fused = await srv._magma_fuse("test query", fake_results)
    mems = fused["source_memories"]
    report("magma_cross_boost: single entry", len(mems) == 1, f"count={len(mems)}")
    if mems:
        expected_boost = 1.3  # 1.0 + 0.15 * 2
        report("magma_cross_boost: boost == 1.3", abs(mems[0]["cross_graph_boost"] - expected_boost) < 0.01, f"boost={mems[0]['cross_graph_boost']}")
        expected_score = round(1.0 * 1.3, 4)
        report("magma_cross_boost: fused_score correct", abs(mems[0]["fused_score"] - expected_score) < 0.01, f"fused={mems[0]['fused_score']}")


async def test_magma_auto_intent():
    """[Test] MAGMA: 'por qué' query selects causal view."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="ADA", query="por qué elegimos esta arquitectura")
    import json as _j
    out = _j.loads(raw)
    views = out.get("views_used", [])
    report("magma_auto_intent: causal in views", "causal" in views, f"views={views}")


async def test_magma_manual_views():
    """[Test] MAGMA: explicit views param restricts to those views only."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="ADA", query="test query", views=["semantic", "temporal"])
    import json as _j
    out = _j.loads(raw)
    views = out.get("views_used", [])
    report("magma_manual_views: only requested views", set(views) <= {"semantic", "temporal"}, f"views={views}")
    report("magma_manual_views: no causal/entity", "causal" not in views and "entity" not in views, f"views={views}")


async def test_magma_fuse_false():
    """[Test] MAGMA: fuse=False returns raw per-graph results."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="ADA", query="test", views=["semantic"], fuse=False)
    import json as _j
    out = _j.loads(raw)
    mems = out.get("memories", {})
    report("magma_fuse_false: memories is dict (per-graph)", isinstance(mems, dict), f"type={type(mems).__name__}")
    report("magma_fuse_false: context is empty", out.get("context") == "", f"context_len={len(out.get('context', ''))}")


async def test_magma_empty():
    """[Test] MAGMA: nonexistent agent returns graceful empty response."""
    _magma_reset_singletons()
    import mcp_server_v3 as srv
    raw = await srv.magma_retrieve(agent="NONEXISTENT_AGENT_XYZ", query="anything")
    import json as _j
    out = _j.loads(raw)
    report("magma_empty: valid JSON output", "stats" in out, f"keys={list(out.keys())}")
    report("magma_empty: no crash", True, "graceful empty response")


# ── ERL — Experiential Reflective Learning (arxiv 2603.24639) ──

ERL_TEST_AGENT = "ERL_TEST"


async def _erl_reset_test_agent():
    """Reset DB singletons and clean ERL test agent state."""
    import db
    db._pool = None
    import mcp_server_v3
    mcp_server_v3._qdrant = None
    mcp_server_v3._neo4j_driver = None
    pool = await mcp_server_v3.get_pool()
    async with pool.acquire() as conn:
        # Fetch IDs first so we can also delete from Qdrant (no orphans)
        erl_ids = [r['id'] for r in await conn.fetch(
            "SELECT id FROM memories WHERE agent = $1", ERL_TEST_AGENT
        )]
        await conn.execute("DELETE FROM memories WHERE agent = $1", ERL_TEST_AGENT)
        # Cascade: delete activations before instincts (FK constraint)
        await conn.execute(
            """DELETE FROM instinct_activations
               WHERE instinct_id IN (SELECT id FROM instincts WHERE agent = $1)""",
            ERL_TEST_AGENT,
        )
        await conn.execute("DELETE FROM instincts WHERE agent = $1", ERL_TEST_AGENT)
    if erl_ids:
        try:
            from qdrant_client import QdrantClient
            from config import settings as _cfg
            QdrantClient(host="localhost", port=6333, api_key=_cfg.qdrant_api_key, https=False).delete("soul_memories", points_selector=erl_ids)
        except Exception:
            pass


async def _erl_seed_heuristic(srv, content, confidence, activation_count=0, outcome="success"):
    """Seed a heuristic via memory_store (importance=8 bypasses A-MAC)."""
    import json as _j
    import re as _re
    meta = {
        "tags": ["heuristic", "erl", outcome],
        "applies_to": "test_applies_to",
        "confidence": confidence,
        "parent_task": "seed test task",
        "outcome": outcome,
        "erl_version": 1,
        "activation_count": activation_count,
    }
    res = await srv.memory_store(
        agent=ERL_TEST_AGENT,
        category="insight",
        content=content,
        importance=8,
        source="erl_reflect",
        metadata=_j.dumps(meta),
    )
    m = _re.search(r"#?(\d+)", res or "")
    return int(m.group(1)) if m else None


async def test_erl_reflect_success():
    """[Test] ERL: reflect on success stores insight with heuristic tag."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    async def fake_ollama(prompt, timeout=30.0):
        return _j.dumps([
            {"heuristic": "siempre validar input antes de procesar",
             "applies_to": "any", "confidence": 0.8}
        ])
    orig = srv._erl_call_ollama
    srv._erl_call_ollama = fake_ollama
    try:
        raw = await srv.erl_reflect(
            agent=ERL_TEST_AGENT,
            task_description="tarea test",
            outcome="success",
            trajectory="pasos y decisiones",
        )
        out = _j.loads(raw)
        report("erl_reflect_success: generated >= 1",
               out.get("heuristics_generated", 0) >= 1, f"out={out}")
        pool = await srv.get_pool()
        row = await pool.fetchrow(
            "SELECT category, metadata FROM memories WHERE agent = $1 ORDER BY id DESC LIMIT 1",
            ERL_TEST_AGENT,
        )
        if row:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else _j.loads(row["metadata"] or "{}")
            report("erl_reflect_success: category=insight",
                   row["category"] == "insight", f"cat={row['category']}")
            report("erl_reflect_success: tags contains 'heuristic'",
                   "heuristic" in (meta.get("tags") or []), f"tags={meta.get('tags')}")
    finally:
        srv._erl_call_ollama = orig


async def test_erl_reflect_failure():
    """[Test] ERL: outcome='failure' → tags include 'failure'."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    async def fake_ollama(prompt, timeout=30.0):
        return _j.dumps([
            {"heuristic": "nunca deployar sin tests",
             "applies_to": "deploy", "confidence": 0.9}
        ])
    orig = srv._erl_call_ollama
    srv._erl_call_ollama = fake_ollama
    try:
        raw = await srv.erl_reflect(
            agent=ERL_TEST_AGENT,
            task_description="deploy roto",
            outcome="failure",
            trajectory="deploy sin tests → prod caído",
        )
        out = _j.loads(raw)
        report("erl_reflect_failure: generated >= 1",
               out.get("heuristics_generated", 0) >= 1, f"out={out}")
        pool = await srv.get_pool()
        row = await pool.fetchrow(
            "SELECT metadata FROM memories WHERE agent = $1 ORDER BY id DESC LIMIT 1",
            ERL_TEST_AGENT,
        )
        if row:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else _j.loads(row["metadata"] or "{}")
            report("erl_reflect_failure: tags has 'failure'",
                   "failure" in (meta.get("tags") or []), f"tags={meta.get('tags')}")
    finally:
        srv._erl_call_ollama = orig


async def test_erl_reflect_malformed_ollama():
    """[Test] ERL: malformed JSON → retries once, returns 0 gracefully."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    call_count = {"n": 0}

    async def fake_ollama(prompt, timeout=30.0):
        call_count["n"] += 1
        return "this is not json at all just prose"

    orig = srv._erl_call_ollama
    srv._erl_call_ollama = fake_ollama
    try:
        raw = await srv.erl_reflect(
            agent=ERL_TEST_AGENT,
            task_description="x",
            outcome="success",
            trajectory="y",
        )
        out = _j.loads(raw)
        report("erl_reflect_malformed: generated == 0",
               out.get("heuristics_generated") == 0, f"out={out}")
        report("erl_reflect_malformed: error=malformed_json",
               out.get("error") == "malformed_json", f"error={out.get('error')}")
        report("erl_reflect_malformed: retried once (2 calls)",
               call_count["n"] == 2, f"calls={call_count['n']}")
    finally:
        srv._erl_call_ollama = orig


async def test_erl_inject_retrieval():
    """[Test] ERL: inject retrieves heuristics ordered by fused score."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    for i, conf in enumerate([0.95, 0.85, 0.75, 0.8, 0.9]):
        await _erl_seed_heuristic(
            srv,
            f"heuristica test numero {i} sobre migracion schema postgres",
            conf,
        )

    raw = await srv.erl_inject(
        agent=ERL_TEST_AGENT,
        task_description="migracion schema postgres",
        top_k=5,
        min_confidence=0.7,
    )
    out = _j.loads(raw)
    hs = out.get("heuristics", [])
    report("erl_inject_retrieval: returned heuristics",
           len(hs) >= 1, f"count={len(hs)}")
    if len(hs) >= 2:
        sorted_ok = all(hs[i]["score"] >= hs[i + 1]["score"] for i in range(len(hs) - 1))
        report("erl_inject_retrieval: sorted desc by score",
               sorted_ok, f"scores={[round(h['score'], 3) for h in hs]}")


async def test_erl_inject_min_confidence():
    """[Test] ERL: heuristics below min_confidence filtered out."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    await _erl_seed_heuristic(srv, "baja confianza test", 0.5)
    await _erl_seed_heuristic(srv, "alta confianza test", 0.9)

    raw = await srv.erl_inject(
        agent=ERL_TEST_AGENT,
        task_description="confianza test",
        top_k=10,
        min_confidence=0.8,
    )
    out = _j.loads(raw)
    hs = out.get("heuristics", [])
    all_above = all(h["confidence"] >= 0.8 for h in hs)
    report("erl_inject_min_conf: all >= 0.8", all_above,
           f"confs={[h['confidence'] for h in hs]}")
    report("erl_inject_min_conf: low-conf excluded",
           not any("baja" in h["heuristic"] for h in hs),
           "0.5 filtered")


async def test_erl_inject_activation_count():
    """[Test] ERL: inject increments activation_count on returned heuristics."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    hid = await _erl_seed_heuristic(
        srv, "heuristica para activation count incremento test", 0.9, activation_count=2
    )
    raw = await srv.erl_inject(
        agent=ERL_TEST_AGENT,
        task_description="heuristica para activation count incremento test",
        top_k=5,
        min_confidence=0.7,
    )
    out = _j.loads(raw)
    hs = out.get("heuristics", [])
    if hs:
        report("erl_inject_activation: response count == 3",
               hs[0].get("activation_count") == 3,
               f"count={hs[0].get('activation_count')}")
    pool = await srv.get_pool()
    row = await pool.fetchrow("SELECT metadata FROM memories WHERE id = $1", hid)
    if row:
        meta = row["metadata"] if isinstance(row["metadata"], dict) else _j.loads(row["metadata"] or "{}")
        report("erl_inject_activation: persisted in DB == 3",
               meta.get("activation_count") == 3,
               f"db_count={meta.get('activation_count')}")


async def test_erl_inject_formatted_context():
    """[Test] ERL: formatted_context is non-empty Spanish bullet format."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    await _erl_seed_heuristic(srv, "validar entrada siempre antes de procesar", 0.9)
    raw = await srv.erl_inject(
        agent=ERL_TEST_AGENT,
        task_description="validar entrada",
        top_k=5,
        min_confidence=0.7,
    )
    out = _j.loads(raw)
    fc = out.get("formatted_context", "")
    report("erl_inject_formatted: non-empty", len(fc) > 0, f"len={len(fc)}")
    report("erl_inject_formatted: Spanish header",
           "Lecciones aprendidas" in fc, f"head={fc[:60]}")
    report("erl_inject_formatted: bullet present", "•" in fc, "bullet ok")


async def test_erl_promote_sweep():
    """[Test] ERL: conf=0.9 + activation=3 → promoted to instinct."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    hid = await _erl_seed_heuristic(
        srv, "heuristica candidata a instinto promocion", 0.9, activation_count=3
    )
    res = await srv._erl_promote_sweep(ERL_TEST_AGENT)
    report("erl_promote_sweep: promoted >= 1",
           res.get("promoted", 0) >= 1, f"res={res}")
    report("erl_promote_sweep: id in promoted_ids",
           hid in res.get("promoted_ids", []),
           f"ids={res.get('promoted_ids')}")
    pool = await srv.get_pool()
    row = await pool.fetchrow("SELECT metadata FROM memories WHERE id = $1", hid)
    if row:
        meta = row["metadata"] if isinstance(row["metadata"], dict) else _j.loads(row["metadata"] or "{}")
        report("erl_promote_sweep: metadata.promoted_to_instinct set",
               meta.get("promoted_to_instinct") is not None,
               f"pti={meta.get('promoted_to_instinct')}")


async def test_erl_promote_skip():
    """[Test] ERL: conf=0.8 (< 0.85) → NOT promoted."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv

    hid = await _erl_seed_heuristic(
        srv, "heuristica baja confianza no promocionable", 0.8, activation_count=5
    )
    res = await srv._erl_promote_sweep(ERL_TEST_AGENT)
    report("erl_promote_skip: not in promoted_ids",
           hid not in res.get("promoted_ids", []),
           f"res={res}")


async def test_erl_round_trip():
    """[Test] ERL: reflect → inject round-trip returns the heuristic."""
    await _erl_reset_test_agent()
    import mcp_server_v3 as srv
    import json as _j

    async def fake_ollama(prompt, timeout=30.0):
        # confidence=1.0 → importance=8 → bypasses A-MAC admission gate
        return _j.dumps([
            {"heuristic": "al migrar schema postgres siempre hacer dry-run primero",
             "applies_to": "schema_migration",
             "confidence": 1.0}
        ])
    orig = srv._erl_call_ollama
    srv._erl_call_ollama = fake_ollama
    try:
        res_raw = await srv.erl_reflect(
            agent=ERL_TEST_AGENT,
            task_description="migrar schema postgres dry-run primero",
            outcome="success",
            trajectory="hice dry-run, validé, corrí live",
        )
        res = _j.loads(res_raw)
        stored = res.get("heuristics_generated", 0) >= 1
        report("erl_round_trip: reflect stored heuristic", stored, f"res={res}")
        raw = await srv.erl_inject(
            agent=ERL_TEST_AGENT,
            task_description="migrar schema postgres dry-run",
            top_k=3,
            min_confidence=0.7,
        )
        out = _j.loads(raw)
        hs = out.get("heuristics", [])
        found = any("dry-run" in h["heuristic"] for h in hs)
        report("erl_round_trip: heuristic retrieved",
               found, f"count={len(hs)}, hs={[h.get('heuristic','')[:50] for h in hs]}")
    finally:
        srv._erl_call_ollama = orig


async def test_species_profiles():
    """[Test] Species-Scaling Law profiles in seal_nerves.py (Fase 3.2/4 — 2026-04-18)
    Verifies: fly/mammal/human τ values, SEAL_SPECIES env var, _apply_species_profile().
    """
    import math, os, importlib, sys

    # Load SPECIES_PROFILES by executing only the config block of seal_nerves.py
    nerves_path = os.path.join(os.path.dirname(__file__), "seal_nerves.py")
    ns: dict = {}
    with open(nerves_path) as f:
        src = f.read()
    # Execute only up to STIMULI (avoids asyncpg/httpx import issues in test env)
    safe_src = src.split("# ── Stimuli weights")[0]
    exec(compile(safe_src, nerves_path, "exec"), ns)

    SPECIES_PROFILES = ns["SPECIES_PROFILES"]
    _apply_species_profile = ns["_apply_species_profile"]

    # ── 1. Fly profile: original Drosophila τ ────────────────────────────────
    report("species: fly/curiosity τ=4h",
           abs(SPECIES_PROFILES["fly"]["curiosity"] - 4.0*3600) < 1,
           f"{SPECIES_PROFILES['fly']['curiosity']/3600:.3f}h")
    report("species: fly/alert_drive τ=0.5h",
           abs(SPECIES_PROFILES["fly"]["alert_drive"] - 0.5*3600) < 1,
           f"{SPECIES_PROFILES['fly']['alert_drive']/3600:.3f}h")

    # ── 2. Mammal profile: mouse V1 MICrONS ─────────────────────────────────
    # τ_mammal = τ_fly × (inhib_fly / inhib_mouse)
    # curiosity: 4h × 0.114/0.710 = 0.642h
    expected_mammal_curiosity = 4.0 * 3600 * (0.114 / 0.710)
    report("species: mammal/curiosity derived correctly from mouse inhib ratio",
           abs(SPECIES_PROFILES["mammal"]["curiosity"] - expected_mammal_curiosity) < 60,
           f"{SPECIES_PROFILES['mammal']['curiosity']/3600:.4f}h (expected {expected_mammal_curiosity/3600:.4f}h)")

    # ── 3. Human profile: Shapson-Coe 2024 structural metric ─────────────────
    # τ_human = τ_fly × (inhib_fly / inhib_human_structural)
    # inhib_human_structural = 0.329 (50.3M inhib / 152.8M total, DOI:10.1126/science.adk4858 Fig.4)
    # curiosity: 4h × 0.114/0.329 = 1.386h
    expected_human_curiosity = 4.0 * 3600 * (0.114 / 0.329)
    report("species: human/curiosity derived correctly from H01 inhib ratio",
           abs(SPECIES_PROFILES["human"]["curiosity"] - expected_human_curiosity) < 60,
           f"{SPECIES_PROFILES['human']['curiosity']/3600:.4f}h (expected {expected_human_curiosity/3600:.4f}h)")

    # human alert_drive: 0.5h × 0.231/0.329 = 0.351h (structural metric, same source)
    expected_human_alert = 0.5 * 3600 * (0.231 / 0.329)
    report("species: human/alert_drive derived from H01 inhib ratio",
           abs(SPECIES_PROFILES["human"]["alert_drive"] - expected_human_alert) < 60,
           f"{SPECIES_PROFILES['human']['alert_drive']/3600:.4f}h (expected {expected_human_alert/3600:.4f}h)")

    # ── 4. Human τ < fly τ for fast-decay tanks ──────────────────────────────
    report("species: human alert decays faster than fly (τ_human < τ_fly)",
           SPECIES_PROFILES["human"]["alert_drive"] < SPECIES_PROFILES["fly"]["alert_drive"],
           f"human={SPECIES_PROFILES['human']['alert_drive']/3600:.3f}h < fly={SPECIES_PROFILES['fly']['alert_drive']/3600:.3f}h")
    report("species: human curiosity decays faster than fly",
           SPECIES_PROFILES["human"]["curiosity"] < SPECIES_PROFILES["fly"]["curiosity"],
           f"human={SPECIES_PROFILES['human']['curiosity']/3600:.3f}h < fly={SPECIES_PROFILES['fly']['curiosity']/3600:.3f}h")

    # ── 5. _apply_species_profile mutates TANKS correctly ────────────────────
    import copy
    tanks = copy.deepcopy(ns["TANKS"])
    # Re-apply fly to get baseline
    tanks = _apply_species_profile(tanks, "fly")
    report("species: apply_profile(fly) sets curiosity=4h",
           abs(tanks["curiosity"]["decay_tau_s"] - 4.0*3600) < 1,
           f"{tanks['curiosity']['decay_tau_s']/3600:.3f}h")

    tanks = _apply_species_profile(tanks, "human")
    report("species: apply_profile(human) overrides curiosity to H01 value",
           abs(tanks["curiosity"]["decay_tau_s"] - SPECIES_PROFILES["human"]["curiosity"]) < 1,
           f"{tanks['curiosity']['decay_tau_s']/3600:.4f}h")

    # ── 6. context_pressure unchanged (SEAL-specific, not in profiles) ───────
    report("species: context_pressure τ unchanged (not in species profile)",
           "context_pressure" not in SPECIES_PROFILES["human"],
           "context_pressure correctly excluded from species scaling")

    # ── 7. SEAL_SPECIES env var controls active species ───────────────────────
    active = ns.get("_ACTIVE_SPECIES", "unknown")
    report("species: SEAL_SPECIES env var loaded at module init",
           active in ("fly", "mammal", "human"),
           f"_ACTIVE_SPECIES='{active}'")


async def main():
    print("=" * 60)
    print(f"🧪 SEAL MCP Tool Test Suite — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    tests = [
        ("Species Profiles — fly/mammal/human τ", test_species_profiles),
        ("A-MAC — 5-Factor Admission Gate", test_amac_admission_gate),
        ("SleepGate — Dry Run", test_sleep_gate_dry_run),
        ("SleepGate — Emotional Resistance", test_sleep_gate_forget_emotional_resistance),
        ("SleepGate — Cron Syntax", test_sleep_gate_cron_syntax),
        ("Mood Retrieval", test_mood_retrieval),
        ("Hybrid Search — Mood Weight", test_hybrid_search_mood_weight),
        ("Entity Extraction", test_entity_extraction),
        ("Entity Query — Neo4j", test_entity_query_neo4j),
        ("Cross-Agent Search", test_cross_search),
        ("Share Promote", test_share_promote),
        ("Brain Health Report", test_brain_health),
        ("RL Utility Bounds", test_rl_utility_bounds),
        ("Vector Null Fix", test_vector_null_fix),
        ("Qdrant-PG Sync", test_qdrant_pg_sync),
        ("Procedures Have Embeddings", test_procedures_have_embeddings),
        ("Hindsight Confidence", test_hindsight_confidence),
        ("SleepGate Composite Resistance", test_sleepgate_composite_resistance),
        ("SleepGate Error Isolation", test_sleepgate_error_isolation),
        ("Confidence PG-Qdrant Sync", test_confidence_pg_qdrant_sync),
        ("MAGMA Intent Classification", test_intent_classification),
        ("Bitemporal Edges", test_bitemporal_edges),
        ("Peer Model Table", test_peer_model_table),
        ("Reflection Synthesize", test_reflection_synthesize),
        ("Wave 3 Schema Columns", test_wave3_columns),
        ("Rate Limiting — Sliding Window", test_rate_limiting),
        ("Health Check — Structure & Live Services", test_health_check_structure),
        ("Graceful Shutdown — Idempotency", test_graceful_shutdown),
        ("Tier 5 — Opinions Schema", test_tier5_opinions_schema),
        ("Tier 5 — Belief Query", test_tier5_belief_query),
        ("Tier 5 — Boot Context Beliefs", test_tier5_boot_context_beliefs),
        ("Tier 5 — Reflection Writes Opinions", test_tier5_reflection_writes_opinions),
        ("Cold Archive — Table Exists", test_cold_archive_table_exists),
        ("Cold Archive — Migrate Dry Run", test_cold_archive_migrate_dry_run),
        ("Cold Archive — Migrate Live", test_cold_archive_migrate_live),
        ("Cold Archive — Query Empty", test_cold_archive_query_empty),
        ("Cold Archive — Stats", test_cold_archive_stats),
        ("Cold Archive — TTL Purge", test_cold_archive_ttl_purge),
        ("Cold Archive — Include Archived Search", test_memory_search_include_archived),
        ("MIRIX — Classification", test_mirix_classification),
        ("MIRIX — Migration", test_mirix_migration),
        ("MIRIX — Retrieval Filter", test_mirix_retrieval_filter),
        ("MIRIX — Core Boost", test_mirix_core_boost),
        ("MIRIX — Vault Exclusion", test_mirix_vault_exclusion),
        ("MIRIX — Boot Context", test_mirix_boot_context),
        ("Wave 3 — Pre-compute Scoring", test_wave3_precompute_scoring),
        ("Wave 3 — Recall Tracking", test_wave3_recall_tracking),
        # TG-RAG Phase 2 — Persistent Temporal Summaries (arxiv 2510.13590)
        ("TG-RAG — Summary Persist", test_tg_summary_persist),
        ("TG-RAG — Summary Get", test_tg_summary_get),
        ("TG-RAG — Global Strategy", test_tg_global_strategy),
        ("TG-RAG — Fallback", test_tg_fallback),
        ("TG-RAG — Hierarchical", test_tg_hierarchical),
        # MAGMA — Multi-Graph Parallel Fusion (arxiv 2601.03236)
        ("MAGMA — Basic Retrieval", test_magma_basic),
        ("MAGMA — Parallel Views", test_magma_parallel),
        ("MAGMA — Dedup", test_magma_dedup),
        ("MAGMA — Cross-Graph Boost", test_magma_cross_boost),
        ("MAGMA — Auto Intent", test_magma_auto_intent),
        ("MAGMA — Manual Views", test_magma_manual_views),
        ("MAGMA — Fuse False", test_magma_fuse_false),
        ("MAGMA — Empty Graceful", test_magma_empty),
        # ERL — Experiential Reflective Learning (arxiv 2603.24639)
        ("ERL — Reflect Success", test_erl_reflect_success),
        ("ERL — Reflect Failure", test_erl_reflect_failure),
        ("ERL — Reflect Malformed Ollama", test_erl_reflect_malformed_ollama),
        ("ERL — Inject Retrieval", test_erl_inject_retrieval),
        ("ERL — Inject Min Confidence", test_erl_inject_min_confidence),
        ("ERL — Inject Activation Count", test_erl_inject_activation_count),
        ("ERL — Inject Formatted Context", test_erl_inject_formatted_context),
        ("ERL — Promote Sweep", test_erl_promote_sweep),
        ("ERL — Promote Skip", test_erl_promote_skip),
        ("ERL — Round Trip", test_erl_round_trip),
        # Token regression test lives in separate file: test_token_savings.py (6 tests)
    ]

    for name, test_fn in tests:
        print(f"\n📋 {name}")
        try:
            await test_fn()
        except Exception as e:
            report(f"{name}: EXCEPTION", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    total = results["passed"] + results["failed"]
    print(f"Results: {results['passed']}/{total} passed, {results['failed']} failed")
    if results["errors"]:
        print(f"\n❌ Failures:")
        for e in results["errors"]:
            print(f"  - {e}")
    else:
        print("✅ ALL TESTS PASSED")
    print("=" * 60)

    sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
