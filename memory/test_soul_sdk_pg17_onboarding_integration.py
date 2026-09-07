from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import soul_memory_sdk_onboard as onboard


ROOT = Path(__file__).resolve().parent
M055 = ROOT / "migrations" / "055_soul_sdk_tenant_role_boundary.sql"
M056 = ROOT / "migrations" / "056_soul_sdk_durable_metering.sql"
R056 = ROOT / "migrations" / "056_soul_sdk_durable_metering.rollback.sql"


BASE_SQL = r"""
CREATE SCHEMA soul_v3;
CREATE ROLE svc_soul_memory_sdk LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS INHERIT PASSWORD 'sdk-test-only';
CREATE ROLE soul_sdk_agent_api NOLOGIN;
CREATE ROLE soul_sdk_tenant_api NOLOGIN;
CREATE ROLE soul_sdk_runtime NOLOGIN;
CREATE TABLE soul_v3.tenants (
  id uuid PRIMARY KEY, name text UNIQUE NOT NULL, is_internal boolean NOT NULL,
  api_keys jsonb NOT NULL DEFAULT '[]', quotas jsonb NOT NULL DEFAULT '{}'
);
ALTER TABLE soul_v3.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.tenants FORCE ROW LEVEL SECURITY;
CREATE TABLE soul_v3.memories (tenant_id uuid NOT NULL, agent text, scope text);
CREATE TABLE soul_v3.inner_monologue (tenant_id uuid NOT NULL);
CREATE TABLE soul_v3.distilled_exchanges (tenant_id uuid NOT NULL);
CREATE TABLE soul_v3.session_memory (tenant_id uuid NOT NULL);
CREATE TABLE soul_v3.memories_archive (tenant_id uuid NOT NULL);
CREATE TABLE soul_v3.memory_retrieval_log (tenant_id uuid NOT NULL, agent_requesting text);
INSERT INTO soul_v3.tenants(id,name,is_internal)
VALUES ('00000000-0000-0000-0000-000000000000','internal',true);
"""


def _run(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), input=input_text, text=True, capture_output=True, check=check, timeout=60
    )


def _psql(container: str, sql: str, *, user: str = "postgres", check: bool = True):
    command = ["docker", "exec", "-i"]
    if user != "postgres":
        command += ["-e", "PGPASSWORD=operator-test-only"]
    command += [container, "psql", "-X", "-v", "ON_ERROR_STOP=1"]
    if user != "postgres":
        command += ["-h", "127.0.0.1"]
    command += ["-U", user, "-d", "sdk_test", "-At"]
    return _run(*command, input_text=sql, check=check)


