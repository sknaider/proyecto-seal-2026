-- Migration 017: pg_trgm GIN index para BM25-like retrieval
-- Spec v3 §8 — Retrieval híbrido semantic + BM25 via pg_trgm

SET search_path = soul_v3;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS memories_content_trgm
    ON memories USING gin (content gin_trgm_ops);
