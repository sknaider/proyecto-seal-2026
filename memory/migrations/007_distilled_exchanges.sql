-- Migration 007: Distilled Exchanges — Structured Distillation (arxiv 2603.13017)
-- The hippocampus of SOUL: compresses session exchanges 11x while preserving retrieval.
-- Created by JARVIS, 2026-04-06.

CREATE TABLE IF NOT EXISTS distilled_exchanges (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent           TEXT NOT NULL,
    -- Core distillation (4 fields from paper)
    exchange_core   TEXT NOT NULL,           -- What was accomplished (1-2 sentences, commit-message style)
    specific_context TEXT NOT NULL,          -- Distinguishing detail + emotional state + decisions
    room_assignments JSONB DEFAULT '[]',    -- Array of {type, key, label} triples
    files_touched   TEXT[] DEFAULT '{}',     -- File paths + MCP tools used
    -- Metadata
    ply_start       INT,                    -- First turn number in this exchange
    ply_end         INT,                    -- Last turn number in this exchange
    source_tokens   INT,                    -- Original token count
    distilled_tokens INT,                   -- Compressed token count
    -- Embedding for vector search (stored in Qdrant, ID reference here)
    qdrant_point_id INT,
    -- Timestamps
    exchange_time   TIMESTAMPTZ,            -- When the original exchange happened
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast session lookup
CREATE INDEX IF NOT EXISTS idx_distilled_session ON distilled_exchanges(session_id, agent);
-- Index for room-based graph queries
CREATE INDEX IF NOT EXISTS idx_distilled_rooms ON distilled_exchanges USING GIN(room_assignments);
-- Index for agent queries
CREATE INDEX IF NOT EXISTS idx_distilled_agent ON distilled_exchanges(agent, created_at DESC);
