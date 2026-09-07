BEGIN;

-- Legacy broad SDK role retained only as rollback metadata. Production MCP
-- now uses mcp_runtime with capability roles. Re-enable explicitly with
-- ALTER ROLE soul_sdk_runtime LOGIN only after a new least-privilege review.
ALTER ROLE soul_sdk_runtime NOLOGIN;

COMMIT;
