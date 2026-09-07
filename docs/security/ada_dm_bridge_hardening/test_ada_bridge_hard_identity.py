"""PostgreSQL 17 integration tests for ADA bridge hard identity.

Run only against a disposable database:
    ADA_BRIDGE_TEST_DSN=postgresql://postgres:...@127.0.0.1:<port>/postgres \
      pytest -q docs/security/ada_dm_bridge_hardening/test_ada_bridge_hard_identity.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest


ROOT = Path(__file__).resolve().parent
UP_SQL = (ROOT / "001_ada_bridge_hard_identity_up.sql").read_text(encoding="utf-8")
DOWN_SQL = (ROOT / "001_ada_bridge_hard_identity_down.sql").read_text(encoding="utf-8")
ADMIN_DSN = os.environ.get("ADA_BRIDGE_TEST_DSN")
TENANT_ADA = "11111111-1111-1111-1111-111111111111"
TENANT_OTHER = "22222222-2222-2222-2222-222222222222"


pytestmark = pytest.mark.skipif(
    not ADMIN_DSN,
    reason="ADA_BRIDGE_TEST_DSN must point to a disposable PostgreSQL 17 database",
)


BASELINE_SQL = f"""
CREATE SCHEMA soul_v3;
CREATE ROLE seal NOLOGIN SUPERUSER;
CREATE ROLE pr_ada_bridge NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS INHERIT;
CREATE ROLE login_ada_bridge LOGIN PASSWORD 'test-only' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
GRANT pr_ada_bridge TO login_ada_bridge;
ALTER ROLE login_ada_bridge SET search_path = soul_v3, public;
ALTER ROLE login_ada_bridge SET app.agent = 'ADA';

CREATE TABLE soul_v3.chat_messages (
  id bigint PRIMARY KEY, channel text NOT NULL, content text NOT NULL
);
CREATE TABLE soul_v3.identity (agent text PRIMARY KEY, boot_context text);
CREATE TABLE soul_v3.working_state (agent text PRIMARY KEY, description text);
CREATE TABLE soul_v3.emotional_diary (
  id bigserial PRIMARY KEY, agent text NOT NULL, key_moment text
);
CREATE TABLE soul_v3.inner_monologue (
  id bigserial PRIMARY KEY, agent text NOT NULL, thought text,
  tenant_id uuid NOT NULL
);
CREATE TABLE soul_v3.memories (
  id bigserial PRIMARY KEY, agent text NOT NULL, scope text NOT NULL,
  content text NOT NULL, tenant_id uuid NOT NULL
);
CREATE TABLE soul_v3.working_state_events (
  id bigserial PRIMARY KEY, agent text NOT NULL, action text
);
CREATE TABLE soul_v3.harness_oracle (
  id bigserial PRIMARY KEY, agent text NOT NULL, state_key text
);
CREATE TABLE soul_v3.harness_truth_registry (
  state_key text PRIMARY KEY, canonical_query text NOT NULL
);
CREATE TABLE soul_v3.memory_poisoning_feedback (
  id bigserial PRIMARY KEY, memory_id bigint NOT NULL, decision text NOT NULL
);

GRANT USAGE ON SCHEMA soul_v3 TO pr_ada_bridge;
GRANT SELECT ON soul_v3.chat_messages, soul_v3.identity,
  soul_v3.working_state, soul_v3.emotional_diary, soul_v3.inner_monologue,
  soul_v3.memories, soul_v3.harness_truth_registry,
  soul_v3.memory_poisoning_feedback TO pr_ada_bridge;
GRANT SELECT, INSERT ON soul_v3.working_state_events,
  soul_v3.harness_oracle TO pr_ada_bridge;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3 TO pr_ada_bridge;

ALTER TABLE soul_v3.chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.chat_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY ada_bridge_chat_read ON soul_v3.chat_messages
  FOR SELECT TO pr_ada_bridge
  USING (channel = 'web_chat' OR lower(channel) = 'dm:ada:william');

DO $do$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'identity', 'working_state', 'emotional_diary', 'working_state_events',
    'harness_oracle'
  ] LOOP
    EXECUTE format('ALTER TABLE soul_v3.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
      'CREATE POLICY soft_agent ON soul_v3.%I FOR ALL TO PUBLIC '
      'USING (agent = NULLIF(current_setting(''app.agent'', true), '''')) '
      'WITH CHECK (agent = NULLIF(current_setting(''app.agent'', true), ''''))',
      table_name
    );
  END LOOP;
