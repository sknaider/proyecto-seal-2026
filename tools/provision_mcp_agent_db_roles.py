#!/usr/bin/env python3
"""Provision hard PostgreSQL identities for the multi-agent SOUL MCP runtime.

The MCP server authenticates the caller token first and then selects one of
five private DSNs.  RLS derives identity from ``session_user``; ``app.agent``
remains request context but cannot expand access.  A sixth broker login has
only capability-read and audit-insert privileges.

The operation is idempotent.  Generated credentials stay outside git in 0600
files.  Use ``--retire-shared`` only after the production cutover is green.
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


AGENTS = ("ADA", "ALICE", "DUM", "JARVIS", "NEXUS")
AGENT_ROLES = {agent: f"mcp_runtime_{agent.lower()}" for agent in AGENTS}
BROKER_ROLE = "mcp_broker_runtime"
SHARED_ROLE = "mcp_runtime"
CONFIG_DIR = Path.home() / ".config" / "seal"
AGENT_CRED_DIR = CONFIG_DIR / "mcp_agents"
BROKER_CRED = CONFIG_DIR / "mcp_broker.dsn"
SYSTEMD_DROPIN = (
    Path.home()
    / ".config/systemd/user/seal-mcp-server.service.d/least-privilege.conf"
)
SSAI_DROPIN = (
    Path.home()
    / ".config/systemd/user/seal-mcp-server.service.d/ssai-dual-verify.conf"
)

# Tables whose owner identity is not named simply ``agent``.
OWNER_EXPRESSIONS = {
    "audit_log": "actor::text = soul_v3.mcp_session_agent()",
    "memory_retrieval_log": "agent_requesting::text = soul_v3.mcp_session_agent()",
    "peer_models": "observer::text = soul_v3.mcp_session_agent()",
    "importance_review": (
        "(reviewed_agent::text = soul_v3.mcp_session_agent() OR "
        "reviewer_agent::text = soul_v3.mcp_session_agent())"
    ),
    "agent_challenges": (
        "(challenger_agent::text = soul_v3.mcp_session_agent() OR "
        "target_agent::text = soul_v3.mcp_session_agent())"
    ),
    "capability_grants": "caller_id::text = soul_v3.mcp_session_agent()",
    "consent_tokens": (
        "(grantor::text = soul_v3.mcp_session_agent() OR "
        "grantee::text = soul_v3.mcp_session_agent())"
    ),
    "debate_log": "soul_v3.mcp_session_agent() = ANY(agents_involved)",
}

MEMORY_RELATION_EXPRESSIONS = {
    "memory_poisoning_feedback": "memory_id",
    "recovery_anchors": "memory_id",
    "utility_updates": "memory_id",
    "index_state": "memory_id",
    "memory_graph_outbox": "memory_id",
    "vision_events": "soul_memory_id",
}

SSAI_REGISTRY_RELATIONS = (
    "ssai_events",
    "ssai_keys",
    "ssai_manifests",
    "ssai_tree_heads",
)


def _private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _existing_password(path: Path, expected_role: str) -> str | None:
    try:
        parsed = urlparse(path.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    if parsed.username == expected_role and parsed.password:
        return parsed.password
    return None


def _dsn(role: str, password: str, application_name: str) -> str:
    return (
        f"postgresql://{role}:{quote(password, safe='')}"
        "@localhost:5433/seal_memory"
        f"?application_name={application_name}"
    )


async def _ensure_login(
    admin: asyncpg.Connection,
    role: str,
    password: str,
) -> None:
    exists = await admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    )
    password_sql = password.replace("'", "''")
    verb = "ALTER" if exists else "CREATE"
    await admin.execute(
        f"{verb} ROLE {role} LOGIN PASSWORD '{password_sql}' "
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


async def _clone_effective_runtime_grants(
    admin: asyncpg.Connection,
    target_role: str,
) -> None:
    tables = await admin.fetch(
        """
        SELECT n.nspname, c.relname,
               has_table_privilege($1, c.oid, 'SELECT') AS s,
               has_table_privilege($1, c.oid, 'INSERT') AS i,
               has_table_privilege($1, c.oid, 'UPDATE') AS u,
               has_table_privilege($1, c.oid, 'DELETE') AS d
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soul_v3' AND c.relkind IN ('r','p','v')
        ORDER BY c.relname
        """,
        SHARED_ROLE,
    )
    for row in tables:
        operations = [
            op
            for op, key in (("SELECT", "s"), ("INSERT", "i"), ("UPDATE", "u"), ("DELETE", "d"))
            if row[key]
        ]
        if operations:
            await admin.execute(
                f"GRANT {', '.join(operations)} ON "
                f"{row['nspname']}.{row['relname']} TO {target_role}"
            )

        # Preserve deliberately narrow column grants (notably the MCP memory
        # update surface) without broadening them to whole-table writes.
        columns = await admin.fetch(
            """
            SELECT a.attname,
                   has_column_privilege($1, c.oid, a.attnum, 'SELECT') AS s,
                   has_column_privilege($1, c.oid, a.attnum, 'INSERT') AS i,
                   has_column_privilege($1, c.oid, a.attnum, 'UPDATE') AS u
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname=$2 AND c.relname=$3
              AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            SHARED_ROLE,
            row["nspname"],
            row["relname"],
        )
        table_level = {"SELECT": row["s"], "INSERT": row["i"], "UPDATE": row["u"]}
        for operation, key in (("SELECT", "s"), ("INSERT", "i"), ("UPDATE", "u")):
            if table_level[operation]:
                continue
            names = [f'"{col["attname"].replace(chr(34), chr(34) * 2)}"' for col in columns if col[key]]
            if names:
                await admin.execute(
                    f"GRANT {operation} ({', '.join(names)}) ON "
                    f"{row['nspname']}.{row['relname']} TO {target_role}"
                )

    sequences = await admin.fetch(
        """
        SELECT n.nspname, c.relname,
               has_sequence_privilege($1, c.oid, 'USAGE') AS usage,
               has_sequence_privilege($1, c.oid, 'SELECT') AS sel,
               has_sequence_privilege($1, c.oid, 'UPDATE') AS upd
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soul_v3' AND c.relkind='S'
        ORDER BY c.relname
        """,
        SHARED_ROLE,
    )
    for row in sequences:
        operations = [
            op
            for op, key in (("USAGE", "usage"), ("SELECT", "sel"), ("UPDATE", "upd"))
            if row[key]
        ]
        if operations:
            await admin.execute(
                f"GRANT {', '.join(operations)} ON SEQUENCE "
                f"{row['nspname']}.{row['relname']} TO {target_role}"
            )


