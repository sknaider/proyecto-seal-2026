#!/usr/bin/env python3
"""SOUL Executive Nervous System — Phase 0 baseline audit.

Read-only audit. Measures runtime/event volume and likely cognitive waste over
the last N hours. Produces JSON plus a Markdown report for William/team review.
"""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)
OUT_DIR = Path("agents/ADA")
AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS", "DUM")
SYSTEM_TYPES = {"heartbeat", "awareness", "system", "hb", "status", "nerves_fire"}
LOW_VALUE_PATTERNS = [
    re.compile(r"\bheartbeat\b", re.I),
    re.compile(r"\bcatchup\b", re.I),
    re.compile(r"\bstatus\s*ok\b", re.I),
    re.compile(r"\bguardia activa\b", re.I),
    re.compile(r"\bsigo vivo\b", re.I),
]


def pct(part: int | float, total: int | float) -> float:
    return round((float(part) / float(total) * 100.0), 2) if total else 0.0


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def rows_to_dict(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


async def fetch_one(conn: asyncpg.Connection, sql: str, *args: Any) -> dict[str, Any]:
    row = await conn.fetchrow(sql, *args)
    return dict(row) if row else {}


async def fetch_all(conn: asyncpg.Connection, sql: str, *args: Any) -> list[dict[str, Any]]:
    return rows_to_dict(await conn.fetch(sql, *args))


async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'soul_v3' AND table_name = $1
        """,
        table,
    )
    return row is not None


async def collect_db_metrics(conn: asyncpg.Connection, hours: int) -> dict[str, Any]:
    interval = f"{hours} hours"
    metrics: dict[str, Any] = {"window_hours": hours}

    metrics["chat_total"] = await fetch_one(
        conn,
        f"""
        SELECT COUNT(*)::int AS total,
               COUNT(*) FILTER (WHERE sender_name = 'William')::int AS william,
               COUNT(*) FILTER (WHERE message_type = ANY($1::text[]))::int AS systemish
        FROM soul_v3.chat_messages
        WHERE created_at >= now() - interval '{interval}'
        """,
        list(SYSTEM_TYPES),
    )

    metrics["chat_by_sender"] = await fetch_all(
        conn,
        f"""
        SELECT COALESCE(sender_name, 'unknown') AS sender, COUNT(*)::int AS count
        FROM soul_v3.chat_messages
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY sender
        ORDER BY count DESC
        LIMIT 20
        """,
    )

    metrics["chat_by_type"] = await fetch_all(
        conn,
        f"""
        SELECT COALESCE(message_type, 'unknown') AS type, COUNT(*)::int AS count
        FROM soul_v3.chat_messages
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY type
        ORDER BY count DESC
        LIMIT 20
        """,
    )

    content_rows = await conn.fetch(
        f"""
        SELECT id, sender_name, message_type, content
        FROM soul_v3.chat_messages
        WHERE created_at >= now() - interval '{interval}'
        """
    )
    low_value = []
    token_total = 0
    token_low = 0
    for row in content_rows:
        text = row["content"] or ""
        tokens = estimate_tokens(text)
        token_total += tokens
        is_low = (row["message_type"] in SYSTEM_TYPES) or any(p.search(text) for p in LOW_VALUE_PATTERNS)
        if is_low:
            token_low += tokens
            low_value.append(row)
    metrics["chat_low_value"] = {
        "count": len(low_value),
        "pct": pct(len(low_value), len(content_rows)),
        "estimated_tokens": token_low,
        "estimated_token_pct": pct(token_low, token_total),
    }

    metrics["event_by_type"] = await fetch_all(
        conn,
        f"""
        SELECT COALESCE(event_type, 'unknown') AS type, COUNT(*)::int AS count
        FROM soul_v3.event_log
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY type
        ORDER BY count DESC
        LIMIT 30
        """,
    )

    metrics["event_by_agent"] = await fetch_all(
        conn,
        f"""
        SELECT COALESCE(agent, 'unknown') AS agent, COUNT(*)::int AS count
        FROM soul_v3.event_log
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY agent
        ORDER BY count DESC
        LIMIT 20
        """,
    )

    metrics["tasks"] = await fetch_all(
        conn,
        f"""
        SELECT agent, status, COUNT(*)::int AS count
        FROM soul_v3.agent_tasks
        WHERE created_at >= now() - interval '{interval}'
           OR completed_at >= now() - interval '{interval}'
           OR status IN ('pending', 'in_progress')
        GROUP BY agent, status
        ORDER BY agent, status
        """
    )

    metrics["working_state"] = await fetch_all(
        conn,
        """
        SELECT agent, task_name, agent_state, emotional_state, technical_state,
               updated_at, EXTRACT(EPOCH FROM (now() - updated_at))::int AS age_seconds
        FROM soul_v3.working_state
        ORDER BY agent
        """,
    )

    metrics["nerves"] = await fetch_all(
        conn,
        f"""
        SELECT agent,
               COUNT(*)::int AS samples,
               COUNT(*) FILTER (WHERE fired)::int AS fires,
               ROUND(AVG(pre_pressure)::numeric, 2)::float AS avg_pressure
        FROM soul_v3.nerves_metrics_log
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY agent
        ORDER BY fires DESC, samples DESC
        """
    )

    metrics["inner_monologue"] = await fetch_all(
        conn,
        f"""
        SELECT agent, COUNT(*)::int AS count,
               MAX(created_at) AS last_at
        FROM soul_v3.inner_monologue
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY agent
        ORDER BY count DESC
        """
    )

    metrics["recall_audit"] = await fetch_all(
        conn,
        f"""
        SELECT agent, mode, COUNT(*)::int AS count,
               ROUND(AVG(elapsed_ms)::numeric, 1)::float AS avg_ms,
               ROUND(AVG(hits_returned)::numeric, 1)::float AS avg_hits
        FROM soul_v3.recall_audit
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY agent, mode
        ORDER BY count DESC
        """
    )

    metrics["tool_budget"] = await fetch_all(
        conn,
        f"""
        SELECT agent, tool_name,
               SUM(call_count)::int AS calls,
               SUM(estimated_tokens)::bigint AS estimated_tokens,
               BOOL_OR(budget_warn) AS any_warn,
               BOOL_OR(budget_block) AS any_block
        FROM soul_v3.tool_budget
        WHERE updated_at >= now() - interval '{interval}'
        GROUP BY agent, tool_name
        ORDER BY estimated_tokens DESC NULLS LAST, calls DESC
        LIMIT 30
        """
    )

    metrics["token_budget"] = await fetch_all(
        conn,
        """
        SELECT agent, daily_budget_input, daily_budget_output,
               consumed_today_input, consumed_today_output, last_reset_at
        FROM soul_v3.agent_token_budget
        ORDER BY agent
        """,
    ) if await table_exists(conn, "agent_token_budget") else []

    metrics["agent_lifecycle"] = await fetch_all(
        conn,
        """
        SELECT agent_name, desired_state, changed_by, changed_at, reason
        FROM soul_v3.agent_lifecycle
        ORDER BY agent_name
        """,
    ) if await table_exists(conn, "agent_lifecycle") else []

    metrics["lifecycle_events"] = await fetch_all(
        conn,
        f"""
        SELECT agent_name, event_type, desired_state, actual_state, COUNT(*)::int AS count
        FROM soul_v3.lifecycle_events
        WHERE created_at >= now() - interval '{interval}'
        GROUP BY agent_name, event_type, desired_state, actual_state
        ORDER BY count DESC
        LIMIT 30
        """
    ) if await table_exists(conn, "lifecycle_events") else []

    return metrics


def collect_systemd_metrics() -> dict[str, Any]:
    result = subprocess.run(
        ["systemctl", "--user", "list-units", "--type=service", "--all", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        line for line in result.stdout.splitlines()
        if re.search(r"(seal|ada|alice|jarvis|nexus|dum|soul)", line, re.I)
    ]
    counts = Counter()
    units = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        unit, load, active, sub = parts[:4]
        counts[f"{active}/{sub}"] += 1
        units.append({"unit": unit.lstrip("●"), "load": load, "active": active, "sub": sub})
    return {
        "total_relevant": len(units),
        "state_counts": dict(sorted(counts.items())),
        "running": [u["unit"] for u in units if u["active"] == "active" and u["sub"] == "running"],
        "not_found": [u["unit"] for u in units if u["load"] == "not-found"],
    }


def summarize_findings(metrics: dict[str, Any]) -> list[str]:
    findings = []
    chat = metrics["db"].get("chat_total", {})
    total = chat.get("total", 0)
    low = metrics["db"].get("chat_low_value", {})
    if total:
        findings.append(
            f"Chat {metrics['db']['window_hours']}h: {total} mensajes; "
            f"{low.get('count', 0)} ({low.get('pct', 0)}%) son low-value/systemish por heurística."
        )
    systemd = metrics["systemd"]
    findings.append(
        f"Systemd relevante: {systemd['total_relevant']} servicios; {len(systemd['running'])} running; not-found={len(systemd['not_found'])}."
    )
    if systemd["not_found"]:
        findings.append("Unidad not-found detectada: " + ", ".join(systemd["not_found"]))
    tool_rows = metrics["db"].get("tool_budget", [])
    if tool_rows:
        top = tool_rows[0]
        findings.append(
            f"Mayor gasto tool_budget: {top.get('agent')}::{top.get('tool_name')} calls={top.get('calls')} est_tokens={top.get('estimated_tokens')}."
        )
    stale_ws = [
        r for r in metrics["db"].get("working_state", [])
        if (r.get("age_seconds") or 0) > 3600
    ]
    if stale_ws:
        findings.append(
            "Working_state >1h stale: " + ", ".join(f"{r['agent']}({r['age_seconds']}s)" for r in stale_ws)
        )
    return findings


def md_table(rows: list[dict[str, Any]], columns: list[str], limit: int | None = None) -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return "_Sin datos._\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def write_report(metrics: dict[str, Any], path: Path) -> None:
    db = metrics["db"]
    findings = summarize_findings(metrics)
    chat = db.get("chat_total", {})
    low = db.get("chat_low_value", {})
    lines = [
        "# SOUL Executive Nervous System — Phase 0 Baseline",
        "",
        f"Generated: {metrics['generated_at']}",
        f"Window: last {db['window_hours']} hours",
        "",
        "## Executive Summary",
        "",
        *[f"- {f}" for f in findings],
        "",
        "## Chat Volume",
        "",
        f"- Total messages: {chat.get('total', 0)}",
        f"- William messages: {chat.get('william', 0)}",
        f"- Systemish by message_type: {chat.get('systemish', 0)}",
        f"- Low-value/systemish heuristic: {low.get('count', 0)} ({low.get('pct', 0)}%)",
        f"- Estimated low-value tokens: {low.get('estimated_tokens', 0)} ({low.get('estimated_token_pct', 0)}%)",
        "",
        "### By sender",
        md_table(db.get("chat_by_sender", []), ["sender", "count"], 20),
        "### By type",
        md_table(db.get("chat_by_type", []), ["type", "count"], 20),
        "## Event Log",
        "",
        "### By type",
        md_table(db.get("event_by_type", []), ["type", "count"], 30),
        "### By agent",
        md_table(db.get("event_by_agent", []), ["agent", "count"], 20),
        "## Agent State",
        "",
        md_table(db.get("working_state", []), ["agent", "task_name", "agent_state", "emotional_state", "age_seconds"], None),
        "## Tasks",
        "",
        md_table(db.get("tasks", []), ["agent", "status", "count"], None),
        "## NERVES",
        "",
        md_table(db.get("nerves", []), ["agent", "samples", "fires", "avg_pressure"], None),
        "## Recall Audit",
        "",
        md_table(db.get("recall_audit", []), ["agent", "mode", "count", "avg_ms", "avg_hits"], 30),
        "## Tool Budget",
        "",
        md_table(db.get("tool_budget", []), ["agent", "tool_name", "calls", "estimated_tokens", "any_warn", "any_block"], 30),
        "## Token Budget",
        "",
        md_table(db.get("token_budget", []), ["agent", "daily_budget_input", "daily_budget_output", "consumed_today_input", "consumed_today_output"], None),
        "## Lifecycle",
        "",
        "### Current",
        md_table(db.get("agent_lifecycle", []), ["agent_name", "desired_state", "changed_by", "reason"], None),
        "### Events",
        md_table(db.get("lifecycle_events", []), ["agent_name", "event_type", "desired_state", "actual_state", "count"], 30),
        "## Systemd Runtime",
        "",
        f"- Relevant services: {metrics['systemd']['total_relevant']}",
        f"- State counts: `{json.dumps(metrics['systemd']['state_counts'], sort_keys=True)}`",
        f"- Running services: {len(metrics['systemd']['running'])}",
        f"- Not found: {', '.join(metrics['systemd']['not_found']) or 'none'}",
        "",
        "## Phase 0 Conclusions",
        "",
        "- Baseline is read-only and reproducible.",
        "- Next step: use this report to tune router dry-run fixtures before enforcement.",
        "- Any not-found unit should be handled as topology hygiene, not as cognitive routing.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"soul_executive_baseline_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"report_soul_runtime_baseline_{ts}.md"

    conn = await asyncpg.connect(DB_URL)
    try:
        db_metrics = await collect_db_metrics(conn, args.hours)
    finally:
        await conn.close()

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": db_metrics,
        "systemd": collect_systemd_metrics(),
    }
    metrics["findings"] = summarize_findings(metrics)

    json_path.write_text(json.dumps(metrics, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    write_report(metrics, md_path)

    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    print("Findings:")
    for item in metrics["findings"]:
        print(f"- {item}")


if __name__ == "__main__":
    asyncio.run(main())
