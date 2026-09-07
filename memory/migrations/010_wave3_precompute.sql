-- Migration 010: Wave 3 Pre-compute Columns
-- Adds decay_score, surprise_score, recall_count, last_recalled_at to memories.
-- These columns are pre-computed by SleepGate nightly batch (scoring_v3.py)
-- so retrieval doesn't need to recompute decay at query time.
--
-- References: HALO (arxiv 2505.07509), A-MEM (arxiv 2502.12110), MemRL (arxiv 2601.03192)
-- ADA — 2026-04-11

BEGIN;

-- decay_score: pre-computed HALO half-life decay [0.0, 1.0]
-- 1.0 = fully relevant, 0.0 = fully decayed
ALTER TABLE memories ADD COLUMN IF NOT EXISTS decay_score REAL;

-- surprise_score: novelty relative to existing memories [0.0, 1.0]
-- 1.0 = completely novel, 0.0 = exact duplicate
ALTER TABLE memories ADD COLUMN IF NOT EXISTS surprise_score REAL;

-- recall_count: how many times this memory was retrieved (MemRL signal)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS recall_count INTEGER NOT NULL DEFAULT 0;

-- last_recalled_at: timestamp of most recent retrieval
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ;

-- Index: fast retrieval ranking by pre-computed decay
CREATE INDEX IF NOT EXISTS idx_memories_decay_score
    ON memories (decay_score DESC NULLS LAST)
    WHERE invalid_at IS NULL;

-- Index: recall frequency for MemRL Bellman updates
CREATE INDEX IF NOT EXISTS idx_memories_recall_count
    ON memories (recall_count DESC)
    WHERE invalid_at IS NULL;

COMMIT;
