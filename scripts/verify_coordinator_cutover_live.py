#!/usr/bin/env python3
"""Fail-closed live verifier for the SEAL coordinator cutover."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.request import urlopen

import asyncpg


REPO = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_function_body_sha(path: Path, function_name: str) -> str:
    """Hash the exact PL/pgSQL body shipped by a versioned migration."""
    sql = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"CREATE OR REPLACE FUNCTION\s+soul_v3\.{re.escape(function_name)}\b"
        rf".*?\nAS \$(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\$\n"
        rf"(?P<body>.*?)\n\$(?P=tag)\$;",
        re.DOTALL,
    )
    match = pattern.search(sql)
    if not match:
        raise RuntimeError(f"cannot extract migration body for {function_name}")
    # PostgreSQL stores the newlines immediately inside the dollar quotes in
    # pg_proc.prosrc.  Include them so the comparison is byte-for-byte.
    stored_body = "\n" + match.group("body") + "\n"
    return hashlib.sha256(stored_body.encode("utf-8")).hexdigest()


async def _verify_function_bodies(conn: asyncpg.Connection) -> None:
    user_migration = REPO / "messages/migrations/20260718_user_delete_with_sessions.sql"
    coordination_migration = (
        REPO / "messages/migrations/20260819_coordination_authority_hardening.sql"
    )
    expected = {
        "coordination_write": _expected_function_body_sha(
            coordination_migration, "coordination_write"
        ),
        "user_delete_with_sessions": _expected_function_body_sha(
            user_migration, "user_delete_with_sessions"
        ),
        "user_role_update_guarded": _expected_function_body_sha(
            user_migration, "user_role_update_guarded"
        ),
    }
    rows = await conn.fetch(
        """
        SELECT proname, prosrc
          FROM pg_proc
         WHERE oid IN (
           'soul_v3.coordination_write(text,jsonb)'::regprocedure,
           'soul_v3.user_delete_with_sessions(text,integer,text)'::regprocedure,
           'soul_v3.user_role_update_guarded(integer,text,integer,text,text)'::regprocedure
         )
        """
    )
    actual = {
        str(row["proname"]): hashlib.sha256(
            str(row["prosrc"]).encode("utf-8")
        ).hexdigest()
        for row in rows
    }
    if actual != expected:
        raise RuntimeError(
            "live function bodies do not match versioned migrations: "
            f"expected={expected}, actual={actual}"
        )


async def _probe_durable_owner_guards(
    conn: asyncpg.Connection | None = None,
) -> None:
    """Attempt both owner-destruction paths and always roll back the probe."""
    owns_connection = conn is None
    if conn is None:
        dsn = os.environ.get("SEAL_DB_DSN", "").strip()
        if not dsn:
            raise RuntimeError("SEAL_DB_DSN is required for durable-owner probes")
        conn = await asyncpg.connect(dsn)
    outer = conn.transaction()
    await outer.start()
    try:
        owner = await conn.fetchrow(
            "SELECT username, role FROM soul_v3.chat_users WHERE id=1"
        )
        if not owner or owner["role"] != "superuser":
            raise RuntimeError("durable owner id=1 is missing or not superuser")
        token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        await conn.execute(
            """
            INSERT INTO soul_v3.chat_sessions
              (user_id,token_hash,expires_at,ip_address,user_agent)
            VALUES (1,$1,now()+interval '5 minutes',NULL,'seal-live-verifier')
            """,
            token_hash,
        )
        await conn.execute("SET LOCAL ROLE login_bus_admin")
        probes = (
            (
                "demote",
                "SELECT soul_v3.user_role_update_guarded($1,$2,$3,$4,$5)",
                (1, "basic", 1, token_hash, "live-verifier-negative-control"),
            ),
            (
                "delete",
                "SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
                (str(owner["username"]), 1, token_hash),
            ),
        )
        for label, statement, args in probes:
            savepoint = conn.transaction()
            await savepoint.start()
            try:
                await conn.fetchval(statement, *args)
            except asyncpg.InsufficientPrivilegeError:
                await savepoint.rollback()
            except Exception:
                await savepoint.rollback()
                raise
            else:
                await savepoint.rollback()
                raise RuntimeError(f"durable owner {label} probe was accepted")
    finally:
        await outer.rollback()
        if owns_connection:
            await conn.close()


async def _db_checks() -> dict[str, object]:
    dsn = os.environ.get("SEAL_PG_ADMIN_DSN", "").strip()
    if not dsn:
        raise RuntimeError("SEAL_PG_ADMIN_DSN is required")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT
              NOT EXISTS (
                SELECT 1 FROM unnest(ARRAY[
                  'soul_v3.coordination_turns','soul_v3.coordination_assignments',
                  'soul_v3.coordination_voice_grants'
                ]) t(name), unnest(ARRAY['INSERT','UPDATE','DELETE']) p(priv)
                WHERE has_table_privilege('pr_bus',t.name,p.priv)
                   OR has_table_privilege('pr_bus_admin',t.name,p.priv)
              ) AS no_direct_dml,
              NOT EXISTS (
                SELECT 1
                  FROM unnest(ARRAY[
                    'login_bus','pr_bus','login_bus_admin','pr_bus_admin'
                  ]) AS r(role_name)
                 WHERE has_column_privilege(
                   r.role_name,'soul_v3.chat_users','role','UPDATE'
                 )
              ) AS no_direct_role_update,
              NOT EXISTS (
                SELECT 1
                  FROM unnest(ARRAY[
                    'login_bus','pr_bus','login_bus_admin','pr_bus_admin'
                  ]) AS r(role_name),
                  unnest(ARRAY[
                    'soul_v3.chat_users','soul_v3.chat_sessions'
                  ]) AS t(table_name)
                 WHERE has_table_privilege(r.role_name,t.table_name,'DELETE')
              ) AS no_direct_user_or_session_delete,
              has_function_privilege(
                'pr_bus_admin','soul_v3.coordination_write(text,jsonb)','EXECUTE'
              ) AS admin_execute,
              NOT has_function_privilege(
                'pr_bus','soul_v3.coordination_write(text,jsonb)','EXECUTE'
              ) AND NOT EXISTS (
                SELECT 1
                FROM pg_proc f, aclexplode(coalesce(f.proacl, acldefault('f',f.proowner))) a
                WHERE f.oid='soul_v3.coordination_write(text,jsonb)'::regprocedure
                  AND a.grantee=0 AND a.privilege_type='EXECUTE'
              ) AS execute_isolated,
              (
                SELECT bool_and(c.relrowsecurity AND c.relforcerowsecurity)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='soul_v3' AND c.relname IN (
                  'coordination_turns','coordination_assignments','coordination_voice_grants'
                )
              ) AS rls_forced,
              (
                SELECT NOT (rolcanlogin OR rolsuper OR rolcreaterole OR rolcreatedb
                            OR rolreplication OR rolbypassrls)
                FROM pg_roles WHERE rolname='coordination_owner'
              ) AS owner_safe,
              NOT EXISTS (
                SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
                WHERE r.rolname='coordination_owner'
              ) AS owner_has_no_members,
              (
                SELECT r.rolname='coordination_owner'
                  AND p.prosecdef
                  AND p.proconfig=ARRAY['search_path=pg_catalog, soul_v3']::text[]
                  FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                 WHERE p.oid='soul_v3.coordination_write(text,jsonb)'::regprocedure
              ) AS coordination_function_boundary_exact,
              has_function_privilege(
                'login_bus_admin','soul_v3.user_delete_with_sessions(text,integer,text)','EXECUTE'
              ) AND has_function_privilege(
                'login_bus_admin',
                'soul_v3.user_role_update_guarded(integer,text,integer,text,text)','EXECUTE'
              ) AS user_functions_bounded,
              to_regprocedure('soul_v3.user_delete_with_sessions(text)') IS NULL
                AND to_regprocedure('soul_v3.user_role_update_guarded(integer,text,text,text)') IS NULL
                AS legacy_user_entrypoints_removed,
              (
                SELECT NOT (rolcanlogin OR rolsuper OR rolcreaterole OR rolcreatedb
                            OR rolreplication OR rolbypassrls)
                FROM pg_roles WHERE rolname='user_mgr_owner'
              ) AS user_owner_safe,
              NOT EXISTS (
                SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
                WHERE r.rolname='user_mgr_owner'
              ) AS user_owner_has_no_members,
              (
                SELECT bool_and(
                  r.rolname='user_mgr_owner'
                  AND p.prosecdef
                  AND p.proconfig=ARRAY['search_path=pg_catalog, soul_v3']::text[]
                )
                  FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                 WHERE p.oid IN (
                   'soul_v3.user_delete_with_sessions(text,integer,text)'::regprocedure,
                   'soul_v3.user_role_update_guarded(integer,text,integer,text,text)'::regprocedure
                 )
              ) AS user_function_boundaries_exact,
              true AS durable_owner_protection_probed_below
            """
        )
        checks = dict(row)
        if not checks or not all(value is True for value in checks.values()):
            raise RuntimeError(f"coordination privilege boundary invalid: {checks}")
        await _verify_function_bodies(conn)
        checks["function_bodies_match_migrations"] = True
        invalid_owner_rejected = False
        try:
            await conn.fetchval(
                "SELECT soul_v3.user_delete_with_sessions($1,$2,$3)",
                "__seal_nonexistent_probe__", -1, "invalid-session-proof",
            )
        except asyncpg.InsufficientPrivilegeError:
            invalid_owner_rejected = True
        if not invalid_owner_rejected:
            raise RuntimeError("bounded user function accepted invalid owner session")
        checks["invalid_owner_session_rejected"] = True
        await _probe_durable_owner_guards()
        checks["durable_owner_effect_probes_rejected"] = True
        return checks
    finally:
        await conn.close()


async def main() -> None:
    with urlopen("http://127.0.0.1:8765/__version", timeout=5) as response:
        version = json.load(response)
    expected_server = _sha(REPO / "messages/chat_server.py")
    expected_coordination = _sha(REPO / "messages/soul_coordination.py")
    if version.get("code_hash") != expected_server:
        raise RuntimeError("live chat_server.py does not match disk")
    if version.get("module_hashes", {}).get("soul_coordination") != expected_coordination:
        raise RuntimeError("live soul_coordination.py does not match disk")
    db = await _db_checks()
    print(json.dumps({
        "ok": True,
        "chat_server": expected_server,
        "soul_coordination": expected_coordination,
        "db": db,
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
