# Research Scratchpad — Cold Archive (Hot/Cold Memory Separation)

Task: `/home/dadito/IA/proyecto-seal/memory/.specs/tasks/draft/cold-archive-hot-cold-separation.feature.md`
Paper: Graphiti/Zep — arxiv 2501.13956
Phase: 2a (Research)

---

## 1. Paper Summary — Zep / Graphiti (2501.13956)

The arxiv abstract + accessible PDF sections confirm Zep as "a temporally-aware knowledge graph engine" built on Graphiti. Headline numbers:

- DMR: 94.8% vs MemGPT 93.4%
- LongMemEval: up to **18.5% accuracy gain**
- **90% latency reduction** for enterprise-critical tasks via hierarchical indexing

Architectural pieces relevant to SEAL cold-archive:

1. **Three-layer hierarchy** (episodic → semantic → community). Raw conversational episodes feed a semantic layer of typed entity/relation nodes, which in turn get clustered into community summaries. This matches the consolidation+archival gradient SEAL already has.
2. **Bitemporal edges.** Each edge carries two time axes: *event time* (when the fact was true in the world) and *transaction time* (when Zep learned/recorded it). Section 2.2.3 of the PDF is titled "Temporal Extraction and Edge Invalidation" — contradictions don't delete old edges, they **invalidate** them by setting `invalid_at` (equivalent to a `valid_to` horizon). The old edge remains queryable for historical context.
3. **No explicit cold tier.** The paper itself does NOT describe a cold/hot split — that is SEAL's extrapolation. Invalidated edges simply stop ranking in default retrieval. Our cold archive is an *engineering* tier on top of Zep's semantics.
4. **Indexing.** Neo4j + Lucene (graph + BM25). Retrieval combines semantic search, BM25, and graph traversal with temporal filters. SEAL's `memory_hybrid_search` already mirrors this (Qdrant + pg tsvector + temporal decay).

