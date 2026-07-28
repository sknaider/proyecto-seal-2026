BEGIN;

-- Revocation rows are authorization state, not disposable telemetry.
-- The current schema does not persist the signed token expiry, while token
-- issuance permits caller-selected TTLs. Age since revocation therefore does
-- not prove that a token has expired. Keep every row until an expiry-bound
-- migration and an explicit retention policy replace this guard.
CREATE OR REPLACE FUNCTION soul_v3.cleanup_revoked_tokens()
RETURNS TABLE (deleted_count integer)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, soul_v3
AS $$
BEGIN
    RAISE EXCEPTION 'revoked_token_retention_indefinite'
        USING ERRCODE = '55000',
              HINT = 'Persist token_expires_at and approve an expiry-bound policy before cleanup.';
END;
$$;

REVOKE ALL ON FUNCTION soul_v3.cleanup_revoked_tokens() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION soul_v3.cleanup_revoked_tokens() FROM soul_admin;
GRANT EXECUTE ON FUNCTION soul_v3.cleanup_revoked_tokens() TO seal;

COMMENT ON FUNCTION soul_v3.cleanup_revoked_tokens() IS
    'Fail-loud retention guard: revocation rows remain indefinitely until token expiry is durably stored and an expiry-bound cleanup policy is approved.';

COMMIT;
