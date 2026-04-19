# Cold Archive — Hot/Cold Memory Separation for SOUL

## Description

### What
Add a Cold Archive layer that physically separates invalidated/decayed memories from the active `memories` table into a dedicated `cold_archive` table. The archive stores compressed summaries (clusters of similar invalidated memories), supports semantic search, and enforces a configurable TTL for permanent deletion.

### Why
1. **Query performance** — Every `memory_search` call filters `invalid_at IS NULL`. As invalidated rows accumulate, index bloat and scan cost grow. Moving cold data out keeps the hot table lean.
2. **Storage hygiene** — No expiration policy exists today. Invalidated memories stay forever. Cold Archive adds a two-stage lifecycle: invalidate -> archive -> purge.
3. **Searchable history** — Archived summaries remain queryable on demand (`include_archived=true`), so old context is not lost, just moved out of the hot path.

### Relationship to existing code
- `memory_lifecycle.py` already has a `memories_archive` table (raw row copy, no compression, no embeddings, no TTL, no MCP tools). Cold Archive replaces and supersedes it with a production-grade design that adds clustering, summary generation, embedded search, and TTL purge.
- SleepGate (`sleep_gate_cron.py`) currently has Phases 1-8. Cold Archive adds a new phase after PRUNE (Phase 3) and before CONSOLIDATE (Phase 4), or as a post-consolidation step — implementation decides placement.

**Paper Reference:** Graphiti/Zep (arxiv 2501.13956) — episodic-to-semantic memory consolidation with bitemporal invalidation.

## Context

### Current State
- SleepGate Phase 3 (PRUNE) soft-invalidates memories with `relevance_score < 0.05` and `importance <= 5`
- Invalidated memories (`invalid_at IS NOT NULL`) stay in the `memories` table indefinitely
- `memory_lifecycle.py` has a basic `memories_archive` table (raw copy, no embedding, no summary clustering, no TTL, no MCP exposure)
- Every query filters `invalid_at IS NULL`, adding overhead as invalidated rows grow
- No batch compression of semantically similar old memories into summaries
- No TTL or expiration policy for permanently removing stale data

### Target Files
- `schema.sql` or new migration `migrations/009_cold_archive.sql` — new table + indexes
- `mcp_server_v2.py` — new MCP tools (3 tools)
- `sleep_gate_cron.py` — new archive phase
- `test_new_tools.py` — tests for new tools
- `memory_lifecycle.py` — deprecate or redirect `memories_archive` usage to new `cold_archive`

## Acceptance Criteria

### Schema
- [ ] `cold_archive` table created with columns: `id BIGSERIAL PK`, `agent TEXT`, `original_memory_ids BIGINT[]`, `summary TEXT`, `embedding vector(768)`, `source_count INT`, `importance_max SMALLINT`, `category TEXT`, `archived_at TIMESTAMPTZ DEFAULT NOW()`, `expires_at TIMESTAMPTZ`, `metadata JSONB`
- [ ] Index on `(agent, archived_at)` for time-range queries
- [ ] HNSW or IVFFlat index on `embedding` for semantic search

### Migration Logic
- [ ] Given invalidated memories older than N days (default: 7, configurable), when SleepGate runs, then those memories are clustered by cosine similarity > 0.90, summarized, and inserted into `cold_archive`
- [ ] Given a cluster of K invalidated memories with similarity > 0.90, when archiving, then a single `cold_archive` row is created with: LLM-generated or concatenated summary, embedding of the summary, `original_memory_ids` listing all source IDs, `importance_max` = max importance across cluster, `source_count` = K
- [ ] Given invalidated memories that do not cluster with any others (singletons), when archiving, then each is archived individually (summary = original content, source_count = 1)
- [ ] After archival, source rows are deleted from `memories` (hard delete — the cold_archive row preserves the content)

### MCP Tools
- [ ] `cold_archive_migrate(agent, min_age_days=7, dry_run=false)` — manually triggers archive migration; returns count of memories archived and clusters formed; `dry_run=true` reports what would happen without writing
- [ ] `cold_archive_query(agent, query, limit=10)` — semantic search on `cold_archive.embedding`; returns summary, source_count, archived_at, original_memory_ids
- [ ] `cold_archive_stats(agent=None)` — returns per-agent counts, total rows, oldest/newest `archived_at`, estimated storage bytes

