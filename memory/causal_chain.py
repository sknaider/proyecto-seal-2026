#!/usr/bin/env python3
"""Operational causal chains for SEAL AGI plan P4.

Uses the existing ``soul_v3.gam_event_graph`` table as an auditable event
ledger. A chain is a sequence of events linked by ``related_event_ids`` and a
shared ``metadata.correlation_id``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg
from seal_secrets import pg_dsn


DEFAULT_AGENT = "ADA"
CHAIN_STAGES = ("order", "plan", "artifact", "test", "outcome", "memory")


@dataclass(frozen=True)
class CausalEventInput:
    stage: str
    event: str
    evidence: str = ""
    artifact_path: str = ""
    outcome: str = ""


@dataclass(frozen=True)
class CausalChainRecord:
    correlation_id: str
    event_ids: list[int]
    stages: list[str]
    complete: bool
    evidence: str


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(resolve_db_dsn())


def validate_chain(events: list[CausalEventInput]) -> tuple[bool, list[str]]:
    stages = [event.stage for event in events]
    return stages == list(CHAIN_STAGES), stages


async def record_causal_chain(
    conn: asyncpg.Connection,
    agent: str,
    correlation_id: str,
    events: list[CausalEventInput],
    temporary: bool = False,
) -> CausalChainRecord:
    complete, stages = validate_chain(events)
    if not complete:
        raise ValueError(f"Invalid causal chain stages: {stages}; expected {list(CHAIN_STAGES)}")

    event_ids: list[int] = []
    previous_id: int | None = None
    for index, item in enumerate(events):
        metadata = {
            "source": "causal_chain",
            "correlation_id": correlation_id,
            "stage": item.stage,
            "stage_index": index,
            "evidence": item.evidence,
            "artifact_path": item.artifact_path,
            "outcome": item.outcome,
            "temporary": temporary,
        }
        related = [previous_id] if previous_id is not None else []
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.gam_event_graph
                (agent, event, related_event_ids, causal_direction, metadata)
            VALUES ($1, $2, $3::bigint[], $4, $5::jsonb)
            RETURNING id
            """,
            agent,
            item.event,
            related,
            "cause" if previous_id is None else "effect",
            json.dumps(metadata, sort_keys=True),
        )
        previous_id = int(row["id"])
        event_ids.append(previous_id)

    return CausalChainRecord(
        correlation_id=correlation_id,
        event_ids=event_ids,
        stages=stages,
        complete=complete,
        evidence=f"causal_chain_events={len(event_ids)} stages={','.join(stages)}",
    )


async def fetch_causal_chain(conn: asyncpg.Connection, correlation_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, agent, event, related_event_ids, causal_direction, metadata, created_at
        FROM soul_v3.gam_event_graph
        WHERE metadata->>'correlation_id'=$1
        ORDER BY (metadata->>'stage_index')::int ASC
        """,
        correlation_id,
    )
    result = []
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        result.append(
            {
                "id": int(row["id"]),
                "agent": row["agent"],
                "event": row["event"],
                "related_event_ids": [int(x) for x in (row["related_event_ids"] or [])],
                "causal_direction": row["causal_direction"],
                "stage": metadata.get("stage") if isinstance(metadata, dict) else None,
                "stage_index": metadata.get("stage_index") if isinstance(metadata, dict) else None,
                "metadata": metadata,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )
    return result


async def cleanup_temporary_chain(conn: asyncpg.Connection, correlation_id: str) -> int:
    return int(
        await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.gam_event_graph
                WHERE metadata->>'correlation_id'=$1
                  AND metadata->>'temporary'='true'
                RETURNING id
            )
            SELECT COUNT(*) FROM deleted
            """,
            correlation_id,
        )
    )


def default_chain(correlation_id: str) -> list[CausalEventInput]:
    return [
        CausalEventInput("order", "William orders AGI plan continuation", evidence=correlation_id),
        CausalEventInput("plan", "ADA selects measured P4 causal chain implementation", evidence="Evaluation Spine phases P0-P3 green"),
        CausalEventInput("artifact", "ADA creates causal_chain.py", artifact_path="memory/causal_chain.py"),
        CausalEventInput("test", "ADA runs causal chain tests", evidence="pytest causal chain suite"),
        CausalEventInput("outcome", "P4 chain recorded and verified", outcome="success"),
        CausalEventInput("memory", "P4 outcome will be stored in SOUL DB", evidence="memory_store after verification"),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record/query SEAL causal chains")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-default")
    record.add_argument("--agent", default=DEFAULT_AGENT)
    record.add_argument("--correlation-id", default=f"manual_{int(time.time() * 1000)}")
    record.add_argument("--temporary", action="store_true")

    query = sub.add_parser("query")
    query.add_argument("correlation_id")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        if args.command == "record-default":
            record = await record_causal_chain(
                conn,
                args.agent,
                args.correlation_id,
                default_chain(args.correlation_id),
                temporary=args.temporary,
            )
            print(json.dumps(asdict(record), indent=2, ensure_ascii=False))
            return 0
        if args.command == "query":
            print(json.dumps(await fetch_causal_chain(conn, args.correlation_id), indent=2, ensure_ascii=False))
            return 0
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
