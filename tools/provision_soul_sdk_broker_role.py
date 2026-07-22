#!/usr/bin/env python3
"""Provision the least-privilege DB boundary for the SOUL Memory SDK API.

The public API is the authenticated broker: it resolves an API-key hash to a
tenant before opening a scoped transaction.  Its login can only look up tenant
keys and SET ROLE into two narrow, NOLOGIN roles.  It never receives the
repository superuser DSN and it cannot SET ROLE to the retired broad SDK role.

The operation is idempotent.  The generated DSN is stored outside git in a
0600 EnvironmentFile and the matching systemd drop-in is written atomically.
"""

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


LOGIN_ROLE = "svc_soul_memory_sdk"
AGENT_ROLE = "soul_sdk_agent_api"
TENANT_ROLE = "soul_sdk_tenant_api"
CONFIG_DIR = Path.home() / ".config" / "seal"
ENV_FILE = CONFIG_DIR / "soul_memory_sdk.env"
DROPIN = (
    Path.home()
    / ".config/systemd/user/seal-memory-sdk-api.service.d/least-privilege.conf"
)


def _private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _existing_password() -> str | None:
    try:
        line = next(
            row for row in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if row.startswith("SEAL_DB_URL=")
        )
        parsed = urlparse(line.split("=", 1)[1].strip())
    except (OSError, StopIteration, ValueError):
        return None
    if parsed.username == LOGIN_ROLE and parsed.password:
        return parsed.password
    return None


async def _ensure_role(
    admin: asyncpg.Connection,
    role: str,
    *,
    login: bool,
    password: str | None = None,
) -> None:
    exists = await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    )
    verb = "ALTER" if exists else "CREATE"
    password_clause = ""
    if login:
        escaped = (password or "").replace("'", "''")
        password_clause = f" PASSWORD '{escaped}'"
    await admin.execute(
        f"{verb} ROLE {role} {'LOGIN' if login else 'NOLOGIN'}{password_clause} "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        "NOBYPASSRLS NOINHERIT"
    )
    await admin.execute(f"ALTER ROLE {role} SET search_path = soul_v3, public")


async def provision(admin: asyncpg.Connection, password: str) -> None:
    await _ensure_role(admin, AGENT_ROLE, login=False)
    await _ensure_role(admin, TENANT_ROLE, login=False)
    await _ensure_role(admin, LOGIN_ROLE, login=True, password=password)

    for role in (AGENT_ROLE, TENANT_ROLE, LOGIN_ROLE):
        await admin.execute(f"REVOKE ALL ON DATABASE seal_memory FROM {role}")
        await admin.execute(f"REVOKE ALL ON SCHEMA soul_v3 FROM {role}")
        await admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA soul_v3 FROM {role}")
        await admin.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA soul_v3 FROM {role}")

    await admin.execute(f"GRANT CONNECT ON DATABASE seal_memory TO {LOGIN_ROLE}")
    await admin.execute(
        f"GRANT USAGE ON SCHEMA soul_v3 TO {LOGIN_ROLE}, {AGENT_ROLE}, {TENANT_ROLE}"
    )
    await admin.execute(
        f"GRANT SELECT (id, api_keys) ON soul_v3.tenants TO {LOGIN_ROLE}"
    )

    await admin.execute(
        f"GRANT SELECT, INSERT ON soul_v3.memories TO {AGENT_ROLE}; "
        f"GRANT SELECT (name), INSERT (name, role, active) "
        f"ON soul_v3.agents TO {AGENT_ROLE}; "
        f"GRANT INSERT ON soul_v3.memory_retrieval_log TO {AGENT_ROLE}"
    )
    await admin.execute(
        f"GRANT SELECT ON soul_v3.memories TO {TENANT_ROLE}; "
        f"GRANT INSERT ON soul_v3.user_read_audit TO {TENANT_ROLE}"
    )
    for sequence, role in (
        ("memories_id_seq", AGENT_ROLE),
        ("memory_retrieval_log_id_seq", AGENT_ROLE),
        ("user_read_audit_id_seq", TENANT_ROLE),
    ):
        if await admin.fetchval("SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{sequence}"):
            await admin.execute(
                f"GRANT USAGE, SELECT ON SEQUENCE soul_v3.{sequence} TO {role}"
            )

    await admin.execute(
        f"GRANT {AGENT_ROLE}, {TENANT_ROLE} TO {LOGIN_ROLE}; "
        f"REVOKE soul_sdk_runtime, soul_sdk_user, soul_sdk_admin FROM {LOGIN_ROLE}"
    )

    await admin.execute(
        f"""
        ALTER TABLE soul_v3.memories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE soul_v3.memories FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS sdk_agent_broker_read ON soul_v3.memories;
        CREATE POLICY sdk_agent_broker_read ON soul_v3.memories
          FOR SELECT TO {AGENT_ROLE}
          USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND (
              agent::text = NULLIF(current_setting('app.agent', true), '')
              OR COALESCE(scope, 'private')::text IN ('team', 'shared', 'public')
            )
          );

        DROP POLICY IF EXISTS sdk_agent_broker_insert ON soul_v3.memories;
        CREATE POLICY sdk_agent_broker_insert ON soul_v3.memories
          FOR INSERT TO {AGENT_ROLE}
          WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND agent::text = NULLIF(current_setting('app.agent', true), '')
            AND COALESCE(scope, 'private')::text IN ('private', 'team')
          );

        DROP POLICY IF EXISTS sdk_tenant_broker_read ON soul_v3.memories;
        CREATE POLICY sdk_tenant_broker_read ON soul_v3.memories
          FOR SELECT TO {TENANT_ROLE}
          USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND NULLIF(current_setting('app.viewer', true), '') = 'user'
            AND NULLIF(current_setting('app.user_id', true), '') IS NOT NULL
            AND COALESCE(scope, 'private')::text IN ('team', 'shared', 'public')
          );
        """
    )


