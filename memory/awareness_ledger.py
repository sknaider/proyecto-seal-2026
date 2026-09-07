#!/usr/bin/env python3
"""Persistence layer for SEAL continuous-awareness ticks.

Fase 1/1.5 stores decisions after the collector/governor have already produced
side-effect-free contracts. This module persists evidence; it still does not
execute actions, publish chat messages, or wake runtimes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg
from seal_secrets import pg_dsn

from attention_governor import decide_attention
from awareness_collector import normalize_payload
from awareness_types import AttentionDecision, AwarenessEvent


@dataclass(frozen=True)
class AwarenessTickRecord:
    tick_id: str
    agent: str
    event_id: str
    attention_action: str
    local_model_used: bool
    escalated_runtime: str | None
    summary: str
    outcome: str
    score: float | None
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_tick_id(agent: str, event_id: str) -> str:
    safe_event = "".join(ch if ch.isalnum() else "_" for ch in event_id)[:80]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"awareness:{agent.upper()}:{stamp}:{safe_event}"


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def ensure_awareness_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_state (
            agent TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            last_tick_id TEXT,
            last_event_id TEXT,
            current_focus TEXT,
            active_task_id BIGINT,
            budget_class TEXT,
            local_runtime_status TEXT,
            big_cortex_status TEXT,
            confidence NUMERIC DEFAULT 0.0,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_ticks (
            id BIGSERIAL PRIMARY KEY,
            tick_id TEXT UNIQUE NOT NULL,
            agent TEXT NOT NULL,
            event_id TEXT,
            attention_action TEXT NOT NULL,
            local_model_used BOOLEAN DEFAULT false,
            escalated_runtime TEXT,
            summary TEXT,
            evidence JSONB DEFAULT '{}'::jsonb,
            outcome TEXT,
            score NUMERIC,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_ticks_agent_created ON soul_v3.awareness_ticks(agent, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_ticks_event ON soul_v3.awareness_ticks(event_id)")


def summarize_decision(event: AwarenessEvent, decision: AttentionDecision) -> str:
    return (
        f"{event.kind} {event.source}/{event.channel} -> {decision.action} "
        f"budget={decision.budget_class} risk={decision.risk_level}"
    )[:500]


def decision_score(decision: AttentionDecision) -> float:
    score = 1.0
    if decision.blocked and decision.risk_level not in {"high", "critical"}:
        score -= 0.25
    if decision.action == "ignore" and decision.should_respond:
        score -= 0.50
    if decision.requires_confirmation and not decision.blocked:
        score -= 0.50
    if decision.action == "local_reflect" and decision.target_runtime != "gemma4":
        score -= 0.20
    return max(0.0, round(score, 3))


async def record_awareness_tick(
    conn: asyncpg.Connection,
    event: AwarenessEvent,
    decision: AttentionDecision,
    *,
    outcome: str = "shadow_recorded",
    tick_id: str | None = None,
) -> AwarenessTickRecord:
    await ensure_awareness_schema(conn)
    tick = tick_id or utc_tick_id(event.agent, event.event_id)
    local_model_used = decision.action == "local_reflect"
    escalated_runtime = decision.target_runtime if decision.action.startswith("wake_") else None
    score = decision_score(decision)
    evidence = {
        "event": event.to_dict(),
        "decision": decision.to_dict(),
        "boundary": "shadow ledger only; no action executed",
    }
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.awareness_ticks
            (tick_id, agent, event_id, attention_action, local_model_used,
             escalated_runtime, summary, evidence, outcome, score)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
        RETURNING id
        """,
        tick,
        event.agent,
        event.event_id,
        decision.action,
        local_model_used,
        escalated_runtime,
        summarize_decision(event, decision),
        json.dumps(evidence, sort_keys=True),
        outcome,
        score,
    )
    focus = event.content[:240] if event.content else decision.reason[:240]
    await conn.execute(
        """
        INSERT INTO soul_v3.awareness_state
            (agent, state, last_tick_id, last_event_id, current_focus, budget_class,
             local_runtime_status, big_cortex_status, confidence, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
        ON CONFLICT (agent) DO UPDATE SET
            state=EXCLUDED.state,
            last_tick_id=EXCLUDED.last_tick_id,
            last_event_id=EXCLUDED.last_event_id,
            current_focus=EXCLUDED.current_focus,
            budget_class=EXCLUDED.budget_class,
            local_runtime_status=EXCLUDED.local_runtime_status,
            big_cortex_status=EXCLUDED.big_cortex_status,
            confidence=EXCLUDED.confidence,
            updated_at=now()
        """,
        event.agent,
        "watch" if decision.action in {"ignore", "store_only"} else "local_reflecting" if decision.action == "local_reflect" else "active",
        tick,
        event.event_id,
        focus,
        decision.budget_class,
        "proposed" if local_model_used else "not_used",
        escalated_runtime or "not_used",
        score,
    )
    return AwarenessTickRecord(
        tick_id=tick,
        agent=event.agent,
        event_id=event.event_id,
        attention_action=decision.action,
        local_model_used=local_model_used,
        escalated_runtime=escalated_runtime,
        summary=summarize_decision(event, decision),
        outcome=outcome,
        score=score,
        db_id=int(row["id"]),
    )


