---
title: Cold Archive — Hot/Cold Memory Separation for SOUL
type: feature
status: draft
created: 2026-04-11
designer: JARVIS
depends_on:
  - MIRIX memory typing (done)
  - HALO decay (done)
  - PROTECTED_CATEGORIES guard (done)
paper: Graphiti/Zep — arxiv 2501.13956
---

# Cold Archive — Hot/Cold Memory Separation for SOUL

## Problem

SOUL's `memories` table grows unbounded. Every memory — active, decayed, invalidated, obsolete — lives in the same hot table that powers `memory_search`, `hybrid_search`, connectome traversal, and every retrieval path.

Consequences observed:
1. **Search latency creeps** as the table grows (Qdrant vector count, pgvector scans, BM25 index).
2. **Invalidated memories still consume RAM/VRAM** via embeddings that will never be retrieved.
3. **Decayed memories (HALO score ≈ 0) dilute ranking** — they're still indexed but never useful.
4. **No forgetting** — SOUL never truly lets go of anything. This contradicts the Graphiti/Zep bitemporal model where obsolete facts are archived, not deleted.
5. **Backup size grows linearly** with agent age; hot backups become expensive.

## Architecture Overview

**Status: ~85% already built — this refinement closes 3 real gaps + 1 consistency fix. NOT greenfield.**

Phase 2b analysis confirmed that `migrations/009_cold_archive.sql`, `_cold_archive_migrate()` in `mcp_server_v2.py:~10009`, and the SleepGate Phase 5.3 sweep already exist and have 7 passing tests in `test_new_tools.py`. The real work is plumbing, not table design.

### Component Map — Current State

| Component | Location | State |
|---|---|---|
| Cold table schema | `migrations/009_cold_archive.sql` | ✅ exists — `cold_archive(id, agent, original_memory_ids[], summary, embedding vector(768), source_count, importance_max, category, archived_at, expires_at, metadata)` |
| Cold sweep implementation | `mcp_server_v2.py:~10009` `_cold_archive_migrate()` | ✅ exists but **leaks Qdrant points** (Gap 1) |
| SleepGate hook | `sleep_gate_cron.py:~Phase 5.3` | ✅ calls sweep nightly; **depends silently on Ollama** (Gap 3) |
| `include_cold` parameter | `mcp_server_v2.py:1229` (`memory_search`), `:2752` (`memory_hybrid_search`) | ⚠️ parameter declared, **never read** (Gap 2) |
| HALO decay | `soul/core/scoring.py:32` `temporal_decay_score()` vs `scoring_v3.py` | ⚠️ **two modules** — authoritative TBD before wiring sweep |
| PROTECTED (MCP) | `mcp_server_v2.py:964` `{correction, trust}` | ⚠️ **inconsistent** with lifecycle |
| PROTECTED (lifecycle) | `memory_lifecycle.py:53` `{identity, core_belief}` | ⚠️ **inconsistent** with MCP |
| `memory_invalidate` | `mcp_server_v2.py:3264` | ✅ sets `invalid_at`, deletes Qdrant point |
| Bitemporal edges | `mcp_server_v2.py:6575` `connectome_bitemporal` | ✅ supports `valid_from/valid_to` |
| Existing Qdrant cleanup code | `memory_lifecycle.py:436-450` `cleanup_qdrant_vectors()` | ✅ reusable as reference |
| Deprecated `memories_archive` | `memory_lifecycle.py:66`, warnings at `:196-199`, `:391-394` | ❌ kill after 14 days of cold live |
| Cold archive tests | `test_new_tools.py` (7 tests) | ✅ covers schema + sweep basics, **no rehydration test** |

### Gap 1 — Qdrant Point Cleanup on Archive

**Bug:** `_cold_archive_migrate()` hard-deletes from `memories` but never calls `qdrant.delete(points=[ids])`. Result: orphan vectors in `seal_memories` collection accumulate with payload `invalid=True`, consuming VRAM indefinitely and polluting semantic search (tombstoned at query time only by payload filter, not removed).

**Decision:** **Delete, not filter-on-retrieval.** Filter-on-retrieval (add `must_not: invalid=true`) was considered but rejected — it keeps the VRAM leak and requires every search path to remember the filter.

**Design:**
1. In `_cold_archive_migrate()`, after the `INSERT INTO cold_archive ... RETURNING id` and before the `DELETE FROM memories WHERE id = ANY(...)`, collect the affected memory IDs.
2. Call `await qdrant.delete(collection_name="seal_memories", points_selector=PointIdsList(points=ids))` in a `try/except` — log failures to `event_log` but do NOT block the sweep (Postgres remains the source of truth).
3. On exception, append a row to `cold_archive_pending_qdrant_delete` (new small table: `memory_id, reason, retry_count, first_seen`) — a reaper inside SleepGate Phase 5.4 retries. This keeps archive atomic from the hot-store perspective.
4. Persist the embedding in `cold_archive.embedding` (already present in schema — confirm column populated; if NULL in current rows, backfill via one-shot script using `mcp_server_v2.py:get_embedding()`).

