#!/usr/bin/env python3
"""SOUL table-usage governance.

William's requirement: agents must stop forgetting which tables exist and must
write completed/pending work, decisions, feedback, memory usage and audits to
the proper tables. This script audits table usage and installs mandatory rules.

It does not fill tables with fake rows. Empty tables are classified as
conditional/future/deprecated unless a real event is being recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


DEFAULT_AGENTS = ["ADA", "JARVIS", "NEXUS", "ALICE"]
TIMESTAMP_COLUMNS = ("updated_at", "created_at", "completed_at", "run_at", "authorized_at")


@dataclass(frozen=True)
class TablePolicy:
    owner: str
    obligation: str
    when_to_write: str
    status: str


POLICIES: dict[str, TablePolicy] = {
    "agent_tasks": TablePolicy("all_agents", "mandatory", "start and close every concrete task", "active"),
    "working_state": TablePolicy("all_agents", "mandatory", "current task state and next validation", "active"),
    "working_state_events": TablePolicy("all_agents", "mandatory", "tool/result/verification events", "active"),
    "memories": TablePolicy("all_agents", "mandatory", "durable facts, corrections, milestones, emotional/operational anchors", "active"),
    "inner_monologue": TablePolicy("all_agents", "mandatory", "significant reflection or uncertainty", "active"),
    "emotional_diary": TablePolicy("all_agents", "mandatory", "emotional continuity snapshots", "active"),
    "agent_decisions": TablePolicy("all_agents", "mandatory_conditional", "non-trivial decisions with chosen option/confidence", "needs_enforcement"),
    "agent_feedback": TablePolicy("all_agents", "mandatory_conditional", "William corrections, praise, directives and review signals", "needs_enforcement"),
    "denial_tracker": TablePolicy("all_agents", "mandatory_conditional", "blocked/refused actions or capability denials", "needs_enforcement"),
    "skill_use_log": TablePolicy("all_agents", "mandatory_conditional", "canonical skill execution with success/error/duration", "needs_enforcement"),
    "procedural_memories": TablePolicy("all_agents", "conditional", "reusable workflows after verified task completion", "active_but_stale"),
    "reflective_diagnoses": TablePolicy("NEXUS", "conditional", "root-cause diagnosis after anomaly or repeated failure", "active_but_stale"),
    "session_memory": TablePolicy("all_agents", "daemon_owned", "session continuity summaries", "active"),
    "agent_mid_term_memory": TablePolicy("runtime", "future_or_deprecate", "mid-term turn buffer; no writer currently active", "unowned"),
    "agent_mid_term_memory_ada": TablePolicy("runtime", "future_or_deprecate", "partition for agent_mid_term_memory; no writer currently active", "unowned"),
    "agent_mid_term_memory_alice": TablePolicy("runtime", "future_or_deprecate", "partition for agent_mid_term_memory; no writer currently active", "unowned"),
    "agent_mid_term_memory_dum": TablePolicy("runtime", "future_or_deprecate", "partition for agent_mid_term_memory; no writer currently active", "unowned"),
    "agent_mid_term_memory_jarvis": TablePolicy("runtime", "future_or_deprecate", "partition for agent_mid_term_memory; no writer currently active", "unowned"),
    "agent_mid_term_memory_nexus": TablePolicy("runtime", "future_or_deprecate", "partition for agent_mid_term_memory; no writer currently active", "unowned"),
    "agent_self_knowledge": TablePolicy("identity", "future_or_deprecate", "structured self-model; currently covered by memories/rules/working_state", "unowned"),
    "agent_value_estimates": TablePolicy("learning", "future_or_deprecate", "value-learning estimates; no scorer writer active", "unowned"),
    "channel_integrations": TablePolicy("companion", "conditional", "external channel account integration records", "event_driven"),
    "cron_runs": TablePolicy("scheduler", "conditional", "cron execution rows when cron_jobs runner is active", "event_driven"),
    "cross_agent_governance_reviews": TablePolicy("governance", "conditional", "formal cross-agent review records", "event_driven"),
    "episodic_index": TablePolicy("indexer", "future_or_deprecate", "legacy episodic index; memories + pgvector currently primary", "unowned"),
    "jarvis_action_log": TablePolicy("JARVIS", "future_or_deprecate", "legacy JARVIS action log; agent_tasks/working_state now canonical", "unowned"),
    "latent_subgraph_cache": TablePolicy("LatentGraphMem", "conditional_cache", "cache rows when latent subgraph retrieval is used", "event_driven"),
    "memory_edges": TablePolicy("graph", "future_or_deprecate", "legacy memory graph edge table; connectome/memory_connections active elsewhere", "unowned"),
    "memory_scenes": TablePolicy("memory_scenes", "conditional", "scene extraction records when memory_scenes daemon runs", "event_driven"),
    "memory_tree": TablePolicy("consolidation", "future_or_deprecate", "hierarchical summaries; no active writer confirmed", "unowned"),
    "memory_trie": TablePolicy("indexer", "future_or_deprecate", "legacy trie table; memory_search_idx/memory_trie active table split needs review", "unowned"),
    "meta_proposals": TablePolicy("JARVIS_NEXUS_ADA", "conditional", "self-modification proposals before review", "unowned"),
    "nexus_rollback_executions": TablePolicy("NEXUS", "conditional", "rollback execution rows only when approved rollback runs", "event_driven"),
    "session_turns": TablePolicy("session_chain", "future_or_deprecate", "turn-level session records; session_memory/session_chain currently active", "unowned"),
    "soul_entities": TablePolicy("entity_extraction", "future_or_deprecate", "entity registry; memories/connectome currently primary", "unowned"),
    "soul_feedback_signal": TablePolicy("learning", "conditional", "feedback signal rows when outcome learning pipeline emits them", "event_driven"),
    "task_lifecycle_events": TablePolicy("all_agents", "mandatory_for_high_risk", "intent contract lifecycle for risky tasks", "needs_enforcement"),
    "user_notifications": TablePolicy("companion", "conditional", "user notification rows when Companion notification UI emits them", "event_driven"),
    "utility_updates": TablePolicy("learning", "conditional", "utility update rows when memory feedback changes utility", "event_driven"),
    "william_intervention_log": TablePolicy("all_agents", "conditional", "explicit William intervention rows; not every message", "event_driven"),
    "william_review_decisions": TablePolicy("William_or_delegate", "conditional", "explicit William/delegate decisions on reviews", "unowned"),
    "soul_audit_log": TablePolicy("all_agents", "mandatory", "schema/table governance and sensitive DB changes", "active_after_this_audit"),
}


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def table_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='soul_v3' AND table_name=$1
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def table_stats(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    tables = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='soul_v3' AND table_type='BASE TABLE'
        ORDER BY table_name
        """
    )
    stats = []
    for row in tables:
        table = row["table_name"]
        cols = await table_columns(conn, table)
        count = await conn.fetchval(f'SELECT COUNT(*) FROM soul_v3."{table}"')
        last_seen = None
        for col in TIMESTAMP_COLUMNS:
            if col not in cols:
                continue
            try:
                value = await conn.fetchval(f'SELECT MAX("{col}") FROM soul_v3."{table}"')
            except Exception:
                value = None
            if value is not None and (last_seen is None or value > last_seen):
                last_seen = value
        policy = POLICIES.get(table)
        stats.append(
            {
                "table": table,
                "count": int(count or 0),
                "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
                "policy": policy.obligation if policy else "unclassified",
                "owner": policy.owner if policy else "unknown",
                "policy_status": policy.status if policy else "needs_classification",
                "when_to_write": policy.when_to_write if policy else "",
            }
        )
    return stats