def test_pg17_wrapper_acl_inspection_and_safe_reapply() -> None:
    if not shutil.which("docker"):
        pytest.skip("docker unavailable")
    if _run("docker", "image", "inspect", "postgres:17-alpine", check=False).returncode:
        pytest.skip("cached postgres:17-alpine unavailable")

    container = f"seal-sdk-pg17-{uuid4().hex[:12]}"
    _run(
        "docker", "run", "-d", "--name", container, "-p", "127.0.0.1::5432",
        "-e", "POSTGRES_PASSWORD=pg17-test-only",
        "-e", "POSTGRES_DB=sdk_test", "postgres:17-alpine",
    )
    try:
        for _ in range(40):
            if _run("docker", "exec", container, "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("isolated PostgreSQL 17 did not become ready")
        port = int(
            _run("docker", "port", container, "5432/tcp").stdout.strip().rsplit(":", 1)[1]
        )

        _psql(container, BASE_SQL)
        _psql(container, M055.read_text())
        _psql(container, M056.read_text())
        # Both migrations must be safely re-applicable under the real PG17
        # role/membership-option implementation.
        _psql(container, M055.read_text())
        _psql(container, M056.read_text())

        _psql(
            container,
            """
            CREATE ROLE svc_soul_sdk_onboard_william LOGIN NOSUPERUSER NOCREATEDB
              NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT
              PASSWORD 'operator-test-only';
            GRANT soul_sdk_onboarding_operator TO svc_soul_sdk_onboard_william
              WITH INHERIT TRUE, SET TRUE;
            """,
        )
        _psql(
            container,
            """
            CREATE ROLE svc_soul_sdk_onboard_henry LOGIN NOSUPERUSER NOCREATEDB
              NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT
              PASSWORD 'operator-test-only';
            GRANT soul_sdk_onboarding_operator TO svc_soul_sdk_onboard_henry
              WITH INHERIT FALSE, SET TRUE;
            """,
        )
        assert _psql(
            container, "SELECT * FROM soul_v3.sdk_lock_external_plan('pro');",
            user="svc_soul_sdk_onboard_henry", check=False,
        ).returncode != 0
        tenant = "12345678-1234-4abc-8def-1234567890ab"
        operator_sql = f"""
        BEGIN;
        SELECT plan_id FROM soul_v3.sdk_lock_external_plan('pro');
        INSERT INTO soul_v3.tenants(id,name,is_internal,api_keys,quotas)
          VALUES ('{tenant}','Acme PG17',false,'[]','{{}}');
        SELECT * FROM soul_v3.sdk_provision_external_tenant_roles('{tenant}');
        INSERT INTO soul_v3.sdk_tenant_subscriptions(tenant_id,plan_id,status)
          VALUES ('{tenant}','pro','active');
        COMMIT;
        SELECT * FROM soul_v3.sdk_provision_external_tenant_roles('{tenant}');
        """
        applied = _psql(container, operator_sql, user="svc_soul_sdk_onboard_william")
        assert "soul_sdk_t_1234567812344abc8def1234567890ab_agent" in applied.stdout

        # Operator needs only EXECUTE on the narrow plan-lock validator.
        assert _psql(
            container, "SELECT plan_id FROM soul_v3.sdk_usage_plans LIMIT 1;",
            user="svc_soul_sdk_onboard_william", check=False,
        ).returncode != 0
        assert _psql(
            container, "UPDATE soul_v3.sdk_usage_plans SET updated_at=now() WHERE plan_id='pro';",
            user="svc_soul_sdk_onboard_william", check=False,
        ).returncode != 0
        # Subscription INSERT is limited to exactly the three control columns.
        assert _psql(
            container,
            f"INSERT INTO soul_v3.sdk_tenant_subscriptions(tenant_id,plan_id,status,valid_from) "
            f"VALUES ('{tenant}','pro','active',now());",
            user="svc_soul_sdk_onboard_william", check=False,
        ).returncode != 0

        contract = _psql(
            container,
            f"""
            SELECT count(*)=2 FROM pg_roles
             WHERE rolname LIKE 'soul_sdk_t_{tenant.replace('-', '')}_%'
               AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
               AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
               AND rolinherit;
            SELECT bool_and(m.inherit_option AND m.set_option)
              FROM pg_auth_members m JOIN pg_roles child ON child.oid=m.member
             WHERE child.rolname LIKE 'soul_sdk_t_{tenant.replace('-', '')}_%'
               AND m.roleid IN (SELECT oid FROM pg_roles WHERE rolname IN
                 ('soul_sdk_tenant_bound','soul_sdk_agent_api','soul_sdk_tenant_api'));
            SELECT bool_and(NOT m.inherit_option AND m.set_option)
              FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
              JOIN pg_roles member ON member.oid=m.member
             WHERE parent.rolname LIKE 'soul_sdk_t_{tenant.replace('-', '')}_%'
               AND member.rolname='svc_soul_memory_sdk';
            """,
        )
        assert contract.stdout.splitlines() == ["t", "t", "t"]

        # Exercise the ambiguous-outcome inspector against real PG17 catalogs,
        # JSONB, ACLs and membership-option columns. One exact key commits;
        # adding any second key makes the result divergent.
        request = onboard.build_request(
            tenant_id=tenant,
            tenant_name="Acme PG17",
            scopes=["read", "write"],
            agents=["ADA"],
            key_label="acme-pg17-1",
            plan_id="pro",
            expires_at="2027-01-01T00:00:00+00:00",
            ttl_seconds=None,
        )
        raw_key = "sk-soul-PG17-EXACT-INSPECTION"
        expected = onboard.build_expected_key_record(request, raw_key, "William")

        async def inspect_exact() -> None:
            conn = await asyncpg.connect(
                host="127.0.0.1", port=port, database="sdk_test",
                user="svc_soul_sdk_onboard_william", password="operator-test-only",
            )
            try:
                await conn.execute(
                    "UPDATE soul_v3.tenants SET api_keys=$2::jsonb WHERE id=$1::uuid",
                    tenant, json.dumps([expected]),
                )
                outcome, recovered = await onboard._inspect_outcome(
                    conn, request, raw_key, expected
                )
                assert outcome == "committed" and recovered is not None
                await conn.execute(
                    "UPDATE soul_v3.tenants SET api_keys=api_keys || $2::jsonb WHERE id=$1::uuid",
                    tenant, json.dumps([dict(expected, sha256="f" * 64)]),
                )
                outcome, recovered = await onboard._inspect_outcome(
                    conn, request, raw_key, expected
                )
                assert outcome == "divergent" and recovered is None
            finally:
                await conn.close()

        asyncio.run(inspect_exact())

        # A matching pre-existing elevated role is rejected, not "normalized"
        # by an ALTER that a CREATEROLE wrapper cannot legally perform.
        unsafe_tenant = "22345678-1234-4abc-8def-1234567890ab"
        unsafe_role = "soul_sdk_t_2234567812344abc8def1234567890ab_agent"
        _psql(
            container,
            f"""
            INSERT INTO soul_v3.tenants(id,name,is_internal) VALUES
              ('{unsafe_tenant}','Unsafe fixture',false);
            CREATE ROLE {unsafe_role} SUPERUSER NOLOGIN;
            INSERT INTO soul_v3.sdk_tenant_role_bindings(db_role,tenant_id,viewer,binding_kind)
              VALUES ('{unsafe_role}','{unsafe_tenant}','agent','tenant');
            """,
        )
        unsafe = _psql(
            container,
            f"SELECT * FROM soul_v3.sdk_provision_external_tenant_roles('{unsafe_tenant}');",
            user="svc_soul_sdk_onboard_william", check=False,
        )
        assert unsafe.returncode != 0
        assert "unsafe attributes" in unsafe.stderr

        # Isolate the external-state preflight from the operator-member check,
        # then prove a failed rollback is an all-or-nothing no-op.
        _psql(
            container,
            "REVOKE soul_sdk_onboarding_operator FROM "
            "svc_soul_sdk_onboard_william, svc_soul_sdk_onboard_henry;",
        )
        snapshot_sql = """
        SELECT md5(jsonb_build_object(
          'tenants', (SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM soul_v3.tenants t),
          'subscriptions', (SELECT jsonb_agg(to_jsonb(s) ORDER BY tenant_id) FROM soul_v3.sdk_tenant_subscriptions s),
          'bindings', (SELECT jsonb_agg(to_jsonb(b) ORDER BY db_role) FROM soul_v3.sdk_tenant_role_bindings b),
          'roles', (SELECT jsonb_agg(rolname ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'soul_sdk_%'),
          'objects', jsonb_build_array(
            to_regprocedure('soul_v3.sdk_lock_external_plan(text)')::text,
            to_regprocedure('soul_v3.sdk_provision_external_tenant_roles(uuid)')::text,
            to_regclass('soul_v3.sdk_usage_plans')::text
          )
        )::text);
        """
        before = _psql(container, snapshot_sql).stdout.strip()
        blocked = _psql(container, R056.read_text(), check=False)
        assert blocked.returncode != 0
        assert "rollback blocked before DDL" in blocked.stderr
        after = _psql(container, snapshot_sql).stdout.strip()
        assert after == before
    finally:
        _run("docker", "rm", "-f", container, check=False)


def test_056_static_acl_and_rollback_contract() -> None:
    sql = M056.read_text()
    rollback = (ROOT / "migrations" / "056_soul_sdk_durable_metering.rollback.sql").read_text()
    assert "GRANT INSERT (tenant_id, plan_id, status)" in sql
    assert "REVOKE SELECT, UPDATE ON soul_v3.sdk_usage_plans FROM soul_sdk_onboarding_operator" in sql
    assert "OWNER TO soul_sdk_plan_lock_owner" in sql
    assert "membership.inherit_option IS TRUE" in sql
    assert "DROP FUNCTION IF EXISTS soul_v3.sdk_lock_external_plan(text)" in rollback
    assert "sdk_plan_lock_read" in rollback and "sdk_plan_lock_update" in rollback
    assert "durable identities" in rollback
    assert "dropped by this rollback" in rollback
    assert rollback.index("rollback blocked before DDL") < rollback.index("DROP TABLE IF EXISTS")
    for counter in (
        "v_external_tenants",
        "v_external_subscriptions",
        "v_usage_windows",
        "v_usage_events",
        "v_external_bindings",
        "v_external_roles",
        "v_operator_members",
    ):
        assert counter in rollback
    rollback_statements = "\n".join(
        line for line in rollback.splitlines() if not line.lstrip().startswith("--")
    )
    assert "CASCADE" not in rollback_statements


def test_pg17_clean_056_reapply_then_rollback_completes() -> None:
    if not shutil.which("docker"):
        pytest.skip("docker unavailable")
    if _run("docker", "image", "inspect", "postgres:17-alpine", check=False).returncode:
        pytest.skip("cached postgres:17-alpine unavailable")
    container = f"seal-sdk-pg17-rollback-{uuid4().hex[:10]}"
    _run(
        "docker", "run", "-d", "--name", container,
        "-e", "POSTGRES_PASSWORD=pg17-test-only",
        "-e", "POSTGRES_DB=sdk_test", "postgres:17-alpine",
    )
    try:
        for _ in range(40):
            if _run("docker", "exec", container, "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("isolated PostgreSQL 17 did not become ready")
        _psql(container, BASE_SQL)
        _psql(container, M055.read_text())
        _psql(container, M056.read_text())
        _psql(container, M056.read_text())
        rolled_back = _psql(container, R056.read_text(), check=False)
        assert rolled_back.returncode == 0, rolled_back.stderr
        state = _psql(
            container,
            """
            SELECT to_regclass('soul_v3.sdk_usage_plans') IS NULL;
            SELECT to_regprocedure('soul_v3.sdk_lock_external_plan(text)') IS NULL;
            SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
              ('soul_sdk_metering_owner','soul_sdk_plan_lock_owner',
               'soul_sdk_onboarding_owner','soul_sdk_onboarding_operator'));
            """,
        )
        assert state.stdout.splitlines() == ["t", "t", "t"]
    finally:
        _run("docker", "rm", "-f", container, check=False)
