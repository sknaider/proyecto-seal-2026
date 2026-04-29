-- Migration: distilled_exchanges v2 schema
-- Adds columns required by session_distill_pipeline.py (H2.7)
-- Old columns (session_date, key_insights, decisions_made, summary) preserved
-- New columns support richer distillation: compression metrics, room assignments, overlap continuity

ALTER TABLE soul_v3.distilled_exchanges
ADD COLUMN IF NOT EXISTS session_id        BIGINT REFERENCES soul_v3.sessions(id),
ADD COLUMN IF NOT EXISTS exchange_core     TEXT,
ADD COLUMN IF NOT EXISTS specific_context  TEXT,
ADD COLUMN IF NOT EXISTS room_assignments  JSONB,
ADD COLUMN IF NOT EXISTS files_touched     TEXT[],
ADD COLUMN IF NOT EXISTS ply_start         INTEGER,
ADD COLUMN IF NOT EXISTS ply_end           INTEGER,
ADD COLUMN IF NOT EXISTS source_tokens     INTEGER,
ADD COLUMN IF NOT EXISTS distilled_tokens  INTEGER,
ADD COLUMN IF NOT EXISTS exchange_time     TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS overlap_context   TEXT,
ADD COLUMN IF NOT EXISTS qdrant_point_id   BIGINT;

CREATE INDEX IF NOT EXISTS idx_distilled_session_id ON soul_v3.distilled_exchanges (session_id);
