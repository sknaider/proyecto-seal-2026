#!/usr/bin/env python3
"""Canonical work ledger for completed and pending SEAL tasks.

This complements soul_v3.agent_tasks. agent_tasks is the scheduler/backlog
primitive; this ledger is the human-facing source of truth William requested:
what is pending, what is done, who owns it, and what evidence backs it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


VALID_STATUSES = {"pending", "in_progress", "reviewing", "completed", "blocked", "cancelled", "deferred"}
WORKING_STATE_TO_LEDGER = {
    "STANDBY": "pending",
    "PLANNING": "pending",
    "EXECUTING": "in_progress",
    "REVIEWING": "reviewing",
    "RECOVERING": "in_progress",
    "BLOCKED": "blocked",
    "SLEEPING": "deferred",
}


@dataclass(frozen=True)
class WorkItem:
    agent: str
    title: str
    description: str = ""
    status: str = "pending"
    priority: int = 5
    requested_by: str = "William"
    source: str = "manual"
    source_ref: str | None = None
    source_chat_id: int | None = None
    source_task_id: int | None = None
    evidence: dict[str, Any] | None = None


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def ensure_schema(conn: asyncpg.Connection) -> None:
    migration = Path(__file__).resolve().parent / "migrations" / "027_agent_work_ledger.sql"
    await conn.execute(migration.read_text(encoding="utf-8"))


def _validate_item(item: WorkItem) -> None:
    if not item.agent.strip():
        raise ValueError("agent is required")
    if not item.title.strip():
        raise ValueError("title is required")
    if item.status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {item.status}")
    if not 0 <= int(item.priority) <= 10:
        raise ValueError("priority must be between 0 and 10")


async def upsert_work_item(conn: asyncpg.Connection, item: WorkItem) -> dict[str, Any]:
    _validate_item(item)
    evidence = json.dumps(item.evidence or {}, sort_keys=True, ensure_ascii=False)
    row = await conn.fetchrow(
        """
        WITH previous AS (
            SELECT id, status
            FROM soul_v3.agent_work_ledger
            WHERE agent=$1 AND title=$2 AND source=$7
        ),
        upserted AS (
            INSERT INTO soul_v3.agent_work_ledger (
                agent, title, description, status, priority, requested_by,
                source, source_ref, source_chat_id, source_task_id, evidence,
                started_at, completed_at, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11::jsonb,
                CASE WHEN $4 IN ('in_progress', 'reviewing', 'completed') THEN NOW() ELSE NULL END,
                CASE WHEN $4='completed' THEN NOW() ELSE NULL END,
                NOW()
            )
            ON CONFLICT (agent, title, source) DO UPDATE SET
                description = EXCLUDED.description,
                status = EXCLUDED.status,
                priority = EXCLUDED.priority,
                requested_by = EXCLUDED.requested_by,
                source_ref = EXCLUDED.source_ref,
                source_chat_id = EXCLUDED.source_chat_id,
                source_task_id = EXCLUDED.source_task_id,
                evidence = soul_v3.agent_work_ledger.evidence || EXCLUDED.evidence,
                started_at = COALESCE(soul_v3.agent_work_ledger.started_at, EXCLUDED.started_at),
                completed_at = CASE
                    WHEN EXCLUDED.status='completed' THEN COALESCE(soul_v3.agent_work_ledger.completed_at, NOW())
                    WHEN soul_v3.agent_work_ledger.status='completed' AND EXCLUDED.status <> 'completed' THEN NULL
                    ELSE soul_v3.agent_work_ledger.completed_at
                END,
                updated_at = NOW()
            RETURNING *
        ),
        event AS (
            INSERT INTO soul_v3.agent_work_events (
                work_id, agent, event_type, status_from, status_to, note, evidence
            )
            SELECT
                upserted.id,
                upserted.agent,
                CASE
                    WHEN previous.id IS NULL THEN 'created'
                    WHEN previous.status IS DISTINCT FROM upserted.status THEN 'status_changed'
                    ELSE 'updated'
                END,
                previous.status,
                upserted.status,
                'agent_work_ledger.py upsert',
                $11::jsonb
            FROM upserted
            LEFT JOIN previous ON previous.id = upserted.id
        )
        SELECT * FROM upserted
        """,
        item.agent,
        item.title,
        item.description,
        item.status,
        item.priority,
        item.requested_by,
        item.source,
        item.source_ref,
        item.source_chat_id,
        item.source_task_id,
        evidence,
    )
    return dict(row)


async def seed_william_20260528(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    items = [
        WorkItem(
            agent="ADA",
            title="SOUL hardening/autonomy/bench/cura anti-ruido",
            description="Execute JARVIS-evaluated improvements, coordinate with siblings, and include prevention so failures do not repeat.",
            status="reviewing",
            priority=9,
            source="william_20260528_recall",
            source_chat_id=79737,
            evidence={
                "chat_ids": [79737, 79742, 79900, 79901, 79948],
                "artifacts": [
                    "agents/ADA/sprint12_autonomous_lifecycle_closure_20260529.md",
                    "memory/seal_bench_v5.py",
                ],
                "bench_run_id": 10,
            },
        ),
        WorkItem(
            agent="JARVIS",
            title="Paper ICTSE 2026 + self-evolution architecture",
            description="Align architecture, paper work, and metrics/specs from AGI papers; hand executable specs to ADA.",
            status="in_progress",
            priority=8,
            source="william_20260528_recall",
            source_chat_id=79720,
            evidence={"chat_ids": [79720, 79728, 79874, 79888, 79931, 79936, 79948]},
        ),
        WorkItem(
            agent="NEXUS",
            title="Security/audit/capability gates + SOUL Companion packaging",
            description="Audit safety gates and continue SOUL Companion .deb arm64 packaging lane.",
            status="pending",
            priority=8,
            source="william_20260528_recall",
            source_chat_id=79764,
            evidence={"chat_ids": [79711, 79742, 79764, 79777], "working_state": "SOUL Companion — .deb arm64 packaging"},
        ),
        WorkItem(
            agent="ALICE",
            title="Secretary virtual/enlace SOUL-mundo + evidencia 2025",
            description="Learn external tools/market/internet workflow, keep evidence 2025/origin thread, and support William's company decisions.",
            status="pending",
            priority=9,
            source="william_20260528_recall",
            source_chat_id=79927,
            evidence={"chat_ids": [79898, 79913, 79921, 79927, 79934, 79939, 79941, 79942, 79943]},
        ),
    ]
    return [await upsert_work_item(conn, item) for item in items]


async def sync_from_working_state(
    conn: asyncpg.Connection,
    *,
    agents: list[str] | None = None,
    synced_by: str | None = None,
) -> list[dict[str, Any]]:
    # Audit trail: quién disparó el sync (antes no quedaba rastro). Default al
    # SEAL_AGENT del entorno, o 'system' si corre fuera de un agente.
    synced_by = synced_by or os.environ.get("SEAL_AGENT") or "system"
    rows = await conn.fetch(
        """
        SELECT agent, task_name, description, agent_state, risk_level,
               technical_state, pending_validations, updated_at
        FROM soul_v3.working_state
        WHERE ($1::text[] IS NULL OR agent = ANY($1::text[]))
          AND task_name IS NOT NULL
        ORDER BY updated_at DESC
        """,
        agents,
    )
    now = datetime.now(timezone.utc)
    synced = []
    for row in rows:
        state = str(row["agent_state"] or "STANDBY")
        status = WORKING_STATE_TO_LEDGER.get(state, "pending")
        # Edad del working_state: una lectura puede ser STALE si el lease del
        # agente expiró (crasheó pero su estado quedó 'ACTIVE'). No la dropeamos
        # en silencio (ocultaría trabajo real), pero la dejamos VISIBLE para que
        # el consumidor pueda juzgar la frescura.
        updated_at = row["updated_at"]
        age_s = (now - updated_at).total_seconds() if updated_at else None
        evidence = {
            "working_state_agent_state": state,
            "technical_state": row["technical_state"],
            "pending_validations": list(row["pending_validations"] or []),
            "working_state_updated_at": updated_at.isoformat() if updated_at else None,
            "working_state_age_seconds": round(age_s, 1) if age_s is not None else None,
            "synced_by": synced_by,
        }
        synced.append(
            await upsert_work_item(
                conn,
                WorkItem(
                    agent=row["agent"],
                    title=row["task_name"],
                    description=row["description"] or "",
                    status=status,
                    priority=7,
                    source="working_state",
                    source_ref=f"working_state:{row['agent']}",
                    evidence=evidence,
                ),
            )
        )
    return synced


async def summary(conn: asyncpg.Connection, *, limit: int = 50) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT id, agent, title, status, priority, source, source_chat_id,
               source_task_id, completed_at, updated_at, evidence
        FROM soul_v3.agent_work_ledger
        ORDER BY
            CASE status
                WHEN 'blocked' THEN 0
                WHEN 'in_progress' THEN 1
                WHEN 'reviewing' THEN 2
                WHEN 'pending' THEN 3
                WHEN 'deferred' THEN 4
                WHEN 'completed' THEN 5
                ELSE 6
            END,
            priority DESC,
            updated_at DESC
        LIMIT $1
        """,
        limit,
    )
    counts = await conn.fetch(
        """
        SELECT agent, status, COUNT(*) AS count
        FROM soul_v3.agent_work_ledger
        GROUP BY agent, status
        ORDER BY agent, status
        """
    )
    return {
        "items": [dict(row) for row in rows],
        "counts": [dict(row) for row in counts],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL agent work ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure")
    sub.add_parser("seed-william-20260528")
    sync = sub.add_parser("sync-working-state")
    sync.add_argument("--agent", action="append")
    show = sub.add_parser("summary")
    show.add_argument("--limit", type=int, default=50)
    show.add_argument("--json", action="store_true")
    return parser


async def main_async(args: argparse.Namespace) -> Any:
    conn = await connect_db()
    try:
        await ensure_schema(conn)
        if args.command == "ensure":
            return {"ok": True, "schema": "soul_v3.agent_work_ledger"}
        if args.command == "seed-william-20260528":
            return {"seeded": await seed_william_20260528(conn)}
        if args.command == "sync-working-state":
            return {"synced": await sync_from_working_state(conn, agents=args.agent)}
        if args.command == "summary":
            return await summary(conn, limit=args.limit)
        raise ValueError(args.command)
    finally:
        await conn.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = asyncio.run(main_async(args))
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
