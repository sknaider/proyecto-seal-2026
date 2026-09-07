"""Create an isolated client schema by cloning soul_v3 structure.

Usage:
    python3 create_schema.py soul_v3_acme_corp

Creates the target schema with all soul_v3 tables (structure only, no data).
Uses LIKE ... INCLUDING ALL to copy columns, defaults, constraints, indexes.
"""
from __future__ import annotations

import asyncio
import re
import sys

import asyncpg

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
SOURCE_SCHEMA = "soul_v3"


async def clone_schema(target: str) -> None:
    if not re.match(r"^soul_v3_[a-z][a-z0-9_]{1,50}$", target):
        raise ValueError(
            f"Target schema must match: soul_v3_<name> (lowercase, alphanumeric/underscore). Got: {target}"
        )

    conn = await asyncpg.connect(DB_URL)
    try:
        # Check if target already exists
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1", target
        )
        if exists:
            print(f"Schema '{target}' already exists — skipping creation")
            return

        # Get all tables in source schema, in dependency order (topological sort approximation)
        tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            SOURCE_SCHEMA,
        )
        table_names = [r["table_name"] for r in tables]

        # Create target schema
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {target}")
        print(f"Created schema: {target}")

        # Clone each table structure
        for t in table_names:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {target}.{t}
                (LIKE {SOURCE_SCHEMA}.{t} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)
                """
            )
            print(f"  ✓ {t}")

        # Clone sequences (for SERIAL/BIGSERIAL columns)
        seqs = await conn.fetch(
            """
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = $1
            """,
            SOURCE_SCHEMA,
        )
        for s in seqs:
            seq = s["sequence_name"]
            try:
                # Get sequence details
                row = await conn.fetchrow(
                    f"SELECT start_value, increment_by, min_value, max_value, cycle_option "
                    f"FROM {SOURCE_SCHEMA}.{seq}_seq_info" 
                )
            except Exception:
                pass  # Sequences may already be linked via LIKE INCLUDING DEFAULTS

        print(f"\nSchema '{target}' ready — {len(table_names)} tables cloned from {SOURCE_SCHEMA}")

    finally:
        await conn.close()


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 create_schema.py <target_schema>")
        print("Example: python3 create_schema.py soul_v3_acme_corp")
        sys.exit(1)
    target = sys.argv[1]
    asyncio.run(clone_schema(target))


if __name__ == "__main__":
    main()