**Where the call goes:** inside the same Postgres transaction block in `_cold_archive_migrate()`, Qdrant delete fires **after** `INSERT` commit but **before** Postgres `DELETE`. If Qdrant fails, Postgres hot row survives → sweep retries it next night. If Postgres DELETE fails after Qdrant delete, the pending-retry table catches the orphan.

### Gap 2 — Transparent Cold-Tier Fallback in `memory_search` / `memory_hybrid_search`

**Bug:** The `include_cold: bool = False` kwarg is declared in `memory_search` (`:1229`) and `memory_hybrid_search` (`:2752`) but the body never branches on it — cold rows are invisible regardless of the flag.

**Design:** Two-tier waterfall with rehydration on hit.

```
                      memory_search(query, k=10, include_cold=False)
                                        │
                                        ▼
                     ┌──────────────────────────────────────┐
                     │  hot tier: hybrid (Qdrant + tsvector)│
                     │         returns hot_results          │
                     └──────────────────┬───────────────────┘
                                        │
                         include_cold == False ?
                                        │
                      ┌─────────────────┴────────────────┐
                      │ YES                              │ NO
                      ▼                                  ▼
             return hot_results              len(hot_results) >= k ?
                                                         │
                                         ┌───────────────┴───────────────┐
                                         │ YES                           │ NO
                                         ▼                               ▼
                                return hot_results           ┌──────────────────────┐
                                                              │ cold tier: pgvector  │
                                                              │   IVFFlat on         │
                                                              │ cold_archive.embedding│
                                                              │   (idx_cold_embedding)│
                                                              └──────────┬───────────┘
                                                                         │
                                                                         ▼
                                                         cold_results (k - len(hot))
                                                                         │
                                                                         ▼
                                                  ┌──────────────────────────────┐
                                                  │ for each cold_hit in top-k:  │
                                                  │   rehydrate(cold_hit)        │
                                                  │     → INSERT INTO memories   │
                                                  │     → upsert Qdrant          │
                                                  │     → DELETE FROM cold_archive│
                                                  │     → event_log: rehydration │
                                                  └──────────────┬───────────────┘
                                                                 │
                                                                 ▼
                                                  merge(hot_results, rehydrated)
                                                       ranked by score
                                                             │
                                                             ▼
                                                       return top-k
```

**Rehydration policy:**
- Trigger: cold row lands in final top-k (not merely in cold candidate pool).
- Copy row back to `memories` using stored `embedding` — **no re-embedding cost** (40 ms saved per hit on RTX 5090).
- Upsert Qdrant point with same vector.
- Delete from `cold_archive`; log `event_type='cold_rehydrate'` to `event_log`.
- Rehydrated row gets `valid_from = NOW()`, preserves original `category`, `importance`. Original `invalid_at` cleared.

**Threshold:** `k - len(hot_results) > 0` triggers cold lookup. Additionally, if `hot_results` best score < `COLD_FALLBACK_SCORE_FLOOR` (default 0.55), trigger cold even at full k — low-confidence hot result may be beaten by an archived memory.

### Gap 3 — Ollama Failure Escalation

**Bug:** SleepGate Phase 5.3 clustering (`cold_archive_migrate` grouping step) calls Ollama `qwen2.5:7b` for cluster summaries. When Ollama is down, the call raises, the exception is caught and logged, and the sweep degrades to **non-clustered 1:1 rows silently**. William never finds out that summarization stopped working.

**Design — Circuit Breaker + DUM alert:**

```python
# new: soul/core/circuit_breaker.py
class OllamaCircuitBreaker:
    FAILURE_THRESHOLD = 3       # trips after 3 consecutive failures
    OPEN_DURATION_SEC = 900     # 15min before half-open retry
    # states: CLOSED → OPEN → HALF_OPEN → CLOSED
```

Integration points:
1. Wrap Ollama call in `_cold_archive_migrate()` with breaker. On trip:
   - Append `event_log_append(event_type='ollama_circuit_open', severity='warning', details={...})`.
   - Write to `~/IA/proyecto-seal/messages/.urgent_trigger` → DUM heartbeat picks it up and messages William.
   - Sweep continues in **degraded mode** (1:1 row copy, no clustering) but sets `metadata->>'degraded'='ollama_down'` on each cold row — allows later re-clustering.
2. Half-open state: next sweep tries one test call; success → closed; failure → re-open 15min.
3. Breaker state persisted in `working_state` keyed by `ollama_breaker_state` so it survives restarts.

### Consistency Fix — Single `PROTECTED_CATEGORIES` Source

`mcp_server_v2.py:964` = `{"correction", "trust"}`, `memory_lifecycle.py:53` = `{"identity", "core_belief"}`. Two sets, zero overlap — whichever path you hit, the other's protected categories are unprotected.

**Design:** Lift to `soul/core/protected.py`:
```python
# soul/core/protected.py
PROTECTED_CATEGORIES: frozenset[str] = frozenset({
    "correction",     # William's explicit corrections (MCP origin)
    "trust",          # trust calibration events (MCP origin)
    "identity",       # identity anchors (lifecycle origin)
    "core_belief",    # OCEAN-binding beliefs (lifecycle origin)
})
PROTECTED_MIN_IMPORTANCE: int = 9
```

