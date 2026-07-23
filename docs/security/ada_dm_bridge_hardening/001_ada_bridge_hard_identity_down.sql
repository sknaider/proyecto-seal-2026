-- Roll back only the incremental hard-identity layer introduced by UP.
-- Productive baseline already has ada_bridge_session_agent() plus three
-- ada_bridge_hard_session_identity policies; restore them exactly.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DROP POLICY IF EXISTS ada_bridge_hard_chat_scope_v1 ON soul_v3.chat_messages;

DO $do$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'identity',
    'working_state',
    'emotional_diary',
    'working_state_events',
    'harness_oracle',
    'inner_monologue',
    'memories'
  ]
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS ada_bridge_hard_agent_identity_v1 ON soul_v3.%I',
      table_name
    );
  END LOOP;
END
$do$;

-- Restore the exact pre-migration grant. A later production migration may
-- choose to remove it permanently after confirming no consumer uses it.
GRANT SELECT ON soul_v3.memory_poisoning_feedback TO pr_ada_bridge;

-- Restore the pre-UP invoker function in place. CREATE OR REPLACE preserves
-- dependent baseline policies; dropping it would fail and would be incorrect.
CREATE OR REPLACE FUNCTION soul_v3.ada_bridge_session_agent()
RETURNS text
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = 'pg_catalog'
AS $function$
  SELECT CASE session_user
           WHEN 'login_ada_bridge' THEN 'ADA'::text
           ELSE NULL::text
         END
$function$;

ALTER FUNCTION soul_v3.ada_bridge_session_agent() OWNER TO seal;
REVOKE ALL ON FUNCTION soul_v3.ada_bridge_session_agent()
  FROM PUBLIC, login_ada_bridge, pr_ada_bridge, ada_bridge_identity_owner;
GRANT EXECUTE ON FUNCTION soul_v3.ada_bridge_session_agent() TO pr_ada_bridge;

DROP FUNCTION IF EXISTS soul_v3.ada_bridge_session_tenant_id();
DROP TABLE IF EXISTS soul_v3.ada_bridge_session_bindings;

ALTER ROLE login_ada_bridge RESET app.tenant_id;
ALTER ROLE login_ada_bridge SET app.agent = 'ADA';

-- Deliberately keep ada_bridge_identity_owner. A rollback cannot know whether
-- a same-named safe role predated this migration; retaining an inert NOLOGIN
-- role avoids destroying pre-existing security state. Re-applying UP reuses
-- it only after validating every safety attribute and zero memberships.

COMMIT;
