#!/usr/bin/env python3
"""Provision dedicated least-privilege logins for legacy operational services.

Default mode is a read-only plan.  ``--apply`` performs database changes and
writes one raw DSN per service under a private local directory.  Passwords and
DSNs are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
from operational_db_credentials import service_pg_dsn  # noqa: E402


@dataclass(frozen=True)
class Policy:
    table: str
    command: str
    expression: str
    restrictive: bool = False
    suffix: str = "capability"


@dataclass(frozen=True)
class ColumnGrant:
    table: str
    privilege: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class BoundaryCheck:
    query: str
    settings: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ViewContract:
    name: str
    columns: tuple[str, ...]
    definition_tokens: tuple[str, ...]
    security_barrier: bool = True
    check_option: str = "NONE"


@dataclass(frozen=True)
class ServiceRole:
    service: str
    database: str
    role: str
    env_name: str
    grants: tuple[tuple[str, str], ...] = ()
    column_grants: tuple[ColumnGrant, ...] = ()
    function_grants: tuple[str, ...] = ()
    policies: tuple[Policy, ...] = ()
    optional_relations: tuple[str, ...] = ()
    boundary_checks: tuple[BoundaryCheck, ...] = ()
    view_contracts: tuple[ViewContract, ...] = ()
    setup: str | None = None


SEAL_MEMORY_ROLES = (
    ServiceRole(
        service="seal-durable-cron", database="seal_memory",
        role="svc_seal_durable_cron", env_name="SEAL_DURABLE_CRON_PG_DSN",
        column_grants=(
            ColumnGrant(
                "session_loops", "SELECT",
                ("agent", "loop_name", "loop_prompt", "interval_seconds", "active", "last_fired"),
            ),
            ColumnGrant(
                "session_loops", "INSERT",
                ("agent", "loop_name", "loop_prompt", "interval_seconds", "active"),
            ),
            ColumnGrant(
                "session_loops", "UPDATE",
                ("loop_prompt", "interval_seconds", "active", "last_fired"),
            ),
        ),
        policies=(
            Policy("session_loops", "SELECT", "true", suffix="read"),
            Policy("session_loops", "INSERT", "true", suffix="write"),
            Policy("session_loops", "UPDATE", "true", suffix="write"),
        ),
        setup="session_loops",
    ),
    ServiceRole(
        service="seal-lifecycle", database="seal_memory", role="svc_seal_lifecycle",
        env_name="SEAL_LIFECYCLE_PG_DSN",
        grants=(("agent_lifecycle", "SELECT"), ("lifecycle_events", "SELECT, INSERT")),
    ),
    ServiceRole(
        service="seal-peer-health", database="seal_memory", role="svc_seal_peer_health",
        env_name="SEAL_PEER_HEALTH_PG_DSN", grants=(("event_log", "SELECT"),),
        policies=(
            Policy("event_log", "SELECT", "true", suffix="capability"),
            Policy(
                "event_log", "SELECT", "event_type = 'heartbeat'",
                restrictive=True, suffix="hard_scope",
            ),
        ),
        boundary_checks=(BoundaryCheck(
            "SELECT count(*) FROM soul_v3.event_log "
            "WHERE event_type IS DISTINCT FROM 'heartbeat'"
        ),),
    ),
    ServiceRole(
        service="seal-continuity", database="seal_memory", role="svc_seal_continuity",
        env_name="SEAL_CONTINUITY_PG_DSN",
        grants=(("inner_monologue", "SELECT"), ("working_state", "SELECT")),
        policies=(
            Policy("inner_monologue", "SELECT", "true", suffix="capability"),
            Policy("working_state", "SELECT", "true", suffix="capability"),
            Policy(
                "inner_monologue", "SELECT",
                "agent = ANY (ARRAY['ADA','ALICE','DUM','JARVIS']::text[])",
                restrictive=True, suffix="hard_scope",
            ),
            Policy(
                "working_state", "SELECT",
                "agent = ANY (ARRAY['ADA','ALICE','DUM','JARVIS']::text[])",
                restrictive=True, suffix="hard_scope",
            ),
        ),
        boundary_checks=(
            BoundaryCheck(
                "SELECT count(*) FROM soul_v3.inner_monologue "
                "WHERE agent <> ALL (ARRAY['ADA','ALICE','DUM','JARVIS']::text[])",
                (("app.agent", "NEXUS"),
                 ("app.tenant_id", "00000000-0000-0000-0000-000000000000")),
            ),
            BoundaryCheck(
                "SELECT count(*) FROM soul_v3.working_state "
                "WHERE agent <> ALL (ARRAY['ADA','ALICE','DUM','JARVIS']::text[])",
                (("app.agent", "NEXUS"),),
            ),
        ),
    ),
    ServiceRole(
        service="seal-memory-monitor", database="seal_memory", role="svc_seal_memory_monitor",
        env_name="SEAL_MEMORY_MONITOR_PG_DSN",
        grants=(
            ("memory_monitor_memories_boundary", "SELECT"),
            ("memory_monitor_audit_boundary", "SELECT"),
            ("memory_monitor_working_state_boundary", "SELECT"),
            ("memory_monitor_capabilities_boundary", "SELECT"),
        ),
        optional_relations=(
            "memory_monitor_audit_boundary", "memory_monitor_working_state_boundary",
        ),
        view_contracts=(
            ViewContract(
                "memory_monitor_memories_boundary",
                ("id", "agent", "content", "created_at", "importance", "scope", "metadata",
                 "revision_count", "drift_score", "last_revision_at", "invalid_at"),
                ("from soul_v3.memories",),
            ),
            ViewContract(
                "memory_monitor_audit_boundary",
                ("agent", "operation", "audit_timestamp", "content_hash"),
                ("from soul_v3.memory_audit_log",),
            ),
            ViewContract(
                "memory_monitor_working_state_boundary",
                ("agent", "state"),
                ("from soul_v3.agent_working_state",),
            ),
            ViewContract(
                "memory_monitor_capabilities_boundary",
                ("drift_schema_available",),
                ("drift_schema_available",),
            ),
        ),
        setup="memory_monitor_boundary",
    ),
    ServiceRole(
        service="seal-harness-watcher", database="seal_memory",
        role="svc_seal_harness_watcher", env_name="SEAL_HARNESS_WATCHER_PG_DSN",
        grants=(("harness_truth_registry", "SELECT"), ("harness_oracle", "INSERT"),
                ("chat_messages", "SELECT")),
        policies=(
            Policy("harness_oracle", "INSERT", "agent = 'seal_harness_watcher'", suffix="write"),
            Policy(
                "chat_messages", "SELECT",
                "channel IN ('web_chat','dm:ada:william') AND "
                "((channel='web_chat' AND lower(sender_name)=ANY(ARRAY['william','henry','jarvis','alice','nexus','dum'])) "
                "OR (channel='dm:ada:william' AND lower(sender_name) IN ('william','henry')))",
                suffix="read",
            ),
        ),
    ),
    ServiceRole(
        service="seal-laptop-activity", database="seal_memory",
        role="svc_seal_laptop_activity", env_name="SEAL_LAPTOP_ACTIVITY_PG_DSN",
        grants=(("laptop_activity", "SELECT, INSERT"),),
    ),
    ServiceRole(
        service="seal-tools-catalog", database="seal_memory", role="svc_seal_tools_catalog",
        env_name="SEAL_TOOLS_CATALOG_PG_DSN",
        column_grants=(
            ColumnGrant(
                "agent_tools_registry", "SELECT",
                ("id", "tool_key", "port", "status", "metadata", "last_seen_at"),
            ),
            ColumnGrant(
                "agent_tools_registry", "UPDATE",
                ("status", "metadata", "last_seen_at", "last_checked_at"),
            ),
        ),
        policies=(
            Policy("agent_tools_registry", "SELECT", "true", suffix="capability"),
            Policy("agent_tools_registry", "SELECT", "true", restrictive=True, suffix="hard_scope"),
            Policy("agent_tools_registry", "UPDATE", "true", suffix="capability"),
            Policy("agent_tools_registry", "UPDATE", "true", restrictive=True, suffix="hard_scope"),
        ),
    ),
)

STANDALONE_ROLES = (
    ServiceRole(
        service="spectre-openai-shim", database="soul_standalone", role="svc_spectre_shim",
        env_name="SPECTRE_STANDALONE_PG_DSN",
        grants=(
            ("spectre_identity_boundary", "SELECT, INSERT"),
            ("spectre_memories_boundary", "SELECT, INSERT"),
        ),
        function_grants=("ensure_spectre_prereqs()",),
        boundary_checks=(
            BoundaryCheck(
                "SELECT count(*) FROM soul_v3.spectre_identity_boundary "
                "WHERE agent <> 'SPECTRE'"
            ),
            BoundaryCheck(
                "SELECT count(*) FROM soul_v3.spectre_memories_boundary "
                "WHERE agent <> 'SPECTRE' OR "
                "tenant_id <> '00000000-0000-0000-0000-000000000000'::uuid"
            ),
        ),
        view_contracts=(
            ViewContract(
                "spectre_identity_boundary",
                ("agent", "personality", "boot_context", "philosophy"),
                ("from soul_v3.identity", "agent = 'SPECTRE'::text"),
                check_option="LOCAL",
            ),
            ViewContract(
                "spectre_memories_boundary",
                ("agent", "scope", "category", "content", "content_hash_sha256",
                 "importance", "tenant_id", "created_at"),
                ("from soul_v3.memories", "agent = 'SPECTRE'::text",
                 "tenant_id = '00000000-0000-0000-0000-000000000000'::uuid"),
                check_option="LOCAL",
            ),
        ),
        setup="spectre_boundary",
    ),
)

ALL_ROLES = SEAL_MEMORY_ROLES + STANDALONE_ROLES
SECRET_DIR = Path.home() / ".config" / "seal" / "postgres-services"
APPLY_ACK = "PROVISION_OPERATIONAL_ROLES"
SENSITIVE_PUBLIC_FUNCTIONS = (
    "memory_dual_usage_stats()",
    "memory_structure_stats()",
)
FUNCTION_CANARY_ROLES = ("fable_ltd", "soul_admin")
REQUIRED_FUNCTION_ROLES = {
    "soul_standalone": ("soul_admin",),
}


def _secret_path(spec: ServiceRole) -> Path:
    return SECRET_DIR / f"{spec.service}.dsn"


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_st = os.lstat(path.parent)
    if not stat.S_ISDIR(parent_st.st_mode) or stat.S_ISLNK(parent_st.st_mode):
        raise RuntimeError(f"refusing non-directory secret parent: {path.parent}")
    if parent_st.st_uid != os.getuid():
        raise RuntimeError(f"refusing secret parent owned by another uid: {path.parent}")
    os.chmod(path.parent, 0o700)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid():
            raise RuntimeError("refusing unsafe temporary secret file")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        _read_private(path)
        dir_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            dir_flags |= os.O_DIRECTORY
        dir_fd = os.open(path.parent, dir_flags)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_private(path: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise RuntimeError(f"unsafe secret file metadata: {path}")
        if opened.st_size > 16384:
            raise RuntimeError(f"secret file exceeds size limit: {path}")
        chunks: list[bytes] = []
        remaining = 16385
        while remaining:
            chunk = os.read(fd, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise RuntimeError(f"secret file exceeds size limit: {path}")
        return b"".join(chunks).decode("utf-8").strip()
    finally:
        os.close(fd)


def _existing_password(path: Path, role: str) -> str | None:
    try:
        parsed = urlsplit(_read_private(path))
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None
    if parsed.username and parsed.password and unquote(parsed.username) == role:
        return unquote(parsed.password)
    return None


def _service_dsn(admin_dsn: str, spec: ServiceRole, password: str) -> str:
    parsed = urlsplit(admin_dsn)
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["application_name"] = spec.service.replace("-", "_")
    return urlunsplit((
        parsed.scheme,
        f"{quote(spec.role, safe='')}:{quote(password, safe='')}@{host}{port}",
        f"/{spec.database}", urlencode(query), "",
    ))


async def _prepare_session_loops(conn) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS soul_v3.session_loops (
               id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
               agent varchar(50) NOT NULL,
               loop_name varchar(100) NOT NULL,
               loop_prompt text NOT NULL,
               interval_seconds integer NOT NULL CHECK (interval_seconds > 0),
               active boolean NOT NULL DEFAULT true,
               last_fired timestamptz,
               created_at timestamptz NOT NULL DEFAULT now(),
               UNIQUE (agent, loop_name)
           )"""
    )
    await conn.execute("ALTER TABLE soul_v3.session_loops ENABLE ROW LEVEL SECURITY")
    await conn.execute("ALTER TABLE soul_v3.session_loops FORCE ROW LEVEL SECURITY")


