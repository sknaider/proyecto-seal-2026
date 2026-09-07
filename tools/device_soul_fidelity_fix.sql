-- Fidelidad de la copia exacta en el device (cierra los gaps del pg_restore).
-- Se aplica al contenedor seal-memory-db del device (psql -U seal -d seal_memory).

-- 1) ROLES GLOBALES faltantes (pg_dump de una DB NO los incluye; por eso 11 RLS fallaron).
CREATE ROLE chat_hist_ro NOLOGIN;
CREATE ROLE chat_msg_ro NOLOGIN;
CREATE ROLE fable_ltd LOGIN;
CREATE ROLE soul_admin NOLOGIN;
CREATE ROLE soul_app NOLOGIN;
CREATE ROLE soul_sdk_admin NOLOGIN;
CREATE ROLE soul_sdk_runtime LOGIN;
CREATE ROLE soul_sdk_user NOLOGIN;
CREATE ROLE tenant_user NOLOGIN;

-- 2) POLÍTICAS RLS que fallaron (ahora que los roles existen). Idempotente (DROP IF EXISTS + CREATE).
DROP POLICY IF EXISTS chat_history_own ON soul_v3.chat_history;
CREATE POLICY chat_history_own ON soul_v3.chat_history AS PERMISSIVE FOR SELECT TO chat_hist_ro USING ((user_id = (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::integer));
DROP POLICY IF EXISTS chat_history_service ON soul_v3.chat_history;
CREATE POLICY chat_history_service ON soul_v3.chat_history AS PERMISSIVE FOR ALL TO soul_sdk_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS chat_messages_channel_own ON soul_v3.chat_messages;
CREATE POLICY chat_messages_channel_own ON soul_v3.chat_messages AS PERMISSIVE FOR SELECT TO chat_msg_ro USING (
CASE
    WHEN (lower((channel)::text) ~~ 'user:%'::text) THEN (split_part((channel)::text, ':'::text, 2) = NULLIF(current_setting('app.current_user_id'::text, true), ''::text))
    WHEN (lower((channel)::text) ~~ 'dm:%'::text) THEN ((lower(split_part((channel)::text, ':'::text, 2)) = lower(NULLIF(current_setting('app.current_identity'::text, true), ''::text))) OR (lower(split_part((channel)::text, ':'::text, 3)) = lower(NULLIF(current_setting('app.current_identity'::text, true), ''::text))))
    ELSE true
END);
DROP POLICY IF EXISTS chat_messages_service ON soul_v3.chat_messages;
CREATE POLICY chat_messages_service ON soul_v3.chat_messages AS PERMISSIVE FOR ALL TO soul_sdk_runtime USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS daily_dreams_agent_read_runtime ON soul_v3.daily_dreams;
CREATE POLICY daily_dreams_agent_read_runtime ON soul_v3.daily_dreams AS PERMISSIVE FOR SELECT TO soul_sdk_runtime USING ((agent = NULLIF(current_setting('app.agent'::text, true), ''::text)));
DROP POLICY IF EXISTS memories_agent_scope_insert ON soul_v3.memories;
CREATE POLICY memories_agent_scope_insert ON soul_v3.memories AS PERMISSIVE FOR INSERT TO soul_sdk_runtime WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) AND ((agent)::text = NULLIF(current_setting('app.agent'::text, true), ''::text)) AND ((COALESCE(scope, 'private'::character varying))::text = ANY ((ARRAY['private'::character varying, 'team'::character varying])::text[]))));
DROP POLICY IF EXISTS memories_agent_scope_read ON soul_v3.memories;
CREATE POLICY memories_agent_scope_read ON soul_v3.memories AS PERMISSIVE FOR SELECT TO soul_sdk_runtime USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) AND (((agent)::text = NULLIF(current_setting('app.agent'::text, true), ''::text)) OR ((COALESCE(scope, 'private'::character varying))::text = ANY ((ARRAY['team'::character varying, 'public'::character varying, 'shared'::character varying])::text[])))));
DROP POLICY IF EXISTS memories_agent_scope_update ON soul_v3.memories;
CREATE POLICY memories_agent_scope_update ON soul_v3.memories AS PERMISSIVE FOR UPDATE TO soul_sdk_runtime USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) AND ((agent)::text = NULLIF(current_setting('app.agent'::text, true), ''::text)))) WITH CHECK (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) AND ((agent)::text = NULLIF(current_setting('app.agent'::text, true), ''::text)) AND ((COALESCE(scope, 'private'::character varying))::text = ANY ((ARRAY['private'::character varying, 'team'::character varying])::text[]))));
DROP POLICY IF EXISTS memories_tenant_user_read ON soul_v3.memories;
CREATE POLICY memories_tenant_user_read ON soul_v3.memories AS PERMISSIVE FOR SELECT TO soul_sdk_user USING (((tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid) AND (NULLIF(current_setting('app.viewer'::text, true), ''::text) = 'user'::text) AND (NULLIF(current_setting('app.user_id'::text, true), ''::text) IS NOT NULL) AND ((COALESCE(scope, 'private'::character varying))::text = ANY ((ARRAY['team'::character varying, 'public'::character varying, 'shared'::character varying])::text[]))));
DROP POLICY IF EXISTS opinions_agent_read_runtime ON soul_v3.opinions;
CREATE POLICY opinions_agent_read_runtime ON soul_v3.opinions AS PERMISSIVE FOR SELECT TO soul_sdk_runtime USING (((agent)::text = NULLIF(current_setting('app.agent'::text, true), ''::text)));

-- 3) igualar extensiones a central: quitar el core timescaledb que la imagen agregó de más (central no lo tiene).
DROP EXTENSION IF EXISTS timescaledb;

-- 4) VERIFICACIÓN por efecto (comparar contra central).
SELECT 'policies_soul_v3=' || count(*) AS check_policies FROM pg_policies WHERE schemaname='soul_v3';
SELECT 'roles_sdk_presentes=' || count(*) AS check_roles FROM pg_roles WHERE rolname IN ('chat_hist_ro','chat_msg_ro','fable_ltd','soul_admin','soul_app','soul_sdk_admin','soul_sdk_runtime','soul_sdk_user','tenant_user');
SELECT 'core_timescaledb=' || COALESCE((SELECT extname FROM pg_extension WHERE extname='timescaledb'),'AUSENTE_ok') AS check_ext;
