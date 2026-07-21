#!/usr/bin/env python3
"""Provision the restricted SUIE login without exposing its password."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg


TOKEN_PATH = Path(
    os.environ.get(
        "SUIE_TOKEN_FILE",
        "/home/dadito/IA/proyecto-seal/var/soul_ingestion/service.token",
    )
)


async def provision() -> None:
    bootstrap_dsn = os.environ.get("SUIE_BOOTSTRAP_DSN")
    if not bootstrap_dsn:
        raise RuntimeError("SUIE_BOOTSTRAP_DSN is required for one-time role provisioning")
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if len(token) < 32 or TOKEN_PATH.stat().st_mode & 0o077:
        raise RuntimeError("SUIE token file is invalid or too broadly readable")
    password = hashlib.sha256(b"seal-suie-db-v1\0" + token.encode("utf-8")).hexdigest()
    connection = await asyncpg.connect(bootstrap_dsn)
    try:
        statement = await connection.fetchval(
            "SELECT format('ALTER ROLE svc_soul_ingestion LOGIN PASSWORD %L', $1::text)",
            password,
        )
        await connection.execute(statement)
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(provision())
