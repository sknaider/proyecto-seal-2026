-- Migration 009: Cold Archive — hot/cold memory separation
-- Moves invalidated memories to a dedicated cold_archive table with:
--   - Cluster summaries (multiple memories → one summary)
--   - pgvector embedding for semantic search
--   - TTL via expires_at for automatic purge
--
-- Paper: Graphiti/Zep (arxiv 2501.13956)
-- JARVIS — 2026-04-11

BEGIN;

-- ── Table ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cold_archive (
    id              BIGSERIAL PRIMARY KEY,
    agent           TEXT NOT NULL,
    original_memory_ids BIGINT[] NOT NULL DEFAULT '{}',
    summary         TEXT NOT NULL,
    embedding       vector(768),
    source_count    INT NOT NULL DEFAULT 1,
    importance_max  SMALLINT,
    category        TEXT,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,          -- NULL = never expires
    metadata        JSONB NOT NULL DEFAULT '{}'
);

-- ── Indexes ────────────────────────────────────────────────────────────────

-- Time-range queries per agent (most common access pattern)
CREATE INDEX IF NOT EXISTS idx_cold_agent_archived
    ON cold_archive (agent, archived_at DESC);

-- TTL purge scan — only rows that CAN expire
CREATE INDEX IF NOT EXISTS idx_cold_expires
    ON cold_archive (expires_at)
    WHERE expires_at IS NOT NULL;

-- Semantic search via pgvector (IVFFlat — good for moderate-size cold data)
-- lists=20 is reasonable for up to ~50K rows; adjust if archive grows larger
CREATE INDEX IF NOT EXISTS idx_cold_embedding
    ON cold_archive USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 20);

-- Category filter
CREATE INDEX IF NOT EXISTS idx_cold_category
    ON cold_archive (category)
    WHERE category IS NOT NULL;

COMMIT;