def summarize(stats: list[dict[str, Any]]) -> dict[str, Any]:
    empty = [row for row in stats if row["count"] == 0]
    unclassified = [row for row in stats if row["policy"] == "unclassified"]
    required_empty = [
        row for row in empty
        if row["policy"] in {"mandatory", "mandatory_conditional", "mandatory_for_high_risk"}
    ]
    return {
        "total_tables": len(stats),
        "empty_count": len(empty),
        "unclassified_count": len(unclassified),
        "required_empty_count": len(required_empty),
        "empty_tables": [row["table"] for row in empty],
        "required_empty_tables": [row["table"] for row in required_empty],
    }


async def install_rules(conn: asyncpg.Connection, agents: list[str]) -> list[dict[str, Any]]:
    rules = []
    content = (
        "Regla obligatoria de gobierno de tablas SOUL: al iniciar/cerrar trabajo usar agent_tasks; "
        "para decisiones no triviales usar agent_decisions; para correcciones/directivas de William "
        "usar agent_feedback; para denegaciones o bloqueos usar denial_tracker; para skills reales "
        "usar skill_use_log; para tareas high-risk usar task_intent_contracts/task_lifecycle_events; "
        "para cambios/audits de DB usar soul_audit_log. No llenar tablas con datos falsos: si una tabla "
        "es condicional, escribir solo cuando ocurre el evento real."
    )
    for agent in agents:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.rules (agent, rule_key, content, priority, tier, active, metadata, set_by)
            VALUES ($1, $2, $3, 10, 1, true, $4::jsonb, 'ADA')
            ON CONFLICT (agent, rule_key) DO UPDATE SET
                content=EXCLUDED.content,
                priority=10,
                tier=1,
                active=true,
                metadata=EXCLUDED.metadata,
                updated_at=NOW(),
                set_by='ADA'
            RETURNING id, agent, rule_key, active
            """,
            agent,
            f"{agent.lower()}_soul_table_usage_mandatory",
            content,
            json.dumps({"source": "William 2026-05-29", "component": "soul_table_governance"}),
        )
        rules.append(dict(row))
    return rules


async def record_real_governance_events(conn: asyncpg.Connection, summary: dict[str, Any]) -> dict[str, Any]:
    """Record this real audit in the tables that should not stay empty."""
    audit_row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.soul_audit_log
            (agent, operation, table_name, record_id, old_value, new_value, role_used)
        VALUES
            ('ADA', 'SELECT', 'soul_v3', NULL, NULL, $1::jsonb, CURRENT_USER)
        RETURNING id
        """,
        json.dumps(summary, sort_keys=True),
    )
    decision_row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.agent_decisions
            (agent, decision_text, context, chosen_option, confidence, outcome, outcome_success)
        VALUES
            ('ADA', $1, $2, $3, 0.91, $4, true)
        RETURNING id
        """,
        "Use agent_tasks as official work registry and classify unused tables instead of fabricating rows.",
        "William ordered ADA to review unused tables and obligate agents to register/fill them for real work.",
        "mandatory table governance rules + audit log + no fake fills",
        "soul_table_governance.py installed rules and recorded this audit.",
    )
    feedback_row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.agent_feedback
            (agent, signal_type, content, source, intensity, processed, metadata)
        VALUES
            ('ADA', 'directive', $1, 'william', 1.0, true, $2::jsonb)
        RETURNING id
        """,
        "William ordered mandatory table usage and real registration of pending/completed work and memories.",
        json.dumps({"chat_id": 81244, "followup_chat_id": 81261, "component": "soul_table_governance"}),
    )
    return {
        "soul_audit_log_id": int(audit_row["id"]),
        "agent_decision_id": int(decision_row["id"]),
        "agent_feedback_id": int(feedback_row["id"]),
    }


