-- Migration 013 — Add generated column 'agent' to rules table
-- Problem: rules is the only table using 'set_by' instead of 'agent'.
--          MCP code uses set_by extensively — cannot rename.
-- Solution: generated column agent = set_by (backward compat + schema uniformity).
-- Benefit: trigger no longer needs COALESCE for rules; future queries use agent directly.

ALTER TABLE rules
    ADD COLUMN IF NOT EXISTS agent TEXT GENERATED ALWAYS AS (set_by) STORED;
