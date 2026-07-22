from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "055_soul_sdk_tenant_role_boundary.sql"
ROLLBACK = ROOT / "migrations" / "055_soul_sdk_tenant_role_boundary.rollback.sql"
CORE_TABLES = (
    "memories",
    "inner_monologue",
    "distilled_exchanges",
    "session_memory",
    "memories_archive",
    "memory_retrieval_log",
)
INTERNAL_TENANT = "00000000-0000-0000-0000-000000000000"


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def rollback_sql() -> str:
    return ROLLBACK.read_text(encoding="utf-8")


def test_migration_covers_six_core_tables_with_restrictive_current_user_rls() -> None:
    sql = migration_sql()
    for table in CORE_TABLES:
        assert f"'{table}'" in sql

    policy_fragment = sql[sql.index("-- Restrictive policies"):]
    assert "AS RESTRICTIVE FOR ALL" in policy_fragment
    assert "tenant_id = soul_v3.sdk_current_tenant_id()" in policy_fragment
    assert "TO soul_sdk_tenant_bound, soul_sdk_agent_api, soul_sdk_tenant_api, soul_sdk_runtime" in policy_fragment

    identity_function = sql[
        sql.index("CREATE OR REPLACE FUNCTION soul_v3.sdk_current_tenant_id()"):
        sql.index("CREATE OR REPLACE FUNCTION soul_v3.provision_sdk_tenant_roles")
    ]
    assert "SECURITY INVOKER" in identity_function
    assert "binding.db_role = current_user::name" in identity_function
    assert "current_setting('app.tenant_id'" not in identity_function


def test_missing_or_disabled_role_mapping_fails_closed() -> None:
    sql = migration_sql()
    assert "db_role name PRIMARY KEY" in sql
    assert "disabled_at timestamptz" in sql
    assert "binding.db_role = current_user::name" in sql
    assert "binding.disabled_at IS NULL" in sql
    assert "USING (db_role = current_user::name AND disabled_at IS NULL)" in sql
    assert "COALESCE" not in sql[
        sql.index("CREATE OR REPLACE FUNCTION soul_v3.sdk_current_tenant_id()"):
        sql.index("REVOKE ALL ON FUNCTION soul_v3.sdk_current_tenant_id()")
    ]


def test_provisioner_creates_deterministic_least_privilege_roles_per_viewer() -> None:
    sql = migration_sql()
    provisioner = sql[
        sql.index("CREATE OR REPLACE FUNCTION soul_v3.provision_sdk_tenant_roles"):
        sql.index("REVOKE ALL ON FUNCTION soul_v3.provision_sdk_tenant_roles")
    ]
    assert "RETURNS TABLE(resolved_viewer text, resolved_db_role name)" in sql
    assert "ON CONFLICT ON CONSTRAINT sdk_tenant_role_bindings_pkey" in sql
    assert "ON CONFLICT (db_role) DO UPDATE" not in sql
    assert sql.count("#variable_conflict error") >= 2
    assert "soul_sdk_t_%s_%s" in sql
    assert "replace(p_tenant_id::text, '-', '')" in sql
    assert "('agent'::text, 'soul_sdk_agent_api'::text)" in sql
    assert "('user'::text, 'soul_sdk_tenant_api'::text)" in sql
    assert "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "NOREPLICATION NOBYPASSRLS INHERIT" in sql
    assert "tenant role name collision" in sql
    assert "tenant role has unexpected parent membership" in sql
    assert "GRANT %I TO %I" in sql
    assert "GRANT USAGE ON SCHEMA soul_v3 TO %I" in provisioner
    assert (
        "soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text) TO %I"
        in provisioner
    )
    assert "GRANT SELECT" not in provisioner
    assert "GRANT ALL" not in provisioner


def test_hash_resolver_is_server_side_unique_and_checks_role_assumption() -> None:
    sql = migration_sql()
    resolver = sql[
        sql.index("CREATE OR REPLACE FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash"):
        sql.index("-- Restrictive policies")
    ]
    assert "p_key_hash !~ '^[0-9a-f]{64}$'" in resolver
    assert "key_record->>'sha256'" in resolver
    assert "key_record->>'revoked_at'" in resolver
    assert "key_record->>'expires_at'" in resolver
    assert "binding.binding_kind = 'tenant'" in resolver
    assert "tenant_role.rolcanlogin IS FALSE" in resolver
    assert "tenant_role.rolbypassrls IS FALSE" in resolver
    assert "viewer_membership.inherit_option IS TRUE" in resolver
    assert "tenant_role.rolname = format(" in resolver
    assert "unexpected_parent.rolname NOT IN" in resolver
    assert "login_role.rolname = session_user" in resolver
    assert "login_membership.set_option IS TRUE" in resolver
    assert "HAVING count(*) = 1" in resolver
    assert "raw" not in resolver.lower()
    sdk_login_grants = sql[
        sql.index(
            "REVOKE ALL ON FUNCTION "
            "soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text)"
        ):
        sql.index("-- Restrictive policies")
    ]
    assert "GRANT USAGE ON SCHEMA soul_v3 TO svc_soul_memory_sdk" in sdk_login_grants
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text)"
        in sdk_login_grants
    )
    assert "GRANT SELECT" not in sdk_login_grants
    assert "GRANT ALL" not in sdk_login_grants


