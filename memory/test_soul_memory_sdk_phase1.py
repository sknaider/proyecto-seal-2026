from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from soul_memory_sdk_phase1 import (  # noqa: E402
    CONFIRM_TEXT,
    CORE_TABLES,
    INTERNAL_TENANT_ID,
    SDK_RUNTIME_ROLE,
    Phase1Preflight,
    TablePreflight,
    build_phase1_plan,
)


def test_phase1_plan_covers_all_core_tables() -> None:
    sql = build_phase1_plan().sql()
    for table in CORE_TABLES:
        assert f"ALTER TABLE soul_v3.{table} ADD COLUMN IF NOT EXISTS tenant_id uuid;" in sql
        assert f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE soul_v3.{table} FORCE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE soul_v3.{table} ALTER COLUMN tenant_id SET DEFAULT '{INTERNAL_TENANT_ID}'::uuid;" in sql
        assert f"CREATE INDEX IF NOT EXISTS {table}_tenant_id_idx ON soul_v3.{table} (tenant_id);" in sql
        assert f"CREATE POLICY tenant_isolation_sdk_v1\nON soul_v3.{table}" in sql
        assert f"{table}_tenant_id_fkey" in sql
        assert f"pg_get_serial_sequence('soul_v3.{table}', 'id')" in sql


def test_phase1_plan_uses_real_internal_tenant_not_magic_string() -> None:
    sql = build_phase1_plan().sql()
    assert INTERNAL_TENANT_ID in sql
    assert "INSERT INTO soul_v3.tenants" in sql
    assert "SEAL Internal" in sql
    assert "is_internal" in sql


def test_phase1_plan_creates_non_superuser_runtime_role() -> None:
    sql = build_phase1_plan().sql()
    assert f"CREATE ROLE {SDK_RUNTIME_ROLE} NOLOGIN NOBYPASSRLS;" in sql
    assert f"ALTER ROLE {SDK_RUNTIME_ROLE} NOBYPASSRLS;" in sql
    assert f"GRANT USAGE ON SCHEMA soul_v3 TO {SDK_RUNTIME_ROLE};" in sql
    assert f"GRANT SELECT, INSERT, UPDATE ON soul_v3.agents TO {SDK_RUNTIME_ROLE};" in sql
    assert f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3 TO {SDK_RUNTIME_ROLE};" in sql
    assert f"ALTER DEFAULT PRIVILEGES IN SCHEMA soul_v3 GRANT USAGE, SELECT ON SEQUENCES TO {SDK_RUNTIME_ROLE};" in sql
    for table in CORE_TABLES:
        assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON soul_v3.{table} TO {SDK_RUNTIME_ROLE};" in sql


def test_phase1_plan_hashes_memory_content_in_database() -> None:
    sql = build_phase1_plan().sql()
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto;" in sql
    assert "content_hash_sha256 char(64)" in sql
    assert "encode(digest(coalesce(NEW.content, ''), 'sha256'), 'hex')" in sql
    assert "memories_tenant_content_hash_idx" in sql


def test_phase1_plan_derives_tenant_from_transaction_setting() -> None:
    sql = build_phase1_plan().sql()
    assert "current_setting('app.tenant_id', true)" in sql
    assert "CREATE OR REPLACE FUNCTION soul_v3.current_tenant_id()" in sql
    assert "WHEN invalid_text_representation THEN" in sql
    assert "tenant_id = soul_v3.current_tenant_id()" in sql
    assert "WITH CHECK" in sql


def test_phase1_apply_requires_exact_confirmation() -> None:
    assert CONFIRM_TEXT == "APPLY SOUL SDK PHASE1 MIGRATION"


def test_phase1_preflight_expected_count_properties() -> None:
    preflight = Phase1Preflight(
        current_user="seal",
        current_user_is_superuser=True,
        sdk_runtime_role_exists=False,
        sdk_runtime_role_can_bypass_rls=None,
        internal_tenant_exists=False,
        pgcrypto_installed=True,
        current_tenant_function_exists=False,
        memories_content_hash_column_exists=False,
        memories_content_hash_null_rows=None,
        memories_content_hash_trigger_exists=False,
        tables=(
            TablePreflight(
                table="memories",
                exists=True,
                row_count=10,
                tenant_column_exists=False,
                null_tenant_rows=None,
                rls_enabled=False,
                force_rls_enabled=False,
                policy_exists=False,
                fk_exists=False,
                tenant_index_exists=False,
            ),
            TablePreflight(
                table="inner_monologue",
                exists=True,
                row_count=5,
                tenant_column_exists=True,
                null_tenant_rows=0,
                rls_enabled=True,
                force_rls_enabled=True,
                policy_exists=True,
                fk_exists=True,
                tenant_index_exists=True,
            ),
        ),
    )
    assert preflight.tenant_backfill_count == 10
    assert preflight.memories_hash_count == 10