Both call sites import from here. Unit test asserts the union is loaded by both modules. This is pre-req for the cold sweep — without it, sweep protection is inconsistent depending on code path.

### Embedding Persistence in Cold Row

Schema check: `migrations/009_cold_archive.sql` **already** has `embedding vector(768)` and `idx_cold_embedding` (IVFFlat). No `ALTER` required.

Gap: current `_cold_archive_migrate()` may not populate it (verify at impl time). If unpopulated for existing rows, one-shot backfill script reads original memory via `original_memory_ids[]`, re-embeds, writes. Going forward, sweep **copies** the hot row's embedding directly — zero compute.

Rehydration path uses `cold_archive.embedding` directly → Qdrant upsert with no model call.

### Connectome Edge Handling

Rule from Graphiti/Zep paper: **never delete edges to archived nodes.** Edges are causal history; pruning them loses explainability of current beliefs.

**Design:**
- Neo4j nodes corresponding to archived memories get `archived=true` property (no delete).
- `connectome_bitemporal_query` (`mcp_server_v2.py:6575` family) accepts new `include_cold: bool = False` kwarg. Default `False` → `WHERE NOT node.archived`. When `True`, include archived nodes in traversal but mark them in response with `is_cold=true`.
- Hot↔cold edge traversal performance stays flat — archived nodes still indexed in Neo4j, only tagged.

### Rejected Alternatives

1. **Second Qdrant collection for cold vectors** — rejected. Cross-collection queries not supported in Qdrant; would require two round-trips and Python-side merge per search. Persisting embedding in Postgres + on-demand Qdrant re-upsert is simpler and has zero storage overhead in Qdrant.
2. **BM25 full index rebuild on sweep** — rejected. SEAL uses Postgres GIN tsvector (`mcp_server_v2.py:2801`), which is **incremental**. `DELETE FROM memories` auto-updates GIN; `VACUUM (INDEX_CLEANUP ON)` after sweep is sufficient. Spec's original "BM25 rebuild" wording was wrong.
3. **Automatic deletion of cold rows after N days** — rejected. William's rule: "cold is forever until I say otherwise." Only a manual MCP tool (out of scope) could delete cold rows.
4. **Postgres native partitioning of `memories`** — rejected. Rebuilding `memories` is destructive and shares indexes across partitions, defeating the Qdrant-decoupling goal.
5. **zstd compression on `cold_archive.summary`** — rejected. Requires `pg_zstd` extension (not installed). Use Postgres 14+ `lz4` (`ALTER TABLE ... SET COMPRESSION lz4`) — 2× faster decompress than pglz, same ratio on short summaries.

### Risks & Mitigation

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Qdrant delete races hot search during sweep → 404s on in-flight queries | Medium | Low (single retry in client) | Schedule sweep in SleepGate window 03:00–06:00; Qdrant optimizer flush tolerated off-peak |
| 2 | Rehydration loop — cold memory always rehydrates, leaks hot-tier growth | Low | High (defeats purpose) | Require rehydration only on final top-k (not candidate pool); add rate limit: max 50 rehydrations/hour via working_state counter |
| 3 | `scoring.py` vs `scoring_v3.py` ambiguity — sweep uses wrong decay | Medium | High (wrong rows archived) | Phase 3 hard block: resolve authoritative module via grep + ADA review BEFORE wiring decay trigger. Add unit test asserting which module is imported |
| 4 | Ollama circuit breaker never closes → summaries degraded forever | Low | Medium | Half-open retry every 15min; DUM reports stale breaker to William at 24h |
| 5 | `PROTECTED_CATEGORIES` lift breaks existing imports / test fixtures | Medium | Medium | Mechanical refactor + grep sweep for both literal sets; keep module-level re-exports in old locations during transition with DeprecationWarning |

## Tentative Scope (retained)

**IN:**
- `soul/core/protected.py` unified `PROTECTED_CATEGORIES` (pre-req).
- Qdrant delete inside `_cold_archive_migrate()` + pending-retry table.
- `include_cold` wiring in `memory_search` / `memory_hybrid_search` with waterfall + rehydration.
- `OllamaCircuitBreaker` + DUM alert path.
- `connectome_bitemporal_query(include_cold=True)` support.
- New tests: Qdrant cleanup, rehydration round-trip, breaker trip/close, protected unified.
- Kill `memories_archive` after 14 days live.

**OUT of scope:**
- Cluster summarization improvements (current 1:1 sweep is enough for v1).
- S3/Glacier tiers.
- Automatic cold deletion.
- Full Graphiti port.

## Open Questions (for refinement phases)

1. What exactly triggers archive? `invalidated=true` OR `active_score < threshold` OR `age_days > N with no access`?
2. Compression: zstd on `content` column, or keep plain text and rely on table compression?
3. How does rehydration interact with Qdrant — do we re-embed on rehydrate, or store the original embedding in cold?
4. Does the connectome lose edges when a node is archived, or do edges persist pointing to cold nodes?
5. What does `memory_search(include_cold=True)` cost vs hot-only? Need a benchmark.

## Implementation Process

### Step 1: Pre-flight — resolve scoring.py vs scoring_v3.py (size: XS) — owner: ADA

