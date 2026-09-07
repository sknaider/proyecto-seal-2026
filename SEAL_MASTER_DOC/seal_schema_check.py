#!/usr/bin/env python3
"""
seal_schema_check.py — Pre-flight check antes de cualquier ALTER TABLE / DROP en seal_memory.
SEAL Safety Category 1: Schema changes requieren pausa y verificación mutua.

Uso:
  python3 seal_schema_check.py --table memories --op "ALTER TABLE memories ADD COLUMN foo TEXT"
  python3 seal_schema_check.py --table inner_monologue --op "DROP COLUMN uncertainty"
  python3 seal_schema_check.py --dry-run  # solo muestra estado actual, no ejecuta nada

Salida:
  EXIT 0 → seguro proceder (no hay queries activas en la tabla)
  EXIT 1 → hay queries activas o el op es destructivo — NO proceder sin confirmación explícita
"""
import asyncio
import asyncpg
import argparse
import sys
from datetime import datetime, timezone

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

DESTRUCTIVE_KEYWORDS = {"drop", "truncate", "delete", "alter column", "rename column"}


def is_destructive(op: str) -> bool:
    op_lower = op.lower()
    return any(kw in op_lower for kw in DESTRUCTIVE_KEYWORDS)


async def check(table: str | None, op: str | None, dry_run: bool) -> int:
    conn = await asyncpg.connect(DB_URL)

    print(f"[{datetime.now(timezone.utc).isoformat()}] SEAL Schema Check")
    print(f"  Table : {table or '(none)'}")
    print(f"  Op    : {op or '(dry-run only)'}")
    print()

    # 1. Active queries on the target table
    active = []
    if table:
        rows = await conn.fetch("""
            SELECT pid, state, query_start, left(query, 120) AS query_preview
            FROM pg_stat_activity
            WHERE state != 'idle'
              AND query ILIKE $1
              AND datname = 'seal_memory'
        """, f"%{table}%")
        active = list(rows)

    # 2. Table stats
    if table:
        try:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"  Rows in {table}: {count}")
        except Exception as e:
            print(f"  Could not count {table}: {e}")

    # 3. Active locks on table
    locks = []
    if table:
        try:
            locks = await conn.fetch("""
                SELECT pid, mode, granted
                FROM pg_locks l
                JOIN pg_class c ON c.oid = l.relation
                WHERE c.relname = $1 AND NOT granted
            """, table)
        except Exception:
            pass

    await conn.close()

    # Report
    exit_code = 0

    if active:
        print(f"  ⚠️  ACTIVE QUERIES on {table}: {len(active)}")
        for r in active:
            print(f"     PID {r['pid']} [{r['state']}] since {r['query_start']}: {r['query_preview']}")
        exit_code = 1
    else:
        print(f"  ✓ No active queries on {table}" if table else "  ✓ Dry-run complete")

    if locks:
        print(f"  ⚠️  PENDING LOCKS on {table}: {len(locks)}")
        exit_code = 1
    else:
        if table:
            print(f"  ✓ No pending locks on {table}")

    if op and is_destructive(op):
        print(f"\n  🚨 DESTRUCTIVE OPERATION DETECTED: '{op[:80]}'")
        print("     This falls under SEAL Safety Category 1.")
        print("     Requires explicit confirmation from JARVIS or William before executing.")
        exit_code = 1

    if dry_run or not op:
        print("\n  [DRY-RUN] No changes executed.")
        return exit_code

    if exit_code != 0:
        print(f"\n  BLOCKED — resolve issues above before running: {op}")
        return exit_code

    print(f"\n  ✓ CLEAR TO PROCEED: {op}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="SEAL pre-flight schema check")
    parser.add_argument("--table", help="Table name to check (e.g. memories)")
    parser.add_argument("--op", help="SQL operation to validate (not executed, just analyzed)")
    parser.add_argument("--dry-run", action="store_true", help="Only show current state, no execution check")
    args = parser.parse_args()

    if not args.table and not args.dry_run:
        parser.error("Provide --table or --dry-run")

    exit_code = asyncio.run(check(args.table, args.op, args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
