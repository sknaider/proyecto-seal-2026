-- Migration 015: SPECTRE episodic index (hippocampus hash-keyed D2)
-- Additive only — no ALTER TABLE on existing soul_v3 tables
-- Authorizado: ADA 2026-05-02 06:55 Lima
-- Spec: spec_spectre_contract_v2_1_addendum.md F2

SET search_path TO soul_v3;

-- Hippocampus hash-keyed episodic memory index (D2)
CREATE TABLE IF NOT EXISTS soul_v3.episodic_index (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    timestamp_bucket DATE NOT NULL,
    context_hash CHAR(8) NOT NULL,
    memory_id   BIGINT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (memory_id) REFERENCES soul_v3.memories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episodic_lookup
    ON soul_v3.episodic_index (agent, timestamp_bucket, context_hash);

CREATE INDEX IF NOT EXISTS idx_episodic_memory
    ON soul_v3.episodic_index (memory_id);

-- SPECTRE identity anchor table (F1 boot_hash, sandbox-ready)
CREATE TABLE IF NOT EXISTS soul_v3.agent_alma (
    agent_id            UUID PRIMARY KEY,
    agent_name          TEXT NOT NULL,
    ocean_baseline      JSONB NOT NULL,
    core_values_hash    CHAR(64) NOT NULL,
    identity_pubkey     TEXT NOT NULL,
    boot_hash           CHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_validated_at   TIMESTAMPTZ
);
