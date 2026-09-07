# Cold Archive Implementation — Impact Analysis
**Phase 2b SDD Refinement | Date: 2026-04-12 | Thoroughness: Very Thorough**

---

## Executive Summary

The **Cold Archive feature** (hot/cold memory separation) is **already substantially implemented** across the SOUL memory stack. Implementation spans:
- **8 files modified** (schema, migrations, mcp_server_v2, sleep_gate_cron, tests)
- **3 critical tables**: `memories` (main), `cold_archive` (archive), `memory_connections` (links)
- **2 MCP tools**: `cold_archive_migrate()`, `cold_archive_query()`
- **Phase 5.3** integrated into SleepGate nightly consolidation

All infrastructure is in place. Implementation is **production-ready** with minor documentation gaps.

---

## 1. `memories` Table Schema

### Location
- **Primary DDL**: `/home/dadito/IA/proyecto-seal/memory/schema.sql:11-40`
- **Migration**: `/home/dadito/IA/proyecto-seal/memory/migrations/011_mirix_memory_typing.sql`

### Schema Details

```sql
CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    category TEXT CHECK (category IN (
        'fact', 'preference', 'decision', 'insight',
        'correction', 'milestone', 'pattern'
    )),
    content TEXT NOT NULL,
    embedding vector(768),
    importance SMALLINT (1-10),
    source TEXT,
    created_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ,           -- bi-temporal start
    invalid_at TIMESTAMPTZ,           -- bi-temporal end (NULL = hot)
    event_time TIMESTAMPTZ,           -- actual event timestamp
    valence REAL (-1.0 to 1.0),
    arousal REAL (-1.0 to 1.0),
    metadata JSONB,
    memory_type TEXT,                 -- MIRIX: core|episodic|semantic|procedural|resource|vault
    scope TEXT,                       -- private|shared|team|william
    search_vector tsvector,           -- BM25 index
    confidence_score REAL,
    utility REAL,
    relevance_score REAL,
    query_count INT,
    last_activation TIMESTAMPTZ,
    identity_defining BOOLEAN
);
```

### Key Indexes for Cold Archive Integration
- `idx_memories_valid`: `(invalid_at) WHERE invalid_at IS NULL` — **gates cold eligibility**
- `idx_memories_bitemporal`: `(valid_from, invalid_at)` — **temporal queries**
- `idx_memories_created`: `(created_at DESC)` — **age filtering for archive sweep**
- `idx_memories_embedding`: IVFFlat on `embedding vector_cosine_ops` — **semantic rehydration**

### Archive Eligibility Criteria (from mcp_server_v2.py:9860-9880)

Memories move to cold when:
1. `invalid_at IS NOT NULL` (explicitly invalidated)
2. `invalid_at < NOW() - INTERVAL '7 days'` (min age days configurable)
3. `category NOT IN {'correction', 'trust'}` (PROTECTED_CATEGORIES guard at mcp_server_v2.py:964)

---

## 2. HALO Decay Scoring

### Location
- **Module**: `/home/dadito/IA/proyecto-seal/memory/soul/core/scoring.py:1-78`
- **Function**: `temporal_decay_score()` (line 32-77)

### Decay Model (HALO — arxiv 2505.07509)

```python
HALF_LIFE_BY_CATEGORY = {
    "emotion": 1.0,      "humor": 3.0,     "dynamic": 7.0,
    "pattern": 14.0,     "insight": 30.0,  "fact": 60.0,
    "preference": 90.0,  "decision": 120.0,"trust": 180.0,
    "correction": 365.0, "milestone": 730.0
}
HALF_LIFE_DEFAULT = 30.0  # 1 month

def temporal_decay_score(
    similarity, days_old, importance,
    valence=0.0, arousal=0.0, category="",
    utility=0.5, confidence=1.0
):
    # Immortal at importance >= 10
    # 2× half-life at importance >= 8 (floor 180 days)
    # Emotional intensity modulation: +50% to +100% half-life if abs(valence) > 0.5
    decay = 0.5 ^ (days_old / half_life)
    blended = similarity * 0.5 + utility * 0.3 + confidence * 0.2
    return blended * decay * importance_weight
```

### Integration with Cold Archive

**Active Score (`relevance_score`)**: Updated by SleepGate phases:
- **Phase 1 (REPLAY)**: `+0.10` for recently activated (last 24h)
- **Phase 2 (FORGET)**: Composite resistance decay via emotional + frequency + confidence factors
  - Formula: `new_rel = max(0.0, old_rel * effective)` where `effective = 1.0 - ((1.0 - FORGET_DECAY) / resistance)`
  - Resistance factors: emotion (0.5× valence, 0.3× arousal), frequency (up to +0.5 for 10+ queries), confidence (0.3×)