def _identity_case() -> str:
    branches = " ".join(
        f"WHEN '{role}' THEN '{agent}'" for agent, role in AGENT_ROLES.items()
    )
    return f"CASE session_user {branches} ELSE NULL END"


async def _install_hard_identity(admin: asyncpg.Connection) -> None:
    roles = ", ".join(AGENT_ROLES.values())
    await admin.execute(
        f"""
        CREATE OR REPLACE FUNCTION soul_v3.mcp_session_agent()
        RETURNS text
        LANGUAGE sql
        STABLE
        SET search_path = pg_catalog
        AS $$ SELECT {_identity_case()} $$;
        REVOKE ALL ON FUNCTION soul_v3.mcp_session_agent() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION soul_v3.mcp_session_agent() TO {roles};
        """
    )

    agent_tables = await admin.fetch(
        """
        SELECT DISTINCT c.relname, c.relrowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='soul_v3' AND c.relkind IN ('r','p')
          AND a.attname='agent' AND a.attnum>0 AND NOT a.attisdropped
          AND (
            has_table_privilege($1, c.oid, 'SELECT') OR
            has_table_privilege($1, c.oid, 'INSERT') OR
            has_table_privilege($1, c.oid, 'UPDATE') OR
            has_table_privilege($1, c.oid, 'DELETE')
          )
        ORDER BY c.relname
        """,
        SHARED_ROLE,
    )
    for row in agent_tables:
        table = row["relname"]
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        if not row["relrowsecurity"]:
            await admin.execute(
                f"DROP POLICY IF EXISTS mcp_legacy_compat_all ON soul_v3.{table}; "
                f"CREATE POLICY mcp_legacy_compat_all ON soul_v3.{table} "
                "FOR ALL TO PUBLIC USING (true) WITH CHECK (true)"
            )
        await admin.execute(
            f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.{table}; "
            f"CREATE POLICY mcp_login_capability ON soul_v3.{table} "
            f"FOR ALL TO {roles} USING (true) WITH CHECK (true)"
        )
        if table == "memories":
            await admin.execute(
                f"DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.{table}; "
                f"DROP POLICY IF EXISTS mcp_hard_read ON soul_v3.{table}; "
                f"DROP POLICY IF EXISTS mcp_hard_insert ON soul_v3.{table}; "
                f"DROP POLICY IF EXISTS mcp_hard_update ON soul_v3.{table}; "
                f"DROP POLICY IF EXISTS mcp_hard_delete ON soul_v3.{table}; "
                f"CREATE POLICY mcp_hard_read ON soul_v3.{table} AS RESTRICTIVE "
                f"FOR SELECT TO {roles} USING ("
                "agent::text = soul_v3.mcp_session_agent() OR "
                "COALESCE(scope, 'private')::text IN ('team','public')); "
                f"CREATE POLICY mcp_hard_insert ON soul_v3.{table} AS RESTRICTIVE "
                f"FOR INSERT TO {roles} WITH CHECK (agent::text = soul_v3.mcp_session_agent()); "
                f"CREATE POLICY mcp_hard_update ON soul_v3.{table} AS RESTRICTIVE "
                f"FOR UPDATE TO {roles} USING (agent::text = soul_v3.mcp_session_agent()) "
                "WITH CHECK (agent::text = soul_v3.mcp_session_agent()); "
                f"CREATE POLICY mcp_hard_delete ON soul_v3.{table} AS RESTRICTIVE "
                f"FOR DELETE TO {roles} USING (agent::text = soul_v3.mcp_session_agent())"
            )
        else:
            await admin.execute(
                f"DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.{table}; "
                f"CREATE POLICY mcp_hard_identity ON soul_v3.{table} AS RESTRICTIVE "
                f"FOR ALL TO {roles} "
                "USING (agent::text = soul_v3.mcp_session_agent()) "
                "WITH CHECK (agent::text = soul_v3.mcp_session_agent())"
            )

    for table, expression in OWNER_EXPRESSIONS.items():
        exists = await admin.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{table}"
        )
        if not exists:
            continue
        has_any = await admin.fetchval(
            """
            SELECT has_table_privilege($1, $2, 'SELECT')
                OR has_table_privilege($1, $2, 'INSERT')
                OR has_table_privilege($1, $2, 'UPDATE')
                OR has_table_privilege($1, $2, 'DELETE')
            """,
            SHARED_ROLE,
            f"soul_v3.{table}",
        )
        if not has_any:
            continue
        rls_was_enabled = await admin.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass",
            f"soul_v3.{table}",
        )
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        if not rls_was_enabled:
            await admin.execute(
                f"DROP POLICY IF EXISTS mcp_legacy_compat_all ON soul_v3.{table}; "
                f"CREATE POLICY mcp_legacy_compat_all ON soul_v3.{table} "
                "FOR ALL TO PUBLIC USING (true) WITH CHECK (true)"
            )
        await admin.execute(
            f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.{table}; "
            f"CREATE POLICY mcp_login_capability ON soul_v3.{table} "
            f"FOR ALL TO {roles} USING (true) WITH CHECK (true); "
            f"DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.{table}; "
            f"CREATE POLICY mcp_hard_identity ON soul_v3.{table} AS RESTRICTIVE "
            f"FOR ALL TO {roles} USING ({expression}) WITH CHECK ({expression})"
        )

    for table, memory_column in MEMORY_RELATION_EXPRESSIONS.items():
        exists = await admin.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{table}"
        )
        if not exists:
            continue
        has_any = await admin.fetchval(
            """
            SELECT has_table_privilege($1, $2, 'SELECT')
                OR has_table_privilege($1, $2, 'INSERT')
                OR has_table_privilege($1, $2, 'UPDATE')
                OR has_table_privilege($1, $2, 'DELETE')
            """,
            SHARED_ROLE,
            f"soul_v3.{table}",
        )
        if not has_any:
            continue
        rls_was_enabled = await admin.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass",
            f"soul_v3.{table}",
        )
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        if not rls_was_enabled:
            await admin.execute(
                f"DROP POLICY IF EXISTS mcp_legacy_compat_all ON soul_v3.{table}; "
                f"CREATE POLICY mcp_legacy_compat_all ON soul_v3.{table} "
                "FOR ALL TO PUBLIC USING (true) WITH CHECK (true)"
            )
        expression = (
            "EXISTS (SELECT 1 FROM soul_v3.memories m "
            f"WHERE m.id = {table}.{memory_column} "
            "AND m.agent::text = soul_v3.mcp_session_agent())"
        )
        await admin.execute(
            f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.{table}; "
            f"CREATE POLICY mcp_login_capability ON soul_v3.{table} "
            f"FOR ALL TO {roles} USING (true) WITH CHECK (true); "
            f"DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.{table}; "
            f"CREATE POLICY mcp_hard_identity ON soul_v3.{table} AS RESTRICTIVE "
            f"FOR ALL TO {roles} USING ({expression}) WITH CHECK ({expression})"
        )

    for table in SSAI_REGISTRY_RELATIONS:
        if not await admin.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{table}"
        ):
            continue
        await admin.execute(f"ALTER TABLE soul_v3.{table} ENABLE ROW LEVEL SECURITY")
        await admin.execute(
            f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.{table}; "
            f"CREATE POLICY mcp_login_capability ON soul_v3.{table} "
            f"FOR ALL TO {roles} USING (true) WITH CHECK (true); "
            f"DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.{table}; "
            f"CREATE POLICY mcp_hard_identity ON soul_v3.{table} AS RESTRICTIVE "
            f"FOR ALL TO {roles} USING (EXISTS ("
            "SELECT 1 FROM soul_v3.ssai_registry r "
            f"WHERE r.id = {table}.registry_id "
            "AND r.agent::text = soul_v3.mcp_session_agent())) "
            "WITH CHECK (EXISTS (SELECT 1 FROM soul_v3.ssai_registry r "
            f"WHERE r.id = {table}.registry_id "
            "AND r.agent::text = soul_v3.mcp_session_agent()))"
        )

    if await admin.fetchval(
        "SELECT to_regclass('soul_v3.ssai_signatures') IS NOT NULL"
    ):
        await admin.execute("ALTER TABLE soul_v3.ssai_signatures ENABLE ROW LEVEL SECURITY")
        await admin.execute(
            f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.ssai_signatures; "
            f"CREATE POLICY mcp_login_capability ON soul_v3.ssai_signatures "
            f"FOR ALL TO {roles} USING (true) WITH CHECK (true); "
            "DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.ssai_signatures; "
            "CREATE POLICY mcp_hard_identity ON soul_v3.ssai_signatures AS RESTRICTIVE "
            f"FOR ALL TO {roles} USING (EXISTS ("
            "SELECT 1 FROM soul_v3.ssai_manifests sm "
            "JOIN soul_v3.ssai_registry r ON r.id=sm.registry_id "
            "WHERE sm.id=ssai_signatures.manifest_id "
            "AND r.agent::text=soul_v3.mcp_session_agent())) "
            "WITH CHECK (EXISTS (SELECT 1 FROM soul_v3.ssai_manifests sm "
            "JOIN soul_v3.ssai_registry r ON r.id=sm.registry_id "
            "WHERE sm.id=ssai_signatures.manifest_id "
            "AND r.agent::text=soul_v3.mcp_session_agent()))"
        )

    # Public team channels remain visible; private channels are bound to the
    # authenticated agent and William, never to a caller-controlled GUC.
    await admin.execute(
        "ALTER TABLE soul_v3.chat_messages ENABLE ROW LEVEL SECURITY; "
        f"DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.chat_messages; "
        f"CREATE POLICY mcp_login_capability ON soul_v3.chat_messages "
        f"FOR SELECT TO {roles} USING (true); "
        "DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.chat_messages; "
        "CREATE POLICY mcp_hard_identity ON soul_v3.chat_messages AS RESTRICTIVE "
        f"FOR SELECT TO {roles} USING ("
        "(lower(channel) NOT LIKE 'dm:%%' AND lower(channel) NOT LIKE 'dm_%%' "
        "AND lower(channel) NOT LIKE 'user:%%') OR "
        "lower(channel) IN ("
        "'dm:' || lower(soul_v3.mcp_session_agent()) || ':william', "
        "'dm:william:' || lower(soul_v3.mcp_session_agent()), "
        "'dm_' || lower(soul_v3.mcp_session_agent()) || '_william', "
        "'dm_william_' || lower(soul_v3.mcp_session_agent())))"
    )

    await admin.execute(
        "ALTER TABLE soul_v3.memory_broadcasts ENABLE ROW LEVEL SECURITY; "
        "DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.memory_broadcasts; "
        "DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.memory_broadcasts; "
        f"CREATE POLICY mcp_login_capability ON soul_v3.memory_broadcasts "
        f"FOR ALL TO {roles} USING (true) WITH CHECK (true); "
        "DROP POLICY IF EXISTS mcp_hard_read ON soul_v3.memory_broadcasts; "
        f"CREATE POLICY mcp_hard_read ON soul_v3.memory_broadcasts AS RESTRICTIVE "
        f"FOR SELECT TO {roles} USING ("
        "from_agent::text=soul_v3.mcp_session_agent() OR "
        "lower(to_scope::text) IN ('team','shared','public')); "
        "DROP POLICY IF EXISTS mcp_hard_insert ON soul_v3.memory_broadcasts; "
        f"CREATE POLICY mcp_hard_insert ON soul_v3.memory_broadcasts AS RESTRICTIVE "
        f"FOR INSERT TO {roles} WITH CHECK ("
        "from_agent::text=soul_v3.mcp_session_agent())"
    )

    # The tool registry is intentionally team-shared, not private agent state.
    # Remove any restrictive policy from an interrupted earlier rollout and
    # preserve the canonical boot view for every authenticated MCP agent.
    await admin.execute(
        "DROP POLICY IF EXISTS mcp_hard_identity ON soul_v3.agent_tools_registry; "
        "DROP POLICY IF EXISTS mcp_login_capability ON soul_v3.agent_tools_registry"
    )
    await admin.execute(
        f"GRANT SELECT ON soul_v3.v_agent_tools_boot TO {roles}"
    )