def test_live_internal_tenant_has_canonical_and_legacy_compatibility_bindings() -> None:
    sql = migration_sql()
    assert INTERNAL_TENANT in sql
    assert "FROM soul_v3.provision_sdk_tenant_roles(" in sql
    for role in ("soul_sdk_agent_api", "soul_sdk_tenant_api", "soul_sdk_runtime"):
        assert f"'{role}'::name" in sql
    assert "'legacy_internal'" in sql
    assert "can never resolve external tenants" in sql


def test_capability_policies_do_not_use_tenant_guc() -> None:
    sql = migration_sql()
    capability_fragment = sql[sql.index("-- Tenant-role capability policies"):]
    assert "current_setting('app.tenant_id'" not in capability_fragment
    assert "current_setting('app.agent'" in capability_fragment
    assert "current_setting('app.viewer'" in capability_fragment


def test_rollback_is_scope_gated_before_destructive_statements() -> None:
    sql = rollback_sql()
    gate = sql.index("rollback blocked:")
    drop_table = sql.index("DROP TABLE soul_v3.sdk_tenant_role_bindings")
    assert gate < drop_table
    assert "tenant_id <> '00000000-0000-0000-0000-000000000000'::uuid" in sql
    assert "Run only after William confirms the exact rollback scope" in sql