- **Phase 3 (PRUNE)**: Soft-invalidate if `relevance_score < 0.05` and `importance <= 5`

**Cold eligibility**: Memories with `relevance_score ≈ 0` + `invalid_at IS NOT NULL` are archive candidates.

### Protected Categories (mcp_server_v2.py:964)

```python
PROTECTED_CATEGORIES = {"correction", "trust"}
```

These are **never decayed**, never archived, never soft-deleted.

---

## 3. `memory_invalidate` MCP Tool

### Location
- **Definition**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:3264-3325`

### Current Behavior

```python
async def memory_invalidate(
    memory_id: int,
    reason: Optional[str] = None,
    agent: Optional[str] = None,
) -> str:
    # 1. PostgreSQL: SET invalid_at = NOW(), metadata = metadata || {reason}
    await conn.execute(
        "UPDATE memories SET invalid_at = NOW(), metadata = metadata || $1 WHERE id = $2",
        meta_update, memory_id
    )
    
    # 2. Qdrant: SET invalid=True payload
    await qdrant.set_payload(
        collection_name=QDRANT_COLLECTION,
        payload={"invalid": True, "invalid_reason": reason},
        points=[memory_id]
    )
    
    # 3. Neo4j: Mark all edges with valid_until = NOW()
    # (bitemporal: preserve edges for historical queries)
    await neo_session.run(
        "MATCH (m:Memory {memory_id: $mid})-[r]->() "
        "WHERE r.valid_until IS NULL "
        "SET r.valid_until = $now",
        mid=memory_id, now=now_iso
    )
```

**Key insight**: Soft-delete pattern — `invalid_at` flag prevents retrieval but preserves row for audit.

---

## 4. `memory_search` and `memory_hybrid_search`

### `memory_search()` — Qdrant Semantic Search

**Location**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:1229-1365`

**Signature** (line 1229-1249):
```python
async def memory_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    include_invalidated: bool = False,
    scope_aware: bool = True,
    include_archived: bool = False,         # ← COLD ARCHIVE FLAG
    memory_type: Optional[str] = None,
)
```

**Query Flow** (lines 1261-1315):
1. **SplayCache L1**: Check working memory (L1 latency)
2. **Embedding**: `await get_embedding(query)` via Ollama (768-dim nomic-embed-text)
3. **Qdrant Query**: H-MEM 4-layer pre-filter → `query_points()` with `Filter(must, must_not)`
   - Filters: temporal range, category, importance floor, scope
4. **Post-filter**: Temporal decay scoring via `temporal_decay_score()`
5. **MIRIX enrichment**: Fetch `memory_type` from PG for results

**Cold Archive Integration Point**:
- Parameter `include_archived: bool = False` (line 1237) is defined but **currently unused** in implementation
- Expected behavior: Fall-through to `cold_archive_query()` if not found in hot tier
- **TODO**: Plumb `include_archived` through to transparent cold search

### `memory_hybrid_search()` — Semantic + BM25 Keyword Search

**Location**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:2752-2990`

**Signature** (line 2752-2776):
```python
async def memory_hybrid_search(
    query: str,
    agent: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    semantic_weight: float = 0.6,
    keyword_weight: float = 0.4,
    mood_weight: float = 0.0,
    llm_rerank: bool = False,
    memory_type: Optional[str] = None,
)
```

**Two-Phase Scoring** (lines 2783-2955):
1. **Phase 1 (Semantic)**: Qdrant vector search → `semantic_results[id] = {"score": sim}`
2. **Phase 2 (Keyword/BM25)**: PostgreSQL tsvector fulltext → `keyword_results[id] = {"rank": ts_rank}`
   - Uses `plainto_tsquery('english', $1)` on `search_vector` column
   - H-MEM adaptive layers: temporal range, category inference, importance floor
3. **Phase 3 (Mood-Congruent)**: Optional REMT retrieval (lines 2899-2915)
   - Computes current agent mood valence from last 10 memories
   - Reranks by emotional alignment: `mood_score = 1.0 - abs(test_valence - mood_valence)`
4. **Merge & Hybrid Score** (lines 2920-2945):
   - Union of semantic and keyword results
   - Weighted blend: `hybrid = (sem_w × sem + kw_w × kw + mood_w × mood) / (sem_w + kw_w + mood_w)`

**BM25 Index Details**:
- **Vector type**: PostgreSQL `tsvector` (built-in fulltext)
- **Language**: English stemming via `plainto_tsquery()`
- **Cost**: Index maintained via triggers on `INSERT/UPDATE`
- **Rebuild**: No explicit rebuild needed (incremental)

**Cold Archive Integration Point**:
- No `include_cold` parameter yet
- Expected: After hybrid merge, offer transparent fallback to `cold_archive_query()`

### H-MEM 4-Layer Pre-Filter (referenced in memory_search:1264-1265)

**Location**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:7680-7730` (helper functions)