async def _configure_broker(admin: asyncpg.Connection, password: str) -> None:
    await _ensure_login(admin, BROKER_ROLE, password)
    await admin.execute(
        "GRANT SELECT ON soul_v3.capability_scope, soul_v3.capability_grants "
        f"TO {BROKER_ROLE}"
    )
    await admin.execute(f"GRANT INSERT ON soul_v3.audit_log TO {BROKER_ROLE}")
    # INSERT ... RETURNING id requires SELECT on the returned column, not on
    # the rest of the immutable audit payload.
    await admin.execute(f"GRANT SELECT (id) ON soul_v3.audit_log TO {BROKER_ROLE}")
    await admin.execute(
        f"GRANT USAGE, SELECT ON SEQUENCE soul_v3.audit_log_id_seq TO {BROKER_ROLE}"
    )


def _write_runtime_files(agent_passwords: dict[str, str], broker_password: str) -> None:
    for agent, role in AGENT_ROLES.items():
        _private_write(
            AGENT_CRED_DIR / f"{agent.lower()}.dsn",
            _dsn(role, agent_passwords[agent], f"seal_mcp_{agent.lower()}") + "\n",
        )
    _private_write(
        BROKER_CRED,
        _dsn(BROKER_ROLE, broker_password, "seal_mcp_broker") + "\n",
    )
    SYSTEMD_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_DROPIN.write_text(
        "[Service]\n"
        "Environment=SEAL_MCP_RUNTIME_DB=1\n"
        "Environment=SEAL_MCP_RUNTIME_CRED=\n"
        "Environment=SEAL_MCP_BROKER_CRED=%h/.config/seal/mcp_broker.dsn\n"
        "Environment=SEAL_MCP_AGENT_CRED_DIR=%h/.config/seal/mcp_agents\n",
        encoding="utf-8",
    )
    SSAI_DROPIN.write_text(
        "[Service]\n"
        "Environment=SEAL_SSAI_MODE=DUAL_VERIFY\n"
        "ExecStartPre=\n"
        "ExecStartPre=-/home/dadito/IA/seal-spark/.venv/bin/python3 "
        "/home/dadito/IA/proyecto-seal/memory/ssai_runtime.py "
        "--dsn-file %h/.config/seal/mcp_agents/ada.dsn verify --agent ADA\n",
        encoding="utf-8",
    )


