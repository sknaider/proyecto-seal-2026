-- Migration 018: KisMATH causal reasoning quality fields
-- Adds causal_quality_score and exploration_regime to reasoning_traces
-- Inspired by: KisMATH paper (Saha et al., 2026) — arxiv.org/abs/2507.11408
-- JARVIS — Team SEAL — 05-may-2026

SET search_path = soul_v3;

ALTER TABLE reasoning_traces
    ADD COLUMN IF NOT EXISTS causal_quality_score FLOAT,
    ADD COLUMN IF NOT EXISTS exploration_regime    VARCHAR(20);

COMMENT ON COLUMN reasoning_traces.causal_quality_score IS
    'KisMATH quality score 0.0-1.0: proportion of causally connected reasoning steps';

COMMENT ON COLUMN reasoning_traces.exploration_regime IS
    'Inferred reasoning style: exponential | bell | linear | unknown';