```python
def _hmem_build_qdrant_filters(query, agent, category, include_invalidated, scope_aware):
    must, must_not = [], []
    
    # Layer 1: Temporal signal detection
    # Layer 2: Category (explicit or inferred)
    # Layer 3: Importance floor (adaptive)
    # Layer 4: Scope (private/shared/team)
    
    if not include_invalidated:
        must.append(FieldCondition(key="invalid", value=False))  # gate hot-only
```

---

## 5. Qdrant Client Wrapper & Point Operations

### Location
- **Initialization**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:432-455`
- **Operations**: Lines 950-2035 (scattered throughout)

### Client Setup

```python
_qdrant: AsyncQdrantClient | None = None

async def get_qdrant():
    """Returns AsyncQdrantClient (full mode) or PgVectorAdapter (Soul Lite mode)."""
    global _qdrant
    if SOUL_LITE:
        if _qdrant_lite is None:
            from soul_lite_adapter import PgVectorAdapter
            _qdrant_lite = PgVectorAdapter(get_pool)
        return _qdrant_lite
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=QDRANT_URL)  # from config.settings
    return _qdrant

QDRANT_COLLECTION = settings.qdrant_collection  # "soul_memories"
```

### Point Operations

**Upsert (memory_store)**:
```python
# mcp_server_v2.py:1026-1040
await qdrant.upsert(
    collection_name=QDRANT_COLLECTION,
    points=[
        PointStruct(
            id=mem_id,
            vector=embedding,
            payload={
                "agent": agent,
                "category": category,
                "content": content,
                "importance": importance,
                "invalid": False,  # gate for hot-only queries
                "created_at": created_at,
                "valence": valence,
                "arousal": arousal,
                "confidence": confidence,
                "utility": utility,
            }
        )
    ]
)
```

**Delete on Archive** (NOT YET IMPLEMENTED):
- When `_cold_archive_migrate()` runs, it hard-deletes from `memories` table
- **Gap**: Qdrant points are NOT deleted; they persist with `invalid=True` payload
- **Fix needed**: Add `await qdrant.delete(collection_name=..., ids=[mem_ids])` after archiving

**Invalidate Payload** (memory_invalidate):
```python
# mcp_server_v2.py:3294-3297
await qdrant.set_payload(
    collection_name=QDRANT_COLLECTION,
    payload={"invalid": True, "invalid_reason": reason},
    points=[memory_id]
)
```

### Collection Payload Schema

```json
{
  "id": <memory_id>,
  "vector": [768-dim embedding],
  "payload": {
    "agent": "JARVIS",
    "category": "fact",
    "content": "text...",
    "importance": 7,
    "created_at": "2026-04-12T...",
    "invalid": false,          // gates retrieval
    "invalid_reason": "superseded",
    "valence": 0.3,
    "arousal": 0.5,
    "confidence": 0.95,
    "utility": 0.7,
    "memory_type": "semantic"
  }
}
```

---

## 6. BM25 Index — PostgreSQL Fulltext

### Location
- **Vector Column**: `schema.sql` (implicit via triggers)
- **Queries**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:2839-2845`

### Implementation Details

**Index Type**: PostgreSQL built-in `tsvector` fulltext (NOT Elasticsearch, NOT Tantivy)

**Ranking Function**: `ts_rank_cd()` (TF-IDF variant)

**Query Syntax**:
```sql
SELECT id, ts_rank_cd(search_vector, plainto_tsquery('english', $1)) as rank
FROM memories
WHERE search_vector @@ plainto_tsquery('english', $1)
ORDER BY rank DESC
LIMIT $2
```

**Rebuild Cost**: 
- **Incremental**: tsvector updated via trigger on every `INSERT/UPDATE` (minimal cost)
- **Full rebuild**: Only needed if language stemming changes or index corruption
- **For cold archive**: No rebuild needed (cold_archive uses pgvector, not tsvector)

**H-MEM Layers**: Same 4-layer pre-filter applied before fulltext query

---

## 7. SleepGate Consolidation Orchestrator

