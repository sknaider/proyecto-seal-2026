-- SOUL Memory SDK: hard tenant identity derived from current_user.
--
-- Deployment contract:
--   1. Resolve the raw API key to its SHA-256 hash server-side.
--   2. Call soul_v3.sdk_resolve_tenant_role_for_key_hash(hash, viewer).
--   3. SET LOCAL ROLE to the returned tenant-specific NOLOGIN role.
--   4. RLS derives tenant identity from current_user. app.tenant_id is not an
--      authority for the restrictive tenant boundary.
--
-- This migration is idempotent and provisions the live SEAL internal tenant.
-- It intentionally does not alter API-key lifecycle records.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $do$
DECLARE
  required_role text;
BEGIN
  FOREACH required_role IN ARRAY ARRAY[
    'svc_soul_memory_sdk',
    'soul_sdk_agent_api',
    'soul_sdk_tenant_api',
    'soul_sdk_runtime'
  ]
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = required_role) THEN
      RAISE EXCEPTION 'required SDK role is missing: %', required_role
        USING ERRCODE = '42704';
    END IF;
  END LOOP;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'soul_sdk_tenant_bound') THEN
    CREATE ROLE soul_sdk_tenant_bound NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;
  ALTER ROLE soul_sdk_tenant_bound NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS NOINHERIT;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'soul_sdk_tenant_resolver') THEN
    CREATE ROLE soul_sdk_tenant_resolver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;
  ALTER ROLE soul_sdk_tenant_resolver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS NOINHERIT;
END
$do$;

