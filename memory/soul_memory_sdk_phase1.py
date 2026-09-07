#!/usr/bin/env python3
"""SOUL Memory SDK Phase 1 tenant migration planner.

Default mode is read-only: it prints the idempotent SQL plan. Live schema writes
require both --apply and an exact confirmation string.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))
INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000000"
CONFIRM_TEXT = "APPLY SOUL SDK PHASE1 MIGRATION"
SDK_RUNTIME_ROLE = "soul_sdk_runtime"
CORE_TABLES = (
    "memories",
    "inner_monologue",
    "distilled_exchanges",
    "session_memory",
    "memories_archive",
    "memory_retrieval_log",
)


@dataclass(frozen=True)
class MigrationPlan:
    statements: tuple[str, ...]

    def sql(self) -> str:
        return "\n\n".join(self.statements) + "\n"


@dataclass(frozen=True)
class TablePreflight:
    table: str
    exists: bool
    row_count: int | None
    tenant_column_exists: bool
    null_tenant_rows: int | None
    rls_enabled: bool
    force_rls_enabled: bool
    policy_exists: bool
    fk_exists: bool
    tenant_index_exists: bool


@dataclass(frozen=True)
class Phase1Preflight:
    current_user: str
    current_user_is_superuser: bool
    sdk_runtime_role_exists: bool
    sdk_runtime_role_can_bypass_rls: bool | None
    internal_tenant_exists: bool
    pgcrypto_installed: bool
    current_tenant_function_exists: bool
    memories_content_hash_column_exists: bool
    memories_content_hash_null_rows: int | None
    memories_content_hash_trigger_exists: bool
    tables: tuple[TablePreflight, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def tenant_backfill_count(self) -> int:
        return sum(t.row_count or 0 for t in self.tables if not t.tenant_column_exists)

    @property
    def memories_hash_count(self) -> int:
        memories = next(t for t in self.tables if t.table == "memories")
        if self.memories_content_hash_column_exists and self.memories_content_hash_null_rows is not None:
            return self.memories_content_hash_null_rows
        return memories.row_count or 0


def _qualified(table: str) -> str:
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name: {table!r}")
    return f"soul_v3.{table}"


def build_phase1_plan() -> MigrationPlan:
    statements: list[str] = [
        "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
        f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{SDK_RUNTIME_ROLE}') THEN
        CREATE ROLE {SDK_RUNTIME_ROLE} NOLOGIN NOBYPASSRLS;
    END IF;
    ALTER ROLE {SDK_RUNTIME_ROLE} NOBYPASSRLS;
END;
$$;
""".strip(),
        """
CREATE TABLE IF NOT EXISTS soul_v3.tenants (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    is_internal boolean NOT NULL DEFAULT false,
    api_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    quotas jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
""".strip(),
        f"""
INSERT INTO soul_v3.tenants (id, name, is_internal, quotas)
VALUES ('{INTERNAL_TENANT_ID}'::uuid, 'SEAL Internal', true, '{{}}'::jsonb)
ON CONFLICT (id) DO UPDATE
SET name=EXCLUDED.name,
    is_internal=EXCLUDED.is_internal;
""".strip(),
        """
CREATE OR REPLACE FUNCTION soul_v3.set_memory_content_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.content_hash_sha256 := encode(digest(coalesce(NEW.content, ''), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
""".strip(),
        """
CREATE OR REPLACE FUNCTION soul_v3.current_tenant_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    tenant text;
BEGIN
    tenant := current_setting('app.tenant_id', true);
    IF tenant IS NULL OR tenant = '' THEN
        RETURN NULL;
    END IF;
    RETURN tenant::uuid;
EXCEPTION
    WHEN invalid_text_representation THEN
        RETURN NULL;
END;
$$;
""".strip(),
        f"GRANT USAGE ON SCHEMA soul_v3 TO {SDK_RUNTIME_ROLE};",
        f"GRANT SELECT, INSERT, UPDATE ON soul_v3.agents TO {SDK_RUNTIME_ROLE};",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3 TO {SDK_RUNTIME_ROLE};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA soul_v3 GRANT USAGE, SELECT ON SEQUENCES TO {SDK_RUNTIME_ROLE};",
    ]

    for table in CORE_TABLES:
        qtable = _qualified(table)
        constraint = f"{table}_tenant_id_fkey"
        policy = "tenant_isolation_sdk_v1"
        statements.extend(
            [
                f"""
DO $$
DECLARE
    seq_name text;
BEGIN
    seq_name := pg_get_serial_sequence('{qtable}', 'id');
    IF seq_name IS NOT NULL THEN
        EXECUTE format(
            'SELECT setval(%L, GREATEST((SELECT COALESCE(MAX(id), 0) + 1 FROM {qtable}), 1), false)',
            seq_name
        );
    END IF;
END;
$$;
""".strip(),
                f"ALTER TABLE {qtable} ADD COLUMN IF NOT EXISTS tenant_id uuid;",
                f"""
UPDATE {qtable}
SET tenant_id = '{INTERNAL_TENANT_ID}'::uuid
WHERE tenant_id IS NULL;
""".strip(),
                f"ALTER TABLE {qtable} ALTER COLUMN tenant_id SET DEFAULT '{INTERNAL_TENANT_ID}'::uuid;",
                f"ALTER TABLE {qtable} ALTER COLUMN tenant_id SET NOT NULL;",
                f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = '{constraint}'
          AND conrelid = '{qtable}'::regclass
    ) THEN
        ALTER TABLE {qtable}
        ADD CONSTRAINT {constraint}
        FOREIGN KEY (tenant_id) REFERENCES soul_v3.tenants(id);
    END IF;
END;
$$;
""".strip(),
                f"ALTER TABLE {qtable} ENABLE ROW LEVEL SECURITY;",
                f"ALTER TABLE {qtable} FORCE ROW LEVEL SECURITY;",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qtable} TO {SDK_RUNTIME_ROLE};",
                f"CREATE INDEX IF NOT EXISTS {table}_tenant_id_idx ON {qtable} (tenant_id);",
                f"DROP POLICY IF EXISTS {policy} ON {qtable};",
                f"""
CREATE POLICY {policy}
ON {qtable}
USING (
    tenant_id = soul_v3.current_tenant_id()
)
WITH CHECK (
    tenant_id = soul_v3.current_tenant_id()
);
""".strip(),
            ]
        )

    statements.extend(
        [
            "ALTER TABLE soul_v3.memories ADD COLUMN IF NOT EXISTS content_hash_sha256 char(64);",
            """
UPDATE soul_v3.memories
SET content_hash_sha256 = encode(digest(coalesce(content, ''), 'sha256'), 'hex')
WHERE content_hash_sha256 IS NULL;
""".strip(),
            "ALTER TABLE soul_v3.memories ALTER COLUMN content_hash_sha256 SET NOT NULL;",
            """
CREATE INDEX IF NOT EXISTS memories_tenant_content_hash_idx
ON soul_v3.memories (tenant_id, content_hash_sha256)
WHERE invalid_at IS NULL;
""".strip(),
            """
DROP TRIGGER IF EXISTS memories_content_hash_sha256_tg ON soul_v3.memories;
CREATE TRIGGER memories_content_hash_sha256_tg
BEFORE INSERT OR UPDATE OF content
ON soul_v3.memories
FOR EACH ROW
EXECUTE FUNCTION soul_v3.set_memory_content_hash();
""".strip(),
        ]
    )
    return MigrationPlan(tuple(statements))


async def inspect_phase1_state() -> Phase1Preflight:
    conn = await asyncpg.connect(DB_URL)
    tables: list[TablePreflight] = []
    try:
        current_user = str(await conn.fetchval("SELECT current_user") or "")
        current_user_is_superuser = bool(
            await conn.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            )
        )
        runtime_row = await conn.fetchrow(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname=$1",
            SDK_RUNTIME_ROLE,
        )
        sdk_runtime_role_exists = runtime_row is not None
        sdk_runtime_role_can_bypass_rls = (
            bool(runtime_row["rolbypassrls"]) if runtime_row is not None else None
        )
        internal_tenant_exists = bool(
            await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM soul_v3.tenants
                    WHERE id=$1::uuid AND is_internal=true
                )
                """,
                INTERNAL_TENANT_ID,
            )
        ) if await conn.fetchval("SELECT to_regclass('soul_v3.tenants') IS NOT NULL") else False
        pgcrypto_installed = bool(
            await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgcrypto')")
        )
        current_tenant_function_exists = bool(
            await conn.fetchval(
                "SELECT to_regprocedure('soul_v3.current_tenant_id()') IS NOT NULL"
            )
        )
        memories_content_hash_column_exists = bool(
            await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema='soul_v3'
                      AND table_name='memories'
                      AND column_name='content_hash_sha256'
                )
                """
            )
        )
        memories_content_hash_null_rows = None
        if memories_content_hash_column_exists:
            memories_content_hash_null_rows = int(
                await conn.fetchval(
                    "SELECT count(*) FROM soul_v3.memories WHERE content_hash_sha256 IS NULL"
                )
                or 0
            )
        memories_content_hash_trigger_exists = bool(
            await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname='memories_content_hash_sha256_tg'
                      AND tgrelid='soul_v3.memories'::regclass
                      AND NOT tgisinternal
                )
                """
            )
        )

        for table in CORE_TABLES:
            qtable = _qualified(table)
            exists = bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", qtable))
            if not exists:
                tables.append(
                    TablePreflight(
                        table=table,
                        exists=False,
                        row_count=None,
                        tenant_column_exists=False,
                        null_tenant_rows=None,
                        rls_enabled=False,
                        force_rls_enabled=False,
                        policy_exists=False,
                        fk_exists=False,
                        tenant_index_exists=False,
                    )
                )
                continue
            row_count = int(await conn.fetchval(f"SELECT count(*) FROM {qtable}") or 0)
            tenant_column_exists = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema='soul_v3'
                          AND table_name=$1
                          AND column_name='tenant_id'
                    )
                    """,
                    table,
                )
            )
            null_tenant_rows = None
            if tenant_column_exists:
                null_tenant_rows = int(
                    await conn.fetchval(f"SELECT count(*) FROM {qtable} WHERE tenant_id IS NULL")
                    or 0
                )
            rls_enabled = bool(
                await conn.fetchval(
                    "SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass",
                    qtable,
                )
            )
            force_rls_enabled = bool(
                await conn.fetchval(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid=$1::regclass",
                    qtable,
                )
            )
            policy_exists = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM pg_policies
                        WHERE schemaname='soul_v3'
                          AND tablename=$1
                          AND policyname='tenant_isolation_sdk_v1'
                    )
                    """,
                    table,
                )
            )
            fk_exists = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname=$1
                          AND conrelid=$2::regclass
                    )
                    """,
                    f"{table}_tenant_id_fkey",
                    qtable,
                )
            )
            tenant_index_exists = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM pg_indexes
                        WHERE schemaname='soul_v3'
                          AND tablename=$1
                          AND indexname=$2
                    )
                    """,
                    table,
                    f"{table}_tenant_id_idx",
                )
            )
            tables.append(
                TablePreflight(
                    table=table,
                    exists=True,
                    row_count=row_count,
                    tenant_column_exists=tenant_column_exists,
                    null_tenant_rows=null_tenant_rows,
                    rls_enabled=rls_enabled,
                    force_rls_enabled=force_rls_enabled,
                    policy_exists=policy_exists,
                    fk_exists=fk_exists,
                    tenant_index_exists=tenant_index_exists,
                )
            )
        return Phase1Preflight(
            current_user=current_user,
            current_user_is_superuser=current_user_is_superuser,
            sdk_runtime_role_exists=sdk_runtime_role_exists,
            sdk_runtime_role_can_bypass_rls=sdk_runtime_role_can_bypass_rls,
            internal_tenant_exists=internal_tenant_exists,
            pgcrypto_installed=pgcrypto_installed,
            current_tenant_function_exists=current_tenant_function_exists,
            memories_content_hash_column_exists=memories_content_hash_column_exists,
            memories_content_hash_null_rows=memories_content_hash_null_rows,
            memories_content_hash_trigger_exists=memories_content_hash_trigger_exists,
            tables=tuple(tables),
        )
    finally:
        await conn.close()


