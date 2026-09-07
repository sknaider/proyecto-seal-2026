-- Delete one chat principal and all of its sessions atomically.
-- Runtime callers retain no general SELECT/DELETE access to chat_sessions.
BEGIN;

DO $role$
BEGIN
  CREATE ROLE user_mgr_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOBYPASSRLS NOREPLICATION;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$role$;

-- Re-application is hardening too: never trust attributes left by an older or
-- locally modified role definition.
ALTER ROLE user_mgr_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOBYPASSRLS NOREPLICATION;

-- NOLOGIN is not an isolation boundary if another role can SET ROLE into the
-- owner.  Refuse to install over a pre-existing membership instead of silently
-- preserving a privilege-escalation path.
DO $authority_verify$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM pg_auth_members m
      JOIN pg_roles r ON r.oid=m.roleid
     WHERE r.rolname='user_mgr_owner'
  ) THEN
    RAISE EXCEPTION 'user_mgr_owner must have no members';
  END IF;
END
$authority_verify$;

GRANT USAGE ON SCHEMA soul_v3 TO user_mgr_owner;
-- PostgreSQL requires UPDATE privilege for SELECT ... FOR UPDATE. The owner is
-- NOLOGIN/NOINHERIT and exposes only the bounded function below.
GRANT SELECT, UPDATE, DELETE ON soul_v3.chat_users TO user_mgr_owner;
GRANT SELECT, DELETE ON soul_v3.chat_sessions TO user_mgr_owner;
GRANT INSERT ON soul_v3.soul_audit_log TO user_mgr_owner;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.soul_audit_log_id_seq TO user_mgr_owner;