async def _prepare_spectre_boundary(conn) -> None:
    await conn.execute(
        """CREATE OR REPLACE FUNCTION soul_v3.ensure_spectre_prereqs()
           RETURNS void
           LANGUAGE sql
           SECURITY DEFINER
           SET search_path = ''
           AS $$
             INSERT INTO soul_v3.tenants (id, name)
             VALUES ('00000000-0000-0000-0000-000000000000', 'default')
             ON CONFLICT (id) DO NOTHING;
             INSERT INTO soul_v3.agents (name, role, active)
             VALUES ('SPECTRE', 'alma nueva standalone (SOUL Core sobre cerebro local)', true)
             ON CONFLICT (name) DO NOTHING;
           $$"""
    )
    await conn.execute(
        "REVOKE ALL ON FUNCTION soul_v3.ensure_spectre_prereqs() FROM PUBLIC"
    )
    await conn.execute(
        """CREATE OR REPLACE VIEW soul_v3.spectre_identity_boundary
           WITH (security_barrier=true) AS
           SELECT agent, personality, boot_context, philosophy
           FROM soul_v3.identity
           WHERE agent = 'SPECTRE'
           WITH LOCAL CHECK OPTION"""
    )
    await conn.execute(
        """CREATE OR REPLACE VIEW soul_v3.spectre_memories_boundary
           WITH (security_barrier=true) AS
           SELECT agent, scope, category, content, content_hash_sha256,
                  importance, tenant_id, created_at
           FROM soul_v3.memories
           WHERE agent = 'SPECTRE'
             AND tenant_id = '00000000-0000-0000-0000-000000000000'::uuid
           WITH LOCAL CHECK OPTION"""
    )
    await conn.execute(
        "REVOKE ALL ON soul_v3.spectre_identity_boundary, "
        "soul_v3.spectre_memories_boundary FROM PUBLIC"
    )