CREATE TABLE IF NOT EXISTS soul_v3.sdk_tenant_role_bindings (
  db_role name PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES soul_v3.tenants(id) ON DELETE RESTRICT,
  viewer text NOT NULL CHECK (viewer IN ('agent', 'user')),
  binding_kind text NOT NULL DEFAULT 'tenant'
    CHECK (binding_kind IN ('tenant', 'legacy_internal')),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by name NOT NULL DEFAULT session_user,
  disabled_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS sdk_tenant_role_one_active_viewer_idx
ON soul_v3.sdk_tenant_role_bindings (tenant_id, viewer)
WHERE binding_kind = 'tenant' AND disabled_at IS NULL;

ALTER TABLE soul_v3.sdk_tenant_role_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.sdk_tenant_role_bindings FORCE ROW LEVEL SECURITY;
REVOKE ALL ON soul_v3.sdk_tenant_role_bindings FROM PUBLIC;
GRANT SELECT ON soul_v3.sdk_tenant_role_bindings TO
  soul_sdk_tenant_bound,
  soul_sdk_agent_api,
  soul_sdk_tenant_api,
  soul_sdk_runtime,
  soul_sdk_tenant_resolver;

DROP POLICY IF EXISTS sdk_tenant_binding_self_read
  ON soul_v3.sdk_tenant_role_bindings;
CREATE POLICY sdk_tenant_binding_self_read
ON soul_v3.sdk_tenant_role_bindings
FOR SELECT
TO soul_sdk_tenant_bound, soul_sdk_agent_api, soul_sdk_tenant_api, soul_sdk_runtime
USING (db_role = current_user::name AND disabled_at IS NULL);

DROP POLICY IF EXISTS sdk_tenant_binding_resolver_read
  ON soul_v3.sdk_tenant_role_bindings;
CREATE POLICY sdk_tenant_binding_resolver_read
ON soul_v3.sdk_tenant_role_bindings
FOR SELECT
TO soul_sdk_tenant_resolver
USING (binding_kind = 'tenant' AND disabled_at IS NULL);

CREATE OR REPLACE FUNCTION soul_v3.sdk_current_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = ''
AS $function$
  SELECT binding.tenant_id
  FROM soul_v3.sdk_tenant_role_bindings AS binding
  WHERE binding.db_role = current_user::name
    AND binding.disabled_at IS NULL
  LIMIT 1
$function$;

REVOKE ALL ON FUNCTION soul_v3.sdk_current_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.sdk_current_tenant_id() TO
  soul_sdk_tenant_bound,
  soul_sdk_agent_api,
  soul_sdk_tenant_api,
  soul_sdk_runtime;

CREATE OR REPLACE FUNCTION soul_v3.provision_sdk_tenant_roles(
  p_tenant_id uuid,
  p_login_role name DEFAULT 'svc_soul_memory_sdk'::name
)
RETURNS TABLE(resolved_viewer text, resolved_db_role name)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $function$
#variable_conflict error
DECLARE
  v_viewer text;
  v_parent_role name;
  v_role_name name;
  v_existing_tenant uuid;
  v_existing_viewer text;
  v_existing_kind text;
  v_login_elevated boolean;
  v_rows integer;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM soul_v3.tenants AS tenant WHERE tenant.id = p_tenant_id
  ) THEN
    RAISE EXCEPTION 'unknown SDK tenant: %', p_tenant_id
      USING ERRCODE = '23503';
  END IF;

  SELECT role.rolsuper OR role.rolbypassrls
    INTO v_login_elevated
  FROM pg_catalog.pg_roles AS role
  WHERE role.rolname = p_login_role::text
    AND role.rolcanlogin;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'SDK login role is missing or cannot login: %', p_login_role
      USING ERRCODE = '42704';
  END IF;
  IF v_login_elevated THEN
    RAISE EXCEPTION 'refusing elevated SDK login role: %', p_login_role
      USING ERRCODE = '42501';
  END IF;

  FOR v_viewer, v_parent_role IN
    SELECT item.viewer, item.parent_role::name
    FROM (VALUES
      ('agent'::text, 'soul_sdk_agent_api'::text),
      ('user'::text, 'soul_sdk_tenant_api'::text)
    ) AS item(viewer, parent_role)
  LOOP
    v_role_name := format(
      'soul_sdk_t_%s_%s',
      replace(p_tenant_id::text, '-', ''),
      v_viewer
    )::name;

    SELECT binding.tenant_id, binding.viewer, binding.binding_kind
      INTO v_existing_tenant, v_existing_viewer, v_existing_kind
    FROM soul_v3.sdk_tenant_role_bindings AS binding
    WHERE binding.db_role = v_role_name;

    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS role WHERE role.rolname = v_role_name::text)
       AND (
         v_existing_tenant IS NULL
         OR v_existing_tenant <> p_tenant_id
         OR v_existing_viewer <> v_viewer
         OR v_existing_kind <> 'tenant'
       ) THEN
      RAISE EXCEPTION 'tenant role name collision: %', v_role_name
        USING ERRCODE = '42710';
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_roles AS role WHERE role.rolname = v_role_name::text
    ) THEN
      EXECUTE format(
        'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        'NOREPLICATION NOBYPASSRLS INHERIT',
        v_role_name
      );
    END IF;

    EXECUTE format(
      'ALTER ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
      'NOREPLICATION NOBYPASSRLS INHERIT',
      v_role_name
    );

    INSERT INTO soul_v3.sdk_tenant_role_bindings (
      db_role, tenant_id, viewer, binding_kind, disabled_at
    )
    VALUES (v_role_name, p_tenant_id, v_viewer, 'tenant', NULL)
    ON CONFLICT ON CONSTRAINT sdk_tenant_role_bindings_pkey DO UPDATE
      SET disabled_at = NULL
      WHERE sdk_tenant_role_bindings.tenant_id = EXCLUDED.tenant_id
        AND sdk_tenant_role_bindings.viewer = EXCLUDED.viewer
        AND sdk_tenant_role_bindings.binding_kind = 'tenant';
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 1 THEN
      RAISE EXCEPTION 'tenant role mapping mismatch: %', v_role_name
        USING ERRCODE = '23505';
    END IF;

    EXECUTE format('GRANT soul_sdk_tenant_bound TO %I', v_role_name);
    EXECUTE format('GRANT %I TO %I', v_parent_role, v_role_name);

    IF EXISTS (
      SELECT 1
      FROM pg_catalog.pg_auth_members AS membership
      JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
      JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
      WHERE member.rolname = v_role_name::text
        AND parent.rolname NOT IN ('soul_sdk_tenant_bound', v_parent_role::text)
    ) THEN
      RAISE EXCEPTION 'tenant role has unexpected parent membership: %', v_role_name
        USING ERRCODE = '0LP01';
    END IF;

    EXECUTE format('GRANT %I TO %I', v_role_name, p_login_role);
    resolved_viewer := v_viewer;
    resolved_db_role := v_role_name;
    RETURN NEXT;
  END LOOP;

  -- The authenticated login needs only enough namespace/function privilege to
  -- invoke the SECURITY DEFINER resolver. It does not receive table access or
  -- schema CREATE; data privileges remain behind the resolved tenant roles.
  EXECUTE format('GRANT USAGE ON SCHEMA soul_v3 TO %I', p_login_role);
  IF pg_catalog.to_regprocedure(
    'soul_v3.sdk_resolve_tenant_role_for_key_hash(text,text)'
  ) IS NOT NULL THEN
    EXECUTE format(
      'GRANT EXECUTE ON FUNCTION '
      'soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text) TO %I',
      p_login_role
    );
  END IF;
END
$function$;

REVOKE ALL ON FUNCTION soul_v3.provision_sdk_tenant_roles(uuid, name) FROM PUBLIC;

