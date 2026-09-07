#!/usr/bin/env python3
"""SEC-2 preflight for SOUL dual memory layers.

This script is intentionally read-only.  It audits whether agent/scope privacy
can be enforced at the database level before rolling dual memory layers beyond
ADA.

Why read-only:
- Current Phase 1 RLS is tenant-oriented.
- SEC-2 asks for agent/scope isolation.
- Enabling strict agent RLS before every runtime sets app.agent could break
  SDK reads, bridges, cron jobs, and maintenance scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg

from seal_secrets import pg_dsn


AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")
SDK_ROLES = ("soul_sdk_runtime", "soul_sdk_user", "soul_sdk_admin")
AGENT_SHARED_SCOPES = ("team", "public", "shared")
REQUIRED_V1_1_POLICIES = {
    "memories_agent_scope_read",
    "memories_agent_scope_insert",
    "memories_agent_scope_update",
    "memories_tenant_user_read",
}


@dataclass
class TableRlsState:
    schema: str
    table: str
    rls_enabled: bool
    force_rls: bool


@dataclass
class RuntimeContextGap:
    file: str
    sets_tenant: bool
    sets_agent: bool
    risk: str


@dataclass
class RoleState:
    role: str
    exists: bool


@dataclass
class PolicyConflict:
    policy: str
    risk: str


def proposed_agent_scope_policy_sql() -> str:
    """Return the proposed SEC-2 policy SQL for review only.

    This is not safe to apply until all callers set app.agent or use a reviewed
    compatibility path. It intentionally keeps tenant isolation as the outer
    condition and adds agent/scope as a second gate.
    """

    return """
-- REVIEW ONLY: do not apply until all runtimes set app.agent.
CREATE POLICY memories_tenant_agent_scope_read
ON soul_v3.memories
FOR SELECT
TO soul_sdk_runtime
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND (
    COALESCE(scope, 'private') IN ('team', 'public', 'shared')
    OR agent = NULLIF(current_setting('app.agent', true), '')
  )
);
""".strip()


def proposed_multi_agent_compat_sql() -> str:
    """Return the consolidated v1.1 SQL proposal for review only.

    Postgres does not support AFTER SELECT triggers, so sdk_user audit must be
    done in the SDK API path or through audited SECURITY DEFINER read functions.
    The SQL below creates the DB shape and RLS policies, but is intentionally
    not applied by this preflight.
    """

    return """
-- REVIEW ONLY: Modelo Consolidado v1.1. Do not apply without William approval.
BEGIN;

-- Roles:
--   soul_sdk_runtime = agent viewer: own private + tenant team/public/shared.
--   soul_sdk_user    = tenant owner/user viewer: all tenant memories, audited by API.
--   soul_sdk_admin   = maintenance/admin, separate explicit approval.

CREATE ROLE soul_sdk_user NOLOGIN;
CREATE ROLE soul_sdk_admin NOLOGIN;

CREATE TABLE IF NOT EXISTS soul_v3.user_read_audit (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL,
  user_id text NOT NULL,
  query_hash text NOT NULL,
  endpoint text NOT NULL,
  rows_read integer NOT NULL,
  latency_ms integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_user_read_audit_tenant_created
ON soul_v3.user_read_audit (tenant_id, created_at DESC);

-- Existing Phase 1 policy is permissive for PUBLIC and would otherwise bypass
-- agent/scope SELECT policies. It must be replaced in the same migration.
DROP POLICY IF EXISTS tenant_isolation_sdk_v1 ON soul_v3.memories;

-- Agent viewer: private is only own agent; team/public are visible inside tenant.
-- Legacy scope='shared' is treated as team-equivalent for backward compatibility.
CREATE POLICY memories_agent_scope_read
ON soul_v3.memories
FOR SELECT
TO soul_sdk_runtime
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND (
    agent = NULLIF(current_setting('app.agent', true), '')
    OR COALESCE(scope, 'private') IN ('team', 'public', 'shared')
  )
);

