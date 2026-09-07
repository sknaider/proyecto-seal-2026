#!/usr/bin/env python3
"""Provision hard per-agent PostgreSQL identities for seal-sync-endpoint.

Each signed agent token/CSR selects a dedicated LOGIN. RLS identity is derived
from ``session_user`` and reinforced with RESTRICTIVE policies plus an exact
least-privilege grant map. Credentials live outside git in a 0600
EnvironmentFile; no shared role or settable GUC is an identity boundary.
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


AGENTS = ("ADA", "ALICE", "DUM", "FABLE", "JARVIS", "NEXUS")
ROLES = {agent: f"svc_seal_sync_{agent.lower()}" for agent in AGENTS}
CONFIG_DIR = Path.home() / ".config" / "seal"
ENV_FILE = CONFIG_DIR / "seal_sync_endpoint_db.env"
DEDICATED_ENV_FILE = Path("/etc/seal-sync-endpoint/db.env")
UNIT_DROPIN = (
    Path.home()
    / ".config/systemd/user/seal-sync-endpoint.service.d/least-privilege-db.conf"
)


def _assert_legacy_runtime_target() -> None:
    """Never rotate DB passwords behind the dedicated system service.

    After the OS-principal cutover, this legacy provisioner would otherwise
    rotate all six roles and then try to write the now root-owned user env,
    leaving the active system service with stale credentials.  Credential
    rotation for the dedicated service must update its root-owned env and the
    database as one coordinated operation.
    """
    try:
        dedicated_active = DEDICATED_ENV_FILE.exists()
    except PermissionError:
        # The expected steady state is root:service 0640 below a 0750 dir,
        # so the shared UID cannot stat the file.  Denial itself proves that
        # this legacy writer must not proceed.
        dedicated_active = True
    if dedicated_active:
        raise RuntimeError(
            "dedicated seal-sync-endpoint cutover activo; "
            "provisioner legacy bloqueado antes de rotar credenciales"
        )


def _read_existing_passwords() -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        if not line.startswith("SEAL_SYNC_DB_DSN_") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        agent = key.removeprefix("SEAL_SYNC_DB_DSN_").upper()
        parsed = urlparse(value.strip())
        if agent in ROLES and parsed.username == ROLES[agent] and parsed.password:
            found[agent] = parsed.password
    return found


def _write_runtime_files(passwords: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for agent in AGENTS:
        role = ROLES[agent]
        dsn = (
            f"postgresql://{role}:{quote(passwords[agent], safe='')}"
            f"@localhost:5433/seal_memory"
            f"?application_name=seal_sync_endpoint_{agent.lower()}"
        )
        lines.append(f"SEAL_SYNC_DB_DSN_{agent}={dsn}")
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ENV_FILE)
    os.chmod(ENV_FILE, 0o600)

    UNIT_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    dropin_content = (
        "[Service]\n"
        "EnvironmentFile=%h/.config/seal/seal_sync_endpoint_db.env\n"
    )
    try:
        current_dropin = UNIT_DROPIN.read_text(encoding="utf-8")
    except OSError:
        current_dropin = ""
    if current_dropin != dropin_content:
        UNIT_DROPIN.write_text(dropin_content, encoding="utf-8")


def _identity_case() -> str:
    return " ".join(
        f"WHEN '{role}' THEN '{agent}'" for agent, role in ROLES.items()
    )


def _agent_domain_sql() -> str:
    """Return the exact row-ownership domain accepted by the sync boundary."""
    return ", ".join(f"'{agent}'" for agent in AGENTS)


async def _configure_role(
    admin: asyncpg.Connection, agent: str, password: str
) -> None:
    role = ROLES[agent]
    exists = await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    )
    escaped = password.replace("'", "''")
    if exists:
        await admin.execute(
            f"ALTER ROLE {role} WITH LOGIN PASSWORD '{escaped}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "NOBYPASSRLS NOINHERIT"
        )
    else:
        await admin.execute(
            f"CREATE ROLE {role} WITH LOGIN PASSWORD '{escaped}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "NOBYPASSRLS NOINHERIT"
        )
    await admin.execute(f"ALTER ROLE {role} SET search_path = soul_v3, pg_catalog")
    await admin.execute(f"REVOKE ALL ON DATABASE seal_memory FROM {role}")
    await admin.execute(f"GRANT CONNECT ON DATABASE seal_memory TO {role}")
    await admin.execute(f"REVOKE ALL ON SCHEMA soul_v3 FROM {role}")
    await admin.execute(f"GRANT USAGE ON SCHEMA soul_v3 TO {role}")
    await admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA soul_v3 FROM {role}")
    await admin.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA soul_v3 FROM {role}")

    await admin.execute(
        "GRANT SELECT ON soul_v3.agents, soul_v3.memories, "
        "soul_v3.revoked_tokens, soul_v3.authorized_devices, "
        f"soul_v3.pending_device_registrations TO {role}"
    )
    await admin.execute(f"GRANT INSERT ON soul_v3.memories TO {role}")
    await admin.execute(
        f"GRANT INSERT, UPDATE ON soul_v3.authorized_devices TO {role}"
    )
    await admin.execute(
        "GRANT INSERT, UPDATE, DELETE ON soul_v3.pending_device_registrations "
        f"TO {role}"
    )
    await admin.execute(
        f"GRANT USAGE, SELECT ON SEQUENCE soul_v3.memories_id_seq TO {role}"
    )


async def _install_rls(admin: asyncpg.Connection) -> None:
    roles = ", ".join(ROLES.values())
    agent_domain = _agent_domain_sql()
    orphan_count = await admin.fetchval(
        f"""
        SELECT count(*)
        FROM soul_v3.pending_device_registrations
        WHERE agent IS NULL OR agent NOT IN ({agent_domain})
        """
    )
    if orphan_count:
        raise RuntimeError(
            "pending_device_registrations contiene "
            f"{orphan_count} filas fuera del dominio per-agent; "
            "no se cambia la frontera automáticamente"
        )
    await admin.execute(
        f"""
        CREATE OR REPLACE FUNCTION soul_v3.seal_sync_session_agent()
        RETURNS text
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $$ SELECT CASE session_user {_identity_case()} ELSE NULL END $$;
        REVOKE ALL ON FUNCTION soul_v3.seal_sync_session_agent() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION soul_v3.seal_sync_session_agent() TO {roles};

        CREATE OR REPLACE FUNCTION soul_v3.seal_sync_machine_is_trusted(text)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM soul_v3.authorized_devices
            WHERE device_fingerprint = $1 AND status = 'active'
          )
        $$;
        REVOKE ALL ON FUNCTION soul_v3.seal_sync_machine_is_trusted(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION soul_v3.seal_sync_machine_is_trusted(text)
          TO {roles}, soul_admin, soul_sdk_admin;

        ALTER TABLE soul_v3.pending_device_registrations
          DROP CONSTRAINT IF EXISTS seal_sync_pending_agent_domain;
        ALTER TABLE soul_v3.pending_device_registrations
          ADD CONSTRAINT seal_sync_pending_agent_domain
          CHECK (agent IN ({agent_domain}));
        """
    )

    # This table had no RLS. A PUBLIC compatibility policy preserves existing
    # readers; the role-targeted RESTRICTIVE clamp narrows only sync logins.
    await admin.execute(
        """
        ALTER TABLE soul_v3.agents ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS seal_sync_agents_compat_read ON soul_v3.agents;
        CREATE POLICY seal_sync_agents_compat_read ON soul_v3.agents
          FOR SELECT TO PUBLIC USING (true);
        """
    )

    policies = {
        "agents": ("SELECT", "upper(name::text) = soul_v3.seal_sync_session_agent()"),
        "memories": ("ALL", "agent::text = soul_v3.seal_sync_session_agent()"),
        "revoked_tokens": ("SELECT", "agent::text = soul_v3.seal_sync_session_agent()"),
        "authorized_devices": ("ALL", "agent::text = soul_v3.seal_sync_session_agent()"),
        "pending_device_registrations": (
            "ALL",
            "agent::text = soul_v3.seal_sync_session_agent()",
        ),
    }
    for table, (command, predicate) in policies.items():
        await admin.execute(
            f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY"
        )
        if table == "pending_device_registrations":
            await admin.execute(
                f"ALTER TABLE soul_v3.{table} FORCE ROW LEVEL SECURITY"
            )
        name = f"seal_sync_{table}_own"
        hard = f"seal_sync_{table}_hard_identity"
        with_check = "" if command == "SELECT" else f" WITH CHECK ({predicate})"
        await admin.execute(
            f"DROP POLICY IF EXISTS {name} ON soul_v3.{table}; "
            f"CREATE POLICY {name} ON soul_v3.{table} "
            f"FOR {command} TO {roles} USING ({predicate}){with_check}"
        )
        await admin.execute(
            f"DROP POLICY IF EXISTS {hard} ON soul_v3.{table}; "
            f"CREATE POLICY {hard} ON soul_v3.{table} "
            f"AS RESTRICTIVE FOR {command} TO {roles} "
            f"USING ({predicate}){with_check}"
        )

    # Preserve the exact pre-cutover capabilities on the newly forced-RLS
    # pending table.
    await admin.execute(
        """
        DROP POLICY IF EXISTS pending_devices_admin_all
          ON soul_v3.pending_device_registrations;
        CREATE POLICY pending_devices_admin_all
          ON soul_v3.pending_device_registrations
          FOR ALL TO soul_admin
          USING (true) WITH CHECK (true);
        DROP POLICY IF EXISTS pending_devices_app_read
          ON soul_v3.pending_device_registrations;
        CREATE POLICY pending_devices_app_read
          ON soul_v3.pending_device_registrations
          FOR SELECT TO soul_app USING (true);
        DROP POLICY IF EXISTS pending_devices_runtime_all
          ON soul_v3.pending_device_registrations;
        CREATE POLICY pending_devices_runtime_all
          ON soul_v3.pending_device_registrations
          FOR ALL TO soul_sdk_runtime
          USING (true) WITH CHECK (true);
        """
    )


async def _verify(
    admin: asyncpg.Connection, passwords: dict[str, str]
) -> None:
    expected_tables = {
        "agents",
        "memories",
        "revoked_tokens",
        "authorized_devices",
        "pending_device_registrations",
    }
    bad = await admin.fetch(
        """
        SELECT rolname FROM pg_roles
        WHERE rolname = ANY($1::text[])
          AND (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit)
        """,
        list(ROLES.values()),
    )
    if bad:
        raise RuntimeError(f"roles privilegiados: {[r['rolname'] for r in bad]}")
    policies = await admin.fetch(
        """
        SELECT tablename, policyname, permissive
        FROM pg_policies
        WHERE schemaname='soul_v3'
          AND policyname LIKE 'seal_sync_%'
          AND tablename = ANY($1::text[])
        """,
        list(expected_tables),
    )
    restrictive = {
        r["tablename"] for r in policies if r["permissive"] == "RESTRICTIVE"
    }
    permissive = {
        r["tablename"]
        for r in policies
        if r["permissive"] == "PERMISSIVE"
        and not r["policyname"].endswith("_compat_read")
    }
    if restrictive != expected_tables:
        raise RuntimeError(f"clamp RESTRICTIVE incompleto: {restrictive}")
    if permissive != expected_tables:
        raise RuntimeError(f"capability PERMISSIVE incompleta: {permissive}")
    domain_constraint = await admin.fetchrow(
        """
        SELECT convalidated, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint
        WHERE conrelid='soul_v3.pending_device_registrations'::regclass
          AND conname='seal_sync_pending_agent_domain'
        """
    )
    if not domain_constraint or not domain_constraint["convalidated"]:
        raise RuntimeError(
            "dominio per-agent de pending_device_registrations ausente/no validado"
        )
    orphan_count = await admin.fetchval(
        """
        SELECT count(*)
        FROM soul_v3.pending_device_registrations
        WHERE agent IS NULL OR agent <> ALL($1::text[])
        """,
        list(AGENTS),
    )
    if orphan_count:
        raise RuntimeError(
            "pending_device_registrations tiene "
            f"{orphan_count} filas invisibles para los seis roles"
        )

    for agent in AGENTS:
        role = ROLES[agent]
        dsn = (
            f"postgresql://{role}:{quote(passwords[agent], safe='')}"
            f"@localhost:5433/seal_memory"
        )
        conn = await asyncpg.connect(dsn)
        try:
            identity = await conn.fetchrow(
                "SELECT session_user, soul_v3.seal_sync_session_agent() AS agent"
            )
            if identity["session_user"] != role or identity["agent"] != agent:
                raise RuntimeError(f"identidad incorrecta: {dict(identity)}")
            await conn.execute("SELECT set_config('app.agent', 'WILLIAM', false)")
            seen = {
                r["agent"]
                for r in await conn.fetch(
                    "SELECT DISTINCT agent::text AS agent FROM soul_v3.memories"
                )
            }
            if seen - {agent}:
                raise RuntimeError(f"{role} vio agentes ajenos: {sorted(seen)}")
            try:
                await conn.execute(
                    """
                    INSERT INTO soul_v3.memories
                      (agent,category,content,content_hash_sha256,tenant_id)
                    VALUES ('WILLIAM','test','deny',repeat('0',64),
                            '00000000-0000-0000-0000-000000000000')
                    """
                )
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise RuntimeError(f"{role} pudo insertar cross-agent")
        finally:
            await conn.close()


async def _provision() -> None:
    existing = _read_existing_passwords()
    passwords = {
        agent: existing.get(agent) or secrets.token_urlsafe(36) for agent in AGENTS
    }
    admin = await asyncpg.connect(pg_dsn(required=True))
    try:
        async with admin.transaction():
            for agent in AGENTS:
                await _configure_role(admin, agent, passwords[agent])
            await _install_rls(admin)
        await _verify(admin, passwords)
    finally:
        await admin.close()
    _write_runtime_files(passwords)
    print(
        "seal-sync-endpoint DB: "
        f"roles={len(ROLES)} per_agent_session_user=OK "
        "restrictive=5/5 permissive=5/5 cross_agent=DENY env_mode=0600"
    )


def main() -> int:
    _assert_legacy_runtime_target()
    asyncio.run(_provision())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