async def _prepare_memory_monitor_boundary(conn) -> None:
    drift_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='soul_v3' AND table_name='memories'
                 AND column_name=ANY($1::text[])""",
            ["revision_count", "drift_score", "last_revision_at"],
        )
    }
    drift_projection = {
        "revision_count": "revision_count::integer AS revision_count" if "revision_count" in drift_columns
        else "NULL::integer AS revision_count",
        "drift_score": "drift_score::double precision AS drift_score" if "drift_score" in drift_columns
        else "NULL::double precision AS drift_score",
        "last_revision_at": "last_revision_at::timestamptz AS last_revision_at" if "last_revision_at" in drift_columns
        else "NULL::timestamptz AS last_revision_at",
    }
    await conn.execute(
        "CREATE OR REPLACE VIEW soul_v3.memory_monitor_memories_boundary "
        "WITH (security_barrier=true) AS "
        "SELECT id, agent, content, created_at, importance, scope, metadata, "
        f"{drift_projection['revision_count']}, {drift_projection['drift_score']}, "
        f"{drift_projection['last_revision_at']}, invalid_at FROM soul_v3.memories"
    )
    await conn.execute(
        "CREATE OR REPLACE VIEW soul_v3.memory_monitor_capabilities_boundary "
        "WITH (security_barrier=true) AS "
        f"SELECT {str(len(drift_columns) == 3).lower()}::boolean "
        "AS drift_schema_available"
    )
    audit_exists = await conn.fetchval(
        "SELECT to_regclass('soul_v3.memory_audit_log') IS NOT NULL"
    )
    if audit_exists:
        await conn.execute(
            """CREATE OR REPLACE VIEW soul_v3.memory_monitor_audit_boundary
               WITH (security_barrier=true) AS
               SELECT agent, operation, audit_timestamp, content_hash
               FROM soul_v3.memory_audit_log"""
        )
    working_exists = await conn.fetchval(
        "SELECT to_regclass('soul_v3.agent_working_state') IS NOT NULL"
    )
    if working_exists:
        await conn.execute(
            """CREATE OR REPLACE VIEW soul_v3.memory_monitor_working_state_boundary
               WITH (security_barrier=true) AS
               SELECT agent, state FROM soul_v3.agent_working_state"""
        )
    relations = [
        "soul_v3.memory_monitor_memories_boundary",
        "soul_v3.memory_monitor_capabilities_boundary",
    ]
    if audit_exists:
        relations.append("soul_v3.memory_monitor_audit_boundary")
    if working_exists:
        relations.append("soul_v3.memory_monitor_working_state_boundary")
    await conn.execute(f"REVOKE ALL ON {', '.join(relations)} FROM PUBLIC")


async def _prepare_service_relations(conn, spec: ServiceRole) -> None:
    if spec.setup == "session_loops":
        await _prepare_session_loops(conn)
    elif spec.setup == "memory_monitor_boundary":
        await _prepare_memory_monitor_boundary(conn)
    elif spec.setup == "spectre_boundary":
        await _prepare_spectre_boundary(conn)


async def _explicit_function_execute(conn, role_name: str, signature: str) -> bool:
    return bool(await conn.fetchval(
        """SELECT EXISTS (
             SELECT 1
             FROM pg_proc p
             JOIN pg_namespace n ON n.oid=p.pronamespace
             CROSS JOIN LATERAL aclexplode(
                 COALESCE(p.proacl, '{}'::aclitem[])
             ) acl
             JOIN pg_roles grantee ON grantee.oid=acl.grantee
             WHERE n.nspname='soul_v3'
               AND p.oid=to_regprocedure($1)
               AND grantee.rolname=$2
               AND acl.privilege_type='EXECUTE'
           )""",
        f"soul_v3.{signature}", role_name,
    ))


async def _public_function_execute(conn, signature: str) -> bool:
    return bool(await conn.fetchval(
        """SELECT EXISTS (
             SELECT 1
             FROM pg_proc p
             JOIN pg_namespace n ON n.oid=p.pronamespace
             CROSS JOIN LATERAL aclexplode(
                 COALESCE(p.proacl, acldefault('f', p.proowner))
             ) acl
             WHERE n.nspname='soul_v3'
               AND p.oid=to_regprocedure($1)
               AND acl.grantee=0
               AND acl.privilege_type='EXECUTE'
           )""",
        f"soul_v3.{signature}",
    ))


async def _verify_sensitive_function_contract(conn, database: str) -> None:
    required_roles = REQUIRED_FUNCTION_ROLES.get(database, ())
    operational_roles = tuple(
        spec.role for spec in ALL_ROLES if spec.database == database
    )
    for signature in SENSITIVE_PUBLIC_FUNCTIONS:
        exists = await conn.fetchval(
            "SELECT to_regprocedure($1) IS NOT NULL", f"soul_v3.{signature}"
        )
        if not exists:
            continue
        if await _public_function_execute(conn, signature):
            raise RuntimeError(f"PUBLIC still executes sensitive function: {signature}")
        for role_name in required_roles:
            if not await conn.fetchval(
                "SELECT has_function_privilege($1, $2, 'EXECUTE')",
                role_name, f"soul_v3.{signature}",
            ):
                raise RuntimeError(
                    f"required function canary lost EXECUTE: {role_name} on {signature}"
                )
        for role_name in operational_roles:
            role_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)", role_name
            )
            if role_exists and await conn.fetchval(
                "SELECT has_function_privilege($1, $2, 'EXECUTE')",
                role_name, f"soul_v3.{signature}",
            ):
                raise RuntimeError(
                    f"operational role executes sensitive function: {role_name} on {signature}"
                )


async def _harden_sensitive_functions(conn, database: str) -> None:
    required_roles = REQUIRED_FUNCTION_ROLES.get(database, ())
    for signature in SENSITIVE_PUBLIC_FUNCTIONS:
        exists = await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", f"soul_v3.{signature}")
        if not exists:
            continue
        retained_canaries: list[str] = []
        for role_name in required_roles:
            role_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)", role_name
            )
            if not role_exists:
                raise RuntimeError(f"required function role is missing: {role_name}")
            effective_before = await conn.fetchval(
                "SELECT has_function_privilege($1, $2, 'EXECUTE')",
                role_name, f"soul_v3.{signature}",
            )
            if not effective_before:
                raise RuntimeError(
                    f"required function role lacked pre-cutover access: {role_name} on {signature}"
                )
            quoted_role = await conn.fetchval("SELECT quote_ident($1)", role_name)
            await conn.execute(
                f"GRANT EXECUTE ON FUNCTION soul_v3.{signature} TO {quoted_role}"
            )
        for role_name in FUNCTION_CANARY_ROLES:
            role_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)", role_name
            )
            explicitly_granted = role_exists and await _explicit_function_execute(
                conn, role_name, signature
            )
            if explicitly_granted:
                retained_canaries.append(role_name)
        await conn.execute(
            f"REVOKE EXECUTE ON FUNCTION soul_v3.{signature} FROM PUBLIC"
        )
        for role_name in retained_canaries:
            if not await conn.fetchval(
                "SELECT has_function_privilege($1, $2, 'EXECUTE')",
                role_name, f"soul_v3.{signature}",
            ):
                raise RuntimeError(
                    f"function canary lost EXECUTE: {role_name} on {signature}"
                )
            quoted_role = await conn.fetchval("SELECT quote_ident($1)", role_name)
            await conn.execute(f"SET LOCAL ROLE {quoted_role}")
            try:
                await conn.fetchval(f"SELECT count(*) FROM soul_v3.{signature}")
            finally:
                await conn.execute("RESET ROLE")
    await _verify_sensitive_function_contract(conn, database)


async def _preflight_policy_recreates(
    conn, specs: tuple[ServiceRole, ...]
) -> set[str]:
    """Detect immutable policy-shape drift before the first mutation."""
    recreate: set[str] = set()
    for spec in specs:
        for policy in spec.policies:
            name = _policy_name(spec, policy)
            existing = await conn.fetchrow(
                """SELECT permissive, cmd FROM pg_policies
                   WHERE schemaname='soul_v3' AND tablename=$1 AND policyname=$2""",
                policy.table, name,
            )
            if not existing:
                continue
            expected_kind = "RESTRICTIVE" if policy.restrictive else "PERMISSIVE"
            if (
                existing["permissive"] != expected_kind
                or existing["cmd"] != policy.command
            ):
                recreate.add(name)
    return recreate


def _policy_clause(policy: Policy) -> str:
    if policy.command == "INSERT":
        return f"WITH CHECK ({policy.expression})"
    if policy.command == "UPDATE":
        return f"USING ({policy.expression}) WITH CHECK ({policy.expression})"
    return f"USING ({policy.expression})"


async def _ensure_policy(
    conn, spec: ServiceRole, policy: Policy, recreate: set[str] | None = None
) -> None:
    exists = await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{policy.table}"
    )
    if not exists:
        raise RuntimeError(
            f"required RLS relation soul_v3.{policy.table} is missing for {spec.service}"
        )
    rls_enabled = await conn.fetchval(
        """SELECT c.relrowsecurity
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='soul_v3' AND c.relname=$1""",
        policy.table,
    )
    if not rls_enabled:
        raise RuntimeError(
            f"RLS is disabled on soul_v3.{policy.table} for {spec.service}"
        )
    role = await conn.fetchval("SELECT quote_ident($1)", spec.role)
    table = await conn.fetchval("SELECT quote_ident($1)", policy.table)
    policy_name_raw = _policy_name(spec, policy)
    policy_name = await conn.fetchval("SELECT quote_ident($1)", policy_name_raw)
    existing = await conn.fetchrow(
        """SELECT permissive, roles, cmd FROM pg_policies
           WHERE schemaname='soul_v3' AND tablename=$1 AND policyname=$2""",
        policy.table, policy_name_raw,
    )
    expected_kind = "RESTRICTIVE" if policy.restrictive else "PERMISSIVE"
    immutable_mismatch = bool(existing) and (
        existing["permissive"] != expected_kind
        or existing["cmd"] != policy.command
    )
    if immutable_mismatch and policy_name_raw not in (recreate or set()):
        raise RuntimeError(
            f"policy immutable shape mismatch was not preflighted: {policy_name_raw}"
        )
    if immutable_mismatch:
        await conn.execute(f"DROP POLICY {policy_name} ON soul_v3.{table}")
        existing = None
    clause = _policy_clause(policy)
    if existing:
        await conn.execute(
            f"ALTER POLICY {policy_name} ON soul_v3.{table} TO {role} {clause}"
        )
    else:
        kind = "AS RESTRICTIVE" if policy.restrictive else "AS PERMISSIVE"
        await conn.execute(
            f"CREATE POLICY {policy_name} ON soul_v3.{table} {kind} "
            f"FOR {policy.command} TO {role} {clause}"
        )


async def _provision_one(
    conn, admin_dsn: str, spec: ServiceRole, recreate_policies: set[str]
) -> tuple[ServiceRole, str]:
    await _prepare_service_relations(conn, spec)
    role = await conn.fetchval("SELECT quote_ident($1)", spec.role)
    password = _existing_password(_secret_path(spec), spec.role) or secrets.token_urlsafe(48)
    password_literal = await conn.fetchval("SELECT quote_literal($1)", password)
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)", spec.role
    )
    if exists:
        memberships = await conn.fetch(
            """SELECT parent.rolname
               FROM pg_auth_members membership
               JOIN pg_roles member ON member.oid=membership.member
               JOIN pg_roles parent ON parent.oid=membership.roleid
               WHERE member.rolname=$1""",
            spec.role,
        )
        if memberships:
            raise RuntimeError(
                f"refusing role with memberships: {spec.role}"
            )
    verb = "ALTER" if exists else "CREATE"
    await conn.execute(
        f"{verb} ROLE {role} NOLOGIN PASSWORD {password_literal} NOSUPERUSER "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
    )
    await conn.execute(f"ALTER ROLE {role} SET search_path = soul_v3, public")
    database = await conn.fetchval("SELECT quote_ident($1)", spec.database)
    await conn.execute(f"GRANT CONNECT ON DATABASE {database} TO {role}")
    await conn.execute(f"REVOKE CREATE ON SCHEMA public FROM {role}")
    await conn.execute(f"GRANT USAGE ON SCHEMA soul_v3 TO {role}")
    await conn.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA soul_v3 FROM {role}")
    await conn.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA soul_v3 FROM {role}")
    await conn.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA soul_v3 FROM {role}")

    for table_name, privileges in spec.grants:
        present = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{table_name}"
        )
        if not present:
            if table_name not in spec.optional_relations:
                raise RuntimeError(
                    f"required relation soul_v3.{table_name} is missing for {spec.service}"
                )
            continue
        table = await conn.fetchval("SELECT quote_ident($1)", table_name)
        await conn.execute(f"GRANT {privileges} ON soul_v3.{table} TO {role}")
        if "INSERT" in privileges:
            sequences = await conn.fetch(
                """SELECT DISTINCT seq_ns.nspname, seq.relname
                   FROM pg_class tbl
                   JOIN pg_namespace tbl_ns ON tbl_ns.oid=tbl.relnamespace
                   JOIN pg_depend dep ON dep.refobjid=tbl.oid AND dep.deptype IN ('a','i')
                   JOIN pg_class seq ON seq.oid=dep.objid AND seq.relkind='S'
                   JOIN pg_namespace seq_ns ON seq_ns.oid=seq.relnamespace
                   WHERE tbl_ns.nspname='soul_v3' AND tbl.relname=$1""",
                table_name,
            )
            for sequence in sequences:
                seq = await conn.fetchval("SELECT quote_ident($1)", sequence["relname"])
                await conn.execute(
                    f"GRANT USAGE ON SEQUENCE soul_v3.{seq} TO {role}"
                )
    for grant in spec.column_grants:
        present = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{grant.table}"
        )
        if not present:
            if grant.table not in spec.optional_relations:
                raise RuntimeError(
                    f"required relation soul_v3.{grant.table} is missing for {spec.service}"
                )
            continue
        table = await conn.fetchval("SELECT quote_ident($1)", grant.table)
        columns = [await conn.fetchval("SELECT quote_ident($1)", c) for c in grant.columns]
        await conn.execute(
            f"GRANT {grant.privilege} ({', '.join(columns)}) "
            f"ON soul_v3.{table} TO {role}"
        )
    for sequence_name in await _expected_sequences(conn, spec):
        sequence = await conn.fetchval("SELECT quote_ident($1)", sequence_name)
        await conn.execute(
            f"GRANT USAGE ON SEQUENCE soul_v3.{sequence} TO {role}"
        )
    for signature in spec.function_grants:
        exists = await conn.fetchval(
            "SELECT to_regprocedure($1) IS NOT NULL", f"soul_v3.{signature}"
        )
        if not exists:
            raise RuntimeError(
                f"required function soul_v3.{signature} is missing for {spec.service}"
            )
        await conn.execute(
            f"GRANT EXECUTE ON FUNCTION soul_v3.{signature} TO {role}"
        )
    for policy in spec.policies:
        await _ensure_policy(conn, spec, policy, recreate_policies)

    return spec, _service_dsn(admin_dsn, spec, password)


def _policy_name(spec: ServiceRole, policy: Policy) -> str:
    raw = f"ops_{spec.role}_{policy.table}_{policy.command.lower()}_{policy.suffix}"
    if len(raw) <= 63:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{raw[:52]}_{digest}"


def _table_privilege_manifest(spec: ServiceRole) -> set[tuple[str, str]]:
    return {
        (table, privilege.strip().upper())
        for table, privileges in spec.grants
        for privilege in privileges.split(",")
    }


def _column_privilege_manifest(spec: ServiceRole) -> set[tuple[str, str, str]]:
    return {
        (grant.table, column, grant.privilege.upper())
        for grant in spec.column_grants
        for column in grant.columns
    }


def _normalize_expression(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", value.lower())
    normalized = normalized.replace("::text", "").replace("::charactervarying", "")
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        wraps = True
        for index, char in enumerate(normalized):
            depth += char == "("
            depth -= char == ")"
            if depth == 0 and index != len(normalized) - 1:
                wraps = False
                break
        if not wraps:
            break
        normalized = normalized[1:-1]
    return normalized


def _expected_policy_expressions(policy: Policy) -> tuple[str | None, str | None]:
    if policy.command == "INSERT":
        return None, policy.expression
    if policy.command == "UPDATE":
        return policy.expression, policy.expression
    return policy.expression, None


async def _expected_sequences(conn, spec: ServiceRole) -> set[str]:
    insert_tables = {
        table for table, privilege in _table_privilege_manifest(spec)
        if privilege == "INSERT"
    }
    insert_tables.update(
        grant.table for grant in spec.column_grants
        if grant.privilege.upper() == "INSERT"
    )
    if not insert_tables:
        return set()
    rows = await conn.fetch(
        """SELECT DISTINCT seq.relname
           FROM pg_class tbl
           JOIN pg_namespace tbl_ns ON tbl_ns.oid=tbl.relnamespace
           JOIN pg_depend dep ON dep.refobjid=tbl.oid AND dep.deptype IN ('a','i')
           JOIN pg_class seq ON seq.oid=dep.objid AND seq.relkind='S'
           WHERE tbl_ns.nspname='soul_v3' AND tbl.relname=ANY($1::text[])""",
        sorted(insert_tables),
    )
    return {row["relname"] for row in rows}


async def _verify_one(spec: ServiceRole, dsn: str, *, connector=None) -> None:
    import asyncpg

    connector = connector or asyncpg.connect
    conn = await connector(dsn, timeout=10)
    try:
        identity = await conn.fetchrow(
            "SELECT current_user, session_user, current_database(), "
            "current_setting('application_name') AS application_name"
        )
        attrs = await conn.fetchrow(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
            "rolbypassrls, rolinherit FROM pg_roles WHERE rolname=current_user"
        )
        expected_app = spec.service.replace("-", "_")
        if (
            identity["current_user"] != spec.role
            or identity["session_user"] != spec.role
            or identity["current_database"] != spec.database
            or identity["application_name"] != expected_app
            or attrs["rolsuper"]
            or attrs["rolcreatedb"]
            or attrs["rolcreaterole"]
            or attrs["rolreplication"]
            or attrs["rolbypassrls"]
            or attrs["rolinherit"]
        ):
            raise RuntimeError(f"least-privilege identity verification failed for {spec.service}")

        memberships = await conn.fetch(
            """SELECT parent.rolname
               FROM pg_auth_members membership
               JOIN pg_roles member ON member.oid=membership.member
               JOIN pg_roles parent ON parent.oid=membership.roleid
               WHERE member.rolname=current_user"""
        )
        if memberships or await conn.fetchval(
            "SELECT pg_has_role(current_user, 'seal', 'MEMBER')"
        ):
            raise RuntimeError(f"role membership boundary failed for {spec.service}")

        other_database = (
            "soul_standalone" if spec.database == "seal_memory" else "seal_memory"
        )
        parsed = urlsplit(dsn)
        cross_dsn = urlunsplit((
            parsed.scheme, parsed.netloc, f"/{other_database}", parsed.query, "",
        ))
        cross_conn = None
        try:
            cross_conn = await connector(cross_dsn, timeout=5)
        except (
            asyncpg.InvalidAuthorizationSpecificationError,
            asyncpg.InvalidCatalogNameError,
            asyncpg.InsufficientPrivilegeError,
        ):
            pass
        else:
            raise RuntimeError(
                f"cross-database CONNECT unexpectedly succeeded for {spec.service}"
            )
        finally:
            if cross_conn is not None:
                await cross_conn.close()

        relation_names = {table for table, _ in spec.grants}
        relation_names.update(grant.table for grant in spec.column_grants)
        for table_name in relation_names:
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{table_name}"
            )
            if not exists:
                if table_name not in spec.optional_relations:
                    raise RuntimeError(
                        f"required relation soul_v3.{table_name} disappeared for {spec.service}"
                    )
                continue

        expected_tables = _table_privilege_manifest(spec)
        table_acl = await conn.fetch(
            """SELECT c.relname, privilege,
                      has_table_privilege(current_user, c.oid, privilege) AS allowed
               FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
                                       'TRUNCATE','REFERENCES','TRIGGER'])
                          AS privileges(privilege)
               WHERE n.nspname='soul_v3' AND c.relkind IN ('r','p','v','m','f')"""
        )
        for row in table_acl:
            expected = (row["relname"], row["privilege"]) in expected_tables
            if row["allowed"] != expected:
                raise RuntimeError(
                    f"table ACL mismatch for {spec.service}: "
                    f"{row['relname']} {row['privilege']} actual={row['allowed']} expected={expected}"
                )

        expected_columns = _column_privilege_manifest(spec)
        column_acl = await conn.fetch(
            """SELECT c.relname, a.attname, privilege,
                      has_column_privilege(current_user, c.oid, a.attnum, privilege) AS allowed
               FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
               CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES'])
                          AS privileges(privilege)
               WHERE n.nspname='soul_v3' AND c.relkind IN ('r','p','v','m','f')"""
        )
        for row in column_acl:
            expected = (
                (row["relname"], row["privilege"]) in expected_tables
                or (row["relname"], row["attname"], row["privilege"]) in expected_columns
            )
            if row["allowed"] != expected:
                raise RuntimeError(
                    f"column ACL mismatch for {spec.service}: "
                    f"{row['relname']}.{row['attname']} {row['privilege']} "
                    f"actual={row['allowed']} expected={expected}"
                )

        expected_sequences = await _expected_sequences(conn, spec)
        sequence_acl = await conn.fetch(
            """SELECT c.relname, privilege,
                      has_sequence_privilege(current_user, c.oid, privilege) AS allowed
               FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               CROSS JOIN unnest(ARRAY['USAGE','SELECT','UPDATE'])
                          AS privileges(privilege)
               WHERE n.nspname='soul_v3' AND c.relkind='S'"""
        )
        for row in sequence_acl:
            expected = row["relname"] in expected_sequences and row["privilege"] == "USAGE"
            if row["allowed"] != expected:
                raise RuntimeError(
                    f"sequence ACL mismatch for {spec.service}: "
                    f"{row['relname']} {row['privilege']} actual={row['allowed']} expected={expected}"
                )

        for signature in SENSITIVE_PUBLIC_FUNCTIONS:
            exists = await conn.fetchval(
                "SELECT to_regprocedure($1) IS NOT NULL", f"soul_v3.{signature}"
            )
            if exists and await conn.fetchval(
                "SELECT has_function_privilege(current_user, $1, 'EXECUTE')",
                f"soul_v3.{signature}",
            ):
                raise RuntimeError(
                    f"sensitive SECURITY DEFINER function remains executable: {signature}"
                )
        expected_functions = set(spec.function_grants)
        executable_definers = await conn.fetch(
            """SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' signature
               FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE n.nspname='soul_v3' AND p.prosecdef
                 AND has_function_privilege(current_user, p.oid, 'EXECUTE')"""
        )
        actual_functions = {row["signature"] for row in executable_definers}
        if actual_functions != expected_functions:
            raise RuntimeError(
                f"SECURITY DEFINER ACL mismatch for {spec.service}: "
                f"actual={sorted(actual_functions)} expected={sorted(expected_functions)}"
            )

        for policy in spec.policies:
            policy_name = _policy_name(spec, policy)
            row = await conn.fetchrow(
                """SELECT permissive, roles, cmd, qual, with_check
                   FROM pg_policies
                   WHERE schemaname='soul_v3' AND tablename=$1 AND policyname=$2""",
                policy.table, policy_name,
            )
            expected_kind = "RESTRICTIVE" if policy.restrictive else "PERMISSIVE"
            expected_qual, expected_check = _expected_policy_expressions(policy)
            if (
                row is None
                or row["permissive"] != expected_kind
                or row["roles"] != [spec.role]
                or row["cmd"] != policy.command
                or _normalize_expression(row["qual"]) != _normalize_expression(expected_qual)
                or _normalize_expression(row["with_check"]) != _normalize_expression(expected_check)
            ):
                raise RuntimeError(
                    f"RLS policy manifest mismatch for {spec.service}: {policy_name}"
                )
            if not await conn.fetchval(
                """SELECT c.relrowsecurity
                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='soul_v3' AND c.relname=$1""",
                policy.table,
            ):
                raise RuntimeError(
                    f"RLS disabled for policy table {policy.table}"
                )

        for contract in spec.view_contracts:
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"soul_v3.{contract.name}"
            )
            if not exists:
                if contract.name in spec.optional_relations:
                    continue
                raise RuntimeError(f"required boundary view disappeared: {contract.name}")
            columns = await conn.fetch(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='soul_v3' AND table_name=$1
                   ORDER BY ordinal_position""",
                contract.name,
            )
            if tuple(row["column_name"] for row in columns) != contract.columns:
                raise RuntimeError(f"boundary view column mismatch: {contract.name}")
            view_row = await conn.fetchrow(
                """SELECT pg_get_viewdef(c.oid, true) AS definition,
                          COALESCE(c.reloptions, ARRAY[]::text[]) AS options,
                          v.check_option
                   FROM pg_class c
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                   JOIN information_schema.views v
                     ON v.table_schema=n.nspname AND v.table_name=c.relname
                   WHERE n.nspname='soul_v3' AND c.relname=$1""",
                contract.name,
            )
            if not view_row:
                raise RuntimeError(f"boundary relation is not a view: {contract.name}")
            definition = _normalize_expression(view_row["definition"]) or ""
            for token in contract.definition_tokens:
                if (_normalize_expression(token) or "") not in definition:
                    raise RuntimeError(f"boundary view definition mismatch: {contract.name}")
            has_barrier = "security_barrier=true" in set(view_row["options"])
            if has_barrier != contract.security_barrier:
                raise RuntimeError(f"boundary view option mismatch: {contract.name}")
            if view_row["check_option"] != contract.check_option:
                raise RuntimeError(f"boundary view check option mismatch: {contract.name}")

        policy_commands = {(policy.table, policy.command) for policy in spec.policies}
        for table, command in policy_commands:
            public_permissive = await conn.fetchval(
                """SELECT EXISTS (
                     SELECT 1 FROM pg_policies
                     WHERE schemaname='soul_v3' AND tablename=$1
                       AND permissive='PERMISSIVE' AND 'public'=ANY(roles)
                       AND cmd IN ('ALL',$2)
                   )""",
                table, command,
            )
            has_restrictive = any(
                policy.table == table and policy.command == command and policy.restrictive
                for policy in spec.policies
            )
            if public_permissive and not has_restrictive:
                raise RuntimeError(
                    f"PUBLIC permissive policy lacks role-scoped restrictive guard: "
                    f"{table} {command}"
                )

        for check in spec.boundary_checks:
            for setting, value in check.settings:
                await conn.execute("SELECT set_config($1, $2, false)", setting, value)
            visible_outside_scope = await conn.fetchval(check.query)
            if visible_outside_scope != 0:
                raise RuntimeError(
                    f"effective RLS boundary failed for {spec.service}: "
                    f"outside_scope={visible_outside_scope}"
                )
        await _run_synthetic_canaries(conn, spec)
    finally:
        await conn.close()