-- Agent viewer writes: tenant must match trusted API key context and the row
-- must belong to the current agent. New SDK writes should use private/team;
-- public requires a separate approval path and shared remains legacy read-only.
CREATE POLICY memories_agent_scope_insert
ON soul_v3.memories
FOR INSERT
TO soul_sdk_runtime
WITH CHECK (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND agent = NULLIF(current_setting('app.agent', true), '')
  AND COALESCE(scope, 'private') IN ('private', 'team')
);

CREATE POLICY memories_agent_scope_update
ON soul_v3.memories
FOR UPDATE
TO soul_sdk_runtime
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND agent = NULLIF(current_setting('app.agent', true), '')
)
WITH CHECK (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND agent = NULLIF(current_setting('app.agent', true), '')
  AND COALESCE(scope, 'private') IN ('private', 'team')
);

-- Tenant user viewer: owner/user can read all memories in the tenant.
-- Every SDK path using this role MUST write soul_v3.user_read_audit.
CREATE POLICY memories_tenant_user_read
ON soul_v3.memories
FOR SELECT
TO soul_sdk_user
USING (
  tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  AND NULLIF(current_setting('app.viewer', true), '') = 'user'
  AND NULLIF(current_setting('app.user_id', true), '') IS NOT NULL
);

-- Public cross-tenant read is intentionally not enabled here.
-- It requires a separate double-approval workflow and policy.
COMMIT;
""".strip()


def evaluate_runtime_context_gaps(files: dict[str, str]) -> list[RuntimeContextGap]:
    """Identify callers that set tenant context but not agent context."""

    gaps: list[RuntimeContextGap] = []
    for path, content in files.items():
        sets_tenant = "app.tenant_id" in content
        sets_agent = "app.agent" in content
        if sets_tenant and not sets_agent:
            gaps.append(
                RuntimeContextGap(
                    file=path,
                    sets_tenant=True,
                    sets_agent=False,
                    risk="would not satisfy strict agent/scope RLS",
                )
            )
    return gaps


def evaluate_viewer_context_gaps(files: dict[str, str]) -> list[RuntimeContextGap]:
    """Identify callers that set tenant context but not viewer context."""

    gaps: list[RuntimeContextGap] = []
    for path, content in files.items():
        sets_tenant = "app.tenant_id" in content
        sets_viewer = "app.viewer" in content
        if sets_tenant and not sets_viewer:
            gaps.append(
                RuntimeContextGap(
                    file=path,
                    sets_tenant=True,
                    sets_agent="app.agent" in content,
                    risk="would not satisfy consolidated v1.1 viewer context",
                )
            )
    return gaps


async def fetch_table_rls_state(conn: asyncpg.Connection, schema: str, table: str) -> TableRlsState:
    row = await conn.fetchrow(
        """
        SELECT n.nspname AS schema_name,
               c.relname AS table_name,
               c.relrowsecurity,
               c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = $1 AND c.relname = $2
        """,
        schema,
        table,
    )
    if not row:
        raise RuntimeError(f"table_not_found:{schema}.{table}")
    return TableRlsState(
        schema=str(row["schema_name"]),
        table=str(row["table_name"]),
        rls_enabled=bool(row["relrowsecurity"]),
        force_rls=bool(row["relforcerowsecurity"]),
    )


async def fetch_policies(conn: asyncpg.Connection, schema: str, table: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT policyname, permissive, roles, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = $1 AND tablename = $2
        ORDER BY policyname
        """,
        schema,
        table,
    )
    return [dict(row) for row in rows]


def evaluate_policy_conflicts(policies: list[dict[str, Any]]) -> list[PolicyConflict]:
    """Flag permissive public policies that would bypass strict agent/scope read."""

    conflicts: list[PolicyConflict] = []
    for policy in policies:
        roles = {str(role) for role in policy.get("roles") or []}
        cmd = str(policy.get("cmd") or "").upper()
        permissive = str(policy.get("permissive") or "").upper()
        if "public" in roles and permissive == "PERMISSIVE" and cmd in {"ALL", "SELECT"}:
            conflicts.append(
                PolicyConflict(
                    policy=str(policy.get("policyname")),
                    risk="permissive PUBLIC policy would OR-bypass stricter agent/scope SELECT policies",
                )
            )
    return conflicts


