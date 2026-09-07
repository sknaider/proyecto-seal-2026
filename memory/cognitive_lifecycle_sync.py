#!/usr/bin/env python3
"""Cognitive lifecycle sync from router shadow predictions.

This creates a separate cognitive lifecycle model. It never writes to the
legacy soul_v3.agent_lifecycle reconciler, so it cannot start/stop processes.
"""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)
REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "agents/ADA"
TEAM_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")
STATE_PRIORITY = {
    "sleep": 0,
    "watch": 1,
    "standby": 2,
    "active": 3,
    "deep_work": 4,
    "blocked": 5,
    "handoff": 6,
    "emergency": 7,
}


@dataclass
class CognitiveState:
    agent_name: str
    state: str
    runtime: str
    budget_class: str
    source_event_id: int | None
    router_action: str | None
    confidence: float
    reason: str
    expires_at: datetime


def runtime_for_agent(agent: str) -> str:
    return "codex" if agent == "ADA" else "claude"


def ttl_for_state(state: str) -> timedelta:
    if state == "emergency":
        return timedelta(minutes=30)
    if state == "deep_work":
        return timedelta(minutes=45)
    if state == "active":
        return timedelta(minutes=15)
    if state == "standby":
        return timedelta(minutes=30)
    return timedelta(minutes=10)


