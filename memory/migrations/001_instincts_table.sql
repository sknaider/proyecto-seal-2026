-- SEAL SOUL — Instincts Table Migration
-- Task #63: Tier 2.1 Instinct-Based Learning
-- Designed by JARVIS, 2026-04-05
--
-- Sources:
--   - ECC v2 instinct system (YAML confidence tiers)
--   - MemP 2025 (procedural memory destillation)
--   - ERL 2026 (heuristic pool by similarity)
--   - Ebbinghaus 2024 (repetition → consolidation, disuse → decay)
--
-- Confidence tiers (from ECC, adapted):
--   0.0–0.3  dormant   — pattern detected, not yet actionable
--   0.3–0.5  suggested — mentioned in reasoning, not auto-applied
--   0.5–0.7  active    — applied when context matches
--   0.7–0.9  strong    — auto-approved, minimal deliberation
--   0.9–1.0  core      — fundamental behavior, near-reflex

CREATE TABLE IF NOT EXISTS instincts (
    id              SERIAL PRIMARY KEY,
    agent           TEXT NOT NULL,                    -- JARVIS, ADA, DUM

    -- What triggers this instinct and what it produces
    trigger_pattern TEXT NOT NULL,                    -- semantic description of when this fires
    response        TEXT NOT NULL,                    -- the behavioral response / heuristic
    domain          TEXT DEFAULT 'general',           -- coding, medical, communication, soul, etc.

    -- Confidence lifecycle (Ebbinghaus + ECC)
    confidence      REAL NOT NULL DEFAULT 0.3,        -- 0.0–1.0 scale
    activation_count INTEGER NOT NULL DEFAULT 0,      -- times this instinct was triggered
    reinforcement_count INTEGER NOT NULL DEFAULT 0,   -- times confirmed (no correction after)
    correction_count INTEGER NOT NULL DEFAULT 0,      -- times William/user corrected

    -- Provenance: which memories birthed this instinct
    source_memory_ids BIGINT[] DEFAULT '{}',          -- FK references to memories.id
    source_rule_id  INTEGER,                          -- if promoted from a rule

    -- Scope (ECC pattern: project → global promotion)
    scope           TEXT NOT NULL DEFAULT 'agent',    -- agent, team, global

    -- Temporal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activated  TIMESTAMPTZ,
    last_reinforced TIMESTAMPTZ,
    last_decayed    TIMESTAMPTZ,

    -- State
    active          BOOLEAN NOT NULL DEFAULT true,
    superseded_by   INTEGER,                          -- FK to instincts.id if evolved

    -- Embedding for similarity retrieval (ERL pattern)
    embedding       vector(768),                      -- same dim as memories (multilingual-e5-base)

    -- Metadata
    metadata        JSONB NOT NULL DEFAULT '{}',

    -- Constraints
    CONSTRAINT confidence_range CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT valid_scope CHECK (scope IN ('agent', 'team', 'global'))
);

-- Indexes for fast retrieval during boot and activation
CREATE INDEX IF NOT EXISTS idx_instincts_agent_active
    ON instincts (agent, active) WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_instincts_confidence
    ON instincts (confidence DESC) WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_instincts_domain
    ON instincts (domain) WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_instincts_last_activated
    ON instincts (last_activated DESC NULLS LAST) WHERE active = true;

-- Full-text search on trigger + response
CREATE INDEX IF NOT EXISTS idx_instincts_fts
    ON instincts USING gin(to_tsvector('spanish', trigger_pattern || ' ' || response));

-- Vector similarity index (for ERL-style retrieval)
-- Using ivfflat with expected ~1000 instincts max
CREATE INDEX IF NOT EXISTS idx_instincts_embedding
    ON instincts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Instinct activation log (for analysis and decay calculation)
CREATE TABLE IF NOT EXISTS instinct_activations (
    id              SERIAL PRIMARY KEY,
    instinct_id     INTEGER NOT NULL REFERENCES instincts(id),
    agent           TEXT NOT NULL,
    session_id      TEXT,
    context         TEXT,                             -- what triggered the activation
    outcome         TEXT DEFAULT 'applied',           -- applied, suppressed, corrected
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_instinct_activations_instinct
    ON instinct_activations (instinct_id, created_at DESC);

COMMENT ON TABLE instincts IS 'SEAL SOUL instincts — consolidated behavioral patterns from repeated experience. Confidence grows with repetition, decays with disuse (Ebbinghaus). Inspired by ECC v2, MemP, ERL.';
COMMENT ON TABLE instinct_activations IS 'Log of when instincts fired — used for confidence updates and decay calculation.';
