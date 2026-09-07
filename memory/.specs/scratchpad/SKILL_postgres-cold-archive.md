---
name: postgres-cold-archive
description: Use when designing hot/cold memory tiering on Postgres + pgvector + Qdrant + BM25 — covers compression choice, partition vs separate-table tradeoff, bitemporal archive triggers, vector index delete/rehydrate cost, and reindex strategy. Applies to SEAL-style agent memory systems and any Graphiti/Zep-inspired bitemporal store.
---

# Postgres Cold Archive — Hot/Cold Memory Tiering

> NOTE: intended install location `~/.claude/skills/postgres-cold-archive/SKILL.md` — researcher agent could not write there (sandbox). Copy this file into place manually when promoting.

Durable how-to for splitting an unbounded memory/fact table into a **hot** tier (fully indexed, fast retrieval) and a **cold** tier (compressed, minimal index, rehydratable). Distilled from the Graphiti/Zep paper (arxiv 2501.13956) and SEAL's migration 009.

## When to use
- A `memories` / `facts` / `events` table that grows unbounded and whose search latency is creeping.
- Stack: Postgres 14+ (`lz4` TOAST), pgvector, Qdrant or similar ANN, BM25 via `tsvector` GIN or tantivy.
- You already have soft-delete via `invalid_at` / `valid_to` (bitemporal). If not, add that first — cold archive without bitemporal semantics loses history irreversibly.

## Core design decisions

### 1. Separate table vs native partitioning
Pick **separate table** (`cold_archive`) unless you control the schema from day 1.
- Separate table = clean boundary, cheap `pg_dump --exclude-table=cold_archive`, independent index strategy, trivial to drop.
- Native `PARTITION BY RANGE` is technically superior (auto-prune) but requires rebuilding the hot table and shares indexes across partitions — disruptive on a live system.

### 2. Compression
- Postgres TOAST + `lz4` on the cold content column: `ALTER TABLE cold_archive ALTER COLUMN summary SET COMPRESSION lz4;`. Requires Postgres 14+.
- `pglz` (default) is fine but ~2× slower decompression. Use `lz4` for anything rehydrated occasionally.
- Skip zstd unless rows are > 8 KB average — the `pg_zstd` extension adds ops burden for marginal gain on short text.
- Application-side `zstandard` into `bytea` is only worth it for large blobs.

### 3. Archive trigger (bitemporal)
Move rows to cold when ALL of:
1. `invalid_at IS NOT NULL` (or `valid_to < NOW()`), AND
2. grace period elapsed (default 7 days — allows rehydration if the invalidation is reverted), AND
3. `category NOT IN PROTECTED_CATEGORIES` (identity, trust, correction — never archive), AND
4. `importance < PROTECTED_MIN_IMPORTANCE` (e.g. 9).

Run the sweep off-peak (cron or nightly consolidation job). Never inline.

### 4. Vector index — delete/rehydrate cost (Qdrant/HNSW)
- Delete is O(1) tombstone, no graph rebuild. Bulk deletes schedule a background segment merge — keep them off-peak to avoid RAM spikes.
- Persist the embedding in the cold row (`embedding vector(768)`). Delete the Qdrant point on archive; on rehydrate, `upsert` back with the stored vector. Zero re-embedding cost.
- Do NOT use a second Qdrant collection for cold. Cross-collection queries don't exist; rehydration-into-hot is simpler.
- After the sweep, let Qdrant's optimizer compact segments naturally (`deleted_threshold` default 0.2).

### 5. BM25 / tsvector reindex
- Postgres GIN on `tsvector` is incremental. No full rebuild on delete. End the sweep with `VACUUM (INDEX_CLEANUP ON) memories;` to flush the GIN pending list.
- tantivy / `pg_search` also deletes incrementally but merges are costly — same off-peak rule.
- Reject spec language like "rebuild BM25 after archive" — unnecessary and expensive.

### 6. Bitemporal edges (connectome)
Do NOT delete graph edges pointing to archived nodes. Mark the node `archived=true` or let traversal skip gracefully. Deleting edges destroys the causal history that makes bitemporal valuable (Graphiti's key insight).

## Rehydration protocol
When a cold row surfaces in a top-k result (via `include_cold=True` fallback):
1. `INSERT INTO memories (...) SELECT ... FROM cold_archive WHERE id=$1`
2. Qdrant `upsert` using the persisted embedding
3. `DELETE FROM cold_archive WHERE id=$1`
4. Log the rehydration event

Rehydration = promotion on access. Matches LRU/working-set intuition.

## Retrieval API shape
Extend search functions with `include_cold: bool = False` (default hot-only, preserves baseline latency). When true:
1. Run hot query normally.
2. If results < k OR explicit historical intent, run a second query against `cold_archive` using its own pgvector index (IVFFlat, `lists ≈ sqrt(N)`).
3. Merge by score, dedupe by original ID.

## Backup policy
- Daily: `pg_dump --exclude-table=cold_archive` (hot only, fast).
- Weekly: full dump including cold.
- Cold is forever unless explicitly purged.

## Anti-patterns to reject
- Second Qdrant collection for cold — unnecessary complexity.
- Rebuilding BM25/GIN after every sweep — GIN is incremental.
- Deleting connectome edges on archive — loses bitemporal value.
- zstd via non-mainline extension for short text — ops burden > benefit.
- Archiving without a grace period — rehydration thrash when contradictions flip back.
- Sweep inline inside request path — always batched, off-peak.
- Mixing protected-category definitions across modules — lift to one canonical constant.

## Sanity checklist
- [ ] `PROTECTED_CATEGORIES` unified in a single module
- [ ] Grace period ≥ 24 h (7 days recommended)
- [ ] Embedding persisted in cold row
- [ ] Sweep inside nightly cron, not request path
- [ ] `include_cold=False` is the default on all public search APIs
- [ ] Rehydration tested round-trip (hot → cold → hot)
- [ ] Stats emitted: rows_archived, bytes_reclaimed, rehydrations_24h
- [ ] Connectome edges to archived nodes preserved, not deleted
- [ ] `VACUUM` runs after sweep
- [ ] Backups exclude cold on fast path

## References
- Graphiti/Zep: arxiv 2501.13956 (bitemporal edges, `invalid_at`, edge invalidation §2.2.3)
- Postgres docs: TOAST storage, `lz4` compression (14+), GIN pending list
- Qdrant: HNSW tombstone semantics, optimizer `deleted_threshold`
- SQL:2011 system-versioned tables (conceptual ancestor)