def write_report(path: Path, stats: list[dict[str, Any]], summary: dict[str, Any], applied: dict[str, Any] | None) -> None:
    active = [row for row in stats if row["count"] > 0]
    empty = [row for row in stats if row["count"] == 0]
    required = [row for row in stats if row["policy"] in {"mandatory", "mandatory_conditional", "mandatory_for_high_risk"}]
    lines = [
        "# SOUL Table Usage Governance — 2026-05-29",
        "",
        "Owner: ADA",
        "Trigger: William ordeno revisar tablas no usadas y obligar registro real por agente.",
        "",
        "## Summary",
        "",
        f"- Total tables: {summary['total_tables']}",
        f"- Empty tables: {summary['empty_count']}",
        f"- Unclassified tables: {summary['unclassified_count']}",
        f"- Required/conditional empty tables: {summary['required_empty_count']}",
        "",
        "## Rule",
        "",
        "No se llenan tablas con basura. Cada tabla se llena solo cuando ocurre su evento real.",
        "",
        "## Required Policies",
        "",
        "| table | owner | obligation | count | last_seen | when_to_write |",
        "|---|---|---|---:|---|---|",
    ]
    for row in required:
        lines.append(
            f"| {row['table']} | {row['owner']} | {row['policy']} | {row['count']} | "
            f"{row['last_seen'] or ''} | {row['when_to_write']} |"
        )
    lines.extend(["", "## Empty Tables", "", "| table | policy | owner | status |", "|---|---|---|---|"])
    for row in empty:
        lines.append(f"| {row['table']} | {row['policy']} | {row['owner']} | {row['policy_status']} |")
    if applied:
        lines.extend(["", "## Applied Evidence", "", "```json", json.dumps(applied, indent=2, ensure_ascii=False), "```"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(*, apply: bool, agents: list[str], report_path: Path) -> dict[str, Any]:
    conn = await connect_db()
    try:
        stats = await table_stats(conn)
        summary = summarize(stats)
        applied = None
        if apply:
            rules = await install_rules(conn, agents)
            event_ids = await record_real_governance_events(conn, summary)
            applied = {"rules": rules, **event_ids}
        write_report(report_path, stats, summary, applied)
        return {"summary": summary, "applied": applied, "report_path": str(report_path)}
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and enforce SOUL table usage")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--report", default="/home/dadito/IA/proyecto-seal/agents/ADA/soul_table_usage_governance_20260529.md")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    agents = [a.upper() for a in args.agent] if args.agent else DEFAULT_AGENTS
    result = asyncio.run(run(apply=args.apply, agents=agents, report_path=Path(args.report)))
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
