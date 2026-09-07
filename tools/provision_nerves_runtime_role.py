#!/usr/bin/env python3
"""Provision hard per-agent PostgreSQL identities for SOUL NERVES.

``app.agent`` remains useful application context, but it is not an identity
boundary because a database client can change custom GUCs.  Each NERVES unit
therefore authenticates with a distinct LOGIN.  Restrictive RLS policies bind
rows to ``session_user`` through ``soul_v3.nerves_session_agent()``.

The operation is idempotent.  Credentials stay outside git as 0600 files.
Use ``--retire-shared`` only after all five units pass their cutover gates.
"""

from __future__ import annotations

import argparse
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


SHARED_ROLE = "svc_soul_nerves"
AGENT_UNITS = {
    "ADA": "seal-ada-nerves.service",
    "ALICE": "seal-alice-nerves.service",
    "DUM": "seal-dum-nerves.service",
    "JARVIS": "seal-nerves-daemon.service",
    "NEXUS": "seal-nexus-nerves.service",
}
AGENT_ROLES = {
    agent: f"svc_soul_nerves_{agent.lower()}" for agent in AGENT_UNITS
}
CONFIG_DIR = Path.home() / ".config" / "seal"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SHARED_ENV = CONFIG_DIR / "soul_nerves_db.env"


def _env_path(agent: str) -> Path:
    return CONFIG_DIR / f"soul_nerves_{agent.lower()}_db.env"


def _existing_password(agent: str) -> str | None:
    role = AGENT_ROLES[agent]
    try:
        for raw in _env_path(agent).read_text(encoding="utf-8").splitlines():
            if raw.startswith("SEAL_DB_DSN="):
                parsed = urlparse(raw.split("=", 1)[1].strip())
                if parsed.username == role and parsed.password:
                    return parsed.password
    except OSError:
        return None
    return None


def _write_runtime_files(passwords: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for agent, unit in AGENT_UNITS.items():
        role = AGENT_ROLES[agent]
        env_path = _env_path(agent)
        dsn = (
            f"postgresql://{role}:{quote(passwords[agent], safe='')}"
            f"@localhost:5433/seal_memory?application_name=seal_nerves_{agent.lower()}"
        )
        tmp = env_path.with_suffix(".env.tmp")
        tmp.write_text(
            f"SEAL_DB_DSN={dsn}\nSEAL_NERVES_BOOTSTRAP_SCHEMA=0\n",
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(env_path)
        os.chmod(env_path, 0o600)

        dropin = UNIT_DIR / f"{unit}.d" / "least-privilege-db.conf"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "[Service]\nEnvironmentFile=\n"
            f"EnvironmentFile=%h/.config/seal/{env_path.name}\n",
            encoding="utf-8",
        )


def _ident_sql() -> str:
    branches = " ".join(
        f"WHEN '{role}' THEN '{agent}'" for agent, role in AGENT_ROLES.items()
    )
    return f"CASE session_user {branches} ELSE NULL END"


async def _configure_role(admin: asyncpg.Connection, agent: str, password: str) -> None:
    role = AGENT_ROLES[agent]
    exists = await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    )
    quoted_password = password.replace("'", "''")
    if not exists:
        await admin.execute(
            f"CREATE ROLE {role} LOGIN PASSWORD '{quoted_password}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "NOBYPASSRLS NOINHERIT"
        )
    else:
        await admin.execute(
            f"ALTER ROLE {role} LOGIN PASSWORD '{quoted_password}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "NOBYPASSRLS NOINHERIT"
        )
    await admin.execute(f"ALTER ROLE {role} SET search_path = soul_v3, public")
    await admin.execute(f"REVOKE ALL ON DATABASE seal_memory FROM {role}")
    await admin.execute(f"GRANT CONNECT ON DATABASE seal_memory TO {role}")
    await admin.execute(f"REVOKE ALL ON SCHEMA soul_v3 FROM {role}")
    await admin.execute(f"GRANT USAGE ON SCHEMA soul_v3 TO {role}")
    await admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA soul_v3 FROM {role}")
    await admin.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA soul_v3 FROM {role}")

    await admin.execute(
        "GRANT SELECT ON soul_v3.agent_tasks, soul_v3.reflective_diagnoses, "
        "soul_v3.gam_event_graph, soul_v3.gam_topics, soul_v3.chat_messages "
        f"TO {role}"
    )
    await admin.execute(
        f"GRANT SELECT, INSERT, UPDATE ON soul_v3.motivation_states TO {role}"
    )
    await admin.execute(f"GRANT INSERT ON soul_v3.nerves_metrics_log TO {role}")
    await admin.execute(
        f"GRANT SELECT, INSERT, UPDATE ON soul_v3.working_state TO {role}"
    )
    await admin.execute(f"GRANT INSERT ON soul_v3.inner_monologue TO {role}")
    await admin.execute(f"GRANT INSERT ON soul_v3.memories TO {role}")
    await admin.execute(
        "GRANT USAGE, SELECT ON SEQUENCE "
        "soul_v3.motivation_states_id_seq, soul_v3.nerves_metrics_log_id_seq, "
        f"soul_v3.inner_monologue_id_seq, soul_v3.memories_id_seq TO {role}"
    )