-- Provision the only live tenant without changing its API-key records.
SELECT *
FROM soul_v3.provision_sdk_tenant_roles(
  '00000000-0000-0000-0000-000000000000'::uuid,
  'svc_soul_memory_sdk'::name
);

-- Compatibility bindings keep the existing internal-only role path working
-- during the coordinated API cutover. They can never resolve external tenants.
INSERT INTO soul_v3.sdk_tenant_role_bindings (
  db_role, tenant_id, viewer, binding_kind, disabled_at
)
VALUES
  ('soul_sdk_agent_api'::name, '00000000-0000-0000-0000-000000000000'::uuid,
   'agent', 'legacy_internal', NULL),
  ('soul_sdk_tenant_api'::name, '00000000-0000-0000-0000-000000000000'::uuid,
   'user', 'legacy_internal', NULL),
  ('soul_sdk_runtime'::name, '00000000-0000-0000-0000-000000000000'::uuid,
   'agent', 'legacy_internal', NULL)
ON CONFLICT (db_role) DO NOTHING;

DO $do$
DECLARE
  role_name name;
  expected_viewer text;
BEGIN
  FOR role_name, expected_viewer IN
    SELECT item.role_name::name, item.viewer
    FROM (VALUES
      ('soul_sdk_agent_api'::text, 'agent'::text),
      ('soul_sdk_tenant_api'::text, 'user'::text),
      ('soul_sdk_runtime'::text, 'agent'::text)
    ) AS item(role_name, viewer)
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM soul_v3.sdk_tenant_role_bindings AS binding
      WHERE binding.db_role = role_name
        AND binding.tenant_id = '00000000-0000-0000-0000-000000000000'::uuid
        AND binding.viewer = expected_viewer
        AND binding.binding_kind = 'legacy_internal'
        AND binding.disabled_at IS NULL
    ) THEN
      RAISE EXCEPTION 'legacy SDK role mapping mismatch: %', role_name
        USING ERRCODE = '23505';
    END IF;
  END LOOP;
END
$do$;

GRANT USAGE ON SCHEMA soul_v3 TO soul_sdk_tenant_resolver;
GRANT SELECT (id, api_keys) ON soul_v3.tenants TO soul_sdk_tenant_resolver;

CREATE OR REPLACE FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash(
  p_key_hash text,
  p_viewer text DEFAULT 'agent'
)
RETURNS TABLE(tenant_id uuid, db_role name)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $function$
#variable_conflict error
BEGIN
  IF p_key_hash IS NULL OR p_key_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;
  IF p_viewer NOT IN ('agent', 'user') THEN
    RETURN;
  END IF;

  RETURN QUERY
  WITH matches AS (
    SELECT tenant.id AS tenant_id, binding.db_role
    FROM soul_v3.tenants AS tenant
    CROSS JOIN LATERAL jsonb_array_elements(tenant.api_keys) AS key_record
    JOIN soul_v3.sdk_tenant_role_bindings AS binding
      ON binding.tenant_id = tenant.id
     AND binding.viewer = p_viewer
     AND binding.binding_kind = 'tenant'
     AND binding.disabled_at IS NULL
    JOIN pg_catalog.pg_roles AS tenant_role
      ON tenant_role.rolname = binding.db_role::text
     AND tenant_role.rolcanlogin IS FALSE
     AND tenant_role.rolsuper IS FALSE
     AND tenant_role.rolbypassrls IS FALSE
     AND tenant_role.rolinherit IS TRUE
    JOIN pg_catalog.pg_auth_members AS boundary_membership
      ON boundary_membership.member = tenant_role.oid
     AND boundary_membership.inherit_option IS TRUE
    JOIN pg_catalog.pg_roles AS boundary_role
      ON boundary_role.oid = boundary_membership.roleid
     AND boundary_role.rolname = 'soul_sdk_tenant_bound'
    JOIN pg_catalog.pg_auth_members AS viewer_membership
      ON viewer_membership.member = tenant_role.oid
     AND viewer_membership.inherit_option IS TRUE
    JOIN pg_catalog.pg_roles AS viewer_role
      ON viewer_role.oid = viewer_membership.roleid
     AND viewer_role.rolname = CASE binding.viewer
       WHEN 'agent' THEN 'soul_sdk_agent_api'
       WHEN 'user' THEN 'soul_sdk_tenant_api'
     END
    JOIN pg_catalog.pg_roles AS login_role
      ON login_role.rolname = session_user
     AND login_role.rolcanlogin IS TRUE
     AND login_role.rolsuper IS FALSE
     AND login_role.rolbypassrls IS FALSE
    JOIN pg_catalog.pg_auth_members AS login_membership
      ON login_membership.roleid = tenant_role.oid
     AND login_membership.member = login_role.oid
     AND login_membership.set_option IS TRUE
    WHERE COALESCE(
      key_record->>'sha256',
      key_record->>'hash',
      key_record->>'key_hash'
    ) = p_key_hash
      AND tenant_role.rolname = format(
        'soul_sdk_t_%s_%s',
        replace(tenant.id::text, '-', ''),
        binding.viewer
      )
      AND NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members AS unexpected_membership
        JOIN pg_catalog.pg_roles AS unexpected_parent
          ON unexpected_parent.oid = unexpected_membership.roleid
        WHERE unexpected_membership.member = tenant_role.oid
          AND unexpected_parent.rolname NOT IN (
            'soul_sdk_tenant_bound',
            CASE binding.viewer
              WHEN 'agent' THEN 'soul_sdk_agent_api'
              WHEN 'user' THEN 'soul_sdk_tenant_api'
            END
          )
      )
      AND COALESCE(key_record->>'revoked_at', '') = ''
      AND CASE
        WHEN COALESCE(key_record->>'expires_at', '') = '' THEN TRUE
        WHEN pg_catalog.pg_input_is_valid(
          key_record->>'expires_at',
          'timestamp with time zone'
        ) THEN (key_record->>'expires_at')::timestamptz > CURRENT_TIMESTAMP
        ELSE FALSE
      END
  )
  SELECT min(matches.tenant_id::text)::uuid,
         min(matches.db_role::text)::name
  FROM matches
  HAVING count(*) = 1;
