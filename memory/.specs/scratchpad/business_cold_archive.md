# Cold Archive — Business Analysis Scratchpad

## 1. Refined Problem Statement

SOUL's `memories` table is monotonically growing: every memory ever stored — active, HALO-decayed (score ≈ 0), invalidated, or obsolete — lives in the same hot table that feeds `memory_search`, `hybrid_search`, connectome traversal, and nightly consolidation. Dead rows still consume Qdrant vector slots and BM25 postings, diluting retrieval ranking and inflating latency on every query. Backups grow linearly with agent age, and there is no mechanism for SOUL to "forget" without destroying history — which violates the Graphiti/Zep bitemporal principle (archive, don't delete). The gap: we have decay scoring and invalidation flags, but no tier they can fall into.

## 2. User Stories

- **As JARVIS**, I want invalidated and fully-decayed memories removed from hot indices so that `memory_search` ranks current facts higher and runs faster as SOUL ages.
- **As ADA**, I want to explicitly query historical context with `include_cold=True` so that I can answer "what did we decide three months ago?" without polluting my default working set.
- **As SleepGate**, I want a nightly idempotent sweep that archives qualifying rows and reports stats so that maintenance is automatic, observable, and safe to re-run.
- **As JARVIS (safety)**, I want `PROTECTED_CATEGORIES` (correction, trust, identity) to be structurally incapable of moving to cold so that identity-critical memories cannot be lost through automation bugs.
- **As any agent**, I want a cold memory I touch to rehydrate transparently back to hot so that re-referenced facts rejoin the working set without manual intervention.

## 3. Acceptance Criteria (Given/When/Then)

**AC1 — Sweep moves qualifying rows hot→cold**
- Given a memory with `invalidated=true` AND `category NOT IN PROTECTED_CATEGORIES`
- When `cold_archive_sweep` runs
- Then the row is inserted into `memories_cold` (with compressed content), deleted from `memories`, its vector is removed from the Qdrant collection, and its BM25 postings are removed from the active index.

**AC2 — Decayed rows archive**
- Given a memory with `active_score < cold_threshold` AND `age_days > min_age` AND `category NOT IN PROTECTED_CATEGORIES`
- When `cold_archive_sweep` runs
- Then the row is moved to `memories_cold` following the same path as AC1.

**AC3 — Protected categories are immune**
- Given a memory with `category IN {correction, trust, identity}` AND `invalidated=true` AND `active_score = 0`
- When `cold_archive_sweep` runs
- Then the memory remains in `memories` hot tier, still indexed in Qdrant and BM25, and is logged as `skipped_protected`.

**AC4 — Transparent cold search**
- Given a query whose best match lives in `memories_cold`
- When an agent calls `memory_search(query, include_cold=True)`
- Then the result set contains the cold row, marked with `tier='cold'`, ordered alongside hot results by relevance.
- And when `include_cold=False` (default), the same call returns only hot rows.

**AC5 — Rehydration round-trip**
- Given a memory resident in `memories_cold`
- When it is returned by `memory_search(include_cold=True)` AND an agent reads the result (access event)
- Then the row is reinserted into `memories`, its vector is re-added to Qdrant, BM25 is updated, and a subsequent `memory_search(..., include_cold=False)` finds it in the hot tier.

**AC6 — Idempotent sweep**
- Given `cold_archive_sweep` has just finished with stats `{archived: N}`
- When `cold_archive_sweep` is invoked a second time with no intervening writes
- Then it completes with `{archived: 0, skipped: 0_new}` and no row is touched in either tier.

**AC7 — Nightly stats reporting**
- Given SleepGate runs the nightly archive sweep
- When the sweep completes (success or partial)
- Then a stats record is written (rows_archived, bytes_reclaimed_bytes, qdrant_vectors_removed, rehydrations_last_24h, protected_skipped, duration_ms) and surfaced to `brain_health_report`.

**AC8 — Qdrant down safety**
- Given Qdrant is unreachable when `cold_archive_sweep` starts
- When the sweep attempts to remove vectors
- Then NO rows are moved hot→cold (transactional abort), no data is lost in either tier, the failure is logged, and the next sweep retries cleanly.

**AC9 — Rehydration under Qdrant failure**
- Given a cold row is selected for rehydration AND Qdrant is unreachable
- When rehydration executes
- Then the row stays in `memories_cold`, the search call still returns the content for the current query, and a retry is scheduled.

## 4. Out-of-Scope (confirmed)

- **Cloud tiering (S3/Glacier/Backblaze):** OUT. Single-node Postgres only.
- **Full Graphiti/Zep framework port:** OUT. We adopt the hot/cold + bitemporal concept, not the codebase.
- **Automatic deletion of cold rows:** OUT. Cold is forever until William explicitly authorizes purge.
- **Archive of connectome edges/nodes:** OUT for v1 — edges may dangle pointing to cold nodes; edge archival is a future feature.
- **Cross-agent shared cold tier:** OUT. Each agent's cold archive is isolated like the hot tier.

## 5. Open Questions (ranked)

1. **Trigger policy:** What is the exact sweep predicate? Options: (a) `invalidated=true` only, (b) `active_score < T AND age_days > N`, (c) union of both. Default proposal: union, with `T=0.05`, `N=30`. — JARVIS to confirm thresholds.
2. **Embedding persistence:** On rehydration, do we re-embed from scratch (safer, costs GPU) or store the original embedding as bytea in `memories_cold` (faster, costs disk ~4KB/row)? — Benchmark needed.
3. **Connectome dangling edges:** When a node archives, do edges to it remain in `connectome_bitemporal` (queryable, may return nulls) or get flagged `endpoint_cold=true`? Affects traversal semantics.
4. **Compression choice:** zstd on `content` column at app layer, vs Postgres TOAST default, vs table-level pg_lz. Trade-off: CPU on read vs disk savings. Expected ratio?
5. **Rehydration policy:** Every cold hit rehydrates, or only after N hits in M days (promotion threshold)? One-hit rehydration risks thrashing on historical queries.

---
- Acceptance criteria written: **9**
- Open questions: **5**
