-- ADA bridge auxiliary SOUL identity clamp.
-- Authorized by William in dm:ada:william #116919 (second DM repair).
-- The legacy permissive app.agent policies remain for compatibility, but this
-- RESTRICTIVE layer makes the authenticated session_user authoritative.

BEGIN;

DO $preflight$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(name, ', ' ORDER BY name)
      INTO missing
      FROM (VALUES
        ('login_ada_bridge'),
        ('pr_ada_bridge')
      ) AS required(name)
     WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = required.name);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing required ADA bridge roles: %', missing;
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name)
      INTO missing
      FROM (VALUES
        ('working_state'),
        ('emotional_diary'),
        ('identity')
      ) AS required(name)
     WHERE to_regclass('soul_v3.' || required.name) IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing required SOUL tables: %', missing;
    END IF;
END
$preflight$;

CREATE OR REPLACE FUNCTION soul_v3.ada_bridge_session_agent()
RETURNS text
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $function$
    SELECT CASE session_user
             WHEN 'login_ada_bridge' THEN 'ADA'::text
             ELSE NULL::text
           END
$function$;

REVOKE ALL ON FUNCTION soul_v3.ada_bridge_session_agent() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.ada_bridge_session_agent() TO pr_ada_bridge;

DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.working_state;
CREATE POLICY ada_bridge_hard_session_identity
ON soul_v3.working_state
AS RESTRICTIVE
FOR SELECT
TO pr_ada_bridge
USING (agent::text = soul_v3.ada_bridge_session_agent());

DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.emotional_diary;
CREATE POLICY ada_bridge_hard_session_identity
ON soul_v3.emotional_diary
AS RESTRICTIVE
FOR SELECT
TO pr_ada_bridge
USING (agent::text = soul_v3.ada_bridge_session_agent());

DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.identity;
CREATE POLICY ada_bridge_hard_session_identity
ON soul_v3.identity
AS RESTRICTIVE
FOR SELECT
TO pr_ada_bridge
USING (agent::text = soul_v3.ada_bridge_session_agent());

COMMIT;
