-- ============================================================================
-- Fase-1 Security: Authorization Gate — DDL PostgreSQL
-- ============================================================================
-- Owner: NEXUS (Infrastructure Security)
-- Date: 2026-07-02
-- Purpose: Create tables, indexes, RLS policies for device auth + token revocation
-- ============================================================================

-- Ensure soul_v3 schema exists
CREATE SCHEMA IF NOT EXISTS soul_v3;

-- ============================================================================
-- TABLE 1: authorized_devices
-- ============================================================================
-- Registry of authorized devices (one entry per device + agent combo)
-- Status: active (usable), suspended (temp block), revoked (permanent)

CREATE TABLE IF NOT EXISTS soul_v3.authorized_devices (
    device_id TEXT PRIMARY KEY,
    device_fingerprint TEXT NOT NULL UNIQUE,  -- sha256:{64-hex}
    agent TEXT NOT NULL,  -- ADA, JARVIS, ALICE, NEXUS, DUM
    registered_by TEXT NOT NULL,  -- Usually 'william'
    registered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_ip INET,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'revoked')) DEFAULT 'active',
    revocation_reason TEXT,
    revoked_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}',  -- Extra: device_name, os_info, etc.

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast lookups
CREATE INDEX idx_authorized_devices_agent
    ON soul_v3.authorized_devices(agent);

CREATE INDEX idx_authorized_devices_status
    ON soul_v3.authorized_devices(status);

CREATE INDEX idx_authorized_devices_registered_at
    ON soul_v3.authorized_devices(registered_at DESC);

CREATE INDEX idx_authorized_devices_device_fingerprint
    ON soul_v3.authorized_devices(device_fingerprint);

-- ============================================================================
-- TABLE 2: revoked_tokens
-- ============================================================================
-- Blacklist of revoked JWT tokens (JTI = device_id)
-- Used to block compromised or expired-but-still-valid tokens

CREATE TABLE IF NOT EXISTS soul_v3.revoked_tokens (
    token_jti TEXT PRIMARY KEY,  -- Nonce/UUID of the token
    device_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_by TEXT NOT NULL,  -- 'william', 'auto-expiry', 'fingerprint_mismatch', etc.
    reason TEXT NOT NULL,  -- device_compromised, explicit_revocation, logout, auto_expiry, fingerprint_mismatch

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (device_id) REFERENCES soul_v3.authorized_devices(device_id)
        ON DELETE CASCADE
);

-- Indexes for fast blacklist checks
CREATE INDEX idx_revoked_tokens_device_id
    ON soul_v3.revoked_tokens(device_id);

CREATE INDEX idx_revoked_tokens_agent
    ON soul_v3.revoked_tokens(agent);

CREATE INDEX idx_revoked_tokens_revoked_at
    ON soul_v3.revoked_tokens(revoked_at DESC);

-- Fast TTL expiry query (for cleanup)
CREATE INDEX idx_revoked_tokens_age
    ON soul_v3.revoked_tokens(revoked_at)
    WHERE revoked_at > CURRENT_TIMESTAMP - INTERVAL '90 days';

-- ============================================================================
-- TABLE 3: token_audit
-- ============================================================================
-- Audit log of all token operations (issue, validate, refresh, revoke)

