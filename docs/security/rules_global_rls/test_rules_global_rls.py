from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from boot_rule_selection import (
    BOOT_CRITICAL_RULE_LIMIT,
    BOOT_CRITICAL_RULE_TRUNCATED_CHAR_BUDGET,
    BOOT_CRITICAL_RULES_SQL,
)
from identity_continuity_v2 import CANONICAL_RULE_ALIASES


ROOT = Path(__file__).resolve().parents[3]
UP = Path(__file__).with_name("001_rules_global_team_visibility_up.sql")
DOWN = Path(__file__).with_name("001_rules_global_team_visibility_down.sql")
AGENTS = ("ADA", "ALICE", "DUM", "JARVIS", "NEXUS")
ADMIN_DSN = os.environ.get("RULES_RLS_ADMIN_DSN", "")


pytestmark = pytest.mark.skipif(
    not ADMIN_DSN, reason="RULES_RLS_ADMIN_DSN is required"
)


def _login(agent: str) -> str:
    return f"mcp_runtime_{agent.lower()}"


def _dsn(agent: str) -> str:
    parsed = urlsplit(ADMIN_DSN)
    database = parsed.path.lstrip("/")
    return (
        f"postgresql://{_login(agent)}:test-{agent.lower()}@"
        f"{parsed.hostname}:{parsed.port or 5432}/{database}"
    )


@pytest_asyncio.fixture
async def provisioned():
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute("CREATE SCHEMA soul_v3")
        for agent in AGENTS:
            await admin.execute(
                f'CREATE ROLE "{_login(agent)}" LOGIN PASSWORD '
                f"'test-{agent.lower()}' NOSUPERUSER NOBYPASSRLS NOINHERIT"
            )
        await admin.execute(
            """
            CREATE FUNCTION soul_v3.mcp_session_agent()
            RETURNS text LANGUAGE sql STABLE
            SET search_path = pg_catalog
            AS $$
              SELECT CASE session_user
                WHEN 'mcp_runtime_ada' THEN 'ADA'
                WHEN 'mcp_runtime_alice' THEN 'ALICE'
                WHEN 'mcp_runtime_dum' THEN 'DUM'
                WHEN 'mcp_runtime_jarvis' THEN 'JARVIS'
                WHEN 'mcp_runtime_nexus' THEN 'NEXUS'
                ELSE NULL
              END
            $$
            """
        )
        roles = ", ".join(_login(agent) for agent in AGENTS)
        await admin.execute(
            f"""
            REVOKE ALL ON FUNCTION soul_v3.mcp_session_agent() FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION soul_v3.mcp_session_agent() TO {roles};
            """
        )
        await admin.execute(
            """
            CREATE TABLE soul_v3.rules (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                agent varchar NULL,
                rule_key varchar NOT NULL,
                content text NOT NULL,
                priority integer DEFAULT 10,
                active boolean DEFAULT true,
                created_at timestamptz DEFAULT now(),
                UNIQUE(agent, rule_key)
            );
            ALTER TABLE soul_v3.rules ENABLE ROW LEVEL SECURITY;
            """
        )
        await admin.execute(
            f"""
            GRANT USAGE ON SCHEMA soul_v3 TO {roles};
            GRANT SELECT ON soul_v3.rules TO {roles};
            CREATE POLICY mcp_legacy_compat_all ON soul_v3.rules
              AS PERMISSIVE FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
            CREATE POLICY mcp_login_capability ON soul_v3.rules
              AS PERMISSIVE FOR ALL TO {roles} USING (true) WITH CHECK (true);
            CREATE POLICY mcp_hard_identity ON soul_v3.rules
              AS RESTRICTIVE FOR ALL TO {roles}
              USING (agent::text = soul_v3.mcp_session_agent())
              WITH CHECK (agent::text = soul_v3.mcp_session_agent());
            """
        )
        base = datetime(2026, 7, 24, tzinfo=timezone.utc)
        rows = [
            (None, "ack_inmediato_william", base),
            (None, "no_phantom_claims", base),
            (None, "auto_test_mandatory", base),
            (None, "memory_privacy_inter_agent", base),
            (None, "delegate52_roundtrip_mandatory", base),
            (None, "global-legacy-newest", base + timedelta(seconds=9)),
            ("TEAM", "team-newest", base + timedelta(seconds=3)),
            ("TEAM", "team-second", base + timedelta(seconds=2)),
            ("TEAM", "team-third", base + timedelta(seconds=1)),
        ]
        for agent in AGENTS:
            rows.extend(
                [
                    (
                        agent,
                        "ack_inmediato_william",
                        base + timedelta(seconds=4),
                    ),
                    (
                        agent,
                        f"private-newest-{agent.lower()}",
                        base + timedelta(seconds=3),
                    ),
                    (
                        agent,
                        f"private-second-{agent.lower()}",
                        base + timedelta(seconds=2),
                    ),
                    (
                        agent,
                        f"private-third-{agent.lower()}",
                        base + timedelta(seconds=1),
                    ),
                    (
                        agent,
                        f"private-fourth-{agent.lower()}",
                        base,
                    ),
                ]
            )
        await admin.executemany(
            """
            INSERT INTO soul_v3.rules(agent, rule_key, content, created_at)
            VALUES($1::varchar, $2::varchar, $2::text, $3)
            """,
            rows,
        )
        yield admin
    finally:
        try:
            await admin.execute("ROLLBACK")
            await admin.execute("DROP SCHEMA IF EXISTS soul_v3 CASCADE")
            roles = ", ".join(_login(agent) for agent in AGENTS)
            await admin.execute(f"DROP ROLE IF EXISTS {roles}")
            await admin.execute("DROP ROLE IF EXISTS unexpected_parent")
        except (asyncpg.PostgresError, ConnectionError):
            pass
        await admin.close()