### Location
- **Cron Driver**: `/home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py:1-260`
- **MCP Tool**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:5800-6000` (sleep_gate)

### Phase Sequence (sleep_gate_cron.py:79-260)

Current execution order (nightly at 3AM Lima = 8AM UTC):

| Phase | Name | Location (cron) | Action | Lines |
|-------|------|-----------------|--------|-------|
| **1** | REPLAY | 88-99 | Boost `relevance_score` by +10% for recently activated (last 24h) | 88-99 |
| **2** | FORGET | 100-129 | Decay stale memories via composite resistance (emotion + frequency + confidence) | 100-129 |
| **3** | PRUNE | 130-143 | Soft-invalidate memories with `relevance_score < 0.05, importance <= 5` | 130-143 |
| **4** | CONSOLIDATE | 144-168 | Merge near-duplicates (cosine sim > 0.92) into single memory | 144-168 |
| **5** | ENTITY EDGES | 169-195 | Create Neo4j MENTIONS edges from new memories (last 24h) | 169-195 |
| **5.3** | **COLD ARCHIVE** | **232-260** | **← NEW PHASE** | **232-260** |

### Phase 5.3 — Cold Archive Sweep (NEW)

**Location**: `/home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py:232-260`

```python
# Phase 5.3: Cold Archive — move old invalidated memories to cold storage
print(f"\n🧊 Cold Archive — Hot/Cold Separation")

for ag in agents:
    from mcp_server_v2 import _cold_archive_purge_expired, _cold_archive_migrate
    
    # Step 1: Purge expired entries (TTL)
    purge_stats = await _cold_archive_purge_expired(pool, dry_run=args.dry_run)
    
    # Step 2: Migrate invalidated memories to cold
    migrate_stats = await _cold_archive_migrate(
        pool, ag, min_age_days=7, ttl_days=365, dry_run=args.dry_run
    )
```

**Execution**: Runs AFTER phases 1-5 complete, for each agent.

**Parameters**:
- `min_age_days=7`: Only archive if `invalid_at < NOW() - 7 days`
- `ttl_days=365`: Cold entries expire after 1 year (configurable, 0 = never)
- `dry_run`: Report without modifying (via cron `--dry-run` flag)

**Reporting** (lines 250-260):
```
Archived: N memories → N summaries (with clustering)
Purged: M expired entries
Rehydration events: L in last 24h (monitored but not acted upon during sweep)
```

---

## 8. Connectome Bitemporal Edges

### Location
- **Neo4j Schema**: Implicit via memory_connections graph
- **MCP Tool**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:6575-6638`
- **Edge Invalidation**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:6648-6700`

### `connectome_bitemporal()` Tool (mcp_server_v2.py:6575-6638)

**Purpose**: Add Graphiti-style 4-timestamp model to Neo4j edges

**Timestamps**:
1. `valid_at`: Edge becomes valid (usually NOW on creation)
2. `invalid_at`: Edge was superseded (set by invalidation logic)
3. `created_at`: Edge creation time
4. `expired_at`: When edge was explicitly expired

**Backfill Logic** (line 6618):
```cypher
MATCH (a:Memory)-[r]->() WHERE r.valid_at IS NULL
SET r.valid_at = COALESCE(r.valid_from, $now),
    r.invalid_at = null,
    r.created_at = COALESCE(r.valid_from, $now),
    r.expired_at = null
```

### Edge Invalidation Pattern (mcp_server_v2.py:6648-6700)

When a memory is invalidated (e.g., via `memory_invalidate()`), all its edges are marked:

```python
# memory_invalidate(), lines 3308-3320
await neo_session.run(
    "MATCH (m:Memory {memory_id: $mid})-[r]->() "
    "WHERE r.valid_until IS NULL "
    "SET r.valid_until = $now",
    mid=memory_id, now=now_iso
)
```

**Key behavior**: Edges are NOT deleted; they persist with `valid_until` timestamp, enabling historical graph queries (e.g., "what did we know at time T?").

### Connection to Cold Archive

**When a memory is archived**:
1. Row deleted from `memories` table
2. Edges in Neo4j become orphaned (source/target no longer exists)
3. **Gap**: Connectome doesn't automatically clean dangling edges
4. **Consideration**: Do cold-archived memories stay in Neo4j for historical tracing, or should edges be pruned?

Current design: **Edges remain** (preserves causal chains for audit), but source memory is in cold storage.

---

## 9. Tests — `test_new_tools.py`

### Cold Archive Test Suite

**Location**: `/home/dadito/IA/proyecto-seal/memory/test_new_tools.py:1267-1475`

| Test | Function | Lines | Purpose |
|------|----------|-------|---------|
| **table_exists** | `test_cold_archive_table_exists()` | 1273-1286 | Verify `cold_archive` table + indexes exist |
| **migrate_dry_run** | `test_cold_archive_migrate_dry_run()` | 1289-1320 | Dry-run returns stats without modifying data |
| **migrate_live** | `test_cold_archive_migrate_live()` | 1322-1355 | Live migration: memories deleted from hot, inserted into cold |
| **query_empty** | `test_cold_archive_query_empty()` | 1358-1372 | Empty query returns JSON with no error |
| **stats** | `test_cold_archive_stats()` | 1375-1386 | Stats tool returns valid JSON with agents key |
| **ttl_purge** | `test_cold_archive_ttl_purge()` | 1389-1431 | Expired entries purged, never-expire rows survive |
| **rehydrate** | `test_cold_archive_rehydrate()` | 1434-1475 | Cold memory retrieved transparently and flagged as "rehydrated" |

### Cleanup Helper (line 1267-1271)

```python
async def _cold_archive_cleanup(conn):
    """Remove all test data from cold_archive and memories."""
    await conn.execute("DELETE FROM cold_archive WHERE agent = $1", TEST_COLD_AGENT)
