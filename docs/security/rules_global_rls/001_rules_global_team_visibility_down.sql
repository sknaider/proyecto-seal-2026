BEGIN;

LOCK TABLE soul_v3.rules IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    required_role text;
    role_row record;
    expected_roles oid[];
    rules_oid oid;
    rules_rls_enabled boolean;
    resolver_oid oid;
    resolver_row record;
    topology "char";
    policy_row record;
    policy_count integer := 0;
    normalized_using text;
    normalized_check text;
BEGIN
    SELECT cls.oid, cls.relrowsecurity
      INTO rules_oid, rules_rls_enabled
      FROM pg_class cls
      JOIN pg_namespace ns ON ns.oid = cls.relnamespace
     WHERE ns.nspname = 'soul_v3' AND cls.relname = 'rules';
    IF rules_oid IS NULL OR NOT rules_rls_enabled THEN
        RAISE EXCEPTION 'soul_v3.rules RLS is missing or disabled';
    END IF;

    SELECT proc.oid,
           proc.prosecdef,
           proc.provolatile,
           proc.prorettype,
           lang.lanname,
           proc.proconfig,
           regexp_replace(proc.prosrc, '[[:space:]]', '', 'g') AS body
      INTO resolver_row
      FROM pg_proc proc
      JOIN pg_namespace ns ON ns.oid = proc.pronamespace
      JOIN pg_language lang ON lang.oid = proc.prolang
     WHERE ns.nspname = 'soul_v3'
       AND proc.proname = 'mcp_session_agent'
       AND proc.pronargs = 0;
    resolver_oid := resolver_row.oid;
    IF resolver_oid IS NULL THEN
        RAISE EXCEPTION 'soul_v3.mcp_session_agent() is missing';
    END IF;
    IF resolver_row.prosecdef
       OR resolver_row.provolatile <> 's'
       OR resolver_row.prorettype <> 'text'::regtype
       OR resolver_row.lanname <> 'sql'
       OR NOT coalesce(resolver_row.proconfig, ARRAY[]::text[])
              @> ARRAY['search_path=pg_catalog']
       OR resolver_row.body <>
          'SELECTCASEsession_userWHEN''mcp_runtime_ada''THEN''ADA''WHEN''mcp_runtime_alice''THEN''ALICE''WHEN''mcp_runtime_dum''THEN''DUM''WHEN''mcp_runtime_jarvis''THEN''JARVIS''WHEN''mcp_runtime_nexus''THEN''NEXUS''ELSENULLEND'
    THEN
        RAISE EXCEPTION
            'mcp_session_agent() definition drifted; refusing rollback';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_proc proc,
               LATERAL aclexplode(
                   coalesce(proc.proacl, acldefault('f', proc.proowner))
               ) acl
         WHERE proc.oid = resolver_oid
           AND acl.grantee = 0
           AND acl.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'PUBLIC EXECUTE remains on mcp_session_agent()';
    END IF;

    FOREACH required_role IN ARRAY ARRAY[
        'mcp_runtime_ada',
        'mcp_runtime_alice',
        'mcp_runtime_dum',
        'mcp_runtime_jarvis',
        'mcp_runtime_nexus'
    ]
    LOOP
        SELECT oid, rolcanlogin, rolsuper, rolbypassrls, rolinherit
          INTO role_row
          FROM pg_roles
         WHERE rolname = required_role;
        IF role_row IS NULL
           OR NOT role_row.rolcanlogin
           OR role_row.rolsuper
           OR role_row.rolbypassrls
           OR role_row.rolinherit
        THEN
            RAISE EXCEPTION
                'unsafe or missing runtime role: %', required_role;
        END IF;
        IF EXISTS (
            SELECT 1
              FROM pg_auth_members
             WHERE member = role_row.oid
        ) THEN
            RAISE EXCEPTION
                'runtime role has unexpected memberships: %', required_role;
        END IF;
        IF NOT has_schema_privilege(required_role, 'soul_v3', 'USAGE')
           OR NOT has_table_privilege(
               required_role, 'soul_v3.rules', 'SELECT'
           )
           OR NOT has_function_privilege(
               required_role, resolver_oid, 'EXECUTE'
           )
        THEN
            RAISE EXCEPTION
                'runtime grant missing for role: %', required_role;
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM pg_policy pol
             WHERE pol.polrelid = rules_oid
               AND pol.polpermissive
               AND pol.polcmd IN ('*', 'r')
               AND (0 = ANY(pol.polroles) OR role_row.oid = ANY(pol.polroles))
        ) THEN
            RAISE EXCEPTION
                'no applicable permissive SELECT policy for role: %',
                required_role;
        END IF;
    END LOOP;

    SELECT ARRAY(
               SELECT oid
                 FROM pg_roles
                WHERE rolname = ANY (ARRAY[
                    'mcp_runtime_ada',
                    'mcp_runtime_alice',
                    'mcp_runtime_dum',
                    'mcp_runtime_jarvis',
                    'mcp_runtime_nexus'
                ])
                ORDER BY oid
           )
      INTO expected_roles;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_policy pol
         WHERE pol.polrelid = rules_oid
           AND pol.polname = 'mcp_login_capability'
           AND pol.polpermissive
           AND pol.polcmd = '*'
           AND (
                SELECT array_agg(role_oid ORDER BY role_oid)
                  FROM unnest(pol.polroles) AS role_oid
               ) = expected_roles
           AND regexp_replace(
               coalesce(pg_get_expr(pol.polqual, pol.polrelid), ''),
               '[()[:space:]]', '', 'g'
           ) = 'true'
           AND regexp_replace(
               coalesce(pg_get_expr(pol.polwithcheck, pol.polrelid), ''),
               '[()[:space:]]', '', 'g'
           ) = 'true'
    ) THEN
        RAISE EXCEPTION
            'mcp_login_capability policy drifted or is missing';
    END IF;

    SELECT pol.polcmd
      INTO topology
      FROM pg_policy pol
     WHERE pol.polrelid = rules_oid
       AND pol.polname = 'mcp_hard_identity';
    IF topology IS NULL OR topology NOT IN ('*', 'r') THEN
        RAISE EXCEPTION 'unexpected mcp_hard_identity topology';
    END IF;

    FOR policy_row IN
        SELECT pol.polname, pol.polcmd, pol.polpermissive, pol.polroles,
               pg_get_expr(pol.polqual, pol.polrelid) AS using_expr,
               pg_get_expr(pol.polwithcheck, pol.polrelid) AS check_expr
          FROM pg_policy pol
         WHERE pol.polrelid = rules_oid
           AND pol.polname IN (
               'mcp_hard_identity',
               'mcp_hard_identity_insert',
               'mcp_hard_identity_update',
               'mcp_hard_identity_delete'
           )
    LOOP
        policy_count := policy_count + 1;
        normalized_using := regexp_replace(
            coalesce(policy_row.using_expr, ''),
            '[()[:space:]]', '', 'g'
        );
        normalized_check := regexp_replace(
            coalesce(policy_row.check_expr, ''),
            '[()[:space:]]', '', 'g'
        );

        IF policy_row.polpermissive
           OR (
                SELECT array_agg(role_oid ORDER BY role_oid)
                  FROM unnest(policy_row.polroles) AS role_oid
              ) <> expected_roles
        THEN
            RAISE EXCEPTION
                'unexpected roles/permissive state for policy %',
                policy_row.polname;
        END IF;

        IF topology = '*' THEN
            IF policy_row.polname <> 'mcp_hard_identity'
               OR policy_row.polcmd <> '*'
               OR normalized_using NOT IN (
                   'agent::text=mcp_session_agent',
                   'agent::text=soul_v3.mcp_session_agent'
               )
               OR normalized_check NOT IN (
                   'agent::text=mcp_session_agent',
                   'agent::text=soul_v3.mcp_session_agent'
               )
            THEN
                RAISE EXCEPTION
                    'unexpected legacy policy; refusing rollback';
            END IF;
        ELSE
            CASE policy_row.polname
                WHEN 'mcp_hard_identity' THEN
                    IF policy_row.polcmd <> 'r'
                       OR normalized_using NOT IN (
                           'agentISNULLORagent::text=''TEAM''::textORagent::text=mcp_session_agent',
                           'agentISNULLORagent::text=''TEAM''::textORagent::text=soul_v3.mcp_session_agent'
                       )
                       OR normalized_check <> ''
                    THEN
                        RAISE EXCEPTION
                            'unexpected SELECT policy; refusing rollback';
                    END IF;
                WHEN 'mcp_hard_identity_insert' THEN
                    IF policy_row.polcmd <> 'a'
                       OR normalized_using <> ''
                       OR normalized_check NOT IN (
                           'agent::text=mcp_session_agent',
                           'agent::text=soul_v3.mcp_session_agent'
                       )
                    THEN
                        RAISE EXCEPTION
                            'unexpected INSERT policy; refusing rollback';
                    END IF;
                WHEN 'mcp_hard_identity_update' THEN
                    IF policy_row.polcmd <> 'w'
                       OR normalized_using NOT IN (
                           'agent::text=mcp_session_agent',
                           'agent::text=soul_v3.mcp_session_agent'
                       )
                       OR normalized_check NOT IN (
                           'agent::text=mcp_session_agent',
                           'agent::text=soul_v3.mcp_session_agent'
                       )
                    THEN
                        RAISE EXCEPTION
                            'unexpected UPDATE policy; refusing rollback';
                    END IF;
                WHEN 'mcp_hard_identity_delete' THEN
                    IF policy_row.polcmd <> 'd'
                       OR normalized_using NOT IN (
                           'agent::text=mcp_session_agent',
                           'agent::text=soul_v3.mcp_session_agent'
                       )
                       OR normalized_check <> ''
                    THEN
                        RAISE EXCEPTION
                            'unexpected DELETE policy; refusing rollback';
                    END IF;
            END CASE;
        END IF;
    END LOOP;

    IF policy_count <> (
        CASE WHEN topology = '*' THEN 1 ELSE 4 END
    ) THEN
        RAISE EXCEPTION
            'unexpected policy count % for topology %',
            policy_count, topology;
    END IF;
END
$preflight$;

DROP POLICY IF EXISTS mcp_hard_identity_delete ON soul_v3.rules;
DROP POLICY IF EXISTS mcp_hard_identity_update ON soul_v3.rules;
DROP POLICY IF EXISTS mcp_hard_identity_insert ON soul_v3.rules;
DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.rules;

CREATE POLICY mcp_hard_identity
ON soul_v3.rules
AS RESTRICTIVE
FOR ALL
TO mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
   mcp_runtime_jarvis, mcp_runtime_nexus
USING (agent::text = soul_v3.mcp_session_agent())
WITH CHECK (agent::text = soul_v3.mcp_session_agent());

COMMIT;