END
$do$;

ALTER TABLE soul_v3.inner_monologue ENABLE ROW LEVEL SECURITY;
CREATE POLICY soft_agent ON soul_v3.inner_monologue FOR ALL TO PUBLIC
USING (
  agent = NULLIF(current_setting('app.agent', true), '')
  AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK (
  agent = NULLIF(current_setting('app.agent', true), '')
  AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
);

ALTER TABLE soul_v3.memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY ada_bridge_memories_read ON soul_v3.memories
FOR SELECT TO pr_ada_bridge
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND (
    agent = NULLIF(current_setting('app.agent', true), '')
    OR scope IN ('team', 'public')
  )
);

-- Productive baseline before this incremental migration: invoker identity
-- function owned by seal plus three restrictive SELECT policies.
CREATE FUNCTION soul_v3.ada_bridge_session_agent()
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
REVOKE ALL ON FUNCTION soul_v3.ada_bridge_session_agent() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.ada_bridge_session_agent() TO pr_ada_bridge;

CREATE POLICY ada_bridge_hard_session_identity ON soul_v3.identity
AS RESTRICTIVE FOR SELECT TO pr_ada_bridge
USING (agent::text = soul_v3.ada_bridge_session_agent());
CREATE POLICY ada_bridge_hard_session_identity ON soul_v3.working_state
AS RESTRICTIVE FOR SELECT TO pr_ada_bridge
USING (agent::text = soul_v3.ada_bridge_session_agent());
CREATE POLICY ada_bridge_hard_session_identity ON soul_v3.emotional_diary
AS RESTRICTIVE FOR SELECT TO pr_ada_bridge
USING (agent = soul_v3.ada_bridge_session_agent());

INSERT INTO soul_v3.chat_messages VALUES
  (1, 'web_chat', 'public'),
  (2, 'dm:ada:william', 'allowed'),
  (3, 'dm:jarvis:william', 'blocked');
INSERT INTO soul_v3.identity VALUES ('ADA', 'a'), ('JARVIS', 'j');
INSERT INTO soul_v3.working_state VALUES ('ADA', 'a'), ('JARVIS', 'j');
INSERT INTO soul_v3.emotional_diary(agent, key_moment)
VALUES ('ADA', 'a'), ('JARVIS', 'j');
INSERT INTO soul_v3.inner_monologue(agent, thought, tenant_id) VALUES
  ('ADA', 'a', '{TENANT_ADA}'),
  ('JARVIS', 'j', '{TENANT_OTHER}');
INSERT INTO soul_v3.memories(agent, scope, content, tenant_id) VALUES
  ('ADA', 'private', 'a-private', '{TENANT_ADA}'),
  ('JARVIS', 'private', 'j-private', '{TENANT_OTHER}'),
  ('JARVIS', 'team', 'j-shared', '{TENANT_ADA}');