### Transparent Search
- [ ] Given `memory_search` is called with `include_archived=true`, when results are returned, then cold_archive results are included with a `source: "cold_archive"` marker, merged and ranked alongside hot results

### TTL Purge
- [ ] `expires_at` defaults to `archived_at + 365 days` (configurable)
- [ ] Given a cold_archive row with `expires_at < NOW()`, when SleepGate runs, then the row is permanently deleted
- [ ] Purge runs as part of the SleepGate phase (same phase as migration, purge first then archive)

### Regression Safety
- [ ] All existing 93+ tests in `test_new_tools.py` still pass
- [ ] New tests cover: migration (happy path), clustering logic, singleton archival, dry_run mode, cold_archive_query, cold_archive_stats, TTL purge, transparent search via include_archived, empty-table edge case

## Scope

### In-Scope
- `cold_archive` table schema and migration
- Archive migration logic (cluster + summarize + insert + hard-delete source)
- 3 new MCP tools (`cold_archive_migrate`, `cold_archive_query`, `cold_archive_stats`)
- `include_archived` parameter on `memory_search`
- SleepGate integration (new phase)
- TTL-based purge
- Deprecation path for `memories_archive` in `memory_lifecycle.py`

### Out-of-Scope
- Modifying SleepGate Phases 1-4 behavior (REPLAY, FORGET, PRUNE, CONSOLIDATE)
- Changing the invalidation criteria in Phase 3 (PRUNE)
- Migrating existing `memories_archive` data into the new `cold_archive` table (separate task)
- UI/dashboard for archive browsing
- Cross-agent archive merging
- Compression of the summary text itself (e.g., zlib)

## Error Scenarios

| Scenario | Expected Behavior |
|---|---|
| Ollama embedding service unavailable during archival | Skip archival for this run; log warning; do not delete source memories; return error count in stats |
| No invalidated memories older than N days | Phase completes successfully with `archived: 0`; no error |
| `cold_archive_migrate` called while SleepGate is already running archive phase | Return error: "Archive migration already in progress" (use advisory lock or flag) |
| `cold_archive_query` on empty archive table | Return empty list, not an error |
| `memory_search(include_archived=true)` but cold_archive table does not exist yet | Gracefully skip archive search; return only hot results; log warning once |
| Hard-delete of source memories fails mid-batch (partial) | Transaction rollback — neither archive insert nor source delete should commit partially; use single transaction per cluster |
| Summary generation fails for a cluster (LLM timeout or error) | Fall back to concatenation of first 3 source contents truncated to 500 chars; log warning |
| `expires_at` is NULL on a cold_archive row | Row never expires (opt-out of TTL); purge query must filter `expires_at IS NOT NULL AND expires_at < NOW()` |

## Implementation Steps

### Step 1: Schema Migration — `cold_archive` table + indexes
**Goal:** Create the `cold_archive` table in PostgreSQL with pgvector embedding column and all required indexes.

**Output:** New migration file `migrations/009_cold_archive.sql` and updated `schema.sql`.