async def _install_identity_boundary(admin: asyncpg.Connection) -> None:
    roles = ", ".join(AGENT_ROLES.values())
    await admin.execute(
        f"""
        CREATE OR REPLACE FUNCTION soul_v3.nerves_session_agent()
        RETURNS text
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $$ SELECT {_ident_sql()} $$;
        REVOKE ALL ON FUNCTION soul_v3.nerves_session_agent() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION soul_v3.nerves_session_agent() TO {roles};
        """
    )

    # Preserve the pre-existing behavior for other roles on tables that did
    # not previously have RLS, then add a role-targeted restrictive boundary.
    for table in ("agent_tasks", "gam_event_graph", "gam_topics", "nerves_metrics_log"):
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        await admin.execute(
            f"DROP POLICY IF EXISTS nerves_compat_all ON soul_v3.{table}; "
            f"CREATE POLICY nerves_compat_all ON soul_v3.{table} FOR ALL TO PUBLIC "
            "USING (true) WITH CHECK (true)"
        )

    agent_tables = (
        "motivation_states",
        "working_state",
        "reflective_diagnoses",
        "inner_monologue",
        "memories",
        "agent_tasks",
        "gam_event_graph",
        "gam_topics",
        "nerves_metrics_log",
    )
    for table in agent_tables:
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        await admin.execute(
            f"DROP POLICY IF EXISTS nerves_hard_session_identity ON soul_v3.{table}; "
            f"CREATE POLICY nerves_hard_session_identity ON soul_v3.{table} "
            f"AS RESTRICTIVE FOR ALL TO {roles} "
            "USING (agent::text = soul_v3.nerves_session_agent()) "
            "WITH CHECK (agent::text = soul_v3.nerves_session_agent())"
        )

    await admin.execute(
        "DROP POLICY IF EXISTS nerves_public_chat_read_v2 ON soul_v3.chat_messages; "
        "CREATE POLICY nerves_public_chat_read_v2 ON soul_v3.chat_messages "
        f"FOR SELECT TO {roles} "
        "USING (lower(channel) NOT LIKE 'dm:%%' "
        "AND lower(channel) NOT LIKE 'dm_%%' "
        "AND lower(channel) NOT LIKE 'user:%%')"
    )


async def _provision(*, retire_shared: bool) -> None:
    passwords = {
        agent: _existing_password(agent) or secrets.token_urlsafe(36)
        for agent in AGENT_UNITS
    }
    admin = await asyncpg.connect(pg_dsn(required=True))
    try:
        async with admin.transaction():
            for agent, password in passwords.items():
                await _configure_role(admin, agent, password)
            await _install_identity_boundary(admin)
            if retire_shared:
                await admin.execute(
                    f"ALTER ROLE {SHARED_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT"
                )
    finally:
        await admin.close()

    _write_runtime_files(passwords)
    modes = {agent: oct(_env_path(agent).stat().st_mode & 0o777) for agent in AGENT_UNITS}
    state = "NOLOGIN" if retire_shared else "LOGIN (rollback only)"
    print(f"per-agent roles provisioned={len(AGENT_ROLES)} env_modes={modes} shared={state}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retire-shared",
        action="store_true",
        help="set svc_soul_nerves NOLOGIN after verified cutover",
    )
    args = parser.parse_args()
    asyncio.run(_provision(retire_shared=args.retire_shared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