async def _expect_denied(conn, statement: str, *args) -> None:
    nested = conn.transaction()
    await nested.start()
    try:
        try:
            await conn.execute(statement, *args)
        except Exception:
            return
        raise RuntimeError(f"negative canary unexpectedly succeeded: {statement}")
    finally:
        await nested.rollback()


async def _run_synthetic_canaries(conn, spec: ServiceRole) -> None:
    """Exercise mutable boundaries inside an always-rolled-back transaction."""
    tx = conn.transaction()
    await tx.start()
    try:
        marker = f"ops_canary_{secrets.token_hex(8)}"
        if spec.service == "seal-durable-cron":
            await conn.execute(
                """INSERT INTO soul_v3.session_loops
                     (agent, loop_name, loop_prompt, interval_seconds, active)
                   VALUES ('ADA', $1, 'operational role canary', 60, true)""",
                marker,
            )
            await conn.execute(
                "UPDATE soul_v3.session_loops SET active=false WHERE loop_name=$1",
                marker,
            )
        elif spec.service == "seal-lifecycle":
            await conn.execute(
                """INSERT INTO soul_v3.lifecycle_events
                     (agent_name, event_type, desired_state, actual_state, detail)
                   VALUES ('ADA', 'operational_canary', 'running', 'running', $1)""",
                marker,
            )
        elif spec.service == "seal-memory-monitor":
            await conn.fetchval(
                "SELECT count(*) FROM soul_v3.memory_monitor_memories_boundary"
            )
            await conn.fetchval(
                "SELECT drift_schema_available "
                "FROM soul_v3.memory_monitor_capabilities_boundary"
            )
        elif spec.service == "seal-harness-watcher":
            await conn.execute(
                """INSERT INTO soul_v3.harness_oracle
                     (agent, state_key, belief_hash, true_hash, belief_snippet,
                      diverged, divergence_kind, escalated_to, escalation_delivered)
                   VALUES ('seal_harness_watcher', $1, $2, $2, 'canary', false,
                           'operational_canary', NULL, false)""",
                marker, hashlib.sha256(marker.encode()).hexdigest()[:16],
            )
        elif spec.service == "seal-laptop-activity":
            await conn.execute(
                """INSERT INTO soul_v3.laptop_activity
                     (agent, source, kind, title, content, project, metadata)
                   VALUES ('ADA', 'operational_canary', 'canary', $1, 'rollback',
                           'SEAL', '{}'::jsonb)""",
                marker,
            )
        elif spec.service == "seal-tools-catalog":
            row = await conn.fetchrow(
                "SELECT id, status FROM soul_v3.agent_tools_registry ORDER BY id LIMIT 1"
            )
            if row:
                await conn.execute(
                    "UPDATE soul_v3.agent_tools_registry SET status=$1 WHERE id=$2",
                    row["status"], row["id"],
                )
                await _expect_denied(
                    conn,
                    "UPDATE soul_v3.agent_tools_registry SET owner_agent=owner_agent WHERE id=$1",
                    row["id"],
                )
        elif spec.service == "spectre-openai-shim":
            await conn.execute("SELECT soul_v3.ensure_spectre_prereqs()")
            await conn.execute(
                """INSERT INTO soul_v3.spectre_memories_boundary
                     (agent, scope, category, content, content_hash_sha256,
                      importance, tenant_id)
                   VALUES ('SPECTRE', 'private', 'canary', $1, $2, 1,
                           '00000000-0000-0000-0000-000000000000'::uuid)""",
                marker, hashlib.sha256(marker.encode()).hexdigest(),
            )
            await _expect_denied(
                conn,
                """INSERT INTO soul_v3.spectre_memories_boundary
                     (agent, scope, category, content, content_hash_sha256,
                      importance, tenant_id)
                   VALUES ('ADA', 'private', 'canary', $1, $2, 1,
                           '00000000-0000-0000-0000-000000000000'::uuid)""",
                marker, hashlib.sha256(marker.encode()).hexdigest(),
            )
            await _expect_denied(conn, "SELECT count(*) FROM soul_v3.memories")
    finally:
        await tx.rollback()