**Goal:** Confirm the authoritative decay module before wiring anything else.
**Files:**
- `soul/core/scoring.py:32` — `temporal_decay_score()` candidate A
- `soul/core/scoring_v3.py` — candidate B
- `mcp_server_v2.py:~10009` — check which module `_cold_archive_migrate()` imports
**Dependencies:** none
**Risks:** Wrong module → sweep archives wrong rows. Mitigation: grep all imports of both modules across repo, decide, document in file header, add unit test asserting the active import.
**Definition of done:** One module marked `# AUTHORITATIVE`, other marked `# DEPRECATED`, test asserting `from soul.core.scoring import temporal_decay_score` matches what `_cold_archive_migrate` uses.

### Step 2: Create `soul/core/protected.py` unified PROTECTED_CATEGORIES (size: S) — owner: ADA

**Goal:** Single source of truth for protected category set.
**Files:**
- `soul/core/protected.py` (new) — `PROTECTED_CATEGORIES = frozenset({"correction","trust","identity","core_belief"})`, `PROTECTED_MIN_IMPORTANCE = 9`
- `mcp_server_v2.py:964` — replace literal `{"correction","trust"}` with import
- `memory_lifecycle.py:53` — replace literal `{"identity","core_belief"}` with import
**Dependencies:** none
**Risks:** Import cycle or stale test fixtures. Mitigation: keep module leaf-level (no SOUL imports); grep-sweep both literal sets.
**Definition of done:** Both call sites import from `soul/core/protected.py`; `grep -rn 'correction.*trust\|identity.*core_belief' --include='*.py'` returns only the new module.

### Step 3: Verify embedding column in memories_cold (size: XS) — owner: ADA

**Goal:** Confirm `migrations/009_cold_archive.sql` has `embedding vector(768)` + `idx_cold_embedding` populated.
**Files:**
- `migrations/009_cold_archive.sql` — inspect schema
- `migrations/010_cold_embedding_backfill.sql` (new, only if gap) — add column / backfill
**Dependencies:** none
**Risks:** Existing rows have NULL embeddings → rehydration re-embed cost. Mitigation: one-shot backfill script using `get_embedding()` if NULL count > 0.
**Definition of done:** `SELECT COUNT(*) FROM memories_cold WHERE embedding IS NULL` returns 0; IVFFlat index present.

### Step 4: Add lz4 compression to memories_cold.summary (size: XS) — owner: ADA

**Goal:** Halve storage for cold summaries (Postgres 14+ native).
**Files:**
- `migrations/011_cold_lz4.sql` (new) — `ALTER TABLE memories_cold ALTER COLUMN summary SET COMPRESSION lz4;`
**Dependencies:** Step 3
**Risks:** Postgres < 14 — verify at runtime. Mitigation: guard with `SELECT current_setting('server_version_num')::int >= 140000`.
**Definition of done:** Migration applied; `\d+ memories_cold` shows `lz4` on summary column.

### Step 5: Wire Qdrant point deletion in `_cold_archive_migrate()` (size: M) — owner: ADA

**Goal:** Stop leaking Qdrant points on archive.
**Files:**
- `mcp_server_v2.py:~10009` `_cold_archive_migrate()` — after `INSERT INTO memories_cold ... RETURNING id`, collect IDs; call `qdrant.delete(collection_name="seal_memories", points_selector=PointIdsList(points=ids))`; wrap in try/except; on failure insert into `cold_archive_qdrant_retry`. Order: Postgres INSERT commit → Qdrant delete → Postgres DELETE.
- `memory_lifecycle.py:436-450` — reference `cleanup_qdrant_vectors()` pattern
**Dependencies:** Step 1, Step 2, Step 6 (retry table must exist before this step writes to it)
**Risks:** Qdrant down during sweep → partial state. Mitigation: retry table catches orphans; Postgres DELETE only proceeds if Qdrant delete OR retry-row insert succeeds.
**Definition of done:** Archive run with Qdrant up leaves 0 orphan points; archive run with Qdrant killed leaves rows in `cold_archive_qdrant_retry`.

### Step 6: Create cold_archive_qdrant_retry table + retry worker (size: S) — owner: ADA

**Goal:** Durable reference for failed Qdrant deletes; drained by SleepGate 5.4.
**Files:**
- `migrations/012_cold_archive_qdrant_retry.sql` (new) — `CREATE TABLE cold_archive_qdrant_retry(memory_id uuid PRIMARY KEY, reason text, retry_count int DEFAULT 0, first_seen timestamptz DEFAULT now(), last_attempt timestamptz)`
- `sleep_gate_cron.py` Phase 5.4 (new sub-phase) — worker: `SELECT memory_id FROM cold_archive_qdrant_retry ORDER BY first_seen LIMIT 500`, attempt Qdrant delete, on success DELETE row, on fail `UPDATE ... SET retry_count = retry_count + 1, last_attempt = now()`.
**Dependencies:** none (but must exist before Step 5 writes)
**Risks:** Retry table grows unbounded if Qdrant persistently down. Mitigation: DUM alert when `retry_count > 10` OR `row_count > 1000`.
**Definition of done:** Table + worker in place; killing Qdrant, running sweep, restarting Qdrant, re-running SleepGate drains the retry table to 0.

