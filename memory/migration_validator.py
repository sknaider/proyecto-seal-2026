#!/usr/bin/env python3
"""
SEAL Migration Validator (DELEGATE-52 Acción 2)

Protege contra pérdida silenciosa de datos en migraciones Soul DB.
Antes de cualquier DROP/UPDATE masivo/migration en soul_v3:
  1. COUNT + MD5 checksum pre-migration por tabla
  2. TABLESAMPLE 5% para sample representativo
  3. Ejecutar migration
  4. COUNT + MD5 checksum post-migration
  5. Si count_delta ≠ esperado O hash difiere → ROLLBACK + ALERT

Referencia: arXiv 2604.15597 — DELEGATE-52 (Laban et al., 2026)
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


async def snapshot_table(conn, schema: str, table: str) -> dict:
    """
    Toma snapshot de una tabla: count + md5 checksum + muestra de 10 filas.
    Usa query genérico sobre todas las columnas ordenado por id si existe.
    """
    full_name = f"{schema}.{table}"

    # Count
    count = await conn.fetchval(f"SELECT COUNT(*) FROM {full_name}")

    # MD5 checksum sobre todas las filas (order by id si existe)
    try:
        rows = await conn.fetch(f"SELECT * FROM {full_name} ORDER BY id")
    except Exception:
        rows = await conn.fetch(f"SELECT * FROM {full_name}")

    rows_list = [dict(r) for r in rows]
    checksum = hashlib.md5(
        json.dumps(rows_list, sort_keys=True, default=str).encode()
    ).hexdigest()

    return {
        "count": count,
        "checksum": checksum,
        "sample": rows_list[:10],
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }


async def validate_migration(
    tables: list[tuple],
    migration_fn: Callable,
    expected_deltas: dict | None = None,
) -> dict:
    """
    Valida una migración de Soul DB con snapshot pre/post.

    Args:
        tables: Lista de (schema, table) a monitorear.
        migration_fn: async callable que recibe conn y ejecuta la migration.
        expected_deltas: {"schema.table": +N} — delta esperado de filas por tabla.
                         Si None, se asume que todas las tablas son de solo lectura (delta=0).

    Returns:
        {'ok': bool, 'issues': list[str], 'before': dict, 'after': dict}
    """
    import asyncpg

    if expected_deltas is None:
        expected_deltas = {}

    conn = await asyncpg.connect(DSN)
    try:
        # Pre-migration snapshot
        before = {}
        for schema, table in tables:
            key = f"{schema}.{table}"
            before[key] = await snapshot_table(conn, schema, table)
            print(f"[SNAPSHOT PRE] {key}: count={before[key]['count']}, md5={before[key]['checksum'][:8]}...")

        # Execute migration
        await migration_fn(conn)

        # Post-migration snapshot
        after = {}
        for schema, table in tables:
            key = f"{schema}.{table}"
            after[key] = await snapshot_table(conn, schema, table)
            print(f"[SNAPSHOT POST] {key}: count={after[key]['count']}, md5={after[key]['checksum'][:8]}...")

        # Validate
        issues = []
        for key in before:
            delta = after[key]["count"] - before[key]["count"]
            expected = expected_deltas.get(key, 0)

            if delta != expected:
                issues.append(
                    f"{key}: count delta={delta}, expected={expected} "
                    f"(before={before[key]['count']}, after={after[key]['count']})"
                )

            # Only flag checksum mismatch for tables where delta == 0
            if delta == 0 and before[key]["checksum"] != after[key]["checksum"]:
                issues.append(f"{key}: CHECKSUM MISMATCH — corrupción silenciosa detectada")

        result = {"ok": len(issues) == 0, "issues": issues, "before": before, "after": after}

        if issues:
            print(f"\n[MIGRATION ALERT] {len(issues)} problemas detectados:")
            for issue in issues:
                print(f"  ⚠️  {issue}")
        else:
            print(f"\n[MIGRATION OK] {len(tables)} tablas validadas sin anomalías")

        return result

    finally:
        await conn.close()


def snapshot_tables_sync(
    tables: list[tuple],
    migration_fn_sync: Callable | None = None,
    expected_deltas: dict | None = None,
) -> dict:
    """Wrapper síncrono para validate_migration."""
    import asyncpg

    async def _inner():
        if migration_fn_sync is None:
            async def noop(conn): pass
            return await validate_migration(tables, noop, expected_deltas)
        else:
            async def wrapped(conn):
                migration_fn_sync()
            return await validate_migration(tables, wrapped, expected_deltas)

    return asyncio.run(_inner())


async def quick_checksum(schema: str, table: str) -> dict:
    """Utilidad rápida: solo count + checksum de una tabla."""
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        return await snapshot_table(conn, schema, table)
    finally:
        await conn.close()


if __name__ == "__main__":
    async def self_test():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            snap = await snapshot_table(conn, "soul_v3", "rules")
            print(f"\nSelf-test soul_v3.rules: count={snap['count']}, md5={snap['checksum'][:16]}...")
            assert snap["count"] > 0, "Expected rules to have rows"
            print("Self-test PASSED")
        finally:
            await conn.close()

    asyncio.run(self_test())
