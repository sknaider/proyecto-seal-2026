#!/usr/bin/env python3
"""Wait until PostgreSQL accepts an authenticated read-only probe."""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import asyncpg


def resolve_dsn() -> str:
    dsn = (os.environ.get("SEAL_DB_DSN") or os.environ.get("SEAL_PG_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("SEAL_DB_DSN/SEAL_PG_DSN is required")
    return dsn


async def wait_ready(timeout_seconds: float, interval_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        conn = None
        try:
            conn = await asyncpg.connect(resolve_dsn(), timeout=min(3.0, interval_seconds))
            await conn.fetchval("SELECT 1")
            return 0
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                await conn.close()
        await asyncio.sleep(interval_seconds)
    print(f"PostgreSQL readiness timeout after {timeout_seconds:.0f}s: {last_error}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.interval <= 0:
        parser.error("timeout and interval must be positive")
    return asyncio.run(wait_ready(args.timeout, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
