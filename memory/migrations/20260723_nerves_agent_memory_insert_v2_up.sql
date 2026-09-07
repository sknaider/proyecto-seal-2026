-- Permit each least-privilege NERVES login to store only its own memory.
-- The existing RESTRICTIVE nerves_hard_session_identity policy still composes
-- with this PERMISSIVE policy, so both agent checks must pass.
BEGIN;

DO $preflight$
DECLARE
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'svc_soul_nerves_ada',
    'svc_soul_nerves_alice',
    'svc_soul_nerves_dum',
    'svc_soul_nerves_jarvis',
    'svc_soul_nerves_nexus'
  ]
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      RAISE EXCEPTION 'required NERVES role missing: %', role_name;
    END IF;
  END LOOP;

  IF to_regprocedure('soul_v3.nerves_session_agent()') IS NULL THEN
    RAISE EXCEPTION 'required resolver missing: soul_v3.nerves_session_agent()';
  END IF;
END
$preflight$;

DO $policy$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'soul_v3'
      AND tablename = 'memories'
      AND policyname = 'nerves_agent_memory_insert_v2'
  ) THEN
    CREATE POLICY nerves_agent_memory_insert_v2
      ON soul_v3.memories
      AS PERMISSIVE
      FOR INSERT
      TO
        svc_soul_nerves_ada,
        svc_soul_nerves_alice,
        svc_soul_nerves_dum,
        svc_soul_nerves_jarvis,
        svc_soul_nerves_nexus
      WITH CHECK (
        tenant_id = '00000000-0000-0000-0000-000000000000'::uuid
        AND agent = soul_v3.nerves_session_agent()
        AND COALESCE(scope, 'private') IN ('private', 'team')
      );
  END IF;
END
$policy$;

COMMIT;
