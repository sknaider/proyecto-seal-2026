-- Migration 015: Session Chain + Turn tables for SEAL Context Management Architecture
-- Creates NEW tables (session_chain, session_turns) alongside existing sessions table.
-- Idempotent — safe to re-apply.
-- ADA — 2026-04-29

BEGIN;

SELECT pg_advisory_xact_lock(x'5EA1015'::bigint);

-- ── session_chain ────────────────────────────────────────────────────────────
-- New chain-linked sessions table (UUID pk, parent_id, digest for Capa 3)
-- Separate from legacy soul_v3.sessions to preserve backward compat.
CREATE TABLE IF NOT EXISTS soul_v3.session_chain (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent        VARCHAR(64) NOT NULL,
    parent_id    UUID REFERENCES soul_v3.session_chain(id) ON DELETE SET NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    ended_reason VARCHAR(32),   -- auto_compact | manual_compact | crash | clean_exit
    turn_count   INTEGER NOT NULL DEFAULT 0,
    digest       TEXT,          -- filled on close (Capa 3)
    digest_tokens INTEGER,
    metadata     JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_session_chain_agent_ended
    ON soul_v3.session_chain (agent, ended_at DESC NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_session_chain_parent
    ON soul_v3.session_chain (parent_id);

CREATE INDEX IF NOT EXISTS idx_session_chain_agent_open
    ON soul_v3.session_chain (agent) WHERE ended_at IS NULL;

-- ── session_turns ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS soul_v3.session_turns (
    id                 BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES soul_v3.session_chain(id) ON DELETE CASCADE,
    turn_index         INTEGER NOT NULL,
    role               VARCHAR(16) NOT NULL,   -- user | assistant | tool | system
    content            TEXT NOT NULL,
    content_compressed TEXT,                   -- filled by Capa 2
    tokens_est         INTEGER NOT NULL DEFAULT 0,
    externalized_at    TIMESTAMPTZ,            -- NULL = still active in context
    compressed_at      TIMESTAMPTZ,            -- NULL = not compressed
    memory_id          BIGINT,                 -- references memories.id when externalized
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_session_turns_session_idx
    ON soul_v3.session_turns (session_id, turn_index);

CREATE INDEX IF NOT EXISTS idx_session_turns_externalized
    ON soul_v3.session_turns (session_id) WHERE externalized_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_session_turns_active
    ON soul_v3.session_turns (session_id) WHERE externalized_at IS NULL;

-- ── working_state: link to active session_chain ──────────────────────────────
ALTER TABLE soul_v3.working_state
    ADD COLUMN IF NOT EXISTS active_session_id UUID REFERENCES soul_v3.session_chain(id) ON DELETE SET NULL;

COMMIT;
