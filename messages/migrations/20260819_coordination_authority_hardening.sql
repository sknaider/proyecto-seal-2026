\set ON_ERROR_STOP on
BEGIN;

SELECT pg_advisory_xact_lock(x'5EA20260819'::bigint);
SELECT set_config('seal.migration_hash', :'migration_hash', true);

DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coordination_owner') THEN
        CREATE ROLE coordination_owner NOLOGIN NOINHERIT;
    END IF;
END
$role$;

-- Idempotent hardening: an existing role must not retain login, superuser or
-- BYPASSRLS attributes from an earlier/manual definition.
ALTER ROLE coordination_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOBYPASSRLS NOREPLICATION;

GRANT USAGE ON SCHEMA soul_v3 TO coordination_owner;
GRANT SELECT, INSERT, UPDATE ON
    soul_v3.coordination_turns,
    soul_v3.coordination_assignments,
    soul_v3.coordination_voice_grants
TO coordination_owner;

CREATE OR REPLACE FUNCTION soul_v3.coordination_write(p_operation text, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $fn$
DECLARE
    turn_row soul_v3.coordination_turns%ROWTYPE;
    changed integer := 0;
    item jsonb;
    requested_assignments jsonb := '[]'::jsonb;
    stored_assignments jsonb := '[]'::jsonb;
    source_id text := nullif(btrim(p->>'source_message_id'), '');
BEGIN
    IF p_operation = 'create_turn' THEN
        IF source_id IS NULL
           OR p->>'mode' NOT IN ('social','discussion','execution','roundtable','direct')
           OR jsonb_typeof(p->'assignments') IS DISTINCT FROM 'array'
           OR jsonb_array_length(p->'assignments') NOT BETWEEN 1 AND 10 THEN
            RAISE EXCEPTION 'invalid coordination turn payload';
        END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(p->'assignments') LOOP
            IF item->>'role' NOT IN ('lead','contributor','speaker')
               OR coalesce(item->>'agent','') !~ '^[A-Z][A-Z0-9_-]{0,31}$' THEN
                RAISE EXCEPTION 'invalid coordination assignment';
            END IF;
            requested_assignments := requested_assignments || jsonb_build_array(
                jsonb_build_object(
                    'agent',upper(item->>'agent'), 'role',item->>'role',
                    'public_write',coalesce((item->>'public_write')::boolean,false),
                    'evidence',coalesce(item->'evidence','{}'::jsonb)
                )
            );
        END LOOP;
        SELECT coalesce(jsonb_agg(value ORDER BY value->>'agent'),'[]'::jsonb)
          INTO requested_assignments
          FROM jsonb_array_elements(requested_assignments);
        IF (SELECT count(DISTINCT value->>'agent') FROM jsonb_array_elements(requested_assignments))
             IS DISTINCT FROM jsonb_array_length(requested_assignments)
           OR (SELECT count(*) FROM jsonb_array_elements(requested_assignments)
               WHERE value->>'role'='lead') <> 1
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(requested_assignments)
               WHERE value->>'role'='lead'
                 AND value->>'agent'=upper(left(p->>'lead_agent',32))
                 AND (value->>'public_write')::boolean
           ) THEN
            RAISE EXCEPTION 'invalid coordination assignment set';
        END IF;
        INSERT INTO soul_v3.coordination_turns
            (source_message_id, requester, request_hash, mode, lead_agent,
             ack_deadline, result_deadline, metadata)
        VALUES (
            source_id, left(p->>'requester', 128), p->>'request_hash', p->>'mode',
            upper(left(p->>'lead_agent', 32)),
            now()+make_interval(secs=>greatest(1,(p->>'ack_seconds')::int)),
            now()+make_interval(secs=>greatest(5,(p->>'result_seconds')::int)),
            coalesce(p->'metadata','{}'::jsonb)
        )
        ON CONFLICT (source_message_id) DO NOTHING
        RETURNING * INTO turn_row;

        IF NOT FOUND THEN
            SELECT * INTO turn_row
              FROM soul_v3.coordination_turns
             WHERE source_message_id=source_id;
            IF turn_row.requester IS DISTINCT FROM left(p->>'requester',128)
               OR turn_row.request_hash IS DISTINCT FROM p->>'request_hash'
               OR turn_row.mode IS DISTINCT FROM p->>'mode'
               OR turn_row.lead_agent IS DISTINCT FROM upper(left(p->>'lead_agent',32))
               OR turn_row.metadata IS DISTINCT FROM coalesce(p->'metadata','{}'::jsonb)
            THEN
                RAISE EXCEPTION 'coordination source replay mismatch';
            END IF;
            SELECT coalesce(jsonb_agg(
                     jsonb_build_object(
                       'agent',agent,'role',role,'public_write',public_write,
                       'evidence',evidence
                     ) ORDER BY agent
                   ),'[]'::jsonb)
              INTO stored_assignments
              FROM soul_v3.coordination_assignments
             WHERE source_message_id=source_id;
            IF stored_assignments IS DISTINCT FROM requested_assignments THEN
                RAISE EXCEPTION 'coordination assignment replay mismatch';
            END IF;
            -- Exact semantic replay is idempotent. It never mutates or adds
            -- assignments to the established turn.
            RETURN jsonb_build_object('ok',true,'replayed',true,'turn',to_jsonb(turn_row));
        END IF;

        FOR item IN SELECT value FROM jsonb_array_elements(p->'assignments') LOOP
            INSERT INTO soul_v3.coordination_assignments
                (source_message_id, agent, role, public_write, evidence)
            VALUES (
                source_id, item->>'agent', item->>'role',
                coalesce((item->>'public_write')::boolean,false),
                coalesce(item->'evidence','{}'::jsonb)
            )
            ON CONFLICT (source_message_id,agent) DO NOTHING;
        END LOOP;
        IF NOT EXISTS (
            SELECT 1 FROM soul_v3.coordination_assignments
             WHERE source_message_id=source_id
               AND agent=upper(left(p->>'lead_agent',32)) AND public_write
        ) THEN
            RAISE EXCEPTION 'lead lacks public-write assignment';
        END IF;
        RETURN jsonb_build_object('ok',true,'replayed',false,'turn',to_jsonb(turn_row));

    ELSIF p_operation = 'update_assignment' THEN
        IF p->>'status' NOT IN ('accepted','working','submitted','done','declined','expired') THEN
            RAISE EXCEPTION 'invalid assignment status';
        END IF;
        UPDATE soul_v3.coordination_assignments
           SET status=p->>'status', evidence=evidence||coalesce(p->'evidence','{}'::jsonb),
               last_heartbeat=now(),
               lease_until=CASE WHEN p->>'lease_seconds' IS NULL THEN lease_until
                                ELSE now()+make_interval(secs=>(p->>'lease_seconds')::int) END
         WHERE source_message_id=source_id AND agent=upper(p->>'agent');
        GET DIAGNOSTICS changed = ROW_COUNT;

    ELSIF p_operation = 'register_grant' THEN
        INSERT INTO soul_v3.coordination_voice_grants
            (grant_id,source_message_id,agent,purpose,expires_at,metadata)
        VALUES (p->>'grant_id',source_id,upper(p->>'agent'),p->>'purpose',
                to_timestamp((p->>'expires_at')::bigint),coalesce(p->'metadata','{}'::jsonb))
        ON CONFLICT (grant_id) DO NOTHING;
        GET DIAGNOSTICS changed = ROW_COUNT;

    ELSIF p_operation = 'consume_grant' THEN
        UPDATE soul_v3.coordination_voice_grants SET consumed_at=now()
         WHERE grant_id=p->>'grant_id' AND source_message_id=source_id
           AND agent=upper(p->>'agent') AND purpose=p->>'purpose'
           AND consumed_at IS NULL AND expires_at>=now();
        GET DIAGNOSTICS changed = ROW_COUNT;

    ELSIF p_operation = 'complete_turn' THEN
        UPDATE soul_v3.coordination_turns
           SET status='completed', final_message_id=p->>'final_message_id', version=version+1
         WHERE source_message_id=source_id AND lead_agent=upper(p->>'lead_agent')
           AND status NOT IN ('completed','cancelled','expired')
           AND (p->>'expected_version' IS NULL OR version=(p->>'expected_version')::int);
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed=1 THEN
            UPDATE soul_v3.coordination_assignments SET status='done',last_heartbeat=now()
             WHERE source_message_id=source_id AND status NOT IN ('done','declined','expired');
        END IF;

    ELSIF p_operation = 'cancel_turn' THEN
        UPDATE soul_v3.coordination_turns SET status='cancelled',version=version+1
         WHERE source_message_id=source_id AND status NOT IN ('completed','cancelled','expired');
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed=1 THEN
            UPDATE soul_v3.coordination_assignments SET status='expired',last_heartbeat=now()
             WHERE source_message_id=source_id AND status NOT IN ('done','declined','expired');
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported coordination operation';
    END IF;
    RETURN jsonb_build_object('ok',changed=1);
