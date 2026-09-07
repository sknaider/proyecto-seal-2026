-- SUIE document search is isolated from soul_v3.memories/recall.
BEGIN;
CREATE INDEX IF NOT EXISTS ingestion_documents_text_search_idx
  ON soul_v3.ingestion_documents USING gin (to_tsvector('simple', normalized_text));
COMMENT ON INDEX soul_v3.ingestion_documents_text_search_idx IS
  'SUIE documentary search only; this index is not queried by SOUL memory recall.';
COMMIT;
