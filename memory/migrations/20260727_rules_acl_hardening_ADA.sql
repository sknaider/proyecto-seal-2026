-- Close legacy PUBLIC RLS and inherited SDK DML on soul_v3.rules.
--
-- This migration changes authorization only; it does not modify rule rows.
-- Rollback: 20260727_rules_acl_hardening_ADA_down.sql

BEGIN;

LOCK TABLE soul_v3.rules IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        WHERE c.oid = 'soul_v3.rules'::regclass
          AND c.relrowsecurity
    ) THEN
        RAISE EXCEPTION 'soul_v3.rules must have RLS enabled';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'soul_v3'
          AND tablename = 'rules'
          AND policyname = 'mcp_legacy_compat_all'
          AND permissive = 'PERMISSIVE'
          AND cmd = 'ALL'
          AND roles = ARRAY['public']::name[]
          AND regexp_replace(coalesce(qual, ''), '[()[:space:]]', '', 'g') = 'true'
          AND regexp_replace(coalesce(with_check, ''), '[()[:space:]]', '', 'g') = 'true'
    ) THEN
        RAISE EXCEPTION 'legacy rules policy drifted; refusing blind rewrite';
    END IF;

    IF to_regclass('soul_v3.stability_guard_autonomy_rules_v') IS NULL THEN
        RAISE EXCEPTION 'stability_guard_autonomy_rules_v is missing';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_roles
        WHERE rolname IN (
            'soul_sdk_runtime', 'pr_consolidate', 'pr_mcp_base',
            'pr_retrieval', 'soul_admin', 'svc_soul_stability_guard'
        )
    ) <> 6 THEN
        RAISE EXCEPTION 'one or more required rules roles are missing';
    END IF;
END
$preflight$;

DROP POLICY mcp_legacy_compat_all ON soul_v3.rules;

DROP POLICY IF EXISTS rules_explicit_readers ON soul_v3.rules;
CREATE POLICY rules_explicit_readers
ON soul_v3.rules
FOR SELECT
TO soul_sdk_runtime, pr_consolidate, pr_mcp_base, pr_retrieval
USING (true);

DROP POLICY IF EXISTS rules_soul_admin_all ON soul_v3.rules;
CREATE POLICY rules_soul_admin_all
ON soul_v3.rules
FOR ALL
TO soul_admin
USING (true)
WITH CHECK (true);

REVOKE INSERT, UPDATE, DELETE
ON soul_v3.rules
FROM soul_sdk_runtime;

-- Views are tables for ALTER DEFAULT PRIVILEGES.  Keep SELECT compatibility
-- for future SDK objects, but stop granting future mutation rights by default.
ALTER DEFAULT PRIVILEGES FOR ROLE seal IN SCHEMA soul_v3
REVOKE INSERT, UPDATE, DELETE ON TABLES FROM soul_sdk_runtime;

-- DISTINCT makes the narrow stability surface structurally non-updatable.
-- Explicit ACL cleanup is still required because CREATE OR REPLACE preserves
-- grants that the old default ACL attached when the view was first created.
CREATE OR REPLACE VIEW soul_v3.stability_guard_autonomy_rules_v
WITH (security_barrier = true) AS
    SELECT DISTINCT id, content, active
    FROM soul_v3.rules
    WHERE id IN (42, 73);

REVOKE ALL
ON soul_v3.stability_guard_autonomy_rules_v
FROM PUBLIC, soul_sdk_runtime;
GRANT SELECT
ON soul_v3.stability_guard_autonomy_rules_v
TO svc_soul_stability_guard;

COMMENT ON POLICY rules_explicit_readers ON soul_v3.rules IS
    'Explicit non-MCP readers. Replaces legacy FOR ALL TO PUBLIC policy.';
COMMENT ON POLICY rules_soul_admin_all ON soul_v3.rules IS
    'Administrative rule mutation path; application SDK receives no DML.';
COMMENT ON VIEW soul_v3.stability_guard_autonomy_rules_v IS
    'Read-only security-barrier projection of autonomy rules 42/73 for Stability Guard.';

COMMIT;
