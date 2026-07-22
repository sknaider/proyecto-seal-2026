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
    assert "soul_sdk_t_%s_%s" in sql
    assert "replace(p_tenant_id::text, '-', '')" in sql
    assert "('agent'::text, 'soul_sdk_agent_api'::text)" in sql
    assert "('user'::text, 'soul_sdk_tenant_api'::text)" in sql
    assert "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "NOREPLICATION NOBYPASSRLS INHERIT" in sql
    assert "tenant role name collision" in sql
    assert "tenant role has unexpected parent membership" in sql
    assert "GRANT %I TO %I" in sql


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
    """Transactional integration gate; requires an explicit admin test DSN.

    The migration must already be installed in the target test database. Every
    fixture row and role created here is rolled back with the outer transaction.
    This test is intentionally not run against the live database by default.
    """

    dsn = os.environ.get("SEAL_SDK_TENANT_BOUNDARY_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("set SEAL_SDK_TENANT_BOUNDARY_TEST_DSN for the transactional DB gate")

    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(dsn)
    tx = conn.transaction()
    await tx.start()
    try:
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
            SELECT db_role::text
            FROM soul_v3.provision_sdk_tenant_roles($1, $2::name)
            WHERE viewer = 'agent'
            """,
            tenant_a,
            login_role,
        )
        role_b = await conn.fetchval(
            """
            SELECT db_role::text
            FROM soul_v3.provision_sdk_tenant_roles($1, $2::name)
            WHERE viewer = 'agent'
            """,
            tenant_b,
            login_role,
        )
        assert re.fullmatch(r"soul_sdk_t_[0-9a-f]{32}_agent", role_a)
        assert re.fullmatch(r"soul_sdk_t_[0-9a-f]{32}_agent", role_b)

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

        await conn.execute(f'GRANT EXECUTE ON FUNCTION soul_v3.sdk_resolve_tenant_role_for_key_hash(text, text) TO "{login_role}"')
        await conn.execute(
            f'CREATE ROLE "{unmapped_role}" NOLOGIN NOSUPERUSER NOCREATEDB '
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
        )
        await conn.execute(f'GRANT soul_sdk_tenant_bound, soul_sdk_agent_api TO "{unmapped_role}"')
        await conn.execute(f'GRANT "{unmapped_role}" TO "{login_role}"')
        await conn.execute(f'SET LOCAL SESSION AUTHORIZATION "{login_role}"')

        resolved_a = await conn.fetchrow(
            "SELECT * FROM soul_v3.sdk_resolve_tenant_role_for_key_hash($1, 'agent')",
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
        await tx.rollback()
        await conn.close()
