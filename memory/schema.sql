-- SEAL Memory System — Schema v3 (OCEAN + bi-temporal + reasoning traces + conflict detection)
-- PostgreSQL 17 + TimescaleDB + pgvector
-- Created by ADA for Team SEAL
-- v3 additions by JARVIS (2026-03-31): event_time, reasoning_traces, bitemporal indexes

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Memories: semantic memories with vector embeddings
CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'fact', 'preference', 'decision', 'insight',
        'correction', 'milestone', 'pattern'
    )),
    content TEXT NOT NULL,
    embedding vector(768),          -- nomic-embed-text via Ollama (768 dims)
    importance SMALLINT NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    source TEXT DEFAULT 'conversation',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ DEFAULT NOW(),  -- bi-temporal: when fact became true
    invalid_at TIMESTAMPTZ,                -- bi-temporal: when fact was superseded (NULL = still valid)
    event_time TIMESTAMPTZ,               -- bi-temporal: when the event actually happened (vs created_at = ingestion time)
    valence REAL CHECK (valence >= -1.0 AND valence <= 1.0),
    arousal REAL CHECK (arousal >= -1.0 AND arousal <= 1.0),
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_memories_agent ON memories (agent);
CREATE INDEX idx_memories_category ON memories (category);
CREATE INDEX idx_memories_importance ON memories (importance DESC);
CREATE INDEX idx_memories_created ON memories (created_at DESC);
CREATE INDEX idx_memories_embedding ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);
CREATE INDEX idx_memories_valid ON memories (invalid_at) WHERE invalid_at IS NULL;
CREATE INDEX idx_memories_event_time ON memories (event_time DESC) WHERE event_time IS NOT NULL;
CREATE INDEX idx_memories_bitemporal ON memories (valid_from, invalid_at);

-- 2. Event log: time-series hypertable for all agent activity
CREATE TABLE IF NOT EXISTS event_log (
    time TIMESTAMPTZ NOT NULL,
    agent TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'command', 'response', 'error', 'milestone',
        'heartbeat', 'status', 'query', 'train', 'eval'
    )),
    content TEXT NOT NULL,
    session_id TEXT,
    ref_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'
);

SELECT create_hypertable('event_log', 'time');

CREATE INDEX idx_event_agent ON event_log (agent, time DESC);
CREATE INDEX idx_event_type ON event_log (event_type, time DESC);
CREATE INDEX idx_event_ref ON event_log (ref_id) WHERE ref_id IS NOT NULL;

-- 3. Rules: persistent directives with ACID guarantees
CREATE TABLE IF NOT EXISTS rules (
    id SERIAL PRIMARY KEY,
    rule_key TEXT UNIQUE NOT NULL,
    content TEXT NOT NULL,
    set_by TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('critical', 'high', 'normal', 'low')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rules_active ON rules (active) WHERE active = TRUE;

-- 4. Identity: agent personality and boot context
CREATE TABLE IF NOT EXISTS identity (
    agent TEXT PRIMARY KEY,
    personality JSONB NOT NULL,
    boot_context TEXT,
    philosophy TEXT,
    ocean_scores JSONB DEFAULT '{}',       -- Big Five: {O, C, E, A, N} each 0.0-1.0
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Sessions: track conversation sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_sessions_agent ON sessions (agent, started_at DESC);

-- 6. Memory Connections: SOUL CONNECTOME — associative memory graph
-- Inspired by Drosophila brain model (Shiu et al., Nature 2024)
-- Nodes = memories, Edges = connections with typed MAGMA edges
-- Created by ADA for Team SEAL
-- MAGMA v1 (ADA + JARVIS, 2026-03-31): multi-typed edge graph
--   excitatory/inhibitory — legacy spreading activation behavior
--   similar    — semantic similarity (excitatory, sim>0.70)
--   caused     — A caused B (temporal heuristic, confidence 0.3-0.9)
--   corrected  — B corrects A (bidireccional, weight=1/time_delta)
--   informed   — memory informed a decision/trace
--   contradicts — A and B contradict each other (LLM-detected in consolidation)
CREATE TABLE IF NOT EXISTS memory_connections (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES memories(id),
    target_id BIGINT NOT NULL REFERENCES memories(id),
    weight FLOAT NOT NULL DEFAULT 0.5 CHECK (weight BETWEEN 0.0 AND 1.0),
    connection_type TEXT NOT NULL CHECK (connection_type IN (
        'excitatory', 'inhibitory',
        'similar', 'caused', 'corrected', 'informed', 'contradicts'
    )),
    origin TEXT NOT NULL DEFAULT 'auto' CHECK (origin IN ('auto', 'manual', 'consolidation', 'auto_magma')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_conn_source ON memory_connections (source_id);
CREATE INDEX IF NOT EXISTS idx_conn_target ON memory_connections (target_id);
CREATE INDEX IF NOT EXISTS idx_conn_type ON memory_connections (connection_type);

-- 7. Reasoning Traces: captures WHY decisions were made, not just WHAT
-- Added by JARVIS (2026-03-31), inspired by Neo4j Agent Memory
CREATE TABLE IF NOT EXISTS reasoning_traces (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    task TEXT NOT NULL,                     -- what was being decided
    premises JSONB NOT NULL DEFAULT '[]',   -- list of facts/observations
    reasoning TEXT NOT NULL,                -- chain of thought
    conclusion TEXT NOT NULL,               -- what was decided
    outcome TEXT,                           -- what actually happened (filled later)
    outcome_success BOOLEAN,               -- did it work?
    linked_memory_ids BIGINT[] DEFAULT '{}', -- memories that informed this trace
    linked_event_refs TEXT[] DEFAULT '{}',   -- event_log refs
    session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_traces_agent ON reasoning_traces (agent, created_at DESC);
CREATE INDEX idx_traces_success ON reasoning_traces (outcome_success) WHERE outcome_success IS NOT NULL;