### Step 7: Implement include_cold waterfall in memory_search (size: M) — owner: ADA

**Goal:** Make the `include_cold` kwarg actually work on the vector path.
**Files:**
- `mcp_server_v2.py:1229` `memory_search()` — read `include_cold`; after hot search, if `include_cold AND (len(hot) < k OR best_hot_score < COLD_FALLBACK_SCORE_FLOOR)`, run pgvector query against `memories_cold.embedding` using IVFFlat; merge; rank; return.
**Dependencies:** Step 3, Step 9 (rehydration hook)
**Risks:** Double scan tanks p95 latency. Mitigation: `COLD_FALLBACK_SCORE_FLOOR = 0.55` default + LIMIT on cold query; benchmark in Step 14.
**Definition of done:** `test_memory_search_include_cold_waterfall` passes; hot-only path unchanged in latency (< 5% regression).

### Step 8: Implement include_cold in hybrid_search (size: M) — owner: ADA

**Goal:** Same waterfall for BM25 + vector hybrid.
**Files:**
- `mcp_server_v2.py:2752` `memory_hybrid_search()` — wire both BM25 path (tsvector query against `memories_cold` if needed) and vector path as in Step 7; reuse RRF fusion.
- `mcp_server_v2.py:2801` — GIN tsvector reference for cold-side query
**Dependencies:** Step 7
**Risks:** RRF fusion weights need retune across hot+cold. Mitigation: keep k=60 default; add test asserting cold hits never outrank hot hits with identical score.
**Definition of done:** Hybrid search with `include_cold=True` returns cold rows when hot is thin; deterministic ranking under fixture data.

### Step 9: Rehydration on top-k hit (size: M) — owner: ADA

**Goal:** Cold → hot promotion with rate limit and zero re-embed.
**Files:**
- `mcp_server_v2.py` (new helper `_rehydrate_cold_memory(memory_id)`) — reads `embedding` from cold row; `INSERT INTO memories (...)` preserving original importance/category with `valid_from=NOW()`, `invalid_at=NULL`; Qdrant upsert with same vector; `DELETE FROM memories_cold`; `event_log_append(event_type='cold_rehydrate')`.
- `mcp_server_v2.py:1229` and `:2752` — call helper only for cold rows in final top-k.
- working_state key `cold_rehydrate_count_hourly` — increment on call; reject if > 50/hour.
**Dependencies:** Step 3, Step 7, Step 8
**Risks:** Rehydrate-loop turns cold→hot→cold→hot. Mitigation: only final top-k triggers; rate limit; event log audit.
**Definition of done:** `test_rehydration_round_trip` + `test_rehydration_rate_limit` pass.

### Step 10: Ollama circuit breaker for Phase 5.3 (size: M) — owner: ADA

**Goal:** Fail loudly when Ollama is down during sweep clustering.
**Files:**
- `soul/core/circuit_breaker.py` (new) — `OllamaCircuitBreaker` class (CLOSED/OPEN/HALF_OPEN, threshold=3, open_duration=900s, state persisted in `working_state` key `ollama_breaker_state`).
- `mcp_server_v2.py:~10009` `_cold_archive_migrate()` — wrap Ollama call; on trip: `event_log_append(event_type='ollama_circuit_open')`, touch `~/IA/proyecto-seal/messages/.urgent_trigger`, continue in degraded mode with `metadata->>'degraded'='ollama_down'` on each cold row.
- `sleep_gate_cron.py:~Phase 5.3` — surface breaker state in sweep stats.
**Dependencies:** Step 5
**Risks:** Breaker never closes → perpetual degraded sweeps. Mitigation: half-open probe every 15min; DUM alerts at 24h stale.
**Definition of done:** `test_ollama_circuit_breaker_degraded_mode` passes; killing Ollama during sweep produces event_log entry + urgent_trigger + degraded rows, not a crash.

### Step 11: include_cold in connectome_bitemporal_query (size: S) — owner: ADA

**Goal:** Edges to archived nodes survive; queries honor flag.
**Files:**
- `mcp_server_v2.py:6575` `connectome_bitemporal_query()` and family — add `include_cold: bool = False`; Cypher: default `WHERE NOT coalesce(node.archived, false)`; when True, include archived and tag response rows with `is_cold=true`.
- Archive sweep: on cold migrate, `MATCH (n {memory_id: $id}) SET n.archived = true` (no delete).
**Dependencies:** Step 5
**Risks:** Neo4j index miss on `archived` property. Mitigation: `CREATE INDEX ON :Memory(archived)` in migration.
**Definition of done:** Query with `include_cold=False` excludes archived nodes; with `True` they appear tagged.

### Step 12: Update soul_backup.py (size: S) — owner: ADA

**Goal:** Back up cold tier + retry table.
**Files:**
- `soul_backup.py` — add `memories_cold` and `cold_archive_qdrant_retry` to pg_dump table list; include embedding column; verify restore.
**Dependencies:** Step 5, Step 6
**Risks:** Backup size spike. Mitigation: lz4 from Step 4 already mitigates; document new size baseline.
**Definition of done:** Backup → wipe → restore round-trip preserves both tables and embeddings.

### Step 13: Extend test_new_tools.py (size: M) — owner: ADA

