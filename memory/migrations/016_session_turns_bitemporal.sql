-- Migration 016: session_turns bitemporal + soft-hide
-- Spec v3 §6.2 — Historia inmutable: nunca DELETE, siempre invalid_at

SET search_path = soul_v3;

ALTER TABLE session_turns
    ADD COLUMN IF NOT EXISTS valid_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS invalid_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hidden         BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS hidden_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hidden_reason  VARCHAR(64),
    ADD COLUMN IF NOT EXISTS supersedes_id  BIGINT REFERENCES session_turns(id);

CREATE INDEX IF NOT EXISTS idx_turns_vigentes
    ON session_turns (session_id, turn_index)
    WHERE invalid_at IS NULL AND hidden = false;
