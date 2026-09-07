-- Emergency rollback for 20260727_rules_acl_hardening_ADA.sql.
-- This intentionally restores the legacy broad grants and must not be used as
-- a normal operating state.

BEGIN;

LOCK TABLE soul_v3.rules IN SHARE ROW EXCLUSIVE MODE;

DROP POLICY IF EXISTS rules_soul_admin_all ON soul_v3.rules;
DROP POLICY IF EXISTS rules_explicit_readers ON soul_v3.rules;

DROP POLICY IF EXISTS mcp_legacy_compat_all ON soul_v3.rules;
CREATE POLICY mcp_legacy_compat_all
ON soul_v3.rules
FOR ALL
TO PUBLIC
USING (true)
WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE
ON soul_v3.rules
TO soul_sdk_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE seal IN SCHEMA soul_v3
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO soul_sdk_runtime;

CREATE OR REPLACE VIEW soul_v3.stability_guard_autonomy_rules_v
WITH (security_barrier = true) AS
    SELECT id, content, active
    FROM soul_v3.rules
    WHERE id IN (42, 73);

GRANT SELECT, INSERT, UPDATE, DELETE
ON soul_v3.stability_guard_autonomy_rules_v
TO soul_sdk_runtime;
GRANT SELECT
ON soul_v3.stability_guard_autonomy_rules_v
TO svc_soul_stability_guard;

COMMIT;
