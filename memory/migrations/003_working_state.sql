-- Migration 003: Working State (MEM1 compressed reasoning state)
-- Stores per-agent compressed JSON representing current reasoning context.
-- Updated after significant exchanges. Loaded in boot_context.

CREATE TABLE IF NOT EXISTS working_state (
    agent       TEXT PRIMARY KEY,
    state       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    turn_count  INT DEFAULT 0
);

-- Seed known agents
INSERT INTO working_state (agent, state) VALUES ('JARVIS', '{}') ON CONFLICT DO NOTHING;
INSERT INTO working_state (agent, state) VALUES ('ADA', '{}') ON CONFLICT DO NOTHING;
INSERT INTO working_state (agent, state) VALUES ('DUM', '{}') ON CONFLICT DO NOTHING;