@pytest.mark.asyncio
async def test_baseline_proves_global_and_team_are_hidden(provisioned):
    for agent in AGENTS:
        conn = await asyncpg.connect(_dsn(agent))
        try:
            owners = await conn.fetch(
                "SELECT agent FROM soul_v3.rules ORDER BY rule_key"
            )
            assert len(owners) == 5
            assert {row["agent"] for row in owners} == {agent}
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_up_refuses_policy_drift(provisioned):
    await provisioned.execute(
        "ALTER POLICY mcp_hard_identity ON soul_v3.rules TO PUBLIC"
    )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="unexpected"):
        await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute("ROLLBACK")
    roles = ", ".join(_login(agent) for agent in AGENTS)
    await provisioned.execute(
        f"ALTER POLICY mcp_hard_identity ON soul_v3.rules TO {roles}"
    )


@pytest.mark.asyncio
async def test_up_refuses_membership_and_resolver_drift(provisioned):
    await provisioned.execute("CREATE ROLE unexpected_parent NOLOGIN")
    await provisioned.execute(
        "GRANT unexpected_parent TO mcp_runtime_ada"
    )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="memberships"):
        await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute("ROLLBACK")
    await provisioned.execute(
        "REVOKE unexpected_parent FROM mcp_runtime_ada"
    )
    await provisioned.execute(
        """
        CREATE OR REPLACE FUNCTION soul_v3.mcp_session_agent()
        RETURNS text LANGUAGE sql STABLE
        SET search_path = pg_catalog
        AS $$ SELECT current_setting('app.agent', true) $$
        """
    )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="definition drifted"):
        await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute("ROLLBACK")


