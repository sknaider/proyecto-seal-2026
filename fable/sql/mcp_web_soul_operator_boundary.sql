BEGIN;

CREATE OR REPLACE FUNCTION soul_v3.mcp_web_soul_operator_watermark()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
    SELECT COALESCE(max(id), 0)::bigint FROM soul_v3.chat_messages
$$;

CREATE OR REPLACE FUNCTION soul_v3.mcp_web_soul_operator_commands(p_after_id bigint)
RETURNS TABLE(message_id bigint, content text, authenticated_operator text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
    SELECT m.id,
           m.content,
           CASE
             WHEN m.sender_type = 'user'
              AND u.id = 1
              AND lower(COALESCE(u.username, '')) = 'william'
              AND lower(COALESCE(u.role, '')) = 'superuser'
             THEN 'William'
             WHEN COALESCE(m.metadata->>'session_token_hash', '') <> ''
              AND m.sender_type = 'user'
              AND EXISTS (
                    SELECT 1
                      FROM soul_v3.chat_sessions s
                      JOIN soul_v3.chat_users su ON su.id=s.user_id
                     WHERE s.token_hash=m.metadata->>'session_token_hash'
                       AND m.sender_id=s.user_id
                       AND s.expires_at>now()
                       AND su.id = 1
                       AND lower(COALESCE(su.username, '')) = 'william'
                       AND lower(COALESCE(su.role, '')) = 'superuser'
                  )
             THEN 'William'
             ELSE NULL
           END AS authenticated_operator
      FROM soul_v3.chat_messages m
      LEFT JOIN soul_v3.chat_users u ON u.id=m.sender_id
     WHERE m.id>p_after_id
       AND m.channel IN ('web_chat','dm:ada:william')
       AND m.content ~* '^\s*OK\s+BROWSER\s+'
     ORDER BY m.id ASC
$$;

REVOKE ALL ON FUNCTION soul_v3.mcp_web_soul_operator_watermark() FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.mcp_web_soul_operator_commands(bigint) FROM PUBLIC;
GRANT USAGE ON SCHEMA soul_v3 TO svc_mcp_web_soul_operator;
GRANT EXECUTE ON FUNCTION soul_v3.mcp_web_soul_operator_watermark() TO svc_mcp_web_soul_operator;
GRANT EXECUTE ON FUNCTION soul_v3.mcp_web_soul_operator_commands(bigint) TO svc_mcp_web_soul_operator;

-- Negative and positive controls execute inside the deployment transaction.
-- A BASIC user may call herself William in display_name and may own a valid
-- session, but neither path authenticates her as the canonical operator.
DO $gate$
DECLARE
    v_after bigint;
    v_basic integer;
    v_direct bigint;
    v_session bigint;
    v_positive bigint;
    v_token text := '__seal_operator_gate_' || pg_backend_pid()::text;
BEGIN
    SELECT COALESCE(max(id), 0) INTO v_after FROM soul_v3.chat_messages;
    INSERT INTO soul_v3.chat_users(username, display_name, password_hash, role)
    VALUES ('__seal_operator_basic_' || pg_backend_pid()::text, 'William', 'not-a-login', 'basic')
    RETURNING id INTO v_basic;

    INSERT INTO soul_v3.chat_messages(sender_type,sender_id,sender_name,channel,message_type,content,metadata)
    VALUES ('user',v_basic,'William','web_chat','conversation','OK BROWSER approve 00000000-0000-0000-0000-000000000000','{}'::jsonb)
    RETURNING id INTO v_direct;

    INSERT INTO soul_v3.chat_sessions(user_id,token_hash,expires_at)
    VALUES (v_basic,v_token,now()+interval '5 minutes');
    INSERT INTO soul_v3.chat_messages(sender_type,sender_id,sender_name,channel,message_type,content,metadata)
    VALUES ('user',v_basic,'William','web_chat','conversation','OK BROWSER approve 00000000-0000-0000-0000-000000000000',jsonb_build_object('session_token_hash',v_token))
    RETURNING id INTO v_session;

    INSERT INTO soul_v3.chat_messages(sender_type,sender_id,sender_name,channel,message_type,content,metadata)
    VALUES ('user',1,'William','web_chat','conversation','OK BROWSER approve 00000000-0000-0000-0000-000000000000','{}'::jsonb)
    RETURNING id INTO v_positive;

    IF EXISTS (
        SELECT 1 FROM soul_v3.mcp_web_soul_operator_commands(v_after)
         WHERE message_id IN (v_direct,v_session) AND authenticated_operator IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'basic/display-name/session spoof authenticated as William';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM soul_v3.mcp_web_soul_operator_commands(v_after)
         WHERE message_id=v_positive AND authenticated_operator='William'
    ) THEN
        RAISE EXCEPTION 'canonical William positive control failed';
    END IF;

    DELETE FROM soul_v3.chat_messages WHERE id IN (v_direct,v_session,v_positive);
    DELETE FROM soul_v3.chat_sessions WHERE token_hash=v_token;
    DELETE FROM soul_v3.chat_users WHERE id=v_basic;
END
$gate$;

COMMIT;