@pytest.mark.asyncio
async def test_adversarial_guc_forgery_role_mismatch_and_unmapped_role() -> None:
    """Apply migration 055, attack it, then remove it from an explicit test DB.

    The DSN must point to a disposable clone with the pre-055 SDK schema and an
    admin login. The test refuses a database where 055 is already present. It
    runs the migration twice (syntax + idempotence), rolls adversarial fixtures
    back transactionally, and executes the scope-gated rollback in ``finally``.
    """

    dsn = os.environ.get("SEAL_SDK_TENANT_BOUNDARY_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("set SEAL_SDK_TENANT_BOUNDARY_TEST_DSN for the transactional DB gate")

    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(dsn)
    tx = None
    migration_applied = False
    try:
        preflight = await conn.fetchrow(
            """
            SELECT
              to_regclass('soul_v3.sdk_tenant_role_bindings') IS NOT NULL AS has_table,
              to_regprocedure('soul_v3.sdk_current_tenant_id()') IS NOT NULL AS has_identity_fn,
              EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'soul_sdk_tenant_bound'
              ) AS has_boundary_role,
              (SELECT rolsuper FROM pg_roles WHERE rolname = session_user) AS is_superuser
            """
        )
        assert preflight is not None
        if preflight["has_table"] or preflight["has_identity_fn"] or preflight["has_boundary_role"]:
            pytest.fail("test DB must be a disposable pre-055 clone; refusing existing 055 objects")
        if not preflight["is_superuser"]:
            pytest.fail("test DB login must be an admin able to create transactional roles")

        try:
            await conn.execute(migration_sql())
        except Exception:
            if conn.is_in_transaction():
                await conn.execute("ROLLBACK")
            raise
        migration_applied = True

        # A second real apply is the idempotence gate, not a string assertion.
        await conn.execute(migration_sql())
        assert await conn.fetchval(
            """
            SELECT count(*)
            FROM soul_v3.sdk_tenant_role_bindings
            WHERE tenant_id = $1::uuid AND disabled_at IS NULL
            """,
            INTERNAL_TENANT,
        ) == 5

        tx = conn.transaction()
        await tx.start()
        tenant_a = uuid4()
        tenant_b = uuid4()
        raw_a = "sk-soul-boundary-a-" + uuid4().hex
        raw_b = "sk-soul-boundary-b-" + uuid4().hex
        hash_a = hashlib.sha256(raw_a.encode()).hexdigest()
        hash_b = hashlib.sha256(raw_b.encode()).hexdigest()
        marker = "sdk-boundary-" + uuid4().hex
        login_role = "soul_sdk_boundary_test_" + uuid4().hex[:12]
        unmapped_role = "soul_sdk_t_unmapped_" + uuid4().hex[:12]
        assert re.fullmatch(r"[a-z0-9_]+", login_role)
        assert re.fullmatch(r"[a-z0-9_]+", unmapped_role)

        await conn.execute(
            f'CREATE ROLE "{login_role}" LOGIN NOSUPERUSER NOCREATEDB '
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
        )
        await conn.executemany(
            """
            INSERT INTO soul_v3.tenants (id, name, api_keys)
            VALUES ($1, $2, $3::jsonb)
            """,
            (
                (tenant_a, marker + "-a", json.dumps([{"sha256": hash_a}])),
                (tenant_b, marker + "-b", json.dumps([{"sha256": hash_b}])),
            ),
        )

        role_a = await conn.fetchval(
            """
            SELECT resolved_db_role::text
            FROM soul_v3.provision_sdk_tenant_roles($1::uuid, $2::name)
            WHERE resolved_viewer = 'agent'
            """,
            tenant_a,
            login_role,
        )
        role_b = await conn.fetchval(
            """
            SELECT resolved_db_role::text
            FROM soul_v3.provision_sdk_tenant_roles($1::uuid, $2::name)
            WHERE resolved_viewer = 'agent'
            """,
            tenant_b,
            login_role,
        )
        assert re.fullmatch(r"soul_sdk_t_[0-9a-f]{32}_agent", role_a)
        assert re.fullmatch(r"soul_sdk_t_[0-9a-f]{32}_agent", role_b)

        login_privileges = await conn.fetchrow(
            """
            SELECT
              has_schema_privilege($1::name, 'soul_v3', 'USAGE') AS schema_usage,
              has_schema_privilege($1::name, 'soul_v3', 'CREATE') AS schema_create,
              has_function_privilege(
                $1::name,
                'soul_v3.sdk_resolve_tenant_role_for_key_hash(text,text)',
                'EXECUTE'
              ) AS resolver_execute,
              has_function_privilege(
                $1::name,
                'soul_v3.provision_sdk_tenant_roles(uuid,name)',
                'EXECUTE'
              ) AS provisioner_execute,
              has_table_privilege(
                $1::name, 'soul_v3.tenants', 'SELECT'
              ) AS tenants_select,
              has_table_privilege(
                $1::name, 'soul_v3.sdk_tenant_role_bindings', 'SELECT'
              ) AS bindings_select
            """,
            login_role,
        )
        assert login_privileges["schema_usage"]
        assert login_privileges["resolver_execute"]
        assert not login_privileges["schema_create"]
        assert not login_privileges["provisioner_execute"]
        assert not login_privileges["tenants_select"]
        assert not login_privileges["bindings_select"]

        await conn.executemany(
            """
            INSERT INTO soul_v3.memories (tenant_id, agent, category, content)
            VALUES ($1, 'ADA', 'fact', $2)
            """,
            (
                (tenant_a, marker + "-row-a"),
                (tenant_b, marker + "-row-b"),
            ),
        )

        await conn.execute(
            f'CREATE ROLE "{unmapped_role}" NOLOGIN NOSUPERUSER NOCREATEDB '
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
        )
        await conn.execute(f'GRANT soul_sdk_tenant_bound, soul_sdk_agent_api TO "{unmapped_role}"')
        await conn.execute(f'GRANT "{unmapped_role}" TO "{login_role}"')
        await conn.execute(f'SET LOCAL SESSION AUTHORIZATION "{login_role}"')

        resolved_a = await conn.fetchrow(
            "SELECT * FROM soul_v3.sdk_resolve_tenant_role_for_key_hash($1::text, 'agent'::text)",
            hash_a,
        )
        assert resolved_a and resolved_a["tenant_id"] == tenant_a
        assert resolved_a["db_role"] == role_a

        # GUC says B, current_user says A: only A is visible.
        await conn.execute(f'SET LOCAL ROLE "{role_a}"')
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_b))
        await conn.execute("SELECT set_config('app.agent', 'ADA', true)")
        rows = await conn.fetch(
            "SELECT tenant_id FROM soul_v3.memories WHERE content LIKE $1 ORDER BY content",
            marker + "-row-%",
        )
        assert [row["tenant_id"] for row in rows] == [tenant_a]
        await conn.execute("RESET ROLE")

        # Role mismatch in the other direction stays bound to current_user B.
        await conn.execute(f'SET LOCAL ROLE "{role_b}"')
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        await conn.execute("SELECT set_config('app.agent', 'ADA', true)")
        rows = await conn.fetch(
            "SELECT tenant_id FROM soul_v3.memories WHERE content LIKE $1 ORDER BY content",
            marker + "-row-%",
        )
        assert [row["tenant_id"] for row in rows] == [tenant_b]
        await conn.execute("RESET ROLE")

        # A role with capability membership but no binding gets zero rows.
        await conn.execute(f'SET LOCAL ROLE "{unmapped_role}"')
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        await conn.execute("SELECT set_config('app.agent', 'ADA', true)")
        assert await conn.fetchval(
            "SELECT count(*) FROM soul_v3.memories WHERE content LIKE $1",
            marker + "-row-%",
        ) == 0
    finally:
        if tx is not None and conn.is_in_transaction():
            await tx.rollback()
        if conn.is_in_transaction():
            await conn.execute("ROLLBACK")
        if migration_applied:
            await conn.execute(rollback_sql())
            cleanup = await conn.fetchrow(
                """
                SELECT
                  to_regclass('soul_v3.sdk_tenant_role_bindings') IS NULL AS table_removed,
                  to_regprocedure('soul_v3.sdk_current_tenant_id()') IS NULL AS function_removed,
                  NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = 'soul_sdk_tenant_bound'
                  ) AS role_removed
                """
            )
            assert cleanup
            assert cleanup["table_removed"]
            assert cleanup["function_removed"]
            assert cleanup["role_removed"]
        await conn.close()
