#!/usr/bin/env python3
"""Provision the read-only PostgreSQL identity used by Stability Guard."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
from seal_secrets import pg_dsn  # noqa: E402


ROLE = "svc_soul_stability_guard"
CRED = Path.home() / ".config" / "seal" / "stability_guard.dsn"


def _private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _existing_password() -> str | None:
    try:
        parsed = urlparse(CRED.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return parsed.password if parsed.username == ROLE else None


async def main() -> int:
    password = _existing_password() or secrets.token_urlsafe(36)
    admin = await asyncpg.connect(pg_dsn(required=True))
    try:
        exists = await admin.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", ROLE
        )
        escaped = password.replace("'", "''")
        await admin.execute(
            f"{'ALTER' if exists else 'CREATE'} ROLE {ROLE} LOGIN "
            f"PASSWORD '{escaped}' NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS NOINHERIT"
        )
        await admin.execute(f"ALTER ROLE {ROLE} SET search_path=soul_v3,public")
        await admin.execute(f"REVOKE ALL ON DATABASE seal_memory FROM {ROLE}")
        await admin.execute(f"GRANT CONNECT ON DATABASE seal_memory TO {ROLE}")
        await admin.execute(f"REVOKE ALL ON SCHEMA soul_v3 FROM {ROLE}")
        await admin.execute(f"GRANT USAGE ON SCHEMA soul_v3 TO {ROLE}")
        await admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA soul_v3 FROM {ROLE}")
        await admin.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA soul_v3 FROM {ROLE}")
        await admin.execute(
            """
            CREATE OR REPLACE VIEW soul_v3.stability_guard_ada_memory_audit_v
            WITH (security_barrier=true) AS
              SELECT at, actor, action, decision
              FROM soul_v3.audit_log
              WHERE actor='ADA' AND action='memory_store';

            CREATE OR REPLACE VIEW soul_v3.stability_guard_autonomy_rules_v
            WITH (security_barrier=true) AS
              SELECT id, content, active
              FROM soul_v3.rules
              WHERE id IN (42,73);

            CREATE OR REPLACE VIEW soul_v3.stability_guard_jarvis_identity_v
            WITH (security_barrier=true) AS
              SELECT agent, boot_context
              FROM soul_v3.identity
              WHERE agent='JARVIS';

            CREATE OR REPLACE VIEW soul_v3.stability_guard_superuser_clients_v
            WITH (security_barrier=true) AS
              SELECT count(*)::bigint AS client_count
              FROM pg_catalog.pg_stat_activity
              WHERE backend_type='client backend'
                AND usename IN ('seal','postgres');
            """
        )
        await admin.execute(
            f"GRANT SELECT ON "
            "soul_v3.stability_guard_ada_memory_audit_v, "
            "soul_v3.stability_guard_autonomy_rules_v, "
            "soul_v3.stability_guard_jarvis_identity_v, "
            f"soul_v3.stability_guard_superuser_clients_v TO {ROLE}"
        )
    finally:
        await admin.close()

    dsn = (
        f"postgresql://{ROLE}:{quote(password, safe='')}"
        "@localhost:5433/seal_memory?application_name=seal_stability_guard"
    )
    _private_write(CRED, dsn + "\n")

    conn = await asyncpg.connect(dsn)
    try:
        attrs = await conn.fetchrow(
            "SELECT rolsuper,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user"
        )
        rules = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.stability_guard_autonomy_rules_v"
        )
        denied = False
        try:
            await conn.fetchval("SELECT count(*) FROM soul_v3.memories")
        except asyncpg.InsufficientPrivilegeError:
            denied = True
        ok = (
            attrs is not None
            and not attrs["rolsuper"]
            and not attrs["rolbypassrls"]
            and not attrs["rolinherit"]
            and rules == 2
            and denied
            and (CRED.stat().st_mode & 0o777) == 0o600
        )
        print(
            {
                "ok": ok,
                "role": ROLE,
                "rules": rules,
                "base_memories_denied": denied,
                "mode": oct(CRED.stat().st_mode & 0o777),
            }
        )
        return 0 if ok else 2
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
