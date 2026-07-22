-- P1: restore Cathedral II indexing without exposing a privileged DSN.
-- The MCP process supplies an already-scoped mcp_runtime connection; this
-- role grants only the five code-graph tables and their ID sequences.

DO $migration$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_mcp_cgraph_write') THEN
        CREATE ROLE pr_mcp_cgraph_write NOLOGIN;
    END IF;
END
$migration$;

ALTER ROLE pr_mcp_cgraph_write
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOLOGIN;

GRANT USAGE ON SCHEMA soul_v3 TO pr_mcp_cgraph_write;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    soul_v3.cgraph_sources,
    soul_v3.cgraph_pages,
    soul_v3.cgraph_chunks,
    soul_v3.cgraph_edges_symbol,
    soul_v3.cgraph_edges_chunk
TO pr_mcp_cgraph_write;

GRANT USAGE, SELECT ON SEQUENCE
    soul_v3.cgraph_sources_id_seq,
    soul_v3.cgraph_pages_id_seq,
    soul_v3.cgraph_chunks_id_seq,
    soul_v3.cgraph_edges_symbol_id_seq,
    soul_v3.cgraph_edges_chunk_id_seq
TO pr_mcp_cgraph_write;

GRANT EXECUTE ON FUNCTION soul_v3.cgraph_update_chunk_fts()
TO pr_mcp_cgraph_write;

GRANT pr_mcp_cgraph_write TO mcp_runtime;

COMMENT ON ROLE pr_mcp_cgraph_write IS
    'Least-privilege Cathedral II writer used by mcp_runtime; path scope is enforced by ToolBroker.';

DO $migration$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_mcp_index_write') THEN
        CREATE ROLE pr_mcp_index_write NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_mcp_diagnosis_write') THEN
        CREATE ROLE pr_mcp_diagnosis_write NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_mcp_governance_write') THEN
        CREATE ROLE pr_mcp_governance_write NOLOGIN;
    END IF;
END
$migration$;

ALTER ROLE pr_mcp_index_write
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOLOGIN;
ALTER ROLE pr_mcp_diagnosis_write
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOLOGIN;
ALTER ROLE pr_mcp_governance_write
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOLOGIN;

GRANT USAGE ON SCHEMA soul_v3 TO
    pr_mcp_index_write, pr_mcp_diagnosis_write, pr_mcp_governance_write;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    soul_v3.index_state,
    soul_v3.memory_search_idx
TO pr_mcp_index_write;
GRANT SELECT, INSERT, UPDATE ON soul_v3.index_runs TO pr_mcp_index_write;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.index_runs_id_seq TO pr_mcp_index_write;

GRANT SELECT, INSERT, UPDATE ON soul_v3.reflective_diagnoses
TO pr_mcp_diagnosis_write;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.reflective_diagnoses_id_seq
TO pr_mcp_diagnosis_write;

GRANT SELECT, INSERT, UPDATE ON
    soul_v3.agent_challenges,
    soul_v3.debate_log
TO pr_mcp_governance_write;
GRANT USAGE, SELECT ON SEQUENCE
    soul_v3.agent_challenges_id_seq,
    soul_v3.debate_log_id_seq
TO pr_mcp_governance_write;

GRANT pr_mcp_index_write, pr_mcp_diagnosis_write, pr_mcp_governance_write
TO mcp_runtime;

COMMENT ON ROLE pr_mcp_index_write IS
    'Per-agent native memory index maintenance for mcp_runtime.';
COMMENT ON ROLE pr_mcp_diagnosis_write IS
    'Per-agent reflective diagnosis read/write for mcp_runtime.';
COMMENT ON ROLE pr_mcp_governance_write IS
    'Team governance tables for mcp_runtime; actor identity is enforced by the MCP wrapper.';