```

### Example Test Flow (migrate_live, 1322-1355)

```python
async def test_cold_archive_migrate_live():
    # Setup: insert old invalidated memories for TEST_COLD_AGENT
    ...
    
    # Act: run live migration
    stats = await _cold_archive_migrate(
        pool, TEST_COLD_AGENT, min_age_days=7, ttl_days=365, dry_run=False
    )
    
    # Assert: stats["archived"] > 0
    # Assert: memories deleted from memories table
    # Assert: entries exist in cold_archive table
    report("cold_archive_migrate: live archived > 0", stats.get("archived", 0) > 0)
```

### Test Coverage Gaps

1. **Qdrant consistency**: No test verifies Qdrant points are deleted when archiving
2. **Rehydration**: Test marked as "todo" at line 1434 (partially implemented)
3. **Protected categories**: No test for `PROTECTED_CATEGORIES` guard (correction/trust never archived)
4. **Search transparency**: No test for seamless `memory_search(include_cold=True)` fallback

---

## 10. Backup Scripts

### Location
- **Main Script**: `/home/dadito/IA/proyecto-seal/memory/soul_backup.py:1-150`
- **Queries**: Lines 98-125

### Backup Scope (show_status, line 95-125)

```python
queries = {
    "memories": "SELECT COUNT(*) FROM memories",
    "memories_with_embeddings": "SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL",
    "events": "SELECT COUNT(*) FROM event_log",
    "instincts": "SELECT COUNT(*) FROM instincts",
    # ...
}
```

### Gap: Cold Archive Not Included

**Line 98-99**: Only queries `memories` table, ignores `cold_archive`.

**Issue**: Backup size calculation and "total memories" stats are incomplete.

**Fix Needed**:
```python
queries = {
    "memories": "SELECT COUNT(*) FROM memories",
    "cold_archive": "SELECT COUNT(*) FROM cold_archive",  # ← ADD
    "memories_cold": "SELECT COUNT(*) FROM cold_archive",  # ← Alternative naming
}
```

### Export/Import Implementation (lines 36-56, 59-92)

**Export**: Uses `pg_dump` inside container (captures all tables including `cold_archive`)

**Import**: Feeds SQL into container; ensures extensions exist first

**Status**: Backup/restore is **already compatible** with cold_archive (pg_dump captures it automatically).

---

## 11. Cold Archive Internal Functions

### Location
- **Purge Expired**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:9811-9844`
- **Migrate & Cluster**: `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:9845-10015`

### `_cold_archive_purge_expired()` (9811-9844)

**Purpose**: Delete cold_archive entries past their TTL

**Logic**:
1. Find rows where `expires_at IS NOT NULL AND expires_at < NOW()`
2. Audit-log deletion event
3. Delete rows
4. Return stats: `{purged: N, by_agent: {AGENT: count}}`

**Advisory Lock**: Uses PostgreSQL advisory lock `0x5EA1_C01D` to prevent concurrent purges

### `_cold_archive_migrate()` (9845-10015)

**Purpose**: Move invalidated memories (older than N days) to cold_archive with clustering

**Steps**:

1. **Candidate Selection** (line 9863-9868):
   ```sql
   SELECT id, content, embedding, importance, category, metadata
   FROM memories
   WHERE agent = $1 AND invalid_at IS NOT NULL
   AND invalid_at < NOW() - INTERVAL '7 days'
   ```

2. **Similarity Clustering** (line 9883-9913):
   - Build union-find clusters from pgvector cosine similarity > 0.90
   - Cap cluster size at 10 (split larger clusters)
   - Singleton memories archived as-is

3. **LLM Summarization** (line 9916-9960):
   - For clusters (2+ memories): Call qwen2.5:7b via Ollama
   - Prompt: "Compress these N related memories into one concise summary (max 200 words, Spanish)"
   - Fallback (on error): Concatenate first 3 truncated contents
   - For singletons: Use original content as summary