def migration_v1_1_applied(policies: list[dict[str, Any]], roles: list[RoleState]) -> bool:
    """Return whether the reviewed v1.1 DB shape is already present."""

    policy_names = {str(policy.get("policyname")) for policy in policies}
    role_state = {role.role: role.exists for role in roles}
    return (
        REQUIRED_V1_1_POLICIES.issubset(policy_names)
        and all(role_state.get(role) for role in SDK_ROLES)
        and not evaluate_policy_conflicts(policies)
    )


async def fetch_role_states(conn: asyncpg.Connection) -> list[RoleState]:
    rows = await conn.fetch(
        """
        SELECT role_name, EXISTS(SELECT 1 FROM pg_roles WHERE rolname = role_name) AS exists
        FROM unnest($1::text[]) AS role_name
        ORDER BY role_name
        """,
        list(SDK_ROLES),
    )
    return [RoleState(role=str(row["role_name"]), exists=bool(row["exists"])) for row in rows]


async def fetch_memory_counts(conn: asyncpg.Connection) -> dict[str, Any]:
    by_scope = await conn.fetch(
        """
        SELECT agent,
               COALESCE(scope, 'private') AS scope,
               COALESCE(metadata->>'layer', 'unset') AS layer,
               COUNT(*)::bigint AS count
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND agent = ANY($1::text[])
        GROUP BY agent, COALESCE(scope, 'private'), COALESCE(metadata->>'layer', 'unset')
        ORDER BY agent, scope, layer
        """,
        list(AGENTS),
    )
    private_counts = await conn.fetch(
        """
        SELECT agent, COUNT(*)::bigint AS private_count
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND agent = ANY($1::text[])
          AND COALESCE(scope, 'private') = 'private'
        GROUP BY agent
        ORDER BY agent
        """,
        list(AGENTS),
    )
    unset_layers = await conn.fetch(
        """
        SELECT agent, COUNT(*)::bigint AS unset_layer_count
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND agent = ANY($1::text[])
          AND metadata->>'layer' IS NULL
        GROUP BY agent
        ORDER BY agent
        """,
        list(AGENTS),
    )
    return {
        "by_agent_scope_layer": [dict(row) for row in by_scope],
        "private_counts": [dict(row) for row in private_counts],
        "unset_layer_counts": [dict(row) for row in unset_layers],
    }


def build_compat_impact(counts: dict[str, Any]) -> dict[str, Any]:
    """Compute read-impact estimates from the existing count report."""

    by_scope: dict[str, dict[str, int]] = {}
    tenant_total = 0
    team_public_total = 0
    for row in counts["by_agent_scope_layer"]:
        agent = str(row["agent"])
        scope = str(row["scope"] or "private")
        count = int(row["count"])
        by_scope.setdefault(agent, {"private": 0, "team": 0, "public": 0, "shared": 0, "other": 0})
        if scope in {"private", "team", "public", "shared"}:
            by_scope[agent][scope] += count
        else:
            by_scope[agent]["other"] += count
        tenant_total += count
        if scope in AGENT_SHARED_SCOPES:
            team_public_total += count

    agent_view_rows: list[dict[str, Any]] = []
    for agent, scope_counts in sorted(by_scope.items()):
        visible = scope_counts["private"] + team_public_total
        hidden_private_other_agents = max(0, tenant_total - visible)
        agent_view_rows.append(
            {
                "agent": agent,
                "private_own": scope_counts["private"],
                "team_public_shared": team_public_total,
                "visible_as_agent_viewer": visible,
                "hidden_private_other_agents": hidden_private_other_agents,
            }
        )
    return {
        "tenant_user_visible_rows": tenant_total,
        "team_public_shared_rows": team_public_total,
        "agent_view_rows": agent_view_rows,
        "note": "Counts are read-only estimates for current active SEAL agents. Legacy scope='shared' is counted as team-equivalent.",
    }