END
$fn$;

ALTER FUNCTION soul_v3.coordination_write(text,jsonb) OWNER TO coordination_owner;
REVOKE ALL ON FUNCTION soul_v3.coordination_write(text,jsonb) FROM PUBLIC, pr_bus;

ALTER TABLE soul_v3.coordination_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.coordination_turns FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.coordination_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.coordination_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.coordination_voice_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.coordination_voice_grants FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS coordination_turns_read ON soul_v3.coordination_turns;
CREATE POLICY coordination_turns_read ON soul_v3.coordination_turns
  FOR SELECT TO pr_bus, pr_bus_admin USING (true);
DROP POLICY IF EXISTS coordination_turns_owner_write ON soul_v3.coordination_turns;
CREATE POLICY coordination_turns_owner_write ON soul_v3.coordination_turns
  FOR ALL TO coordination_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS coordination_assignments_read ON soul_v3.coordination_assignments;
CREATE POLICY coordination_assignments_read ON soul_v3.coordination_assignments
  FOR SELECT TO pr_bus, pr_bus_admin USING (true);
DROP POLICY IF EXISTS coordination_assignments_owner_write ON soul_v3.coordination_assignments;
CREATE POLICY coordination_assignments_owner_write ON soul_v3.coordination_assignments
  FOR ALL TO coordination_owner USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS coordination_grants_read ON soul_v3.coordination_voice_grants;
