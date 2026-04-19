-- Migration 004: Tool Observations (ECC v2.1 pattern — auto-observation layer)
-- Records every MCP tool call for pattern detection and instinct generation.
-- Lightweight: no embeddings, no LLM calls at write time.

CREATE TABLE IF NOT EXISTS tool_observations (
    id              BIGSERIAL PRIMARY KEY,
    agent           TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    input_summary   TEXT,
    output_summary  TEXT,
    success         BOOLEAN DEFAULT true,
    latency_ms      INT,
    session_id      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_obs_agent_tool ON tool_observations (agent, tool_name);
CREATE INDEX IF NOT EXISTS idx_obs_created ON tool_observations (created_at);
CREATE INDEX IF NOT EXISTS idx_obs_tool_name ON tool_observations (tool_name);
