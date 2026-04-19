-- Migration 005: Memory scopes for multi-agent visibility
-- Inspired by CrewAI hierarchical scopes + AutoGen delta sync

-- Add scope column to memories
ALTER TABLE memories ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'private';

-- Scope values:
--   private  = only the owning agent can see (default)
--   shared   = visible to ADA + JARVIS
--   team     = visible to all agents (ADA, JARVIS, DUM)
--   william  = only William can see (sensitive/personal)

-- Index for scope-filtered queries
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories (scope);

-- Composite index: agent + scope for common queries
CREATE INDEX IF NOT EXISTS idx_memories_agent_scope ON memories (agent, scope) WHERE invalid_at IS NULL;

-- Memory broadcasts table — event-driven notifications
CREATE TABLE IF NOT EXISTS memory_broadcasts (
    id SERIAL PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    from_agent TEXT NOT NULL,
    to_scope TEXT NOT NULL,         -- shared, team
    broadcast_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_by JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["ADA", "JARVIS"]
    summary TEXT                    -- short summary for quick consumption
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_scope ON memory_broadcasts (to_scope, broadcast_at DESC);
CREATE INDEX IF NOT EXISTS idx_broadcasts_unread ON memory_broadcasts (to_scope)
    WHERE NOT (read_by ? 'ADA' AND read_by ? 'JARVIS');

-- Add scope to instincts too (already has it but ensure consistency)
-- instincts.scope already exists from seed_instincts.py

-- Trigger function: auto-broadcast high-importance shared/team memories
CREATE OR REPLACE FUNCTION fn_auto_broadcast() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.scope IN ('shared', 'team') AND NEW.importance >= 8 AND NEW.invalid_at IS NULL THEN
        INSERT INTO memory_broadcasts (memory_id, from_agent, to_scope, summary)
        VALUES (NEW.id, NEW.agent, NEW.scope, LEFT(NEW.content, 200));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_broadcast ON memories;
CREATE TRIGGER trg_auto_broadcast
    AFTER INSERT OR UPDATE OF scope, importance ON memories
    FOR EACH ROW EXECUTE FUNCTION fn_auto_broadcast();