CREATE POLICY coordination_grants_read ON soul_v3.coordination_voice_grants
  FOR SELECT TO pr_bus, pr_bus_admin USING (true);
DROP POLICY IF EXISTS coordination_grants_owner_write ON soul_v3.coordination_voice_grants;
CREATE POLICY coordination_grants_owner_write ON soul_v3.coordination_voice_grants
  FOR ALL TO coordination_owner USING (true) WITH CHECK (true);

REVOKE INSERT, UPDATE, DELETE ON
    soul_v3.coordination_turns,
    soul_v3.coordination_assignments,
    soul_v3.coordination_voice_grants
FROM pr_bus, pr_bus_admin;
GRANT SELECT ON
    soul_v3.coordination_turns,
    soul_v3.coordination_assignments,
    soul_v3.coordination_voice_grants
TO pr_bus;
GRANT SELECT ON
    soul_v3.coordination_turns,
    soul_v3.coordination_assignments,
    soul_v3.coordination_voice_grants
TO pr_bus_admin;
GRANT EXECUTE ON FUNCTION soul_v3.coordination_write(text,jsonb) TO pr_bus_admin;

DO $authority_verify$
DECLARE attrs record;
BEGIN
    SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
      INTO attrs FROM pg_roles WHERE rolname='coordination_owner';
    IF attrs.rolcanlogin OR attrs.rolsuper OR attrs.rolcreaterole OR attrs.rolcreatedb
       OR attrs.rolreplication OR attrs.rolbypassrls THEN
        RAISE EXCEPTION 'coordination_owner attributes are unsafe';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles r ON r.oid=m.roleid
        WHERE r.rolname='coordination_owner'
    ) THEN
        RAISE EXCEPTION 'coordination_owner must have no members';
    END IF;
END
$authority_verify$;

INSERT INTO soul_v3.schema_migrations(migration_name,migration_hash,actor)
VALUES ('20260819_coordination_authority_hardening',
        current_setting('seal.migration_hash'),'ADA')
ON CONFLICT (migration_name) DO UPDATE
SET migration_hash=EXCLUDED.migration_hash, actor=EXCLUDED.actor, applied_at=now()
WHERE soul_v3.schema_migrations.migration_hash=EXCLUDED.migration_hash;

DO $verify$
DECLARE stored_hash text;
BEGIN
    SELECT migration_hash INTO stored_hash FROM soul_v3.schema_migrations
     WHERE migration_name='20260819_coordination_authority_hardening';
    IF stored_hash IS DISTINCT FROM current_setting('seal.migration_hash') THEN
        RAISE EXCEPTION 'migration ledger hash mismatch';
    END IF;
END
$verify$;

COMMIT;