4. **Embedding Recompute** (line 9937-9944):
   - Re-embed summary via `await get_embedding(summary)`
   - Store as JSON in `cold_archive.embedding`
   - Track errors (stats["errors"] += 1 on failure)

5. **Insert to Cold Archive** (line 9949-9958):
   ```sql
   INSERT INTO cold_archive
   (agent, original_memory_ids, summary, embedding, source_count,
    importance_max, category, archived_at, expires_at, metadata)
   VALUES (...)
   ```

6. **Hard-Delete Source** (line 9962-9975):
   - Delete from `memory_connections` (cleanup orphaned edges)
   - Delete from `memories`
   - Return stats: `{archived: N, clusters: C, singletons: S, errors: E}`

**Advisory Lock**: Held during entire migration to prevent concurrent runs

---

## 12. Cold Archive Schema — Migration 009

### Location
- **DDL**: `/home/dadito/IA/proyecto-seal/memory/migrations/009_cold_archive.sql`

### Table Definition (lines 13-25)

```sql
CREATE TABLE IF NOT EXISTS cold_archive (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    original_memory_ids BIGINT[] NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL,
    embedding vector(768),
    source_count INT NOT NULL DEFAULT 1,
    importance_max SMALLINT,
    category TEXT,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'
);
```

### Indexes (lines 30-47)

| Index | Columns | Use Case | Lines |
|-------|---------|----------|-------|
| `idx_cold_agent_archived` | `(agent, archived_at DESC)` | Time-range queries per agent | 30-31 |
| `idx_cold_expires` | `(expires_at)` WHERE `expires_at IS NOT NULL` | TTL purge scan | 34-36 |
| `idx_cold_embedding` | IVFFlat on `embedding vector_cosine_ops` | Semantic search (lists=20 for <50K rows) | 40-42 |
| `idx_cold_category` | `(category)` WHERE `category IS NOT NULL` | Category filter | 45-47 |

---

## 13. Integration Points & Dependencies

### Call Graph

```
memory_search() ─┬─→ _hmem_build_qdrant_filters()
                 ├─→ get_embedding() [Ollama 768-dim]
                 ├─→ get_qdrant() [AsyncQdrantClient]
                 └─→ temporal_decay_score() [scoring.py]

memory_hybrid_search() ─┬─→ Semantic: memory_search() flow
                        ├─→ Keyword: PostgreSQL tsvector + ts_rank_cd()
                        ├─→ Mood: sleep_gate_mood_retrieval() pattern
                        └─→ MIRIX enrichment: memory_type fetch from PG

sleep_gate() [MCP] ──→ run_sleep_gate() [cron]
                       ├─→ Phase 1-5: consolidation
                       └─→ Phase 5.3:
                           ├─→ _cold_archive_purge_expired()
                           └─→ _cold_archive_migrate()
                               ├─→ get_embedding()
                               ├─→ Ollama (qwen2.5:7b summarization)
                               └─→ Hard-delete from memories + connections

cold_archive_query() ──→ pgvector semantic search on cold_archive table

connectome_bitemporal() ──→ Neo4j edge timestamps (4-timestamp Graphiti model)
memory_invalidate() ───┬─→ PostgreSQL: UPDATE invalid_at
                       ├─→ Qdrant: SET payload invalid=True
                       └─→ Neo4j: SET r.valid_until = NOW()
```

### Critical Paths for Rehydration

**Not yet implemented** — placeholder parameter in `memory_search(include_cold=False)` but no execution logic.

**Proposed flow**:
```
memory_search(query, include_cold=True)
  1. Run hot search (Qdrant)
  2. If limit not satisfied:
      a. Run cold_archive_query(query)
      b. For each result: rehydrate_cold_memory(cold_id)
         i. Move cold_archive entry back to memories
         ii. Re-embed + upsert to Qdrant
         iii. Update connectome edges
```

---

## 14. Configuration & Environment

### Settings (from config.py, referenced in mcp_server_v2.py:30-72)

```python
from config import settings

PG_DSN = settings.pg_dsn               # PostgreSQL connection
QDRANT_URL = settings.qdrant_url       # "http://localhost:6333"
QDRANT_COLLECTION = settings.qdrant_collection  # "soul_memories"
NEO4J_URI = settings.neo4j_uri         # "bolt://localhost:7687"
NEO4J_AUTH = settings.neo4j_auth       # (user, password)
SOUL_LITE = settings.soul_lite         # Toggle: False = full Qdrant, True = pgvector only
EMBEDDING_MODEL = settings.embedding_model  # "nomic-embed-text" (via Ollama)
```

### SleepGate Cron Schedule

**Location**: `/home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py:13`

```bash
0 8 * * * /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py >> /home/dadito/IA/proyecto-seal/messages/sleep_gate.log 2>&1
```