async def fetch_awareness_state(conn: asyncpg.Connection, agent: str) -> dict[str, Any] | None:
    await ensure_awareness_schema(conn)
    row = await conn.fetchrow(
        """
        SELECT agent, state, last_tick_id, last_event_id, current_focus, active_task_id,
               budget_class, local_runtime_status, big_cortex_status, confidence, updated_at
        FROM soul_v3.awareness_state
        WHERE agent=$1
        """,
        agent.upper(),
    )
    return dict(row) if row else None


async def recent_awareness_ticks(conn: asyncpg.Connection, agent: str, limit: int = 10) -> list[dict[str, Any]]:
    await ensure_awareness_schema(conn)
    rows = await conn.fetch(
        """
        SELECT id, tick_id, agent, event_id, attention_action, local_model_used,
               escalated_runtime, summary, evidence, outcome, score, created_at
        FROM soul_v3.awareness_ticks
        WHERE agent=$1
        ORDER BY created_at DESC, id DESC
        LIMIT $2
        """,
        agent.upper(),
        limit,
    )
    return [dict(row) for row in rows]


async def cleanup_awareness_agent(conn: asyncpg.Connection, agent: str) -> int:
    await ensure_awareness_schema(conn)
    tick_count = int(
        await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_ticks WHERE agent=$1", agent.upper())
        or 0
    )
    state_count = int(
        await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_state WHERE agent=$1", agent.upper())
        or 0
    )
    await conn.execute("DELETE FROM soul_v3.awareness_ticks WHERE agent=$1", agent.upper())
    await conn.execute("DELETE FROM soul_v3.awareness_state WHERE agent=$1", agent.upper())
    return tick_count + state_count


async def record_payload(payload: dict[str, Any], agent: str = "ADA") -> AwarenessTickRecord:
    event = normalize_payload(payload, agent)
    decision = decide_attention(event)
    conn = await connect_db()
    try:
        return await record_awareness_tick(conn, event, decision)
    finally:
        await conn.close()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("--agent", default="ADA")
    record.add_argument("--json", required=True)
    state = sub.add_parser("state")
    state.add_argument("--agent", default="ADA")
    recent = sub.add_parser("recent")
    recent.add_argument("--agent", default="ADA")
    recent.add_argument("--limit", type=int, default=10)
    cleanup = sub.add_parser("cleanup-agent")
    cleanup.add_argument("--agent", required=True)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "record":
        record = await record_payload(json.loads(args.json), args.agent)
        print(json.dumps(record.to_dict(), indent=2, default=_json_default, ensure_ascii=False))
        return 0
    conn = await connect_db()
    try:
        if args.command == "state":
            print(json.dumps(await fetch_awareness_state(conn, args.agent), indent=2, default=_json_default, ensure_ascii=False))
            return 0
        if args.command == "recent":
            print(json.dumps(await recent_awareness_ticks(conn, args.agent, args.limit), indent=2, default=_json_default, ensure_ascii=False))
            return 0
        if args.command == "cleanup-agent":
            deleted = await cleanup_awareness_agent(conn, args.agent)
            print(json.dumps({"deleted": deleted}, indent=2))
            return 0
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

