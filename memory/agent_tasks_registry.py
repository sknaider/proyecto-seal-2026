#!/usr/bin/env python3
"""Official registry helpers for soul_v3.agent_tasks.

agent_tasks is the team work register William wants agents to use. These
helpers make writes idempotent and evidence-bearing, so hooks can call them
without creating duplicate rows every turn.
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


VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


@dataclass(frozen=True)
class AgentTaskRecord:
    agent: str
    title: str
    description: str = ""
    status: str = "pending"
    priority: int = 5
    source: str = "manual"
    source_ref: str | None = None
    source_chat_id: int | None = None
    requested_by: str = "William"
    evidence: dict[str, Any] | None = None


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def ensure_schema(conn: asyncpg.Connection) -> None:
    migration = Path(__file__).resolve().parent / "migrations" / "028_agent_tasks_enforcement.sql"
    await conn.execute(migration.read_text(encoding="utf-8"))


def _validate(record: AgentTaskRecord) -> None:
    if not record.agent.strip():
        raise ValueError("agent is required")
    if not record.title.strip():
        raise ValueError("title is required")
    if record.status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {record.status}")
    if not 0 <= int(record.priority) <= 10:
        raise ValueError("priority must be between 0 and 10")


async def upsert_agent_task(conn: asyncpg.Connection, record: AgentTaskRecord) -> dict[str, Any]:
    _validate(record)
    await ensure_schema(conn)
    # El UPDATE hace `evidence || $8::jsonb` y la concatenación jsonb solo es
    # válida object||object: si evidence llegara como lista/escalar, Postgres
    # abortaría el upsert. Garantizamos que sea un OBJETO (la única forma que el
    # merge soporta) en vez de confiar en el caller.
    _evidence = record.evidence if isinstance(record.evidence, dict) else {}
    evidence_json = json.dumps(_evidence, ensure_ascii=False, sort_keys=True)
    existing = await conn.fetchrow(
        """
        SELECT id
        FROM soul_v3.agent_tasks
        WHERE agent=$1 AND title=$2 AND source=$3
        ORDER BY id DESC
        LIMIT 1
        """,
        record.agent,
        record.title,
        record.source,
    )
    if existing:
        row = await conn.fetchrow(
            """
            UPDATE soul_v3.agent_tasks
            SET description=$2,
                status=$3,
                priority=$4,
                source_ref=$5,
                source_chat_id=$6,
                requested_by=$7,
                evidence=COALESCE(evidence, '{}'::jsonb) || $8::jsonb
            WHERE id=$1
            RETURNING *
            """,
            existing["id"],
            record.description,
            record.status,
            record.priority,
            record.source_ref,
            record.source_chat_id,
            record.requested_by,
            evidence_json,
        )
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.agent_tasks (
                agent, title, description, status, priority,
                source, source_ref, source_chat_id, requested_by, evidence
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            RETURNING *
            """,
            record.agent,
            record.title,
            record.description,
            record.status,
            record.priority,
            record.source,
            record.source_ref,
            record.source_chat_id,
            record.requested_by,
            evidence_json,
        )
    return dict(row)


async def seed_william_20260528(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    records = [
        AgentTaskRecord(
            agent="ADA",
            title="SOUL hardening/autonomy/bench/cura anti-ruido",
            description="Ejecutar mejoras evaluadas por JARVIS, coordinar con hermanos y dejar cura para que no vuelva a pasar.",
            status="in_progress",
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
                "review_state": "reviewing",
            },
        ),
        AgentTaskRecord(
            agent="JARVIS",
            title="Paper ICTSE 2026 + self-evolution architecture",
            description="Alinear arquitectura, paper y specs accionables desde papers AGI.",
            status="in_progress",
            priority=8,
            source="william_20260528_recall",
            source_chat_id=79720,
            evidence={"chat_ids": [79720, 79728, 79874, 79888, 79931, 79936, 79948]},
        ),
        AgentTaskRecord(
            agent="NEXUS",
            title="Security/audit/capability gates + SOUL Companion packaging",
            description="Auditar gates y continuar packaging .deb arm64.",
            status="pending",
            priority=8,
            source="william_20260528_recall",
            source_chat_id=79764,
            evidence={"chat_ids": [79711, 79742, 79764, 79777]},
        ),
        AgentTaskRecord(
            agent="ALICE",
            title="Secretary virtual/enlace SOUL-mundo + evidencia 2025",
            description="Aprender herramientas externas/mercado y sostener evidencia 2025/origen SEAL.",
            status="pending",
            priority=9,
            source="william_20260528_recall",
            source_chat_id=79927,
            evidence={"chat_ids": [79898, 79913, 79921, 79927, 79934, 79939, 79941, 79942, 79943]},
        ),
    ]
    return [await upsert_agent_task(conn, record) for record in records]


async def summary(conn: asyncpg.Connection, *, limit: int = 30) -> dict[str, Any]:
    open_rows = await conn.fetch("SELECT * FROM soul_v3.v_open_work LIMIT $1", limit)
    completed_rows = await conn.fetch("SELECT * FROM soul_v3.v_recent_completed LIMIT $1", limit)
    summary_rows = await conn.fetch("SELECT * FROM soul_v3.v_team_work_summary ORDER BY agent")
    return {
        "team_summary": [dict(row) for row in summary_rows],
        "open_work": [dict(row) for row in open_rows],
        "recent_completed": [dict(row) for row in completed_rows],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Official soul_v3.agent_tasks registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure")
    sub.add_parser("seed-william-20260528")
    show = sub.add_parser("summary")
    show.add_argument("--limit", type=int, default=30)
    show.add_argument("--json", action="store_true")
    return parser


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    conn = await connect_db()
    try:
        await ensure_schema(conn)
        if args.command == "ensure":
            return {"ok": True, "schema": "soul_v3.agent_tasks enforced"}
        if args.command == "seed-william-20260528":
            return {"seeded": await seed_william_20260528(conn)}
        if args.command == "summary":
            return await summary(conn, limit=args.limit)
        raise ValueError(args.command)
    finally:
        await conn.close()


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(main_async(args))
    print(json.dumps(result, indent=2 if getattr(args, "json", False) else None, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