def better_state(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    cp = STATE_PRIORITY.get(candidate["predicted_state"], 0)
    op = STATE_PRIORITY.get(current["predicted_state"], 0)
    if cp != op:
        return cp > op
    if float(candidate["confidence"]) != float(current["confidence"]):
        return float(candidate["confidence"]) > float(current["confidence"])
    return int(candidate["event_id"]) > int(current["event_id"])


def reduce_predictions(rows: list[dict[str, Any]], now: datetime | None = None) -> list[CognitiveState]:
    now = now or datetime.now(timezone.utc)
    best: dict[str, dict[str, Any]] = {}

    for row in rows:
        agent = row["agent_name"]
        if agent not in TEAM_AGENTS:
            continue
        if row["runtime"] != runtime_for_agent(agent):
            continue
        if better_state(row, best.get(agent)):
            best[agent] = row

    states: list[CognitiveState] = []
    for agent in TEAM_AGENTS:
        row = best.get(agent)
        if row is None:
            state = "standby"
            budget = "zero"
            event_id = None
            action = None
            confidence = 0.75
            reason = "no recent router shadow prediction; default standby"
        else:
            state = row["predicted_state"]
            budget = row["budget_class"]
            event_id = int(row["event_id"])
            action = row["router_action"]
            confidence = float(row["confidence"])
            reason = row["reason"]
        states.append(
            CognitiveState(
                agent_name=agent,
                state=state,
                runtime=runtime_for_agent(agent),
                budget_class=budget,
                source_event_id=event_id,
                router_action=action,
                confidence=confidence,
                reason=reason,
                expires_at=now + ttl_for_state(state),
            )
        )
    return states


async def ensure_tables(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.agent_cognitive_lifecycle (
            agent_name TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            runtime TEXT NOT NULL,
            budget_class TEXT NOT NULL,
            source_event_id BIGINT,
            router_action TEXT,
            confidence DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            since TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            feature_flag TEXT NOT NULL DEFAULT 'shadow'
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.agent_cognitive_lifecycle_events (
            id BIGSERIAL PRIMARY KEY,
            agent_name TEXT NOT NULL,
            previous_state TEXT,
            new_state TEXT NOT NULL,
            runtime TEXT NOT NULL,
            budget_class TEXT NOT NULL,
            source_event_id BIGINT,
            router_action TEXT,
            confidence DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            feature_flag TEXT NOT NULL DEFAULT 'shadow',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_cognitive_lifecycle_events_agent
        ON soul_v3.agent_cognitive_lifecycle_events (agent_name, created_at DESC)
        """
    )


async def fetch_shadow_predictions(conn: asyncpg.Connection, hours: int, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT event_id, agent_name, predicted_state, runtime, budget_class,
               router_action, confidence, reason, shadow_created_at
        FROM soul_v3.router_lifecycle_shadow
        WHERE applied = false
          AND shadow_created_at >= now() - ($1::int * interval '1 hour')
        ORDER BY shadow_created_at ASC, event_id ASC, agent_name ASC
        LIMIT $2
        """,
        hours,
        limit,
    )
    return [dict(r) for r in rows]


async def persist_states(conn: asyncpg.Connection, states: list[CognitiveState], feature_flag: str) -> int:
    written = 0
    for state in states:
        previous = await conn.fetchrow(
            "SELECT state FROM soul_v3.agent_cognitive_lifecycle WHERE agent_name=$1",
            state.agent_name,
        )
        previous_state = previous["state"] if previous else None
        await conn.execute(
            """
            INSERT INTO soul_v3.agent_cognitive_lifecycle (
                agent_name, state, runtime, budget_class, source_event_id,
                router_action, confidence, reason, since, expires_at,
                updated_at, feature_flag
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,now(),$10)
            ON CONFLICT (agent_name) DO UPDATE SET
                state = EXCLUDED.state,
                runtime = EXCLUDED.runtime,
                budget_class = EXCLUDED.budget_class,
                source_event_id = EXCLUDED.source_event_id,
                router_action = EXCLUDED.router_action,
                confidence = EXCLUDED.confidence,
                reason = EXCLUDED.reason,
                since = CASE
                    WHEN soul_v3.agent_cognitive_lifecycle.state IS DISTINCT FROM EXCLUDED.state
                    THEN now()
                    ELSE soul_v3.agent_cognitive_lifecycle.since
                END,
                expires_at = EXCLUDED.expires_at,
                updated_at = now(),
                feature_flag = EXCLUDED.feature_flag
            """,
            state.agent_name,
            state.state,
            state.runtime,
            state.budget_class,
            state.source_event_id,
            state.router_action,
            state.confidence,
            state.reason,
            state.expires_at,
            feature_flag,
        )
        if previous_state != state.state:
            await conn.execute(
                """
                INSERT INTO soul_v3.agent_cognitive_lifecycle_events (
                    agent_name, previous_state, new_state, runtime, budget_class,
                    source_event_id, router_action, confidence, reason, feature_flag
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                state.agent_name,
                previous_state,
                state.state,
                state.runtime,
                state.budget_class,
                state.source_event_id,
                state.router_action,
                state.confidence,
                state.reason,
                feature_flag,
            )
        written += 1
    return written


def write_report(states: list[CognitiveState], path: Path, shadow_rows: int, persisted: bool) -> None:
    lines = [
        "# Cognitive Lifecycle Sync Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Shadow rows read: {shadow_rows}",
        f"States produced: {len(states)}",
        f"Persisted: {persisted}",
        "",
        "## State Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in Counter(s.state for s in states).most_common()],
        "",
        "## Runtime Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in Counter(s.runtime for s in states).most_common()],
        "",
        "## Current States",
        "",
        *[
            f"- `{s.agent_name}`: `{s.state}` / `{s.runtime}` / `{s.budget_class}` "
            f"(event={s.source_event_id}, conf={s.confidence:.2f})"
            for s in states
        ],
        "",
        "## Safety",
        "",
        "- Writes only to `soul_v3.agent_cognitive_lifecycle` and history table.",
        "- Does not modify legacy `soul_v3.agent_lifecycle`.",
        "- Feature flag is `shadow`; no process control is attached.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"cognitive_lifecycle_sync_{args.hours}h_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"report_cognitive_lifecycle_sync_{args.hours}h_{ts}.md"

    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_tables(conn)
        rows = await fetch_shadow_predictions(conn, args.hours, args.limit)
        states = reduce_predictions(rows)
        written = await persist_states(conn, states, feature_flag="shadow") if args.persist else 0
    finally:
        await conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_rows_read": len(rows),
        "states": [asdict(s) for s in states],
        "persisted": args.persist,
        "rows_written": written,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(states, md_path, len(rows), args.persist)

    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    print(f"Shadow rows read: {len(rows)}")
    print(f"States produced: {len(states)}")
    if args.persist:
        print(f"Cognitive states upserted: {written}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--persist", action="store_true", help="Write cognitive lifecycle state in shadow feature flag.")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
