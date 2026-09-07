-- NEXUS Cathedral II Code Graph — SOUL native schema
-- Tables prefixed cgraph_ to avoid collisions with existing SOUL schema.
-- Compatible with soul-memory-db PostgreSQL :5433 (pgvector already enabled).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Source repositories indexed for code search
CREATE TABLE IF NOT EXISTS cgraph_sources (
  id          SERIAL PRIMARY KEY,
  root_path   TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Files within a source (one row per file)
CREATE TABLE IF NOT EXISTS cgraph_pages (
  id           SERIAL PRIMARY KEY,
  source_id    INTEGER NOT NULL REFERENCES cgraph_sources(id) ON DELETE CASCADE,
  file_path    TEXT NOT NULL,
  language     TEXT,
  content_hash TEXT,
  indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_cgraph_pages_source ON cgraph_pages(source_id);
CREATE INDEX IF NOT EXISTS idx_cgraph_pages_lang   ON cgraph_pages(language);

-- Symbol-level code chunks (functions, classes, methods)
CREATE TABLE IF NOT EXISTS cgraph_chunks (
  id                   SERIAL PRIMARY KEY,
  page_id              INTEGER NOT NULL REFERENCES cgraph_pages(id) ON DELETE CASCADE,
  chunk_index          INTEGER NOT NULL,
  chunk_text           TEXT NOT NULL,
  language             TEXT,
  symbol_name          TEXT,
  symbol_type          TEXT,
  symbol_name_qualified TEXT,
  parent_symbol_path   TEXT[],
  doc_comment          TEXT,
  start_line           INTEGER,
  end_line             INTEGER,
  embedding            vector(1536),
  search_vector        TSVECTOR,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(page_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_cgraph_chunks_page     ON cgraph_chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_cgraph_chunks_symbol   ON cgraph_chunks(symbol_name) WHERE symbol_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cgraph_chunks_qualified ON cgraph_chunks(symbol_name_qualified) WHERE symbol_name_qualified IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cgraph_chunks_fts      ON cgraph_chunks USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_cgraph_chunks_vec      ON cgraph_chunks USING hnsw (embedding vector_cosine_ops);

-- FTS trigger: weight A = doc_comment + symbol_name_qualified, weight B = chunk_text
CREATE OR REPLACE FUNCTION cgraph_update_chunk_fts() RETURNS TRIGGER AS $fn$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english', COALESCE(NEW.doc_comment, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.symbol_name_qualified, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.chunk_text, '')), 'B');
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS cgraph_chunk_fts_trigger ON cgraph_chunks;
CREATE TRIGGER cgraph_chunk_fts_trigger
  BEFORE INSERT OR UPDATE OF chunk_text, doc_comment, symbol_name_qualified
  ON cgraph_chunks
  FOR EACH ROW EXECUTE FUNCTION cgraph_update_chunk_fts();

-- Resolved call edges: both from_chunk_id and to_chunk_id are known
CREATE TABLE IF NOT EXISTS cgraph_edges_chunk (
  id            SERIAL PRIMARY KEY,
  from_chunk_id INTEGER NOT NULL REFERENCES cgraph_chunks(id) ON DELETE CASCADE,
  to_chunk_id   INTEGER NOT NULL REFERENCES cgraph_chunks(id) ON DELETE CASCADE,
  from_symbol   TEXT NOT NULL,
  to_symbol     TEXT NOT NULL,
  edge_type     TEXT NOT NULL DEFAULT 'calls',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT cgraph_edges_chunk_uniq UNIQUE (from_chunk_id, to_chunk_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_cgraph_ec_from ON cgraph_edges_chunk(from_chunk_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_cgraph_ec_to   ON cgraph_edges_chunk(to_chunk_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_cgraph_ec_sym  ON cgraph_edges_chunk(to_symbol, edge_type);

-- Unresolved edges: target known by qualified name only (defined in another file not yet indexed)
CREATE TABLE IF NOT EXISTS cgraph_edges_symbol (
  id            SERIAL PRIMARY KEY,
  from_chunk_id INTEGER NOT NULL REFERENCES cgraph_chunks(id) ON DELETE CASCADE,
  from_symbol   TEXT NOT NULL,
  to_symbol     TEXT NOT NULL,
  edge_type     TEXT NOT NULL DEFAULT 'calls',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT cgraph_edges_symbol_uniq UNIQUE (from_chunk_id, to_symbol, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_cgraph_es_from ON cgraph_edges_symbol(from_chunk_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_cgraph_es_sym  ON cgraph_edges_symbol(to_symbol, edge_type);
