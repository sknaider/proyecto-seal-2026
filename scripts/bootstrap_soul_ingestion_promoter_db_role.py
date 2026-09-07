#!/usr/bin/env python3
"""Provision the isolated SUIE promoter login without printing secrets."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg


TOKEN_PATH = Path(
    os.environ.get(
        "SUIE_PROMOTER_TOKEN_FILE",
        "/home/dadito/IA/proyecto-seal/var/soul_ingestion/promoter.token",
    )
)


async def provision() -> None:
    bootstrap_dsn = os.environ.get("SUIE_BOOTSTRAP_DSN")
    if not bootstrap_dsn:
        raise RuntimeError("SUIE_BOOTSTRAP_DSN is required for one-time role provisioning")
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if len(token) < 32 or TOKEN_PATH.lstat().st_mode & 0o077 or TOKEN_PATH.is_symlink():
        raise RuntimeError("SUIE promoter token file is invalid or too broadly readable")
    password = hashlib.sha256(b"seal-suie-promoter-db-v1\0" + token.encode("utf-8")).hexdigest()
    connection = await asyncpg.connect(bootstrap_dsn)
    try:
        statement = await connection.fetchval(
            "SELECT format('ALTER ROLE svc_soul_ingestion_promoter LOGIN PASSWORD %L', $1::text)",
            password,
        )
        await connection.execute(statement)
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(provision())