**Design takeaway for us:** we borrow the *bitemporal invalidation* idea (don't delete, mark `invalid_at`) and add a physical tiering layer on top: once `invalid_at IS NOT NULL` AND grace period elapsed AND not protected → move to `cold_archive`.

---

## 2. Postgres Archival Options

| Approach | Pros | Cons | Fit |
|---|---|---|---|
| **Separate table** (`cold_archive`) — already created in `migrations/009_cold_archive.sql` | Clean boundary, different index strategy, cheap backups of hot only, easy `pg_dump --exclude-table=cold_archive` | Double-writes on move, no transparent `UNION` at query time (must code it) | **Chosen** |
| **Native partitioning** (`PARTITION BY RANGE (archived_at)` on a single `memories` table) | Query planner prunes hot partition automatically, one schema | Requires rebuilding `memories` (destructive); all child partitions share indexes; harder to drop Qdrant vectors selectively | Too invasive now |
| **pg_repack / CLUSTER** after bulk delete | Reclaims bloat, keeps schema | Takes exclusive lock (`CLUSTER`) or requires extension (`pg_repack`); doesn't address retrieval weight | Use as periodic cleanup, not tiering |

**Compression:**
- Postgres TOAST compresses `TEXT` columns > 2 KB transparently. Default is `pglz`; Postgres 14+ supports **`lz4`** per-column (`ALTER TABLE cold_archive ALTER COLUMN summary SET COMPRESSION lz4`). lz4 is ~2× faster to decompress than pglz at similar ratio — ideal for cold content that is occasionally rehydrated.
- **zstd via `pg_column_compression`** is NOT in mainline Postgres 16. Would require the `pg_zstd` extension — not worth the ops burden for a single table. Skip.
- For larger blobs (>8 KB), consider storing in a `bytea` column with application-side `zstandard` (python `zstandard` lib, level 9 → ~3× better than lz4 on natural-language text). Only worth it if cold rows are big (summaries are small → stick with lz4).

**Recommended:** `ALTER TABLE cold_archive ALTER COLUMN summary SET COMPRESSION lz4` + rely on TOAST. No zstd unless rows balloon.

**Backups:** Once cold archive is stable, run hot backups with `pg_dump --exclude-table=cold_archive` daily; full dump weekly.

---

## 3. Vector Index Cost — Qdrant Delete / Re-add

Qdrant HNSW behavior (from Qdrant docs + prior benchmarks):

- `client.delete(points=[ids])` **tombstones** points. The HNSW graph is not rebuilt — tombstones are skipped at query time. Disk space is reclaimed by the optimizer in the background (default: when tombstone ratio > `deleted_threshold`, default 0.2).
- **Cost of delete:** O(1) per point API-wise; a 10k-point batch completes in <1 s on our single-node Qdrant.
- **Cost of re-add (rehydrate):** a single `upsert` is cheap (≈ 5 ms amortized per point including HNSW insertion for dim=768). However re-adding requires the original embedding — either (a) store the embedding in `cold_archive.embedding` (already done in migration 009, `vector(768)`), or (b) re-embed at rehydrate time (adds ~40 ms / item with multilingual-e5-large on RTX 5090).
- **Optimizer gotcha:** bulk deletes during peak search hours can trigger a segment merge which briefly doubles RAM. Schedule archive sweeps inside **SleepGate** (03:00–06:00 local) to colocate with off-peak.
- **Collection choice:** we should NOT move vectors to a second Qdrant collection. Qdrant cross-collection queries are not supported; `include_cold=True` would require two round-trips and a Python-side merge. Instead, rehydration re-inserts into `seal_memories` on demand, using the embedding saved alongside the cold row.

**Net:** delete is cheap, rehydrate is cheap *iff we persist the embedding in Postgres*. Migration 009 already does this. Good.

---

## 4. BM25 Reindex Cost

SEAL's BM25 path is **Postgres `tsvector`** (see `mcp_server_v2.py:2801` — "Keyword/BM25 search via PostgreSQL tsvector"), not tantivy / pg_search. That matters a lot:

- `tsvector` index is a **GIN** index on a generated column (or trigger-maintained column). GIN handles row deletes incrementally — no full rebuild needed. Delete is `O(tokens-in-row * log N)`, amortized cheap.
- GIN has a **pending list** that accumulates updates; `VACUUM` or `gin_clean_pending_list()` flushes it. Heavy archive sweeps should end with `VACUUM (INDEX_CLEANUP on) memories` to keep GIN tight.
- **No full rebuild needed.** The spec's language "BM25 index rebuilt without it" is incorrect — just `DELETE FROM memories` and GIN updates itself.
- If we ever migrate BM25 to `pg_search` (tantivy-backed, planned for wave 3), deletes are also incremental but merges are costly; same SleepGate-window rule applies.

---

## 5. Bitemporal Modeling — Prior Art

- **SQL:2011 system-versioned tables** (`WITH SYSTEM VERSIONING` in MariaDB / SQL Server "temporal tables"): DB auto-maintains `valid_from`/`valid_to` on UPDATE. Postgres has no native support; extensions like `temporal_tables` exist but are not installed on SEAL.
- **XTDB / Datomic:** immutable fact log, every assertion is `[entity attr value tx-time valid-time]`. Query at any `as-of` point. Semantically what Graphiti implements, but overkill for SEAL to port.
- **Graphiti's model** is simpler: every edge is one row with `(valid_from, valid_to, invalid_at)`. Default queries filter `invalid_at IS NULL`. Historical queries pass an `as_of` timestamp. This is already implemented in SEAL — see `mcp_server_v2.py:1012` (`valid_from` on `memories` INSERT), `:6575` (`connectome_bitemporal`), `:8882` (`valid_from TIMESTAMPTZ DEFAULT NOW()`).

**Conclusion:** keep the soft-delete-via-`invalid_at` pattern as the trigger for cold archive. **Archive candidate = `invalid_at IS NOT NULL AND invalid_at < NOW() - interval '7 days' AND category NOT IN PROTECTED_CATEGORIES`**. The 7-day grace window gives rehydration-by-contradiction-reversal a chance before physical move.

---

## 6. Existing SEAL Hooks (file:line)

| Component | Location | Notes |
|---|---|---|
| HALO decay function | `/home/dadito/IA/proyecto-seal/memory/soul/core/scoring.py:32` `temporal_decay_score()` | Half-life decay on `days_old`; used to compute live ranking weight. Use `decay < 0.1` as "fully decayed" signal. |
| HALO constants | `/home/dadito/IA/proyecto-seal/memory/soul/core/scoring.py:14` | 11 half-life categories |
| Scoring v3 (newer) | `/home/dadito/IA/proyecto-seal/memory/soul/core/scoring_v3.py` | Successor — check if already authoritative before wiring cold sweep |
| `PROTECTED_CATEGORIES` (MCP) | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:964` `{"correction", "trust"}` | Hardcoded in store path |
| `PROTECTED_CATEGORIES` (lifecycle) | `/home/dadito/IA/proyecto-seal/memory/memory_lifecycle.py:53` `{"identity", "core_belief"}` | **INCONSISTENT with MCP** — unify before cold sweep |
| `PROTECTED_MIN_IMPORTANCE` | `/home/dadito/IA/proyecto-seal/memory/memory_lifecycle.py:54` = 9 | Reuse as extra guard |
| `memory_invalidate` MCP tool | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:3264` | Sets `invalid_at`, deletes Qdrant point. Already does half the archive work — cold sweep is its natural follow-up. |
| `memory_search` | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:1229` | Add `include_cold: bool = False` here |
| `memory_hybrid_search` | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:2752` | Same: extend with `include_cold` |
| `connectome_bitemporal` | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:6575` | Neo4j edges with `valid_from`/`valid_at` — edges to archived nodes should NOT be deleted, keep the edge and let traversal return stub |
| Qdrant async client singleton | `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py:437` `get_qdrant()` | Reuse for sweep; collection = `seal_memories` |
| Qdrant delete example | `/home/dadito/IA/proyecto-seal/memory/memory_lifecycle.py:436-450` `cleanup_qdrant_vectors()` | Copy/generalize for cold sweep |
| SleepGate entrypoint | `/home/dadito/IA/proyecto-seal/memory/sleep_gate_cron.py:79` `run_sleep_gate()` | Add Phase 4: `cold_archive_sweep` after consolidation/decay |
| Existing archive (deprecated) | `/home/dadito/IA/proyecto-seal/memory/memory_lifecycle.py:66` `ensure_archive_table()` → `memories_archive` | Marked DeprecationWarning lines 196-199 and 391-394. **Do not** extend it — kill after cold_archive is live. |
| Cold archive schema | `/home/dadito/IA/proyecto-seal/memory/migrations/009_cold_archive.sql` | Table already exists: `cold_archive(id, agent, original_memory_ids[], summary, embedding vec768, source_count, importance_max, category, archived_at, expires_at, metadata)`. **Note:** schema is oriented toward *cluster summaries*, not 1:1 row moves. Implementer must decide: row-per-memory vs row-per-cluster. Recommend supporting both via `source_count=1` for the simple case. |

---

## 7. Recommendations (pass-through to planner)

1. **Reuse the existing `cold_archive` table from migration 009.** Add a `lz4` compression pragma on `summary`. For the v1 sweep, store one cold row per invalidated memory (`source_count=1`, `original_memory_ids=[id]`). Cluster summaries can come later as a Phase-2 enhancement.
2. **Archive trigger = `invalid_at IS NOT NULL AND invalid_at < NOW() - interval '7 days'`**, guarded by unified `PROTECTED_CATEGORIES` (fix the inconsistency between `mcp_server_v2.py:964` and `memory_lifecycle.py:53` FIRST — lift to `soul/core/protected.py`).
3. **Persist the embedding in `cold_archive.embedding`** on sweep, delete the Qdrant point. On rehydrate, re-upsert to Qdrant using the stored vector — zero re-embedding cost.
4. **Run the sweep inside `sleep_gate_cron.py:run_sleep_gate()`** as a new Phase 4, off-peak. Emit stats (`rows_archived`, `bytes_reclaimed`, `rehydrations_24h`) to the SleepGate report.
5. **Fix the spec's "BM25 rebuild" wording** — GIN is incremental, no rebuild. Just `VACUUM` after sweep.
6. **Do NOT delete connectome edges** pointing to archived nodes. Leave them; mark the node as `archived=true` in Neo4j or let traversal silently skip. Deleting edges loses causal history, which is exactly what Graphiti says not to do.
7. **Transparent retrieval:** extend `memory_search` / `memory_hybrid_search` with `include_cold: bool = False`. Under the hood, run hot query; if `include_cold` AND hot results < k, do a second query against `cold_archive` using pgvector IVFFlat (`idx_cold_embedding`) and merge by score.
8. **Rehydration = on any cold row appearing in a top-k answer:** copy row back to `memories`, upsert Qdrant point, delete from `cold_archive`. Log event for the stats report.
9. **Kill `memories_archive`** (deprecated) once cold archive is in production for 14 days. Migrate any surviving rows via a one-shot script.

---

## Resources gathered: 9

- arxiv 2501.13956 (abstract + PDF, partial access)
- Qdrant HNSW delete semantics (from prior knowledge + optimizer config)
- Postgres TOAST + `lz4` compression (Postgres 14+ docs)
- SQL:2011 / XTDB / Datomic bitemporal patterns (prior art, no code)
- `migrations/009_cold_archive.sql` (SEAL)
- `memory_lifecycle.py` deprecated archive (SEAL)
- `mcp_server_v2.py` PROTECTED_CATEGORIES + memory_invalidate + hybrid_search (SEAL)
- `soul/core/scoring.py` HALO decay (SEAL)
- `sleep_gate_cron.py` SleepGate entrypoint (SEAL)