CREATE OR REPLACE FUNCTION soul_v3.user_delete_with_sessions(
  p_username text, p_actor_user_id integer, p_session_token_hash text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $function$
DECLARE
  target_user_id integer;
  target_role text;
  owner_count integer;
  deleted_users integer;
  actor_username text;
  actor_role text;
  target_username text;
BEGIN
  PERFORM pg_advisory_xact_lock(6000273874612473938);
  SELECT u.username, u.role INTO actor_username, actor_role
    FROM soul_v3.chat_sessions s
    JOIN soul_v3.chat_users u ON u.id=s.user_id
   WHERE s.user_id=p_actor_user_id AND s.token_hash=p_session_token_hash
     AND s.expires_at>now() AND s.created_at + interval '30 days'>now();
  IF p_actor_user_id IS DISTINCT FROM 1 OR actor_role IS DISTINCT FROM 'superuser' THEN
    RAISE EXCEPTION 'owner session required' USING ERRCODE='42501';
  END IF;
  SELECT id, username, role INTO target_user_id, target_username, target_role
  FROM soul_v3.chat_users
  WHERE lower(username) = lower(p_username)
  FOR UPDATE;

  IF target_user_id IS NULL THEN
    RETURN 0;
  END IF;

  -- The durable owner principal is the root of this authority boundary.  It
  -- cannot use the bounded API to destroy the identity that future calls must
  -- authenticate as, even when a second superuser exists.
  IF target_user_id = 1 THEN
    RAISE EXCEPTION 'durable owner cannot be deleted' USING ERRCODE='42501';
  END IF;

  IF target_role = 'superuser' THEN
    SELECT count(*) INTO owner_count FROM soul_v3.chat_users WHERE role='superuser';
    IF owner_count <= 1 THEN
      RETURN -1;
    END IF;
  END IF;

  DELETE FROM soul_v3.chat_sessions WHERE user_id = target_user_id;
  DELETE FROM soul_v3.chat_users WHERE id = target_user_id;
  GET DIAGNOSTICS deleted_users = ROW_COUNT;
  IF deleted_users = 1 THEN
    INSERT INTO soul_v3.soul_audit_log
      (agent,operation,table_name,record_id,old_value,new_value,role_used)
    VALUES (
      actor_username,'DELETE','chat_users',target_user_id,
      jsonb_build_object('username',target_username,'role',target_role),
      jsonb_build_object('deleted',true,'actor',actor_username),
      'chat_server'
    );
  END IF;
  RETURN deleted_users;
END
$function$;

CREATE OR REPLACE FUNCTION soul_v3.user_role_update_guarded(
  p_user_id integer, p_new_role text, p_actor_user_id integer,
  p_session_token_hash text, p_reason text DEFAULT ''
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $role_function$
DECLARE
  target soul_v3.chat_users%ROWTYPE;
  owner_count integer;
  actor_username text;
  actor_role text;
BEGIN
  IF p_new_role NOT IN ('superuser','admin','basic') THEN
    RAISE EXCEPTION 'invalid role';
  END IF;
  PERFORM pg_advisory_xact_lock(6000273874612473938);
  SELECT u.username, u.role INTO actor_username, actor_role
    FROM soul_v3.chat_sessions s
    JOIN soul_v3.chat_users u ON u.id=s.user_id
   WHERE s.user_id=p_actor_user_id AND s.token_hash=p_session_token_hash
     AND s.expires_at>now() AND s.created_at + interval '30 days'>now();
  IF p_actor_user_id IS DISTINCT FROM 1 OR actor_role IS DISTINCT FROM 'superuser' THEN
    RAISE EXCEPTION 'owner session required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO target FROM soul_v3.chat_users WHERE id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok',false,'error','not_found');
  END IF;
  IF p_user_id = 1 AND p_new_role <> 'superuser' THEN
    RAISE EXCEPTION 'durable owner cannot be demoted' USING ERRCODE='42501';
  END IF;
  IF target.role='superuser' AND p_new_role<>'superuser' THEN
    SELECT count(*) INTO owner_count FROM soul_v3.chat_users WHERE role='superuser';
    IF owner_count <= 1 THEN
      RETURN jsonb_build_object('ok',false,'error','last_superuser');
    END IF;
  END IF;
  UPDATE soul_v3.chat_users SET role=p_new_role WHERE id=p_user_id;
  INSERT INTO soul_v3.soul_audit_log
    (agent,operation,table_name,record_id,old_value,new_value,role_used)
  VALUES (
    actor_username,'UPDATE','chat_users',p_user_id,
    jsonb_build_object('username',target.username,'role',target.role),
    jsonb_build_object('username',target.username,'role',p_new_role,'campo','role',
                       'actor',actor_username,'motivo',left(p_reason,200),'reversible',true),
    'chat_server'
  );
  RETURN jsonb_build_object('ok',true,'user',jsonb_build_object(
    'id',target.id,'username',target.username,'display_name',target.display_name,'role',p_new_role
  ));
END
$role_function$;

ALTER FUNCTION soul_v3.user_delete_with_sessions(text,integer,text) OWNER TO user_mgr_owner;
ALTER FUNCTION soul_v3.user_role_update_guarded(integer,text,integer,text,text) OWNER TO user_mgr_owner;
REVOKE ALL ON FUNCTION soul_v3.user_delete_with_sessions(text,integer,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.user_role_update_guarded(integer,text,integer,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.user_delete_with_sessions(text,integer,text)
  TO login_bus_admin, pr_bus_admin;
GRANT EXECUTE ON FUNCTION soul_v3.user_role_update_guarded(integer,text,integer,text,text)
  TO login_bus_admin, pr_bus_admin;

-- Retire pre-hardening entry points so the owner-session proof cannot be
-- bypassed through an older overload left in a live installation.
DROP FUNCTION IF EXISTS soul_v3.user_delete_with_sessions(text);
DROP FUNCTION IF EXISTS soul_v3.user_role_update_guarded(integer,text,text,text);

-- The bounded SECURITY DEFINER functions own every destructive user/session
-- operation.  A table-level DELETE is an authority bypass even without SELECT:
-- an unqualified DELETE can still wipe the entire table.
REVOKE SELECT ON soul_v3.chat_users FROM login_bus_admin, pr_bus_admin;
REVOKE SELECT (username) ON soul_v3.chat_users FROM login_bus_admin, pr_bus_admin;
REVOKE DELETE ON soul_v3.chat_users, soul_v3.chat_sessions
  FROM login_bus, pr_bus, login_bus_admin, pr_bus_admin;

-- Shared runtime identities can update liveness only.  Authority (`role`) is
-- writable exclusively through the guarded SECURITY DEFINER function above.
REVOKE UPDATE ON soul_v3.chat_users
  FROM login_bus, pr_bus, login_bus_admin, pr_bus_admin;
GRANT UPDATE (last_seen) ON soul_v3.chat_users TO login_bus, pr_bus;

COMMIT;