async def _harden_database_connect(
    conn, database: str, operational_roles: tuple[str, ...]
) -> None:
    """Replace PUBLIC CONNECT with the current effective allowlist.

    Operational roles are deliberately excluded from the preserved set and
    receive CONNECT only on their declared database later in the transaction.
    """
    database_ident = await conn.fetchval("SELECT quote_ident($1)", database)
    preserve = await conn.fetch(
        """SELECT rolname FROM pg_roles
           WHERE rolname <> ALL($1::text[])
             AND has_database_privilege(rolname, $2, 'CONNECT')
           ORDER BY rolname""",
        list(operational_roles), database,
    )
    for row in preserve:
        role = await conn.fetchval("SELECT quote_ident($1)", row["rolname"])
        await conn.execute(f"GRANT CONNECT ON DATABASE {database_ident} TO {role}")
    await conn.execute(f"REVOKE CONNECT ON DATABASE {database_ident} FROM PUBLIC")
    for role_name in operational_roles:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role_name
        )
        if exists:
            role = await conn.fetchval("SELECT quote_ident($1)", role_name)
            await conn.execute(f"REVOKE CONNECT ON DATABASE {database_ident} FROM {role}")


async def _prepare_group(
    admin_env: str, specs: tuple[ServiceRole, ...]
) -> tuple[str, list[tuple[ServiceRole, str]], list[str]]:
    import asyncpg

    admin_dsn = service_pg_dsn(admin_env)
    conn = await asyncpg.connect(admin_dsn, timeout=10)
    try:
        is_superuser = await conn.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        )
        if not is_superuser:
            raise RuntimeError(f"{admin_env} must resolve to a PostgreSQL superuser")
        provisioned: list[tuple[ServiceRole, str]] = []
        async with conn.transaction():
            recreate = await _preflight_policy_recreates(conn, specs)
            await _harden_database_connect(
                conn, specs[0].database, tuple(item.role for item in ALL_ROLES)
            )
            await _harden_sensitive_functions(conn, specs[0].database)
            for spec in specs:
                provisioned.append(
                    await _provision_one(conn, admin_dsn, spec, recreate)
                )
    finally:
        await conn.close()
    ready: list[tuple[ServiceRole, str]] = []
    failures: list[str] = []
    for spec, dsn in provisioned:
        try:
            _write_private(_secret_path(spec), dsn)
        except Exception:
            failures.append(spec.service)
        else:
            ready.append((spec, dsn))
    return admin_dsn, ready, failures