**Goal:** Cover every gap closure with deterministic tests.
**Files:**
- `test_new_tools.py` — add:
  - `test_cold_archive_qdrant_cleanup` (point absent from collection after archive)
  - `test_cold_archive_qdrant_retry_on_failure` (mock Qdrant down → retry row → worker drains)
  - `test_memory_search_include_cold_waterfall` (hot empty → cold surfaces)
  - `test_rehydration_round_trip` (cold → search → hot)
  - `test_rehydration_rate_limit` (51st rehydrate in 1h rejected)
  - `test_protected_categories_unified` (both modules expose same frozenset)
  - `test_ollama_circuit_breaker_degraded_mode` (breaker open → sweep continues, rows flagged)
  - `test_sweep_idempotent` (two sequential sweeps → second is no-op)
**Dependencies:** Steps 2, 5, 6, 7, 9, 10
**Risks:** Flaky Qdrant fixture. Mitigation: use dedicated test collection with teardown.
**Definition of done:** 8 new tests green in isolation and in full-suite run.

### Step 14: Run full test_new_tools.py suite (size: XS) — owner: ADA

**Goal:** Confirm 207 existing + 8 new tests pass.
**Files:**
- `test_new_tools.py` — full run
**Dependencies:** Step 13
**Risks:** Regression in unrelated tests from protected.py lift. Mitigation: bisect against Step 2 if any fail.
**Definition of done:** `pytest test_new_tools.py -v` → 215+ passed, 0 failed.

### Step 15: Update MEMORY.md for ADA and JARVIS (size: XS) — owner: ADA

**Goal:** Document new hot/cold architecture in team memory.
**Files:**
- `~/.claude/projects/-home-dadito-IA-proyecto-seal/memory/MEMORY.md` (ADA) — add "Cold Archive Architecture" reference
- `~/IA/proyecto-seal/memory/MEMORY.md` (JARVIS, if separate) — same
**Dependencies:** Step 14
**Risks:** none
**Definition of done:** Both MEMORY.md files reference the architecture; `memory_store` called with category `project` importance 7.

---

**Critical path:** Step 1 → Step 2 → Step 6 → Step 5 → Step 9 → Step 7 → Step 8 → Step 13 → Step 14.

## Parallelization Plan

### Wave 1 (no dependencies)
- Step 1: pre-flight scoring resolution (XS)
- Step 2: `soul/core/protected.py` unification (S)
- Step 3: verify embedding column in `memories_cold` (XS)
- Step 6: create `cold_archive_qdrant_retry` table + retry worker (S)

### Wave 2 (depends on Wave 1)
- Step 4: lz4 compression on summary (XS) — needs Step 3
- Step 5: wire Qdrant delete in `_cold_archive_migrate()` (M) — needs Steps 1, 2, 6
- Step 12: `soul_backup.py` updates (S) — needs Step 6 (can start early; finalize after Step 5)

### Wave 3 (depends on Wave 2)
- Step 10: Ollama circuit breaker (M) — needs Step 5
- Step 11: connectome `include_cold` + Neo4j `archived` flag (S) — needs Step 5

### Wave 4 (depends on Wave 3 + Step 3)
- Step 9: rehydration helper + rate limit (M) — needs Steps 3, 5
- Step 7: `memory_search` waterfall (M) — needs Steps 3, 9

### Wave 5 (depends on Wave 4)
- Step 8: `memory_hybrid_search` waterfall (M) — needs Step 7

### Wave 6 (integration)
- Step 13: test_new_tools.py extension (M) — needs Steps 2, 5, 6, 7, 9, 10
- Step 14: full suite run (XS) — needs Step 13
- Step 15: MEMORY.md updates (XS) — needs Step 14

### Critical path
1 → 2 → 6 → 5 → 9 → 7 → 8 → 13 → 14

Size estimates (1 ADA, solo): XS≈0.5h, S≈1.5h, M≈3h.
Critical-path duration: 0.5 + 1.5 + 1.5 + 3 + 3 + 3 + 3 + 3 + 0.5 = **19 h** wall-clock.
Adding Step 15 (XS, 0.5h) + Step 3 (parallel to 1/2/6 in Wave 1, absorbed): total ≈ **19.5 h** single-worker on critical path; full sequential all-15-steps would be ≈ 26 h.

### Parallelizable branches (hypothetical second worker)
- Branch A: **Step 10** (Ollama breaker) — becomes independent after Step 5; can run parallel to Steps 7/9.
- Branch B: **Step 11** (connectome `include_cold` + Neo4j `archived` flag) — independent after Step 5.
- Branch C: **Step 12** (soul_backup) — independent after Steps 5/6, can land anytime before Step 13.
- Branch D: **Step 4** (lz4) — independent after Step 3; tiny, can slot anywhere in Wave 2/3.

With a second worker taking Branches A+B+C+D off the critical path, wall-clock compresses to the critical path itself (~19 h) vs ~26 h sequential → **~27% timeline reduction**. A third worker yields no additional gain — critical path dominates.

## Acceptance Criteria (initial draft)

Given a memory with `invalidated=true` and category not in `PROTECTED_CATEGORIES`
When `cold_archive_sweep` runs
Then the row is moved to `memories_cold`, removed from Qdrant, and BM25 index rebuilt without it

