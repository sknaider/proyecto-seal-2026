-- ADA DM bridge: hard database identity derived from session_user.
--
-- This migration intentionally leaves the existing permissive
-- ada_bridge_chat_read policy in place and adds a restrictive scope policy.
-- Existing app.agent/app.tenant_id GUCs remain compatibility selectors; they
-- are no longer authorities for the hard boundary.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $do$
DECLARE
  role_is_unsafe boolean;
  has_bridge_membership boolean;
  unexpected_memberships text[];
BEGIN
  SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication,
         rolcanlogin
    INTO role_is_unsafe, has_bridge_membership
  FROM pg_catalog.pg_roles
  WHERE rolname = 'login_ada_bridge';

  IF NOT FOUND OR has_bridge_membership IS NOT TRUE THEN
    RAISE EXCEPTION 'required LOGIN role login_ada_bridge is missing'
      USING ERRCODE = '42704';
  END IF;
  IF role_is_unsafe THEN
    RAISE EXCEPTION 'refusing elevated login_ada_bridge role'
      USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = 'login_ada_bridge'
      AND parent.rolname = 'pr_ada_bridge'
  ) THEN
    RAISE EXCEPTION 'login_ada_bridge must inherit pr_ada_bridge'
      USING ERRCODE = '42501';
  END IF;

  SELECT array_agg(parent.rolname ORDER BY parent.rolname)
    INTO unexpected_memberships
  FROM pg_catalog.pg_auth_members AS membership
  JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
  JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
  WHERE member.rolname = 'login_ada_bridge'
    AND parent.rolname <> 'pr_ada_bridge';

  IF unexpected_memberships IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected login_ada_bridge memberships: %', unexpected_memberships
      USING ERRCODE = '42501';
  END IF;
END
$do$;

-- SECURITY DEFINER must never run as the database superuser. This owner has
-- no login, no inheritance and no role memberships; it owns only the binding
-- table and the two narrow lookup functions created below.
DO $do$
DECLARE
  owner_is_unsafe boolean;
  owner_inherits boolean;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'ada_bridge_identity_owner'
  ) THEN
    CREATE ROLE ada_bridge_identity_owner
      NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;

  SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole
           OR rolcreatedb OR rolreplication,
         rolinherit
    INTO owner_is_unsafe, owner_inherits
  FROM pg_catalog.pg_roles
  WHERE rolname = 'ada_bridge_identity_owner';

  IF owner_is_unsafe OR owner_inherits THEN
    RAISE EXCEPTION 'ada_bridge_identity_owner is not a safe dedicated owner'
      USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = 'ada_bridge_identity_owner'
  ) THEN
    RAISE EXCEPTION 'ada_bridge_identity_owner must not inherit memberships'
      USING ERRCODE = '42501';
  END IF;
END
$do$;

GRANT USAGE ON SCHEMA soul_v3 TO ada_bridge_identity_owner;

CREATE TABLE IF NOT EXISTS soul_v3.ada_bridge_session_bindings (
  db_role name PRIMARY KEY,
  agent text NOT NULL,
  tenant_id uuid NOT NULL,
  disabled_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by name NOT NULL DEFAULT session_user,
  CONSTRAINT ada_bridge_binding_login CHECK (db_role::text = 'login_ada_bridge'),
  CONSTRAINT ada_bridge_binding_agent CHECK (agent = 'ADA')
);

ALTER TABLE soul_v3.ada_bridge_session_bindings
  OWNER TO ada_bridge_identity_owner;
REVOKE ALL ON soul_v3.ada_bridge_session_bindings FROM PUBLIC;
REVOKE ALL ON soul_v3.ada_bridge_session_bindings FROM login_ada_bridge, pr_ada_bridge;

DO $do$
DECLARE
  resolved_tenant uuid;
  tenant_count integer;
BEGIN
  SELECT (array_agg(DISTINCT tenant_id))[1], count(DISTINCT tenant_id)::integer
    INTO resolved_tenant, tenant_count
  FROM soul_v3.memories
  WHERE agent = 'ADA' AND tenant_id IS NOT NULL;

  IF tenant_count <> 1 OR resolved_tenant IS NULL THEN
    RAISE EXCEPTION 'ADA bridge tenant binding is ambiguous: tenant_count=%', tenant_count
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO soul_v3.ada_bridge_session_bindings (
    db_role, agent, tenant_id, disabled_at
  ) VALUES (
    'login_ada_bridge', 'ADA', resolved_tenant, NULL
  )
  ON CONFLICT (db_role) DO UPDATE
    SET agent = EXCLUDED.agent,
        tenant_id = EXCLUDED.tenant_id,
        disabled_at = NULL;

  -- Compatibility only. Restrictive policies below ignore these GUCs as an
  -- authority and derive the hard identity from session_user.
  EXECUTE format(
    'ALTER ROLE login_ada_bridge SET app.tenant_id = %L',
    resolved_tenant::text
  );
  ALTER ROLE login_ada_bridge SET app.agent = 'ADA';
