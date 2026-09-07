-- Migration 021: Consolidation v2 — REM-style sleep consolidation
-- Crea infraestructura para GAP 2: pattern abstraction, schema induction,
-- connectome hebbian, counterfactual replay, procedural rehearsal.
--
-- Tablas nuevas (no altera nada existente):
--   schemas        — esquemas mentales reutilizables (GAP 2.B)
--   memory_edges   — grafo hebbian de co-activación (GAP 2.C)
-- Columnas nuevas (limpias, no parches):
--   procedural_memories.last_rehearsed (GAP 2.F)

SET search_path = soul_v3;

-- GAP 2.B: schemas inducidos
CREATE TABLE IF NOT EXISTS schemas (
    id              BIGSERIAL PRIMARY KEY,
    agent           TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    action_template TEXT NOT NULL,
    success_count   INT  DEFAULT 0,
    invalid_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    metadata        JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_schemas_agent ON schemas(agent) WHERE invalid_at IS NULL;

-- GAP 2.C: memory_edges (hebbian connectome)
CREATE TABLE IF NOT EXISTS memory_edges (
    a_id            BIGINT NOT NULL,
    b_id            BIGINT NOT NULL,
    weight          FLOAT  DEFAULT 0.1,
    co_count        INT    DEFAULT 1,
    last_reinforced TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (a_id, b_id),
    CHECK (a_id < b_id),
    FOREIGN KEY (a_id) REFERENCES memories(id) ON DELETE CASCADE,
    FOREIGN KEY (b_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_memory_edges_a ON memory_edges(a_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_b ON memory_edges(b_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_weight ON memory_edges(weight DESC);

-- GAP 2.F: procedural rehearsal tracking
ALTER TABLE procedural_memories
    ADD COLUMN IF NOT EXISTS last_rehearsed TIMESTAMPTZ;
