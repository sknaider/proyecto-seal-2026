-- Migration 019: consent_tokens — cross-agent privacy consent
-- Supports spec_memory_privacy_enforcement (JARVIS — 2026-05-06)
SET search_path = soul_v3;

CREATE TABLE IF NOT EXISTS consent_tokens (
    token        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grantor      VARCHAR(20) NOT NULL,
    grantee      VARCHAR(20) NOT NULL,
    tool_pattern TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    used_count   INT DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consent_tokens_grantee
    ON consent_tokens(grantee, expires_at);
