-- SOUL v3 — Missing tables from migration audit
-- NEXUS provided to ADA for inclusion in soul_v3 schema
-- Source: public.* tables in seal_memory DB
-- All FK to soul_v3.agents(name)

SET search_path TO soul_v3, public;

-- ============================================================
-- INNER LIFE — emotional + cognitive trace
-- ============================================================

-- inner_monologue: stream of thought during a session
CREATE TABLE IF NOT EXISTS inner_monologue (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    session_id TEXT,
    turn_number INTEGER,
    thought TEXT NOT NULL,
    emotional_state TEXT,
    uncertainty TEXT,
    intention TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_im_agent_session ON inner_monologue(agent, session_id, turn_number);
CREATE INDEX idx_im_created ON inner_monologue(created_at DESC);

-- diary: daily reflection entries (1 per agent per day max)
CREATE TABLE IF NOT EXISTS diary (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    session_date DATE NOT NULL DEFAULT CURRENT_DATE,
    entry TEXT NOT NULL,
    mood TEXT,
    key_moments JSONB DEFAULT '[]',
    style_anchor JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent, session_date)
);
CREATE INDEX idx_diary_agent_date ON diary(agent, session_date DESC);

-- motivation_states: nerves tank values per agent (continuous, real-time)
CREATE TABLE IF NOT EXISTS motivation_states (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    tank TEXT NOT NULL,                        -- alert_drive, curiosity, social_drive, task_drive, context_pressure
    value REAL NOT NULL DEFAULT 0.0,
    last_update TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_fired TIMESTAMPTZ,
    fire_count INTEGER NOT NULL DEFAULT 0,
    metadata JSONB,
    UNIQUE (agent, tank)
);
CREATE INDEX idx_motivation_agent ON motivation_states(agent);

-- ============================================================
-- DRIFT — OCEAN evolution tracking
-- ============================================================

-- drift_events: discrete OCEAN drift events (granular)
CREATE TABLE IF NOT EXISTS drift_events (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    trait TEXT NOT NULL CHECK (trait IN ('O','C','E','A','N')),
    delta DOUBLE PRECISION NOT NULL CHECK (ABS(delta) <= 0.02),
    drift_before DOUBLE PRECISION,
    drift_after DOUBLE PRECISION,
    cause TEXT NOT NULL,
    emotional_state TEXT,
    ref_memory_id BIGINT REFERENCES memories(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_drift_events_agent_time ON drift_events(agent, created_at DESC);

-- drift_metrics: aggregated drift snapshots
CREATE TABLE IF NOT EXISTS drift_metrics (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    ocean_measured JSONB,
    ocean_baseline JSONB,
    drift_score REAL,
    style_measured JSONB,
    style_baseline JSONB,
    alert_level TEXT DEFAULT 'normal' CHECK (alert_level IN ('normal','warning','critical')),
    measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details JSONB
);
CREATE INDEX idx_drift_metrics_agent_time ON drift_metrics(agent, measured_at DESC);

-- ocean_base_values: baseline per agent per dimension (for drift comparison)
CREATE TABLE IF NOT EXISTS ocean_base_values (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    dimension VARCHAR(1) NOT NULL CHECK (dimension IN ('O','C','E','A','N')),
    base_value REAL NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent, dimension)
);

-- ============================================================
-- NERVES SYSTEM — feedback loops
-- ============================================================

-- nerves_metrics_log: every fire/measure event of nerves tanks
CREATE TABLE IF NOT EXISTS nerves_metrics_log (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    tank TEXT NOT NULL,
    pre_pressure REAL,
    threshold REAL,
    fired BOOLEAN NOT NULL DEFAULT FALSE,
    action_result TEXT,
    fire_latency_ms INTEGER,
    ocean_param TEXT,
    session_id TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_nerves_metrics_agent_tank_time ON nerves_metrics_log(agent, tank, created_at DESC);
CREATE INDEX idx_nerves_metrics_fired ON nerves_metrics_log(fired, created_at DESC) WHERE fired = TRUE;

-- soul_feedback_signal: rewards/penalties feeding instinct EMA updates
CREATE TABLE IF NOT EXISTS soul_feedback_signal (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    signal TEXT NOT NULL,                      -- 'reward', 'penalty', 'neutral'
    action_ref TEXT,
    context TEXT,
    instinct_id BIGINT REFERENCES instincts(id),
    ema_delta DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_soul_feedback_agent_time ON soul_feedback_signal(agent, created_at DESC);

-- instinct_activations: log every time an instinct fires
CREATE TABLE IF NOT EXISTS instinct_activations (
    id BIGSERIAL PRIMARY KEY,
    instinct_id BIGINT NOT NULL REFERENCES instincts(id),
    agent VARCHAR(20) NOT NULL REFERENCES agents(name),
    session_id TEXT,
    context TEXT,
    outcome TEXT DEFAULT 'applied',           -- 'applied', 'skipped', 'failed'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_instinct_activations_instinct ON instinct_activations(instinct_id, created_at DESC);
CREATE INDEX idx_instinct_activations_agent_time ON instinct_activations(agent, created_at DESC);

-- ============================================================
-- IDENTITY — extended agent identity beyond OCEAN
-- ============================================================

-- identity: rich identity fields (boot_context, philosophy, etc.)
CREATE TABLE IF NOT EXISTS identity (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL REFERENCES agents(name) UNIQUE,
    personality JSONB NOT NULL DEFAULT '{}',
    boot_context TEXT,
    philosophy TEXT,
    ocean_scores JSONB DEFAULT '{}',
    ocean_baseline JSONB,
    ocean_lock_hash TEXT,
    ocean_locked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- COMMENTS
-- ============================================================
COMMENT ON TABLE inner_monologue IS 'Stream of thought during session (turn-by-turn cognitive trace)';
COMMENT ON TABLE diary IS 'Daily reflection (1 per agent per day, key_moments + style_anchor)';
COMMENT ON TABLE motivation_states IS 'Nerves tank values (current state, fire counts)';
COMMENT ON TABLE drift_events IS 'Discrete OCEAN drift events (NEW v3: CHECK ABS(delta)<=0.02)';
COMMENT ON TABLE drift_metrics IS 'Aggregated drift snapshots (style + OCEAN combined)';
COMMENT ON TABLE ocean_base_values IS 'Baseline OCEAN per dimension per agent';
COMMENT ON TABLE nerves_metrics_log IS 'Nerves tank fire/measure events (51K+ rows in v2)';
COMMENT ON TABLE soul_feedback_signal IS 'Rewards/penalties feeding instinct EMA';
COMMENT ON TABLE instinct_activations IS 'Per-fire log of instinct activations';
COMMENT ON TABLE identity IS 'Rich identity (philosophy, boot_context, OCEAN lock)';
