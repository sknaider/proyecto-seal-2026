-- Migration 008: Extend opinions table for D-MEM Tier 5 (belief synthesis)
-- opinions already has: agent, belief, confidence, evidence_count, embedding, metadata
-- We add: topic, category, source_memory_ids, active, superseded_by, importance,
--         last_challenged, invalid_at, search_vector, status, updated_at
--
-- Decision: extend opinions instead of new table (JARVIS + ADA consensus)
-- ADA — 2026-04-11

BEGIN;

-- New columns for Tier 5
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS topic TEXT DEFAULT 'general';
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'general';
-- NOTE: source_memory_ids already exists as JSONB (created by JARVIS earlier)
-- ALTER TABLE opinions ADD COLUMN IF NOT EXISTS source_memory_ids JSONB DEFAULT '[]';
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS superseded_by INTEGER REFERENCES opinions(id);
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS importance REAL DEFAULT 7.0;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS last_challenged TIMESTAMPTZ;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS invalid_at TIMESTAMPTZ;
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE opinions ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Indexes for Tier 5 queries
CREATE INDEX IF NOT EXISTS idx_opinions_agent_topic ON opinions(agent, topic);
CREATE INDEX IF NOT EXISTS idx_opinions_agent_active ON opinions(agent, active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_opinions_status ON opinions(agent, status);
CREATE INDEX IF NOT EXISTS idx_opinions_search ON opinions USING gin(search_vector);

-- Trigger: auto-update search_vector and updated_at
CREATE OR REPLACE FUNCTION opinions_tier5_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('spanish',
        coalesce(NEW.topic, '') || ' ' || coalesce(NEW.content, ''));
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_opinions_tier5 ON opinions;
CREATE TRIGGER trg_opinions_tier5
    BEFORE INSERT OR UPDATE ON opinions
    FOR EACH ROW EXECUTE FUNCTION opinions_tier5_trigger();

-- Backfill existing rows
UPDATE opinions SET
    topic = 'general',
    status = 'active',
    active = TRUE,
    updated_at = now()
WHERE topic IS NULL;

COMMIT;
