-- Migration 014: Add linked_memory_ids to soul_v3.reasoning_traces
-- Column was omitted from initial soul_v3 schema.
-- Referenced by backfill_trace_links.py and soul_awareness.py — causes runtime errors without it.
-- JARVIS — 2026-04-29

BEGIN;

SELECT pg_advisory_xact_lock(x'5EA1014'::bigint);

ALTER TABLE soul_v3.reasoning_traces
    ADD COLUMN IF NOT EXISTS linked_memory_ids BIGINT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_reasoning_traces_linked_memories
    ON soul_v3.reasoning_traces USING gin(linked_memory_ids);

COMMIT;