END
$function$;

ALTER FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text)
  OWNER TO soul_sdk_tenant_resolver;
REVOKE ALL ON FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text)
  FROM PUBLIC;
GRANT USAGE ON SCHEMA soul_v3 TO svc_soul_memory_sdk;
GRANT EXECUTE ON FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text)
  TO svc_soul_memory_sdk;

-- Restrictive policies are AND-combined with existing permissive capability
-- policies. For SDK identities, tenant authority now comes only from the
-- active current_user binding. An unmapped role therefore sees/writes zero rows.
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
    EXECUTE format('ALTER TABLE soul_v3.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE soul_v3.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
      'DROP POLICY IF EXISTS sdk_hard_tenant_identity_v1 ON soul_v3.%I',
      table_name
    );
    EXECUTE format(
      'CREATE POLICY sdk_hard_tenant_identity_v1 ON soul_v3.%I '
      'AS RESTRICTIVE FOR ALL '
      'TO soul_sdk_tenant_bound, soul_sdk_agent_api, soul_sdk_tenant_api, soul_sdk_runtime '
      'USING (tenant_id = soul_v3.sdk_current_tenant_id()) '
      'WITH CHECK (tenant_id = soul_v3.sdk_current_tenant_id())',
      table_name
    );
  END LOOP;
END
$do$;

-- Tenant-role capability policies omit app.tenant_id. The restrictive policy
-- above supplies the tenant predicate; agent/viewer GUCs retain their narrower
-- authorization purposes and cannot select another tenant.
DROP POLICY IF EXISTS sdk_tenant_role_agent_read ON soul_v3.memories;
CREATE POLICY sdk_tenant_role_agent_read
ON soul_v3.memories
FOR SELECT
TO soul_sdk_agent_api
USING (
  agent = NULLIF(current_setting('app.agent', true), '')
  OR COALESCE(scope, 'private') IN ('team', 'shared', 'public')
);

DROP POLICY IF EXISTS sdk_tenant_role_agent_insert ON soul_v3.memories;
CREATE POLICY sdk_tenant_role_agent_insert
ON soul_v3.memories
FOR INSERT
TO soul_sdk_agent_api
WITH CHECK (
  agent = NULLIF(current_setting('app.agent', true), '')
  AND COALESCE(scope, 'private') IN ('private', 'team')
);

DROP POLICY IF EXISTS sdk_tenant_role_user_read ON soul_v3.memories;
CREATE POLICY sdk_tenant_role_user_read
ON soul_v3.memories
FOR SELECT
TO soul_sdk_tenant_api
USING (
  NULLIF(current_setting('app.viewer', true), '') = 'user'
  AND NULLIF(current_setting('app.user_id', true), '') IS NOT NULL
  AND COALESCE(scope, 'private') IN ('team', 'shared', 'public')
);

DROP POLICY IF EXISTS sdk_tenant_role_retrieval_log_insert
  ON soul_v3.memory_retrieval_log;
CREATE POLICY sdk_tenant_role_retrieval_log_insert
ON soul_v3.memory_retrieval_log
FOR INSERT
TO soul_sdk_agent_api
WITH CHECK (
  agent_requesting = COALESCE(
    NULLIF(current_setting('app.agent', true), ''),
    'sdk_api'
  )
);

COMMIT;