async def provision(*, retire_shared: bool) -> None:
    agent_passwords = {
        agent: _existing_password(
            AGENT_CRED_DIR / f"{agent.lower()}.dsn", role
        )
        or secrets.token_urlsafe(36)
        for agent, role in AGENT_ROLES.items()
    }
    broker_password = (
        _existing_password(BROKER_CRED, BROKER_ROLE) or secrets.token_urlsafe(36)
    )
    admin = await asyncpg.connect(pg_dsn(required=True))
    try:
        async with admin.transaction():
            for agent, role in AGENT_ROLES.items():
                await _ensure_login(admin, role, agent_passwords[agent])
                await _clone_effective_runtime_grants(admin, role)
            await _configure_broker(admin, broker_password)
            await _install_hard_identity(admin)
            if retire_shared:
                await admin.execute(
                    f"ALTER ROLE {SHARED_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT"
                )
    finally:
        await admin.close()
    _write_runtime_files(agent_passwords, broker_password)
    modes = {
        p.name: oct(p.stat().st_mode & 0o777)
        for p in [*AGENT_CRED_DIR.glob("*.dsn"), BROKER_CRED]
    }
    print(
        f"mcp_agent_roles={len(AGENT_ROLES)} broker=1 "
        f"credential_modes={modes} shared_retired={retire_shared}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retire-shared",
        action="store_true",
        help="set the old shared mcp_runtime login NOLOGIN after verified cutover",
    )
    args = parser.parse_args()
    asyncio.run(provision(retire_shared=args.retire_shared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