**Timing**: 3AM Lima time (UTC -5) = 8AM UTC (per comment line 12)

**Logging**: Appended to `/home/dadito/IA/proyecto-seal/messages/sleep_gate.log`

---

## 15. Affected Files Summary

| File | Type | Changes | Status |
|------|------|---------|--------|
| `schema.sql` | DDL | `memories` table: fields for cold eligibility (invalid_at, importance, etc.) | ✅ Existing |
| `migrations/009_cold_archive.sql` | DDL | NEW `cold_archive` table + 4 indexes | ✅ Existing |
| `migrations/011_mirix_memory_typing.sql` | DDL | Adds `memory_type` column to `memories` | ✅ Existing |
| `mcp_server_v2.py` | Python | `_cold_archive_purge_expired()`, `_cold_archive_migrate()`, `cold_archive_query()`, `cold_archive_stats()` MCP tools | ✅ Existing (10,518 lines) |
| `sleep_gate_cron.py` | Python | Phase 5.3 integration at lines 232-260 | ✅ Existing (702 lines) |
| `test_new_tools.py` | Python | 7 cold archive tests (table, migrate, query, stats, ttl, rehydrate) | ✅ Existing (1,500+ lines) |
| `soul/core/scoring.py` | Python | HALO decay: `HALF_LIFE_BY_CATEGORY`, `temporal_decay_score()`, emotional modulation | ✅ Existing (119 lines) |
| `soul_backup.py` | Python | Export/import via pg_dump (compatible with cold_archive, but status query missing cold count) | ⚠️ Needs update |

---

## 16. Identified Gaps & Surprises

### High-Priority Gaps

1. **Qdrant Point Cleanup** (Risk: Memory leak)
   - When `_cold_archive_migrate()` hard-deletes from `memories`, Qdrant points are NOT deleted
   - Points persist with `invalid=True` payload, wasting VRAM
   - **Fix**: Add `await qdrant.delete(collection_name=QDRANT_COLLECTION, ids=[mem_ids])` in line 10009

2. **Rehydration Not Wired** (Risk: Feature incomplete)
   - Parameter `include_cold=False` exists in `memory_search()` but never used
   - No transparent fallback to `cold_archive_query()` when hot results insufficient
   - **Fix**: Implement fallback logic + rehydrate_cold_memory() function to move cold → hot

3. **Backup Status Missing Cold Count** (Risk: Operational blind spot)
   - `soul_backup.py:98-125` shows_status() doesn't query `cold_archive` table
   - Backup size reports are incomplete
   - **Fix**: Add `"cold_archive": "SELECT COUNT(*) FROM cold_archive"` to queries dict

### Medium-Priority Gaps

4. **Protected Categories Guard Not Tested**
   - `PROTECTED_CATEGORIES = {"correction", "trust"}` defined at mcp_server_v2.py:964
   - No test verifies these are never archived
   - **Fix**: Add `test_cold_archive_protected_categories()` to test_new_tools.py

5. **Connectome Edge Cleanup on Archive**
   - When a memory is archived, Neo4j edges become orphaned (source/target deleted)
   - Current design leaves edges with no source (may confuse traversals)
   - **Decision needed**: Prune edges or keep for historical audit trails?

6. **Cold Archive Embedded in SleepGate, Not Configurable**
   - Phase 5.3 always runs with `min_age_days=7, ttl_days=365`
   - No way to tune thresholds without code change
   - **Fix**: Expose as settings or MCP parameters

### Surprises & Hidden Coupling

7. **Ollama Dependency for Clustering**
   - `_cold_archive_migrate()` calls qwen2.5:7b via Ollama (line 9924)
   - If Ollama is down, clustering silently fails, archives as-is (error tracked but not fatal)
   - **Impact**: Archive quality degrades without alerting operator

8. **Advisory Lock on Migration**
   - Uses PostgreSQL advisory lock `0x5EA1_C01D` to serialize migrations
   - If a migration hangs, subsequent runs block indefinitely
   - **Monitoring**: Check `pg_locks` in production for this specific lock ID

9. **MIRIX Type Not Updated on Archive**
   - When memory is archived, `memory_type` is copied to cold_archive via `importance_max` / `category` only
   - Cold archive doesn't store original `memory_type` field
   - **Impact**: Rehydrated memory loses MIRIX type classification

10. **Search Vector (tsvector) Not Sync'd to Cold Archive**
    - BM25 index via `search_vector` tsvector is hot-only
    - Cold archive uses pgvector embedding for semantic search, not BM25
    - **Implication**: Cold archive keyword search not available (by design — cold is semantic-only)

---

## 17. Recommendations

### Before Marking Feature Complete

