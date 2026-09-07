-- SECURITY DOWNGRADE: restores the SDK's former GUC-only tenant boundary.
-- Run only after William confirms the exact rollback scope.
-- This rollback refuses to continue if any external tenant role was provisioned.

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $do$
DECLARE
  external_binding_count bigint;
BEGIN
  SELECT count(*)
    INTO external_binding_count
  FROM soul_v3.sdk_tenant_role_bindings
  WHERE binding_kind = 'tenant'
    AND tenant_id <> '00000000-0000-0000-0000-000000000000'::uuid;

  IF external_binding_count <> 0 THEN
    RAISE EXCEPTION
      'rollback blocked: % external tenant role binding(s) require explicit scope',
      external_binding_count
      USING ERRCODE = '55000';
  END IF;
END
$do$;

DO $do$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'memories',
    'inner_monologue',
    'distilled_exchanges',
    'session_memory',
    'memories_archive',
    'memory_retrieval_log'
  ]
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS sdk_hard_tenant_identity_v1 ON soul_v3.%I',
      table_name
    );
  END LOOP;
END
$do$;

DROP POLICY IF EXISTS sdk_tenant_role_agent_read ON soul_v3.memories;
DROP POLICY IF EXISTS sdk_tenant_role_agent_insert ON soul_v3.memories;
DROP POLICY IF EXISTS sdk_tenant_role_user_read ON soul_v3.memories;
DROP POLICY IF EXISTS sdk_tenant_role_retrieval_log_insert
  ON soul_v3.memory_retrieval_log;

DROP FUNCTION IF EXISTS soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text);
DROP FUNCTION IF EXISTS soul_v3.provision_sdk_tenant_roles(uuid, name);
DROP FUNCTION IF EXISTS soul_v3.sdk_current_tenant_id();

DO $do$
DECLARE
  role_name name;
BEGIN
  FOR role_name IN
    SELECT binding.db_role
    FROM soul_v3.sdk_tenant_role_bindings AS binding
    WHERE binding.binding_kind = 'tenant'
      AND binding.tenant_id = '00000000-0000-0000-0000-000000000000'::uuid
  LOOP
    EXECUTE format('REVOKE %I FROM svc_soul_memory_sdk', role_name);
    EXECUTE format('REVOKE soul_sdk_tenant_bound FROM %I', role_name);
    EXECUTE format('REVOKE soul_sdk_agent_api FROM %I', role_name);
    EXECUTE format('REVOKE soul_sdk_tenant_api FROM %I', role_name);
    EXECUTE format('DROP ROLE %I', role_name);
  END LOOP;
END
$do$;

DROP TABLE soul_v3.sdk_tenant_role_bindings;
REVOKE SELECT (id, api_keys) ON soul_v3.tenants FROM soul_sdk_tenant_resolver;
REVOKE USAGE ON SCHEMA soul_v3 FROM soul_sdk_tenant_resolver;
DROP ROLE soul_sdk_tenant_resolver;
DROP ROLE soul_sdk_tenant_bound;

COMMIT;