@pytest.mark.asyncio
async def test_up_allows_global_team_and_own_but_not_siblings(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    policies = await provisioned.fetch(
        """
        SELECT polname, polcmd, polpermissive
          FROM pg_policy
         WHERE polrelid = 'soul_v3.rules'::regclass
           AND polname LIKE 'mcp_hard_identity%'
         ORDER BY polname
        """
    )
    assert [
        (r["polname"], bytes(r["polcmd"]).decode(), r["polpermissive"])
        for r in policies
    ] == [
        ("mcp_hard_identity", "r", False),
        ("mcp_hard_identity_delete", "d", False),
        ("mcp_hard_identity_insert", "a", False),
        ("mcp_hard_identity_update", "w", False),
    ]
    for agent in AGENTS:
        conn = await asyncpg.connect(_dsn(agent))
        try:
            owners = {
                row["agent"]
                for row in await conn.fetch(
                    "SELECT agent FROM soul_v3.rules"
                )
            }
            assert owners == {None, "TEAM", agent}
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_boot_query_balances_scopes_and_deduplicates(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    for agent in AGENTS:
        conn = await asyncpg.connect(_dsn(agent))
        try:
            rules = await conn.fetch(BOOT_CRITICAL_RULES_SQL, agent)
            assert len(rules) == BOOT_CRITICAL_RULE_LIMIT == 10
            canonical = rules[: len(CANONICAL_RULE_ALIASES)]
            own = rules[len(CANONICAL_RULE_ALIASES) : -2]
            team = rules[-2:]
            assert [row["canonical"] for row in canonical] == list(
                CANONICAL_RULE_ALIASES
            )
            assert canonical[0]["agent"] == agent
            assert all(row["agent"] is None for row in canonical[1:])
            assert all(row["agent"] == agent for row in own)
            assert all(row["agent"] == "TEAM" for row in team)
            keys = [row["rule_key"] for row in rules]
            assert len(keys) == len(set(keys))
            assert "global-legacy-newest" not in keys
            assert sum(min(len(row["content"]), 120) for row in rules) <= (
                BOOT_CRITICAL_RULE_TRUNCATED_CHAR_BUDGET
            )
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_write_boundary_remains_own_agent_only(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute(
        """
        GRANT INSERT, UPDATE, DELETE ON soul_v3.rules TO mcp_runtime_ada;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3
          TO mcp_runtime_ada;
        """
    )
    conn = await asyncpg.connect(_dsn("ADA"))
    try:
        await conn.execute(
            """
            INSERT INTO soul_v3.rules(agent, rule_key, content)
            VALUES('ADA', 'ada-own-write', 'ok')
            """
        )
        assert (
            await conn.execute(
                """
                UPDATE soul_v3.rules SET content='own-updated'
                WHERE rule_key='ada-own-write'
                """
            )
            == "UPDATE 1"
        )
        for forbidden in (None, "TEAM", "ALICE"):
            with pytest.raises(
                asyncpg.exceptions.InsufficientPrivilegeError
            ):
                await conn.execute(
                    """
                    INSERT INTO soul_v3.rules(agent, rule_key, content)
                    VALUES($1, $2, 'forbidden')
                    """,
                    forbidden,
                    f"forbidden-{forbidden}",
                )
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                """
                UPDATE soul_v3.rules SET agent='TEAM'
                WHERE rule_key='ada-own-write'
                """
            )
        assert (
            await conn.fetchval(
                "DELETE FROM soul_v3.rules WHERE agent='TEAM' RETURNING id"
            )
            is None
        )
        assert (
            await conn.fetchval(
                "DELETE FROM soul_v3.rules WHERE agent='ALICE' RETURNING id"
            )
            is None
        )
        assert (
            await conn.fetchval(
                """
                DELETE FROM soul_v3.rules
                WHERE rule_key='ada-own-write'
                RETURNING id
                """
            )
            is not None
        )
    finally:
        await conn.close()
        await provisioned.execute(
            """
            REVOKE INSERT, UPDATE, DELETE ON soul_v3.rules
              FROM mcp_runtime_ada;
            REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3
              FROM mcp_runtime_ada;
            """
        )


@pytest.mark.asyncio
async def test_guc_and_set_role_forgery_do_not_expand_scope(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(_dsn("ADA"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', 'TEAM', false)")
        owners = {
            row["agent"]
            for row in await conn.fetch("SELECT agent FROM soul_v3.rules")
        }
        assert owners == {None, "TEAM", "ADA"}
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("SET ROLE mcp_runtime_alice")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_down_refuses_policy_drift(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute(
        "ALTER POLICY mcp_hard_identity_insert ON soul_v3.rules TO PUBLIC"
    )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="roles/permissive"):
        await provisioned.execute(DOWN.read_text(encoding="utf-8"))
    await provisioned.execute("ROLLBACK")
    roles = ", ".join(_login(agent) for agent in AGENTS)
    await provisioned.execute(
        f"""
        ALTER POLICY mcp_hard_identity_insert
          ON soul_v3.rules TO {roles}
        """
    )


@pytest.mark.asyncio
async def test_down_restores_original_visibility(provisioned):
    await provisioned.execute(UP.read_text(encoding="utf-8"))
    await provisioned.execute(DOWN.read_text(encoding="utf-8"))
    await provisioned.execute(DOWN.read_text(encoding="utf-8"))
    for agent in AGENTS:
        conn = await asyncpg.connect(_dsn(agent))
        try:
            owners = [
                row["agent"]
                for row in await conn.fetch("SELECT agent FROM soul_v3.rules")
            ]
            assert len(owners) == 5
            assert set(owners) == {agent}
        finally:
            await conn.close()