async def build_preflight_report() -> dict[str, Any]:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rls = await fetch_table_rls_state(conn, "soul_v3", "memories")
        policies = await fetch_policies(conn, "soul_v3", "memories")
        role_states = await fetch_role_states(conn)
        counts = await fetch_memory_counts(conn)
    finally:
        await conn.close()

    runtime_files = {
        "memory/soul_memory_sdk_runtime.py": open("memory/soul_memory_sdk_runtime.py", encoding="utf-8").read(),
        "messages/ada_codex_remote_bridge.py": open("messages/ada_codex_remote_bridge.py", encoding="utf-8").read(),
        "memory/active_recall_hook.py": open("memory/active_recall_hook.py", encoding="utf-8").read(),
    }
    gaps = evaluate_runtime_context_gaps(runtime_files)
    viewer_gaps = evaluate_viewer_context_gaps(runtime_files)
    compat_impact = build_compat_impact(counts)
    applied = migration_v1_1_applied(policies, role_states)
    if applied:
        reason = "applied; consolidated v1.1 DB policies are active"
        next_steps = [
            "Keep sdk_user tenant-owner endpoints disabled until API audit path is implemented.",
            "Run SDK healthchecks and RLS smoke tests after service restart.",
            "Monitor policy_conflicts and runtime/viewer gaps in the next audits.",
        ]
    elif gaps or viewer_gaps:
        reason = "read_only_preflight; strict SEC-2 needs app.agent rollout first"
        next_steps = [
            "Update runtime context helpers to set app.agent/app.viewer where strict RLS is required.",
            "Run NEXUS audit before applying any RLS policy.",
            "Apply only after a count/impact report is explicitly approved by William.",
        ]
    else:
        reason = "read_only_preflight; app.agent/app.viewer gaps clear, consolidated v1.1 still needs NEXUS audit and William approval"
        next_steps = [
            "Have NEXUS audit the consolidated v1.1 policy and sdk_user audit path.",
            "Implement sdk_user audit in API/function path before enabling tenant-owner reads.",
            "Run NEXUS audit against the proposed RLS policy and service callers.",
            "Prepare exact COUNT/impact report before any policy change.",
            "Apply only after William explicitly approves the SQL scope.",
        ]

    return {
        "ok_to_apply": (not applied and not gaps and not viewer_gaps),
        "migration_v1_1_applied": applied,
        "reason": reason,
        "table_rls": asdict(rls),
        "policies": policies,
        "policy_conflicts": [asdict(conflict) for conflict in evaluate_policy_conflicts(policies)],
        "role_states": [asdict(role) for role in role_states],
        "memory_counts": counts,
        "compat_impact": compat_impact,
        "runtime_context_gaps": [asdict(gap) for gap in gaps],
        "viewer_context_gaps": [asdict(gap) for gap in viewer_gaps],
        "proposed_policy_sql": proposed_agent_scope_policy_sql(),
        "proposed_multi_agent_compat_sql": proposed_multi_agent_compat_sql(),
        "next_steps": next_steps,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "SEC-2 Dual Memory Preflight (read-only)",
        f"ok_to_apply={report['ok_to_apply']} reason={report['reason']}",
        f"migration_v1_1_applied={report['migration_v1_1_applied']}",
        "table_rls="
        f"enabled={report['table_rls']['rls_enabled']} force={report['table_rls']['force_rls']}",
        f"policies={len(report['policies'])}",
        f"policy_conflicts={len(report['policy_conflicts'])}",
        f"runtime_context_gaps={len(report['runtime_context_gaps'])}",
        f"viewer_context_gaps={len(report['viewer_context_gaps'])}",
        "roles="
        + ",".join(
            f"{role['role']}:{'exists' if role['exists'] else 'missing'}"
            for role in report["role_states"]
        ),
        "impact="
        f"tenant_user_visible_rows={report['compat_impact']['tenant_user_visible_rows']} "
        f"team_public_shared_rows={report['compat_impact']['team_public_shared_rows']}",
    ]
    for gap in report["runtime_context_gaps"]:
        lines.append(f"- gap {gap['file']}: {gap['risk']}")
    for gap in report["viewer_context_gaps"]:
        lines.append(f"- viewer gap {gap['file']}: {gap['risk']}")
    for conflict in report["policy_conflicts"]:
        lines.append(f"- policy conflict {conflict['policy']}: {conflict['risk']}")
    lines.append("next_steps:")
    for step in report["next_steps"]:
        lines.append(f"- {step}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    report = await build_preflight_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_report(report))


if __name__ == "__main__":
    asyncio.run(main())