Given a memory in `memories_cold`
When `memory_search(query, include_cold=True)` returns it in top results
Then the memory is rehydrated back to `memories` and re-indexed transparently

Given a memory with `category='correction'` and `invalidated=true`
When `cold_archive_sweep` runs
Then the memory stays in the hot tier (protected)

Given SleepGate runs nightly
When archive sweep completes
Then stats are reported: rows archived, bytes reclaimed, rehydration events in last 24h

## Verification

### Verification 1: Sweep moves qualifying rows hot→cold AND deletes Qdrant points
**Acceptance criterion:** Given a memory with `invalidated=true` and category not in `PROTECTED_CATEGORIES`, when `cold_archive_sweep` runs, then the row is moved to `memories_cold`, removed from Qdrant, and BM25 index rebuilt without it.
**How to verify:**
- Test: `test_cold_archive_qdrant_cleanup` in `test_new_tools.py`
- Manual check: `psql -c "SELECT id FROM memories WHERE id='<X>'"` returns 0 rows; `SELECT id FROM memories_cold WHERE '<X>' = ANY(original_memory_ids)` returns 1; `curl qdrant/collections/seal_memories/points/<X>` returns 404; `SELECT id FROM memories WHERE to_tsvector('spanish',content) @@ plainto_tsquery('<term>')` excludes the archived row.
**Judge rubric (1-5):**
- 5: All three stores consistent: row gone from `memories`, present in `memories_cold` with embedding, Qdrant point absent, tsvector query excludes it.
- 3: Postgres + Qdrant consistent but cold row missing embedding, OR tsvector still finds the row due to stale GIN (VACUUM missing).
- 1: Orphan Qdrant point remains OR hot row not deleted.
**Pass threshold:** ≥ 4

### Verification 2: Protected categories stay hot even if invalidated (unified protected.py)
**Acceptance criterion:** Given a memory with `category='correction'` and `invalidated=true`, when `cold_archive_sweep` runs, then the memory stays in the hot tier (protected).
**How to verify:**
- Test: `test_protected_categories_unified` + targeted sweep test asserting each of `{correction, trust, identity, core_belief}` survives a sweep with `invalidated=true`.
- Manual check: `grep -rn 'correction.*trust\|identity.*core_belief' --include='*.py'` returns only `soul/core/protected.py`.
**Judge rubric (1-5):**
- 5: All 4 categories survive sweep; both `mcp_server_v2.py` and `memory_lifecycle.py` import from `soul/core/protected.py`; no literal duplicates.
- 3: All 4 survive but one module still has the literal set alongside the import (dead code).
- 1: Any of the 4 categories archived OR import divergence persists.
**Pass threshold:** ≥ 4

### Verification 3: memory_search(include_cold=True) returns cold rows transparently (waterfall)
**Acceptance criterion:** Given a memory in `memories_cold`, when `memory_search(query, include_cold=True)` is called and hot results < k or best hot score < `COLD_FALLBACK_SCORE_FLOOR`, then cold rows surface in the merged top-k.
**How to verify:**
- Test: `test_memory_search_include_cold_waterfall` + `test_memory_hybrid_search_include_cold_waterfall`
- Manual check: archive a known row, call `memory_search(query=<matching text>, k=10, include_cold=True)`, assert the id appears with score > 0.
**Judge rubric (1-5):**
- 5: Hot-only path unchanged (< 5% p95 regression); cold rows appear only when floor triggered; RRF ranking deterministic; both `memory_search` and `memory_hybrid_search` wired.
- 3: Cold rows surface but latency regression > 5% in hot-only path OR only one of the two search functions wired.
- 1: `include_cold=True` returns same as `include_cold=False` (kwarg still ignored) OR crashes.
**Pass threshold:** ≥ 4

### Verification 4: Rehydration round-trip (cold → search → hot, accessible normally)
**Acceptance criterion:** Given a memory in `memories_cold`, when `memory_search(..., include_cold=True)` returns it in final top-k, then the memory is rehydrated into `memories`, re-indexed in Qdrant, removed from `memories_cold`, and subsequently retrievable via a plain `include_cold=False` search.
**How to verify:**
- Test: `test_rehydration_round_trip`
- Manual check: archive memory M → search with include_cold → second search with include_cold=False → assert M returned; `event_log` has `cold_rehydrate` row; `SELECT COUNT(*) FROM memories_cold WHERE id=M` = 0.
**Judge rubric (1-5):**
- 5: Round trip clean, no re-embed (stored embedding reused), event logged, `valid_from` updated, `invalid_at` cleared.
- 3: Round trip works but re-embeds unnecessarily OR event not logged.
- 1: Rehydration fails, duplicates row in both tables, or memory lost.
**Pass threshold:** ≥ 4

### Verification 5: Rehydration rate-limited (50/hr enforced)
**Acceptance criterion:** The 51st rehydration attempt within a 1h window is rejected without promoting the cold row.
**How to verify:**
- Test: `test_rehydration_rate_limit`
- Manual check: `working_state_get('cold_rehydrate_count_hourly')` returns counter; forcing 51 rehydrates logs rejection event.
**Judge rubric (1-5):**
- 5: Hard cap at 50; 51st returns error/skip, cold row untouched, counter resets at hour boundary.
- 3: Cap enforced but counter resets late or rejection silent (no event_log entry).
- 1: No rate limit OR limit bypassed.
**Pass threshold:** ≥ 4

