#!/usr/bin/env python3
"""Apply and verify the additive SSAI SHADOW/DUAL_VERIFY registry migration."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT / "memory"
sys.path.insert(0, str(MEMORY))

from seal_secrets import pg_dsn  # noqa: E402

MIGRATION = MEMORY / "migrations" / "047_ssai_shadow_registry.sql"
TABLES = {
    "ssai_registry",
    "ssai_candidate_projections",
    "ssai_keys",
    "ssai_manifests",
    "ssai_signatures",
    "ssai_events",
    "ssai_tree_heads",
    "ssai_rollout_state",
    "ssai_rollout_events",
    "ssai_verification_runs",
}
IMMUTABLE = TABLES - {"ssai_registry", "ssai_rollout_state"}


async def verify(conn: asyncpg.Connection) -> None:
    present = {
        row["table_name"]
        for row in await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='soul_v3' AND table_name = ANY($1::text[])
            """,
            sorted(TABLES),
        )
    }
    if present != TABLES:
        raise RuntimeError(f"missing SSAI tables: {sorted(TABLES - present)}")

    rls_rows = await conn.fetch(
        """
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soul_v3' AND relname = ANY($1::text[])
        """,
        sorted(TABLES),
    )
    bad_rls = sorted(
        row["relname"]
        for row in rls_rows
        if not row["relrowsecurity"] or not row["relforcerowsecurity"]
    )
    if bad_rls:
        raise RuntimeError(f"SSAI tables without FORCE RLS: {bad_rls}")

    trigger_rows = await conn.fetch(
        """
        SELECT c.relname, t.tgname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soul_v3' AND NOT t.tgisinternal
          AND c.relname = ANY($1::text[])
        """,
        sorted(IMMUTABLE | {"ssai_rollout_state"}),
    )
    trigger_names = {row["tgname"] for row in trigger_rows}
    expected_triggers = {
        f"{table}_immutable_{suffix}"
        for table in IMMUTABLE
        for suffix in ("row", "truncate")
    } | {"ssai_rollout_transition_guard"}
    missing_triggers = expected_triggers - trigger_names
    if missing_triggers:
        raise RuntimeError(f"missing SSAI guards: {sorted(missing_triggers)}")

    required_policies = {
        "ssai_registry_runtime_read",
        "ssai_projection_runtime_read",
        "ssai_runs_runtime_insert",
        "ssai_rollout_events_runtime_read",
    }
    policies = {
        row["policyname"]
        for row in await conn.fetch(
            """
            SELECT policyname FROM pg_policies
            WHERE schemaname='soul_v3' AND tablename LIKE 'ssai_%'
            """
        )
    }
    if not required_policies.issubset(policies):
        raise RuntimeError(f"missing SSAI policies: {sorted(required_policies - policies)}")

    runtime_grants = await conn.fetchval(
        """
        SELECT has_table_privilege(
            'pr_mcp_audit_write', 'soul_v3.ssai_verification_runs', 'INSERT'
        ) AND has_table_privilege(
            'pr_mcp_base', 'soul_v3.ssai_registry', 'SELECT'
        ) AND NOT has_table_privilege(
            'pr_mcp_base', 'soul_v3.ssai_events', 'UPDATE'
        )
        """
    )
    if not runtime_grants:
        raise RuntimeError("SSAI runtime grants violate least privilege")


async def main() -> int:
    sql = MIGRATION.read_text(encoding="utf-8")
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        await conn.execute(sql)
        await verify(conn)
    finally:
        await conn.close()
    print(
        f"ssai_registry_migration=ok tables={len(TABLES)} "
        f"immutable={len(IMMUTABLE)} force_rls={len(TABLES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