CREATE TABLE IF NOT EXISTS soul_v3.token_audit (
    token_id BIGSERIAL PRIMARY KEY,
    device_id TEXT,
    agent TEXT,
    action TEXT NOT NULL CHECK (
        action IN ('issued', 'validated_ok', 'validated_fail', 'revoked', 'refresh_ok', 'refresh_fail')
    ),
    reason TEXT,  -- e.g. "signature_invalid", "expired", "fingerprint_mismatch", "device_compromised"
    ip_address INET,
    user_agent TEXT,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for audit queries
CREATE INDEX idx_token_audit_device_id
    ON soul_v3.token_audit(device_id);

CREATE INDEX idx_token_audit_agent
    ON soul_v3.token_audit(agent);

CREATE INDEX idx_token_audit_created_at
    ON soul_v3.token_audit(created_at DESC);

CREATE INDEX idx_token_audit_action
    ON soul_v3.token_audit(action, created_at DESC);

-- ============================================================================
-- TABLE 4: pending_device_registrations (temporary, for approval workflow)
-- ============================================================================
-- Stores CSR data awaiting William's 2FA approval

CREATE TABLE IF NOT EXISTS soul_v3.pending_device_registrations (
    device_id TEXT PRIMARY KEY,
    device_fingerprint TEXT NOT NULL,
    agent TEXT NOT NULL,
    csr_data JSONB NOT NULL,  -- Full CSR payload (device_id, fingerprint, os_info, etc.)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP + INTERVAL '24 hours'
);

-- Cleanup old pending registrations
CREATE INDEX idx_pending_device_registrations_expires_at
    ON soul_v3.pending_device_registrations(expires_at);

-- ============================================================================
-- ROW-LEVEL SECURITY (RLS) POLICIES
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE soul_v3.authorized_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.revoked_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.token_audit ENABLE ROW LEVEL SECURITY;

-- Policy: authorized_devices
-- Agent can only see/modify their own devices
-- Admin (is_admin='true') can see all

CREATE POLICY authorized_devices_agent_isolation
    ON soul_v3.authorized_devices
    FOR SELECT
    USING (
        agent = current_setting('soul.agent', false)
        OR current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

CREATE POLICY authorized_devices_agent_insert
    ON soul_v3.authorized_devices
    FOR INSERT
    WITH CHECK (
        current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

CREATE POLICY authorized_devices_agent_update
    ON soul_v3.authorized_devices
    FOR UPDATE
    USING (
        current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

-- Policy: revoked_tokens
-- Agent sees revocations of their own devices; admin sees all

CREATE POLICY revoked_tokens_agent_isolation
    ON soul_v3.revoked_tokens
    FOR SELECT
    USING (
        agent = current_setting('soul.agent', false)
        OR current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

CREATE POLICY revoked_tokens_admin_insert
    ON soul_v3.revoked_tokens
    FOR INSERT
    WITH CHECK (
        current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

-- Policy: token_audit
-- Agent sees audit of their own agent; admin sees all

CREATE POLICY token_audit_agent_isolation
    ON soul_v3.token_audit
    FOR SELECT
    USING (
        agent = current_setting('soul.agent', false)
        OR current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

CREATE POLICY token_audit_admin_insert
    ON soul_v3.token_audit
    FOR INSERT
    WITH CHECK (
        current_setting('soul.is_admin', false) = 'true'
        OR current_setting('soul.is_admin', false) = '1'
    );

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function: Set security context (agent identity + admin flag)
CREATE OR REPLACE FUNCTION soul_v3.set_security_context(
    agent_name TEXT,
    is_admin BOOLEAN DEFAULT FALSE
)
RETURNS void AS $$
BEGIN
    PERFORM set_config('soul.agent', agent_name, false);
    PERFORM set_config('soul.is_admin', CASE WHEN is_admin THEN 'true' ELSE 'false' END, false);
END;
$$ LANGUAGE plpgsql;

-- Function: Get authorized device status
CREATE OR REPLACE FUNCTION soul_v3.get_device_status(
    device_id_in TEXT
)
RETURNS TABLE (
    device_id TEXT,
    agent TEXT,
    status TEXT,
    is_revoked BOOLEAN,
    last_seen_at TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.device_id,
        d.agent,
        d.status,
        (d.status = 'revoked') AS is_revoked,
        d.last_seen_at
    FROM soul_v3.authorized_devices d
    WHERE d.device_id = device_id_in;
END;
$$ LANGUAGE plpgsql;

-- Function: Check if token is revoked
CREATE OR REPLACE FUNCTION soul_v3.is_token_revoked(
    token_jti_in TEXT
)
RETURNS BOOLEAN AS $$
DECLARE
    revoked BOOLEAN;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM soul_v3.revoked_tokens
        WHERE token_jti = token_jti_in
    ) INTO revoked;

    RETURN revoked;
END;
$$ LANGUAGE plpgsql;

-- Function: Revoke all tokens for a device
CREATE OR REPLACE FUNCTION soul_v3.revoke_device_tokens(
    device_id_in TEXT,
    agent_in TEXT,
    reason_in TEXT DEFAULT 'explicit_revocation',
    revoked_by_in TEXT DEFAULT 'william'
)
RETURNS TABLE (
    revoked_count INTEGER,
    status TEXT
) AS $$
DECLARE
    count_revoked INTEGER;
BEGIN
    -- Revoke all tokens for this device
    INSERT INTO soul_v3.revoked_tokens
    (token_jti, device_id, agent, revoked_by, reason)
    VALUES
    (device_id_in, device_id_in, agent_in, revoked_by_in, reason_in)
    ON CONFLICT (token_jti) DO NOTHING;

    GET DIAGNOSTICS count_revoked = ROW_COUNT;

    -- Update device status
    UPDATE soul_v3.authorized_devices
    SET
        status = 'revoked',
        revoked_at = CURRENT_TIMESTAMP,
        revocation_reason = reason_in
    WHERE device_id = device_id_in AND agent = agent_in;

    RETURN QUERY SELECT count_revoked, 'revoked'::TEXT;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- MAINTENANCE PROCEDURES
-- ============================================================================

-- Cleanup old revocation entries (>90 days)
-- Run daily via cron: psql -d soul_v3 -c "SELECT soul_v3.cleanup_revoked_tokens();"

CREATE OR REPLACE FUNCTION soul_v3.cleanup_revoked_tokens()
RETURNS TABLE (
    deleted_count INTEGER
) AS $$
DECLARE
    count_deleted INTEGER;
BEGIN
    DELETE FROM soul_v3.revoked_tokens
    WHERE revoked_at < CURRENT_TIMESTAMP - INTERVAL '90 days';

    GET DIAGNOSTICS count_deleted = ROW_COUNT;

    RETURN QUERY SELECT count_deleted;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old pending registrations (>24 hours)
CREATE OR REPLACE FUNCTION soul_v3.cleanup_pending_registrations()
RETURNS TABLE (
    deleted_count INTEGER
) AS $$
DECLARE
    count_deleted INTEGER;
BEGIN
    DELETE FROM soul_v3.pending_device_registrations
    WHERE expires_at < CURRENT_TIMESTAMP;

    GET DIAGNOSTICS count_deleted = ROW_COUNT;

    RETURN QUERY SELECT count_deleted;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old audit entries (>180 days)
CREATE OR REPLACE FUNCTION soul_v3.cleanup_token_audit()
RETURNS TABLE (
    deleted_count INTEGER
) AS $$
DECLARE
    count_deleted INTEGER;
BEGIN
    DELETE FROM soul_v3.token_audit
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '180 days';

    GET DIAGNOSTICS count_deleted = ROW_COUNT;

    RETURN QUERY SELECT count_deleted;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- VIEWS FOR OPERATIONAL MONITORING
-- ============================================================================

-- View: Active devices by agent
CREATE OR REPLACE VIEW soul_v3.v_active_devices AS
SELECT
    device_id,
    device_fingerprint,
    agent,
    status,
    registered_at,
    last_seen_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen_at))::INTEGER AS seconds_since_seen
FROM soul_v3.authorized_devices
WHERE status = 'active';

-- View: Revocation history (last 7 days)
CREATE OR REPLACE VIEW soul_v3.v_recent_revocations AS
SELECT
    rt.token_jti,
    rt.device_id,
    rt.agent,
    rt.reason,
    rt.revoked_by,
    rt.revoked_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - rt.revoked_at))::INTEGER AS seconds_ago
FROM soul_v3.revoked_tokens rt
WHERE rt.revoked_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
ORDER BY rt.revoked_at DESC;

-- View: Failed validation attempts (last 24h)
CREATE OR REPLACE VIEW soul_v3.v_failed_validations AS
SELECT
    ta.device_id,
    ta.agent,
    ta.reason,
    ta.ip_address,
    ta.created_at,
    COUNT(*) AS attempt_count
FROM soul_v3.token_audit ta
WHERE
    ta.action LIKE 'validated_fail'
    AND ta.created_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'
GROUP BY ta.device_id, ta.agent, ta.reason, ta.ip_address, ta.created_at
ORDER BY ta.created_at DESC;

-- ============================================================================
-- GRANTS (example, adjust per your security model)
-- ============================================================================

-- Create restricted app role (for MCP server)
CREATE ROLE soul_app NOSUPERUSER NOBYPASSRLS NOINHERIT;

-- Grant minimal permissions to app role
GRANT USAGE ON SCHEMA soul_v3 TO soul_app;
GRANT SELECT ON soul_v3.authorized_devices TO soul_app;
GRANT SELECT ON soul_v3.revoked_tokens TO soul_app;
GRANT INSERT ON soul_v3.token_audit TO soul_app;
GRANT SELECT ON soul_v3.pending_device_registrations TO soul_app;

-- Grant function usage
GRANT EXECUTE ON FUNCTION soul_v3.set_security_context TO soul_app;
GRANT EXECUTE ON FUNCTION soul_v3.get_device_status TO soul_app;
GRANT EXECUTE ON FUNCTION soul_v3.is_token_revoked TO soul_app;

-- Create admin role (for SOUL operators)
CREATE ROLE soul_admin NOSUPERUSER NOBYPASSRLS INHERIT;
GRANT ALL PRIVILEGES ON SCHEMA soul_v3 TO soul_admin;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA soul_v3 TO soul_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA soul_v3 TO soul_admin;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA soul_v3 TO soul_admin;

-- ============================================================================
-- FINAL CHECKS
-- ============================================================================

-- Verify tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'soul_v3'
  AND table_name IN ('authorized_devices', 'revoked_tokens', 'token_audit', 'pending_device_registrations')
ORDER BY table_name;

-- Verify RLS is enabled
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'soul_v3' AND tablename IN ('authorized_devices', 'revoked_tokens', 'token_audit');

-- ============================================================================
-- END DDL
-- ============================================================================