### Verification 6: Sweep idempotent
**Acceptance criterion:** Running `cold_archive_sweep` twice back-to-back produces identical state after the second run (second run is a no-op).
**How to verify:**
- Test: `test_sweep_idempotent`
- Manual check: `SELECT COUNT(*) FROM memories_cold` before/after second run is unchanged; no new Qdrant deletes; no new event_log entries of type `cold_archive_migrate`.
**Judge rubric (1-5):**
- 5: Second run archives 0 rows, 0 Qdrant calls, 0 event_log writes.
- 3: Second run archives 0 rows but still writes a "no-op" event_log entry (acceptable).
- 1: Second run re-archives the same rows, duplicates in `memories_cold`, or crashes.
**Pass threshold:** ≥ 4

### Verification 7: Stats reported after nightly sweep
**Acceptance criterion:** When SleepGate archive sweep completes, it reports: rows archived, bytes reclaimed, rehydration events in last 24h.
**How to verify:**
- Test: assert SleepGate 5.3 return dict has keys `{rows_archived, bytes_reclaimed, rehydrations_24h, breaker_state, degraded_count}`.
- Manual check: `event_log_query(event_type='cold_archive_sweep_stats')` returns the structured payload.
**Judge rubric (1-5):**
- 5: All 5 keys present with correct types; breaker state + degraded count included.
- 3: 3 core keys present (rows_archived, bytes_reclaimed, rehydrations_24h) but breaker/degraded missing.
- 1: No stats emitted or malformed.
**Pass threshold:** ≥ 4

### Verification 8: Qdrant down → no data loss (retry table populated, worker drains)
**Acceptance criterion:** When Qdrant is unavailable during sweep, orphan memory IDs are persisted to `cold_archive_qdrant_retry` and a subsequent SleepGate 5.4 pass drains the table back to 0 once Qdrant recovers.
**How to verify:**
- Test: `test_cold_archive_qdrant_retry_on_failure`
- Manual check: `docker stop qdrant`, run sweep, `SELECT COUNT(*) FROM cold_archive_qdrant_retry` > 0; `docker start qdrant`, run SleepGate 5.4, count returns to 0.
**Judge rubric (1-5):**
- 5: Retry table populated on failure, Postgres hot-state consistent, worker drains on recovery, DUM alert fires when `retry_count > 10`.
- 3: Retry table populated + drains but no DUM alert on stuck entries.
- 1: Orphan vectors leaked silently OR hot row deleted before Qdrant confirmed.
**Pass threshold:** ≥ 4

### Verification 9: Ollama down → sweep continues in degraded mode (rows marked, breaker tripped)
**Acceptance criterion:** When Ollama fails 3+ consecutive times during sweep, circuit breaker opens, sweep continues in degraded 1:1 mode with `metadata->>'degraded'='ollama_down'`, event_log entry `ollama_circuit_open` written, and `.urgent_trigger` touched for DUM.
**How to verify:**
- Test: `test_ollama_circuit_breaker_degraded_mode`
- Manual check: stop Ollama, run sweep, check `SELECT metadata->>'degraded' FROM memories_cold WHERE archived_at > NOW() - interval '5 min'`; inspect `event_log_query(event_type='ollama_circuit_open')`; `ls -la ~/IA/proyecto-seal/messages/.urgent_trigger`.
**Judge rubric (1-5):**
- 5: Breaker opens at 3 failures, persists to `working_state`, sweep completes degraded, all 3 signals present (metadata flag, event_log, urgent_trigger), half-open probe after 15min.
- 3: Breaker opens and sweep completes but missing one of the signals (e.g., no urgent_trigger).
- 1: Sweep crashes OR silently drops rows OR breaker state lost on restart.
**Pass threshold:** ≥ 4

### Verification 10: Connectome edges to cold nodes remain queryable
**Acceptance criterion:** When a memory is archived, its Neo4j node is marked `archived=true` (not deleted). `connectome_bitemporal_query(include_cold=False)` excludes archived nodes; `include_cold=True` includes them tagged with `is_cold=true`. Incoming/outgoing edges are preserved.
**How to verify:**
- Test: new `test_connectome_cold_edge_preservation` (if space in Step 13) or manual Cypher assertion.
- Manual check: `MATCH (n:Memory {memory_id:'<X>'}) RETURN n.archived` returns `true`; `MATCH (a)-[r]->(n) WHERE n.memory_id='<X>' RETURN count(r)` unchanged from pre-archive; query with `include_cold=False` does not surface `<X>`; query with `include_cold=True` surfaces it with `is_cold=true`.
**Judge rubric (1-5):**
- 5: Edges intact, both query modes behave correctly, `archived` index present in Neo4j for perf.
- 3: Edges intact and both modes work but no Neo4j index on `archived` (perf risk).
- 1: Edges dropped OR `include_cold=False` still returns archived nodes.
**Pass threshold:** ≥ 4
