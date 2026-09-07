-- Reversible rollback for 20260722_p1_mcp_cgraph_runtime_ADA.sql.
-- This only removes the new capability role; it does not delete indexed data.

REVOKE pr_mcp_cgraph_write FROM mcp_runtime;
REVOKE pr_mcp_index_write, pr_mcp_diagnosis_write FROM mcp_runtime;
REVOKE pr_mcp_governance_write FROM mcp_runtime;
DROP ROLE IF EXISTS pr_mcp_cgraph_write;
DROP ROLE IF EXISTS pr_mcp_index_write;
DROP ROLE IF EXISTS pr_mcp_diagnosis_write;
DROP ROLE IF EXISTS pr_mcp_governance_write;
