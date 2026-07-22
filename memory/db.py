"""Database connection pool for SEAL Memory."""
from __future__ import annotations
from seal_secrets import pg_dsn

import asyncio
import os
from pathlib import Path
import stat
from typing import Mapping
from urllib.parse import urlsplit

import asyncpg


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() not in {"", "0", "false", "off", "no"}


def _validated_runtime_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuntimeError("MCP runtime credential is not a valid PostgreSQL URL") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.username:
        raise RuntimeError("MCP runtime credential is not a valid PostgreSQL URL")
    if parsed.username.lower() in {"seal", "postgres"}:
        raise RuntimeError("MCP runtime credential resolves to a privileged login; refusing startup")
    return value


def _read_private_dsn(path: Path, *, expected_user: str | None = None) -> str:
    """Read a runtime DSN only from a private, regular credential file."""
    try:
        info = path.stat()
    except OSError as exc:
        raise RuntimeError(
            f"MCP runtime credential unavailable at {path}; refusing privileged fallback"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"MCP runtime credential is not a regular file: {path}")
    if info.st_mode & 0o077:
        raise RuntimeError(f"MCP runtime credential permissions are not private: {path}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"MCP runtime credential unreadable at {path}") from exc
    if not value:
        raise RuntimeError(f"MCP runtime credential is empty at {path}")
    value = _validated_runtime_url(value)
    if expected_user is not None:
        actual_user = (urlsplit(value).username or "").lower()
        if actual_user != expected_user.lower():
            raise RuntimeError(
                f"MCP runtime credential principal mismatch at {path}; "
                f"expected {expected_user}"
            )
    return value


def resolve_mcp_agent_db_url(
    agent: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the hard DB identity for one authenticated MCP agent.

    Agent data credentials deliberately cannot be supplied inline in the
    environment: they live in individual 0600 files and the login name must
    match the authenticated agent.  This makes a caller-controlled GUC
    insufficient to cross the database boundary.
    """
    env = os.environ if environ is None else environ
    normalized = (agent or "").strip().lower()
    if normalized not in {"ada", "alice", "dum", "jarvis", "nexus"}:
        raise RuntimeError("MCP agent DB identity is unknown; refusing database access")
    cred_dir = Path(
        env.get("SEAL_MCP_AGENT_CRED_DIR", ".seal_mcp_agent_creds")
    )
    if not cred_dir.is_absolute():
        cred_dir = Path(__file__).resolve().parents[1] / cred_dir
    return _read_private_dsn(
        cred_dir / f"{normalized}.dsn",
        expected_user=f"mcp_runtime_{normalized}",
    )


def resolve_db_url(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the process DB principal without silently escaping MCP least privilege.

    The central MCP process historically imported helpers which called this module's
    pool directly.  Those helpers must use the same restricted credential as the MCP
    facade, never the repository-wide ``seal`` fallback.  Other daemons keep their
    existing explicit ``SEAL_DB_URL``/secret behavior.
    """
    env = os.environ if environ is None else environ
    if _enabled(env.get("SEAL_MCP_RUNTIME_DB")):
        runtime_url = (
            env.get("SEAL_MCP_BROKER_DB_URL")
            or env.get("SEAL_MCP_RUNTIME_DB_URL")
            or ""
        ).strip()
        if runtime_url:
            return _validated_runtime_url(runtime_url)

        cred_path = Path(
            env.get("SEAL_MCP_BROKER_CRED")
            or env.get("SEAL_MCP_RUNTIME_CRED", ".seal_mcp_runtime_cred")
        )
        if not cred_path.is_absolute():
            cred_path = Path(__file__).resolve().parents[1] / cred_path
        return _read_private_dsn(cred_path)

    explicit = (env.get("SEAL_DB_URL") or "").strip()
    if explicit:
        return explicit

    return pg_dsn(required=True)


DB_URL = resolve_db_url()
SEAL_SCHEMA = os.environ.get("SEAL_SCHEMA", "soul_v3")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(
            DB_URL,
            min_size=1,
            max_size=3,
            server_settings={"search_path": SEAL_SCHEMA},
        )
    return _pool


async def close_pool():
    global _pool
    if _pool and not _pool._closed:
        try:
            await asyncio.wait_for(_pool.close(), timeout=10)
        except asyncio.TimeoutError:
            pass
        _pool = None