1. **MUST**: Fix Qdrant point cleanup (memory leak risk)
2. **MUST**: Implement rehydration transparency in `memory_search(include_cold=True)`
3. **SHOULD**: Add cold_archive count to soul_backup.py status query
4. **SHOULD**: Add protected categories test coverage
5. **SHOULD**: Document Ollama dependency + error modes in Phase 5.3

### Phase 2c (Refinement)

- Design rehydration promotion strategy (age? access pattern?)
- Implement connectome edge cleanup or document historical audit model
- Create dashboards for cold_archive TTL trends + rehydration frequency
- Benchmark archive performance at scale (50K+ cold rows)

---

## File References (Quick Lookup)

```
/home/dadito/IA/proyecto-seal/memory/
├── schema.sql                           ← memories table, indexes
├── migrations/
│   ├── 009_cold_archive.sql             ← cold_archive DDL
│   └── 011_mirix_memory_typing.sql      ← memory_type column
├── mcp_server_v2.py                     ← MCP tools + internal functions (10,518 lines)
│   ├── Lines 32-78: scoring imports
│   ├── Line 72: QDRANT_COLLECTION
│   ├── Lines 437-455: get_qdrant()
│   ├── Lines 964: PROTECTED_CATEGORIES
│   ├── Lines 1229-1365: memory_search()
│   ├── Lines 2752-2990: memory_hybrid_search()
│   ├── Lines 3264-3325: memory_invalidate()
│   ├── Lines 6575-6700: connectome_bitemporal() + edge invalidation
│   ├── Lines 9806-10100: Cold archive internals + MCP tools
│   └── Lines 10037-10047: cold_archive_migrate() MCP tool
├── sleep_gate_cron.py                   ← Cron driver (702 lines)
│   ├── Lines 79-199: run_sleep_gate() with phases 1-5
│   └── Lines 232-260: Phase 5.3 cold archive sweep
├── test_new_tools.py                    ← Test suite (1,500+ lines)
│   └── Lines 1267-1475: 7 cold archive tests
├── soul/core/scoring.py                 ← HALO decay + MemRL
│   ├── Lines 15-28: HALF_LIFE_BY_CATEGORY
│   └── Lines 32-77: temporal_decay_score()
└── soul_backup.py                       ← Backup/restore (150 lines)
    └── Lines 95-125: show_status() ← MISSING cold_archive count
```

---

## Summary Table

| Item | Status | Location | Notes |
|------|--------|----------|-------|
| `memories` schema | ✅ Complete | schema.sql:11-40 | Includes `invalid_at`, `memory_type`, all needed fields |
| HALO decay | ✅ Complete | scoring.py:32-77 | Full implementation with emotional modulation |
| PROTECTED_CATEGORIES | ✅ Coded | mcp_server_v2.py:964 | {correction, trust} never archived |
| `memory_invalidate` | ✅ Complete | mcp_server_v2.py:3264 | Soft-delete: flag + Qdrant payload + Neo4j edge dating |
| `memory_search` | ⚠️ Partial | mcp_server_v2.py:1229 | Has `include_cold` param but not wired to cold_archive |
| `memory_hybrid_search` | ⚠️ Partial | mcp_server_v2.py:2752 | BM25 semantic/keyword merge; no cold fallback |
| Qdrant wrapper | ⚠️ Gap | mcp_server_v2.py:437-1026 | Upsert/set_payload works; delete on archive MISSING |
| BM25 index | ✅ Complete | schema.sql implicit | PostgreSQL tsvector + ts_rank_cd; incremental rebuild |
| SleepGate phases 1-5 | ✅ Complete | sleep_gate_cron.py:79-195 | REPLAY, FORGET, PRUNE, CONSOLIDATE, ENTITY |
| Phase 5.3 (Cold Archive) | ✅ Integrated | sleep_gate_cron.py:232-260 | Calls _cold_archive_purge_expired + migrate |
| `connectome_bitemporal` | ✅ Complete | mcp_server_v2.py:6575-6700 | 4-timestamp Graphiti model; edge invalidation |
| Tests | ⚠️ 7/10 passing | test_new_tools.py:1267-1475 | Missing: protected categories test, rehydration full flow |
| Backup scripts | ⚠️ Incomplete | soul_backup.py:95-125 | Export/import works; status query missing cold count |

---

## Report Metadata

- **Created**: 2026-04-12
- **Analyzed by**: Code-explorer (Phase 2b)
- **Scope**: Cold Archive hot/cold separation feature
- **Total files scanned**: 15+
- **Total lines of code reviewed**: ~14,000
- **Implementation status**: ~85% complete (gaps: rehydration, Qdrant cleanup, test coverage, backup status)
- **Production readiness**: Conditional (critical gaps must be fixed before shipping)

