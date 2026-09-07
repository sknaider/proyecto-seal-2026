-- Migration 006: MemRL Utility Score (arxiv 2601.03192)
-- Adds utility scoring with Bellman updates to memories
-- utility = learned value of a memory based on actual usage outcomes

-- Add utility_score to memories (default 0.5 = neutral)
ALTER TABLE memories ADD COLUMN IF NOT EXISTS utility_score FLOAT NOT NULL DEFAULT 0.5;

-- Index for utility-weighted retrieval
CREATE INDEX IF NOT EXISTS idx_memories_utility ON memories (utility_score DESC) WHERE invalid_at IS NULL;

-- Track utility updates for analysis
CREATE TABLE IF NOT EXISTS utility_updates (
    id SERIAL PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    old_utility FLOAT NOT NULL,
    new_utility FLOAT NOT NULL,
    reward FLOAT NOT NULL,          -- 1.0 = memory was useful, 0.0 = not useful
    context TEXT,                    -- what task/query the memory was used for
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_utility_updates_memory ON utility_updates (memory_id, updated_at DESC);
