-- Migration 011: MIRIX Memory Typing — formal 6-type memory system
-- Adds memory_type column + auto-classifies existing memories
-- Paper: MIRIX (arxiv 2507.07957) — 85% LoCoMo accuracy
-- JARVIS — 2026-04-12

BEGIN;

-- Advisory lock to prevent concurrent migration
SELECT pg_advisory_xact_lock(x'5EA1011'::bigint);

-- ── Column ────────────────────────────────────────────────────────────────
-- 6 MIRIX types: core, episodic, semantic, procedural, resource, vault
ALTER TABLE memories ADD COLUMN IF NOT EXISTS memory_type TEXT DEFAULT 'episodic';

-- ── Auto-classify existing memories ───────────────────────────────────────
-- Core: identity, trust, preferences, emotions
UPDATE memories SET memory_type = 'core'
WHERE category IN ('emotion', 'trust', 'preference')
  AND (memory_type IS NULL OR memory_type = 'episodic');

-- Semantic: knowledge, insights, facts, patterns, decisions
UPDATE memories SET memory_type = 'semantic'
WHERE category IN ('insight', 'fact', 'pattern', 'decision')
  AND (memory_type IS NULL OR memory_type = 'episodic');

-- Episodic stays as default for: milestone, dynamic, humor, correction, etc.

-- ── Index ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_memories_type
    ON memories (memory_type, agent);

-- ── Constraint ────────────────────────────────────────────────────────────
-- Only add if not exists (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_type'
    ) THEN
        ALTER TABLE memories ADD CONSTRAINT chk_memory_type
            CHECK (memory_type IN ('core', 'episodic', 'semantic', 'procedural', 'resource', 'vault'));
    END IF;
END $$;

COMMIT;