**Details:**
- Table `cold_archive` per acceptance criteria: `id BIGSERIAL PK`, `agent TEXT NOT NULL`, `original_memory_ids BIGINT[]`, `summary TEXT NOT NULL`, `embedding vector(768)`, `source_count INT NOT NULL DEFAULT 1`, `importance_max SMALLINT`, `category TEXT`, `archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `expires_at TIMESTAMPTZ` (default via application logic: `archived_at + 365 days`, allow NULL for no-expiry opt-out), `metadata JSONB NOT NULL DEFAULT '{}'`
- Indexes: composite `idx_cold_agent_archived` on `(agent, archived_at DESC)`, `idx_cold_expires` on `(expires_at) WHERE expires_at IS NOT NULL` for TTL scans, `idx_cold_embedding` USING ivfflat `(embedding vector_cosine_ops) WITH (lists = 20)`, `idx_cold_category` on `(category)`
- No FK to `memories` since source rows are hard-deleted after archival

**Success criteria:**
- `CREATE TABLE cold_archive` executes without error on existing DB (port 5433)
- `\d cold_archive` shows all expected columns, types, and constraints
- All indexes created; `\di` lists them
- Migration is idempotent (`IF NOT EXISTS`)

**Size:** S
**Dependencies:** None
**Risk:** Low — additive DDL only, no existing tables modified

---

### Step 2: Archive Migration Logic — cluster, summarize, insert, hard-delete
**Goal:** Implement `_cold_archive_migrate(pool, agent, min_age_days=7, ttl_days=365, dry_run=False)` async function that moves invalidated memories older than N days from `memories` to `cold_archive` with clustering.

**Output:** New internal async function in `mcp_server_v2.py` (placed near other internal helpers, around the sleep_gate/consolidation section).

**Details:**
- **Select candidates:** `SELECT id, content, embedding, importance, category, metadata FROM memories WHERE agent = $1 AND invalid_at IS NOT NULL AND invalid_at < NOW() - INTERVAL '1 day' * $2`
- **Cluster:** For candidates with embeddings, compute pairwise cosine similarity via pgvector (`1 - (a.embedding <=> b.embedding) > 0.90`). Use greedy union-find to form clusters (cap cluster size at 10 to bound LLM call length).
- **Singletons:** Memories that do not cluster with any other are archived individually (`summary = content`, `source_count = 1`).
- **Summary generation:** For clusters of 2+, call Ollama `qwen2.5:7b` with a compression prompt (reuse pattern from `sleep_gate_cron.py` Phase 5.5 distillation). On LLM failure, fall back to concatenation of first 3 contents truncated to 500 chars.
- **Embedding:** Generate embedding for each summary via `get_embedding(summary_text)`.
- **Insert + Delete in transaction:** Single transaction per batch — INSERT all cold entries, then DELETE source rows from `memories` + clean orphaned `memory_connections`. Rollback on any failure.
- **Advisory lock:** Use `pg_advisory_xact_lock(hash)` to prevent concurrent migration (SleepGate vs manual MCP trigger).
- **Return:** `{"archived": N, "clusters": C, "singletons": S, "deleted_connections": D, "errors": E}`

**Success criteria:**
- Invalidated memories older than `min_age_days` are moved to `cold_archive`
- Clusters of similar memories produce a single summary row with populated `original_memory_ids` and `source_count`
- Source rows are deleted from `memories` after successful insert
- Orphaned `memory_connections` (source_id or target_id referencing deleted memories) are cleaned up
- Transaction rollback on failure: no partial state
- `dry_run=True` returns stats without writing anything
- Idempotent: running twice does not duplicate (candidates must still exist in `memories`)

**Size:** L
**Dependencies:** Step 1
**Risk:** Medium — hard-deletes from `memories` table. Transaction wrapping and advisory lock are critical. LLM dependency (Ollama) adds runtime failure mode.

---

### Step 3: TTL Auto-Purge — delete expired cold entries
**Goal:** Implement `_cold_archive_purge_expired(pool, dry_run=False)` that permanently deletes cold entries past their `expires_at`.

**Output:** New internal async function in `mcp_server_v2.py`.

**Details:**
- Query: `DELETE FROM cold_archive WHERE expires_at IS NOT NULL AND expires_at < NOW() RETURNING id, agent, source_count`
- Before deletion, log purged entry IDs to `event_log` with `event_type = 'status'`, `content = 'cold_archive_purge'`, `metadata = {"purged_ids": [...]}` for audit trail
- `dry_run=True`: SELECT count only, do not delete
- Return: `{"purged": N, "by_agent": {"JARVIS": X, "ADA": Y}}`

**Success criteria:**
- Entries with `expires_at < NOW()` are deleted
- Entries with `expires_at IS NULL` are untouched (no-expiry opt-out)
- Entries with future `expires_at` are untouched
- Purge is logged to `event_log` before deletion

**Size:** S
**Dependencies:** Step 1
**Risk:** Low — straightforward DELETE with WHERE clause. Audit log provides safety net.

---

### Step 4: MCP Tools — `cold_archive_migrate`, `cold_archive_query`, `cold_archive_stats`
**Goal:** Expose three new MCP tools following existing `@mcp.tool()` patterns in `mcp_server_v2.py`.

**Output:** Three new decorated functions in `mcp_server_v2.py`.

**Details:**

**4a. `cold_archive_migrate(agent: str, min_age_days: int = 7, ttl_days: int = 365, dry_run: bool = False) -> str`**
- Calls `_cold_archive_purge_expired()` first (purge before archive, per acceptance criteria)
- Then calls `_cold_archive_migrate()` for the specified agent
- Returns JSON with combined stats from both operations
- Docstring with Args section per existing tool pattern

**4b. `cold_archive_query(query: str, agent: Optional[str] = None, category: Optional[str] = None, limit: int = 10) -> str`**
- Semantic search directly on `cold_archive` table using pgvector (NOT Qdrant — cold data stays out of hot vector index)
- `get_embedding(query)` -> `ORDER BY embedding <=> $vec LIMIT $limit`
- Filter by agent and/or category if provided
- Returns JSON array: `[{id, agent, summary, category, source_count, original_memory_ids, importance_max, archived_at, similarity}]`
- Empty table returns `"No archived memories found."`, not an error

**4c. `cold_archive_stats(agent: Optional[str] = None) -> str`**
- Single query with GROUP BY agent: `count(*)`, `count(*) FILTER (WHERE source_count > 1)` as compressed, `min(archived_at)`, `max(archived_at)`, `count(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at < NOW() + INTERVAL '30 days')` as expiring_soon
- If agent specified, filter to that agent
- Returns JSON with per-agent breakdown + totals

**Pattern compliance:** `@mcp.tool()` decorator, `Optional` type hints from typing, docstring with Args, `pool = await get_pool()`, return `json.dumps(...)`, error handling with try/except returning error string.

**Success criteria:**
- All three tools registered and visible in MCP tool listing
- `cold_archive_query("medical topic")` returns relevant results sorted by similarity
- `cold_archive_stats()` returns valid JSON with all specified fields
- `cold_archive_migrate(agent="JARVIS", dry_run=True)` returns preview without side effects
- Empty archive table does not raise exceptions

**Size:** M
**Dependencies:** Steps 2, 3
**Risk:** Low — additive tools, no modification to existing tools

---

### Step 5: SleepGate Phase Integration — nightly cold archive cycle
**Goal:** Add Cold Archive as a new phase in `sleep_gate_cron.py` that runs migration, compression, and TTL purge during nightly consolidation.

**Output:** Modified `sleep_gate_cron.py` with new phase block.

**Details:**
- Place after existing Phase 5 (entity edges) and before Phase 5.5 (auto-distillation). Label as `Phase 5.3: Cold Archive`
- Duplicate the migration + compression + purge logic locally in `sleep_gate_cron.py` (avoid circular imports with `mcp_server_v2.py` — the cron runs standalone). Alternatively, extract shared logic into a `cold_archive.py` module imported by both.
- Decision: prefer extraction into `cold_archive.py` to avoid code duplication. This module exports `migrate()`, `compress()`, `purge_expired()`.
- For each agent: run purge -> migrate -> stats summary
- Print output in existing format: section header with emoji, per-agent stats
- Respect `--dry-run` flag
- Add `archive_migrated`, `archive_purged` keys to stats dict

**Success criteria:**
- `python3 sleep_gate_cron.py --dry-run` shows Cold Archive phase in output
- `python3 sleep_gate_cron.py --dry-run --agent JARVIS` runs without error
- Live run migrates qualifying memories and purges expired entries
- No regression in existing phases (verify all phase outputs still print)

**Size:** M
**Dependencies:** Steps 2, 3 (or the extracted `cold_archive.py` module)
**Risk:** Low — additive phase appended to existing flow. Existing phases are untouched.

---

### Step 6: Transparent Query — `include_archived` parameter in `memory_search`
**Goal:** Extend existing `memory_search` MCP tool to optionally include cold archive results alongside active results.

**Output:** Modified `memory_search()` function in `mcp_server_v2.py`.

**Details:**
- Add parameter: `include_archived: bool = False`
- When `False` (default): zero code path change. Early return before any cold logic.
- When `True`:
  - After the existing Qdrant search completes, run a pgvector query on `cold_archive`: `SELECT id, agent, summary, category, importance_max, archived_at, 1 - (embedding <=> $1) as similarity FROM cold_archive WHERE ... ORDER BY embedding <=> $1 LIMIT $2`
  - Apply agent/category filters matching the hot query
  - Mark cold results with `"source": "cold_archive"` and hot results with `"source": "active"`
  - Apply a cold penalty multiplier (0.7x on decayed_score) since archived memories are inherently less relevant
  - Merge both result lists, re-sort by decayed_score descending, cap at `limit`
- Graceful degradation: if `cold_archive` table does not exist (not yet migrated), catch the `UndefinedTableError` and skip with a log warning. Return only hot results.

**Success criteria:**
- `memory_search("topic", include_archived=True)` returns results from both stores
- Cold results have `"source": "cold_archive"` field
- Default behavior (`include_archived=False`) is byte-identical to current output — zero overhead, no extra queries
- Graceful fallback if table missing
- Performance: cold pgvector query adds <200ms (single indexed scan)

**Size:** M
**Dependencies:** Step 1 (table must exist), Step 4b (validates query pattern)
**Risk:** Medium — modifying `memory_search`, a core high-traffic tool. Default-off flag and early return ensure zero regression for existing callers. Test carefully.

---

### Step 7: Deprecate `memories_archive` in `memory_lifecycle.py`
**Goal:** Mark the existing `memories_archive` table and related code in `memory_lifecycle.py` as deprecated in favor of `cold_archive`.

**Output:** Modified `memory_lifecycle.py` with deprecation warnings.

**Details:**
- Add deprecation comment and `warnings.warn()` to any function that writes to `memories_archive`
- Redirect new archival calls to `cold_archive` logic if available
- Do NOT delete the table or drop existing data (out of scope — separate migration task)
- Update any references in `mcp_server_v2.py` if they call `memory_lifecycle` archival functions

**Success criteria:**
- No new data is written to `memories_archive` when `cold_archive` table exists
- Deprecation warning is logged on any attempt to use old archival path
- Existing `memories_archive` data is untouched

**Size:** S
**Dependencies:** Steps 1, 2
**Risk:** Low — additive deprecation warnings, no data changes

---

### Step 8: Tests — full coverage for cold archive feature
**Goal:** Add comprehensive tests to `test_new_tools.py` covering all cold archive functionality.

**Output:** New test functions appended to `test_new_tools.py`, following existing `report(name, passed, detail)` pattern.

**Details:**
- `test_cold_archive_table_exists()` — verify table and indexes exist via `information_schema`
- `test_cold_archive_migrate_dry_run()` — insert a test memory, invalidate it 8 days ago, run migrate with `dry_run=True`, verify row still in `memories`
- `test_cold_archive_migrate_live()` — same setup, run migrate live, verify row moved to `cold_archive` and deleted from `memories`
- `test_cold_archive_clustering()` — insert two test memories with near-identical content (cosine > 0.90), invalidate both 8 days ago, run migrate, verify single cold entry with `source_count = 2` and both IDs in `original_memory_ids`
- `test_cold_archive_singleton()` — insert one unique invalidated memory, run migrate, verify archived with `source_count = 1`
- `test_cold_archive_query_semantic()` — insert a cold entry with known summary, query by semantically similar text, verify returned with similarity score
- `test_cold_archive_query_empty()` — query on empty table, verify returns empty list (not error)
- `test_cold_archive_stats()` — verify returns valid JSON with per-agent counts and date fields
- `test_cold_archive_ttl_purge()` — insert cold entry with `expires_at` in the past, run purge, verify deleted
- `test_cold_archive_ttl_null_survives()` — insert cold entry with `expires_at = NULL`, run purge, verify it survives
- `test_memory_search_include_archived()` — insert both an active memory and a cold entry, search with `include_archived=True`, verify both appear with correct `source` markers
- `test_memory_search_default_excludes_archived()` — same setup, search without flag, verify only active memory returned
- All tests use `agent = 'TEST_COLD'` and clean up with `DELETE FROM cold_archive WHERE agent = 'TEST_COLD'` + `DELETE FROM memories WHERE agent = 'TEST_COLD'` in teardown
- Follow existing async pattern: `asyncpg.create_pool()`, `async with pool.acquire() as conn`

**Success criteria:**
- All new tests pass (12+ tests)
- All existing 93+ tests still pass
- No residual test data left in DB after run
- Tests run in <30 seconds total

**Size:** M
**Dependencies:** Steps 1-7 (all — tests validate the full feature)
**Risk:** Low — tests are additive and use isolated agent name to avoid conflicts with production data