async def apply_plan(plan: MigrationPlan) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        async with conn.transaction():
            for statement in plan.statements:
                await conn.execute(statement)
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan/apply SOUL Memory SDK Phase 1 tenant migration.")
    parser.add_argument("--preflight", action="store_true", help="Inspect current DB state without schema writes.")
    parser.add_argument("--out", type=Path, help="Write --preflight JSON to this file.")
    parser.add_argument("--apply", action="store_true", help="Execute schema writes. Omit for dry-run SQL.")
    parser.add_argument("--confirm", default="", help=f"Required with --apply: {CONFIRM_TEXT!r}")
    parser.add_argument("--expected-tenant-backfill", type=int, help="Required with --apply.")
    parser.add_argument("--expected-memories-hash", type=int, help="Required with --apply.")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preflight:
        result = await inspect_phase1_state()
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0
    plan = build_phase1_plan()
    if not args.apply:
        print(plan.sql(), end="")
        return 0
    if args.confirm != CONFIRM_TEXT:
        print("refusing_apply: exact --confirm text required")
        return 2
    if args.expected_tenant_backfill is None or args.expected_memories_hash is None:
        print("refusing_apply: expected counts required")
        return 2
    preflight = await inspect_phase1_state()
    if preflight.tenant_backfill_count != args.expected_tenant_backfill:
        print(
            "refusing_apply: tenant_backfill_count_mismatch "
            f"expected={args.expected_tenant_backfill} actual={preflight.tenant_backfill_count}"
        )
        return 2
    if preflight.memories_hash_count != args.expected_memories_hash:
        print(
            "refusing_apply: memories_hash_count_mismatch "
            f"expected={args.expected_memories_hash} actual={preflight.memories_hash_count}"
        )
        return 2
    await apply_plan(plan)
    print("applied=true")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