async def verify(runtime_dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(
        runtime_dsn,
        server_settings={"application_name": "soul_sdk_boundary_verify"},
    )
    try:
        attrs = await conn.fetchrow(
            """
            SELECT r.rolsuper, r.rolbypassrls, r.rolinherit,
                   session_user AS session_user, current_user AS current_user
            FROM pg_roles r WHERE r.rolname=session_user
            """
        )
        tenant_count = await conn.fetchval("SELECT count(*) FROM soul_v3.tenants")
        denied_broad_role = False
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE soul_sdk_runtime")
        except asyncpg.InsufficientPrivilegeError:
            denied_broad_role = True

        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {AGENT_ROLE}")
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true)",
                "00000000-0000-0000-0000-000000000000",
            )
            await conn.execute("SELECT set_config('app.agent','ADA',true)")
            foreign_private = await conn.fetchval(
                """
                SELECT count(*) FROM soul_v3.memories
                WHERE invalid_at IS NULL AND agent <> 'ADA'
                  AND COALESCE(scope,'private')='private'
                """
            )
            unrelated_denied = False
            try:
                await conn.fetchval("SELECT count(*) FROM soul_v3.agent_tasks")
            except asyncpg.InsufficientPrivilegeError:
                unrelated_denied = True

        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {TENANT_ROLE}")
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true)",
                "00000000-0000-0000-0000-000000000000",
            )
            await conn.execute("SELECT set_config('app.viewer','user',true)")
            await conn.execute("SELECT set_config('app.user_id','boundary-canary',true)")
            private_visible = await conn.fetchval(
                """
                SELECT count(*) FROM soul_v3.memories
                WHERE invalid_at IS NULL AND COALESCE(scope,'private')='private'
                """
            )

        return {
            "login": attrs["session_user"],
            "current_user": attrs["current_user"],
            "superuser": bool(attrs["rolsuper"]),
            "bypassrls": bool(attrs["rolbypassrls"]),
            "inherit": bool(attrs["rolinherit"]),
            "tenant_lookup": int(tenant_count),
            "foreign_private": int(foreign_private),
            "tenant_private_visible": int(private_visible),
            "unrelated_table_denied": unrelated_denied,
            "broad_role_denied": denied_broad_role,
        }
    finally:
        await conn.close()


async def main() -> int:
    password = _existing_password() or secrets.token_urlsafe(36)
    runtime_dsn = (
        f"postgresql://{LOGIN_ROLE}:{quote(password, safe='')}"
        "@localhost:5433/seal_memory?application_name=soul_memory_sdk_api"
    )
    admin = await asyncpg.connect(pg_dsn(required=True))
    try:
        async with admin.transaction():
            await provision(admin, password)
    finally:
        await admin.close()

    _private_write(ENV_FILE, f"SEAL_DB_URL={runtime_dsn}\n")
    _private_write(
        DROPIN,
        "[Service]\n"
        f"EnvironmentFile={ENV_FILE}\n",
    )
    report = await verify(runtime_dsn)
    ok = (
        report["login"] == LOGIN_ROLE
        and not report["superuser"]
        and not report["bypassrls"]
        and not report["inherit"]
        and report["tenant_lookup"] >= 1
        and report["foreign_private"] == 0
        and report["tenant_private_visible"] == 0
        and report["unrelated_table_denied"]
        and report["broad_role_denied"]
    )
    print({"ok": ok, **report, "env_mode": oct(ENV_FILE.stat().st_mode & 0o777)})
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
