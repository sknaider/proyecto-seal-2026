-- Migration 002: Procedural Memory (MemP pattern)
-- Stores reusable workflows extracted from successful task trajectories.
-- Build → Retrieve → Update lifecycle with reflect-based improvement.

CREATE TABLE IF NOT EXISTS procedural_memories (
    id              BIGSERIAL PRIMARY KEY,
    agent           TEXT NOT NULL,                    -- JARVIS, ADA, etc.
    task_type       TEXT,                             -- category: coding, research, medical, customs
    query           TEXT NOT NULL,                    -- task description / retrieval key
    workflow        TEXT NOT NULL,                    -- narrative workflow (paragraph or steps)
    facts           JSONB DEFAULT '{}',               -- structured key-value facts
    build_policy    TEXT DEFAULT 'direct' CHECK (build_policy IN ('direct', 'round', 'reflect')),
    hit_count       INT DEFAULT 0,                    -- times retrieved
    success_count   INT DEFAULT 0,                    -- times led to success
    fail_count      INT DEFAULT 0,                    -- times led to failure
    active          BOOLEAN DEFAULT true,
    embedding       vector(768),                      -- multilingual-e5-base
    source_task     TEXT,                             -- original task ID or description
    reflection      TEXT,                             -- LLM analysis of failures (reflect update)
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    -- Decay rule: auto-deactivate if hit >= 3 and success_rate < 0.5
    CONSTRAINT valid_counts CHECK (hit_count >= 0 AND success_count >= 0 AND fail_count >= 0)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_proc_agent_active ON procedural_memories (agent, active);
CREATE INDEX IF NOT EXISTS idx_proc_task_type ON procedural_memories (task_type) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_proc_success_rate ON procedural_memories ((success_count::float / NULLIF(hit_count, 0))) WHERE active = true AND hit_count >= 3;
CREATE INDEX IF NOT EXISTS idx_proc_embedding_hnsw ON procedural_memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_proc_fts ON procedural_memories USING gin (to_tsvector('spanish', query || ' ' || workflow));
