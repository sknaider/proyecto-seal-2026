-- Migration 020: idle_curiosity_log
-- Registra cada disparo de curiosidad espontánea por agente
-- Permite auditoría y evitar repetición de temas

SET search_path = soul_v3;

CREATE TABLE IF NOT EXISTS idle_curiosity_log (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    question    TEXT NOT NULL,
    output_type TEXT NOT NULL CHECK (output_type IN ('web_chat', 'inner_monologue')),
    seed_memory_id BIGINT,
    seed_content TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_idle_curiosity_agent_ts
    ON idle_curiosity_log (agent, created_at DESC);