INSERT INTO soul_v3.working_state_events(agent, action)
VALUES ('ADA', 'a'), ('JARVIS', 'j');
INSERT INTO soul_v3.harness_oracle(agent, state_key)
VALUES ('ADA', 'a'), ('JARVIS', 'j');
INSERT INTO soul_v3.harness_truth_registry VALUES ('cursor', 'SELECT 1');
INSERT INTO soul_v3.memory_poisoning_feedback(memory_id, decision)
VALUES (1, 'allow');
"""


async def _connect_as_bridge(admin: asyncpg.Connection) -> asyncpg.Connection:
    parsed = urlparse(ADMIN_DSN)
    database = parsed.path.lstrip("/") or await admin.fetchval("SELECT current_database()")
    return await asyncpg.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 5432,
        database=database,
        user="login_ada_bridge",
        password="test-only",
    )


def test_hard_identity_up_and_down_on_pg17() -> None:
    async def scenario() -> None:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            assert await admin.fetchval("SHOW server_version_num") >= "170000"
            await admin.execute(BASELINE_SQL)

            bridge = await _connect_as_bridge(admin)
            try:
                await bridge.execute("SELECT set_config('app.agent','JARVIS',false)")
                for table in ("identity", "working_state", "emotional_diary"):
                    assert await bridge.fetchval(
                        f"SELECT count(*) FROM soul_v3.{table} WHERE agent='JARVIS'"
                    ) == 0
                # Not covered by the productive baseline; UP must extend the
                # same hard identity to these auxiliary grants.
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.working_state_events "
                    "WHERE agent='JARVIS'"
                ) == 1
            finally:
                await bridge.close()

            await admin.execute(UP_SQL)
            await admin.execute(UP_SQL)  # idempotent re-application

            owner = await admin.fetchrow(
                "SELECT rolcanlogin,rolsuper,rolinherit,rolcreaterole,rolcreatedb,"
                "rolreplication,rolbypassrls FROM pg_roles "
                "WHERE rolname='ada_bridge_identity_owner'"
            )
            assert owner is not None
            assert dict(owner) == {
                "rolcanlogin": False,
                "rolsuper": False,
                "rolinherit": False,
                "rolcreaterole": False,
                "rolcreatedb": False,
                "rolreplication": False,
                "rolbypassrls": False,
            }
            functions = await admin.fetch(
                "SELECT p.proname,r.rolname AS owner,p.prosecdef,p.proconfig "
                "FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='soul_v3' "
                "AND p.proname=ANY($1::text[]) ORDER BY p.proname",
                ["ada_bridge_session_agent", "ada_bridge_session_tenant_id"],
            )
            assert len(functions) == 2
            assert all(row["owner"] == "ada_bridge_identity_owner" for row in functions)
            assert all(row["prosecdef"] is True for row in functions)
            assert all(row["proconfig"] == ["search_path=\"\""] for row in functions)
            assert await admin.fetchval(
                "SELECT tableowner='ada_bridge_identity_owner' FROM pg_tables "
                "WHERE schemaname='soul_v3' "
                "AND tablename='ada_bridge_session_bindings'"
            ) is True
            assert await admin.fetchval(
                "SELECT has_table_privilege('pr_ada_bridge', "
                "'soul_v3.ada_bridge_session_bindings', 'SELECT')"
            ) is False
            function_acl = await admin.fetch(
                "SELECT p.proname,COALESCE(r.rolname,'PUBLIC') AS grantee,"
                "acl.privilege_type "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(p.proacl) acl "
                "LEFT JOIN pg_roles r ON r.oid=acl.grantee "
                "WHERE n.nspname='soul_v3' "
                "AND p.proname=ANY($1::text[]) ORDER BY 1,2,3",
                ["ada_bridge_session_agent", "ada_bridge_session_tenant_id"],
            )
            assert {
                (row["proname"], row["grantee"], row["privilege_type"])
                for row in function_acl
            } == {
                ("ada_bridge_session_agent", "ada_bridge_identity_owner", "EXECUTE"),
                ("ada_bridge_session_agent", "pr_ada_bridge", "EXECUTE"),
                (
                    "ada_bridge_session_tenant_id",
                    "ada_bridge_identity_owner",
                    "EXECUTE",
                ),
                ("ada_bridge_session_tenant_id", "pr_ada_bridge", "EXECUTE"),
            }

            bridge = await _connect_as_bridge(admin)
            try:
                assert await bridge.fetchval("SELECT session_user") == "login_ada_bridge"
                assert await bridge.fetchval("SELECT soul_v3.ada_bridge_session_agent()") == "ADA"
                assert str(
                    await bridge.fetchval("SELECT soul_v3.ada_bridge_session_tenant_id()")
                ) == TENANT_ADA
                await bridge.execute("SET ROLE pr_ada_bridge")
                assert await bridge.fetchval("SELECT current_user") == "pr_ada_bridge"
                assert await bridge.fetchval("SELECT session_user") == "login_ada_bridge"
                assert await bridge.fetchval(
                    "SELECT soul_v3.ada_bridge_session_agent()"
                ) == "ADA"
                await bridge.execute("RESET ROLE")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bridge.execute("SET ROLE ada_bridge_identity_owner")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bridge.fetchval(
                        "SELECT count(*) FROM soul_v3.ada_bridge_session_bindings"
                    )

                await bridge.execute("SELECT set_config('app.agent','JARVIS',false)")
                await bridge.execute(
                    "SELECT set_config('app.tenant_id',$1,false)", TENANT_OTHER
                )
                for table in (
                    "identity",
                    "working_state",
                    "emotional_diary",
                    "working_state_events",
                    "harness_oracle",
                    "inner_monologue",
                ):
                    assert await bridge.fetchval(
                        f"SELECT count(*) FROM soul_v3.{table} WHERE agent='JARVIS'"
                    ) == 0

                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.chat_messages"
                ) == 2
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.chat_messages "
                    "WHERE channel='dm:jarvis:william'"
                ) == 0

                await bridge.execute(
                    "SELECT set_config('app.tenant_id',$1,false)", TENANT_ADA
                )
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.memories WHERE agent='JARVIS'"
                ) == 1  # explicitly shared team memory in ADA's tenant
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.memories "
                    "WHERE agent='JARVIS' AND scope='private'"
                ) == 0

                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bridge.fetchval(
                        "SELECT count(*) FROM soul_v3.memory_poisoning_feedback"
                    )
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.harness_truth_registry"
                ) == 1

                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bridge.execute(
                        "INSERT INTO soul_v3.working_state_events(agent,action) "
                        "VALUES ('JARVIS','blocked')"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bridge.execute(
                        "INSERT INTO soul_v3.harness_oracle(agent,state_key) "
                        "VALUES ('JARVIS','blocked')"
                    )
            finally:
                await bridge.close()

            original_chat_policy = await admin.fetchval(
                "SELECT count(*) FROM pg_policies WHERE schemaname='soul_v3' "
                "AND tablename='chat_messages' AND policyname='ada_bridge_chat_read'"
            )
            assert original_chat_policy == 1

            await admin.execute(DOWN_SQL)
            await admin.execute(DOWN_SQL)  # idempotent rollback
            assert await admin.fetchval(
                "SELECT count(*) FROM pg_roles "
                "WHERE rolname='ada_bridge_identity_owner'"
            ) == 1
            assert await admin.fetchval(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid=p.pronamespace WHERE n.nspname='soul_v3' "
                "AND p.proname='ada_bridge_session_tenant_id'"
            ) == 0
            restored_function = await admin.fetchrow(
                "SELECT r.rolname AS owner,p.prosecdef,p.proconfig "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "JOIN pg_roles r ON r.oid=p.proowner "
                "WHERE n.nspname='soul_v3' "
                "AND p.proname='ada_bridge_session_agent'"
            )
            assert dict(restored_function) == {
                "owner": "seal",
                "prosecdef": False,
                "proconfig": ["search_path=pg_catalog"],
            }
            restored_acl = await admin.fetch(
                "SELECT COALESCE(r.rolname,'PUBLIC') AS grantee,"
                "acl.privilege_type,acl.is_grantable "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(p.proacl) acl "
                "LEFT JOIN pg_roles r ON r.oid=acl.grantee "
                "WHERE n.nspname='soul_v3' "
                "AND p.proname='ada_bridge_session_agent'"
            )
            assert {
                (row["grantee"], row["privilege_type"], row["is_grantable"])
                for row in restored_acl
            } == {
                ("seal", "EXECUTE", False),
                ("pr_ada_bridge", "EXECUTE", False),
            }
            restored_policies = await admin.fetch(
                "SELECT tablename,permissive,roles,cmd FROM pg_policies "
                "WHERE schemaname='soul_v3' "
                "AND policyname='ada_bridge_hard_session_identity' "
                "AND tablename=ANY($1::text[]) ORDER BY tablename",
                ["identity", "working_state", "emotional_diary"],
            )
            assert len(restored_policies) == 3
            assert all(row["permissive"] == "RESTRICTIVE" for row in restored_policies)
            assert all(row["roles"] == ["pr_ada_bridge"] for row in restored_policies)
            assert all(row["cmd"] == "SELECT" for row in restored_policies)
            bridge = await _connect_as_bridge(admin)
            try:
                await bridge.execute("SELECT set_config('app.agent','JARVIS',false)")
                for table in ("identity", "working_state", "emotional_diary"):
                    assert await bridge.fetchval(
                        f"SELECT count(*) FROM soul_v3.{table} WHERE agent='JARVIS'"
                    ) == 0
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.working_state_events "
                    "WHERE agent='JARVIS'"
                ) == 1
                assert await bridge.fetchval(
                    "SELECT count(*) FROM soul_v3.memory_poisoning_feedback"
                ) == 1
            finally:
                await bridge.close()
        finally:
            await admin.close()

    asyncio.run(scenario())