END
$do$;

CREATE OR REPLACE FUNCTION soul_v3.ada_bridge_session_agent()
RETURNS text
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = ''
AS $function$
  SELECT binding.agent
  FROM soul_v3.ada_bridge_session_bindings AS binding
  WHERE binding.db_role = session_user::name
    AND binding.disabled_at IS NULL
  LIMIT 1
$function$;

ALTER FUNCTION soul_v3.ada_bridge_session_agent()
  OWNER TO ada_bridge_identity_owner;

CREATE OR REPLACE FUNCTION soul_v3.ada_bridge_session_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = ''
AS $function$
  SELECT binding.tenant_id
  FROM soul_v3.ada_bridge_session_bindings AS binding
  WHERE binding.db_role = session_user::name
    AND binding.disabled_at IS NULL
  LIMIT 1
$function$;

ALTER FUNCTION soul_v3.ada_bridge_session_tenant_id()
  OWNER TO ada_bridge_identity_owner;

REVOKE ALL ON FUNCTION soul_v3.ada_bridge_session_agent() FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.ada_bridge_session_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.ada_bridge_session_agent() TO pr_ada_bridge;
GRANT EXECUTE ON FUNCTION soul_v3.ada_bridge_session_tenant_id() TO pr_ada_bridge;

-- The existing permissive policy remains intact. This restrictive policy
-- ensures that no future permissive policy can widen the bridge into another
-- DM channel.
DROP POLICY IF EXISTS ada_bridge_hard_chat_scope_v1 ON soul_v3.chat_messages;
CREATE POLICY ada_bridge_hard_chat_scope_v1
ON soul_v3.chat_messages
AS RESTRICTIVE
FOR SELECT
TO pr_ada_bridge
USING (
  session_user = 'login_ada_bridge'
  AND (
    channel = 'web_chat'
    OR lower(channel) = 'dm:ada:william'
  )
);

DO $do$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'identity',
    'working_state',
    'emotional_diary',
    'working_state_events',
    'harness_oracle'
  ]
  LOOP
    EXECUTE format('ALTER TABLE soul_v3.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
      'DROP POLICY IF EXISTS ada_bridge_hard_agent_identity_v1 ON soul_v3.%I',
      table_name
    );
    EXECUTE format(
      'CREATE POLICY ada_bridge_hard_agent_identity_v1 ON soul_v3.%I '
      'AS RESTRICTIVE FOR ALL TO pr_ada_bridge '
      'USING (agent::text = soul_v3.ada_bridge_session_agent()) '
      'WITH CHECK (agent::text = soul_v3.ada_bridge_session_agent())',
      table_name
    );
  END LOOP;
END
$do$;

DROP POLICY IF EXISTS ada_bridge_hard_agent_identity_v1 ON soul_v3.inner_monologue;
CREATE POLICY ada_bridge_hard_agent_identity_v1
ON soul_v3.inner_monologue
AS RESTRICTIVE
FOR ALL
TO pr_ada_bridge
USING (
  agent::text = soul_v3.ada_bridge_session_agent()
  AND tenant_id = soul_v3.ada_bridge_session_tenant_id()
)
WITH CHECK (
  agent::text = soul_v3.ada_bridge_session_agent()
  AND tenant_id = soul_v3.ada_bridge_session_tenant_id()
);

DROP POLICY IF EXISTS ada_bridge_hard_agent_identity_v1 ON soul_v3.memories;
CREATE POLICY ada_bridge_hard_agent_identity_v1
ON soul_v3.memories
AS RESTRICTIVE
FOR ALL
TO pr_ada_bridge
USING (
  tenant_id = soul_v3.ada_bridge_session_tenant_id()
  AND (
    agent::text = soul_v3.ada_bridge_session_agent()
    OR COALESCE(scope, 'private') IN ('team', 'public')
  )
)
WITH CHECK (
  tenant_id = soul_v3.ada_bridge_session_tenant_id()
  AND agent::text = soul_v3.ada_bridge_session_agent()
);

-- The bridge code does not query this table. Keeping this global detector
-- feed readable would violate least privilege because it has no agent column.
REVOKE SELECT ON soul_v3.memory_poisoning_feedback FROM pr_ada_bridge;

COMMIT;