async def _set_role_login(admin_dsn: str, role_name: str, enabled: bool) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn, timeout=10)
    try:
        async with conn.transaction():
            role = await conn.fetchval("SELECT quote_ident($1)", role_name)
            await conn.execute(f"ALTER ROLE {role} {'LOGIN' if enabled else 'NOLOGIN'}")
    finally:
        await conn.close()


async def _verify_admin_invariants(admin_dsn: str, database: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn, timeout=10)
    try:
        await _verify_sensitive_function_contract(conn, database)
    finally:
        await conn.close()


async def _activate_group(
    admin_dsn: str, prepared: list[tuple[ServiceRole, str]]
) -> tuple[list[str], list[str]]:
    active: list[str] = []
    failed: list[str] = []
    for spec, dsn in prepared:
        try:
            await _set_role_login(admin_dsn, spec.role, True)
            await _verify_one(spec, dsn)
        except Exception:
            try:
                await _set_role_login(admin_dsn, spec.role, False)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"CRITICAL: could not restore NOLOGIN for {spec.service}"
                ) from rollback_error
            await _verify_admin_invariants(admin_dsn, spec.database)
            failed.append(spec.service)
        else:
            active.append(spec.service)
    return active, failed


def _print_plan() -> None:
    print(f"plan_only=true roles={len(ALL_ROLES)} secrets_emitted=false")
    for spec in ALL_ROLES:
        print(
            f"service={spec.service} database={spec.database} role={spec.role} "
            f"env={spec.env_name} secret_file={_secret_path(spec)}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ack", default="")
    args = parser.parse_args()
    if not args.apply:
        _print_plan()
        return 0
    if args.ack != APPLY_ACK:
        parser.error(f"--apply requires --ack {APPLY_ACK}")
    groups = (
        ("SEAL_MEMORY_ADMIN_PG_DSN", SEAL_MEMORY_ROLES),
        ("SOUL_STANDALONE_ADMIN_PG_DSN", STANDALONE_ROLES),
    )
    prepared_groups: list[
        tuple[str, str, list[tuple[ServiceRole, str]], list[str]]
    ] = []
    failed = False
    for admin_env, specs in groups:
        database = specs[0].database
        try:
            admin_dsn, ready, secret_failures = await _prepare_group(admin_env, specs)
        except Exception as exc:
            failed = True
            print(
                f"database={database} status=prepare_failed roles_login=0 "
                f"roles_nologin={len(specs)} error_type={type(exc).__name__}",
                file=sys.stderr,
            )
            continue
        if secret_failures:
            failed = True
        prepared_groups.append((database, admin_dsn, ready, secret_failures))

    for database, admin_dsn, ready, secret_failures in prepared_groups:
        active, verify_failures = await _activate_group(admin_dsn, ready)
        if verify_failures:
            failed = True
        nologin = sorted(secret_failures + verify_failures)
        print(
            f"database={database} status={'partial' if nologin else 'verified'} "
            f"roles_login={len(active)} roles_nologin={len(nologin)}"
        )
    if failed:
        return 1
    print(
        f"applied=true roles={len(ALL_ROLES)} secret_dir={SECRET_DIR} "
        "secret_mode=0600 secrets_emitted=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
