#!/usr/bin/env python3
"""Shadow bridge from router decisions to lifecycle predictions.

This is intentionally non-enforcing. It reads soul_v3.router_decision_ledger
and writes predicted lifecycle transitions to a separate shadow table.
"""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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


@dataclass
class LifecyclePrediction:
    event_id: int
    agent_name: str
    predicted_state: str
    runtime: str
    budget_class: str
    router_action: str
    confidence: float
    reason: str
    source_decided_at: str | None


def runtime_for_agent(agent: str) -> str:
    return "codex" if agent == "ADA" else "claude"


def state_for_budget(budget_class: str, action: str) -> str:
    if budget_class == "emergency":
        return "emergency"
    if budget_class in {"deep", "code_heavy"}:
        return "deep_work"
    if action in {"wake_agent", "wake_team", "delegate_codex"}:
        return "active"
    return "watch"


def predict_from_decision(row: dict[str, Any]) -> list[LifecyclePrediction]:
    action = row["action"]
    if action == "persist_only":
        return []

    budget = row["budget_class"]
    confidence = float(row["confidence"])
    reason = row["reason"]
    event_id = int(row["event_id"])
    decided_at = row.get("decided_at")
    source_decided_at = decided_at.isoformat() if hasattr(decided_at, "isoformat") else str(decided_at) if decided_at else None

    if action == "wake_team":
        return [
            LifecyclePrediction(
                event_id=event_id,
                agent_name=agent,
                predicted_state=state_for_budget(budget, action),
                runtime=runtime_for_agent(agent),
                budget_class=budget,
                router_action=action,
                confidence=confidence,
                reason=reason,
                source_decided_at=source_decided_at,
            )
            for agent in TEAM_AGENTS
        ]

    target = row.get("target_agent")
    if not target:
        return []

    runtime = row.get("runtime") or runtime_for_agent(target)
    return [
        LifecyclePrediction(
            event_id=event_id,
            agent_name=target,
            predicted_state=state_for_budget(budget, action),
            runtime=runtime,
            budget_class=budget,
            router_action=action,
            confidence=confidence,
            reason=reason,
            source_decided_at=source_decided_at,
        )
    ]


async def ensure_shadow_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.router_lifecycle_shadow (
            id BIGSERIAL PRIMARY KEY,
            event_id BIGINT NOT NULL,
            agent_name TEXT NOT NULL,
            predicted_state TEXT NOT NULL,
            runtime TEXT NOT NULL,
            budget_class TEXT NOT NULL,
            router_action TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            source_decided_at TIMESTAMPTZ,
            shadow_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied BOOLEAN NOT NULL DEFAULT false,
            UNIQUE (event_id, agent_name)
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_router_lifecycle_shadow_agent
        ON soul_v3.router_lifecycle_shadow (agent_name, shadow_created_at DESC)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_router_lifecycle_shadow_applied
        ON soul_v3.router_lifecycle_shadow (applied, shadow_created_at DESC)
        """
    )


async def fetch_router_decisions(conn: asyncpg.Connection, hours: int, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT event_id, action, target_agent, runtime, budget_class, confidence,
               reason, decided_at
        FROM soul_v3.router_decision_ledger
        WHERE decided_at >= now() - ($1::int * interval '1 hour')
        ORDER BY decided_at ASC, event_id ASC
        LIMIT $2
        """,
        hours,
        limit,
    )
    return [dict(r) for r in rows]


async def persist_predictions(conn: asyncpg.Connection, predictions: list[LifecyclePrediction]) -> int:
    if not predictions:
        return 0
    rows = [
        (
            p.event_id,
            p.agent_name,
            p.predicted_state,
            p.runtime,
            p.budget_class,
            p.router_action,
            p.confidence,
            p.reason,
            datetime.fromisoformat(p.source_decided_at.replace("Z", "+00:00")) if p.source_decided_at else None,
        )
        for p in predictions
    ]
    await conn.executemany(
        """
        INSERT INTO soul_v3.router_lifecycle_shadow (
            event_id, agent_name, predicted_state, runtime, budget_class,
            router_action, confidence, reason, source_decided_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (event_id, agent_name) DO UPDATE SET
            predicted_state = EXCLUDED.predicted_state,
            runtime = EXCLUDED.runtime,
            budget_class = EXCLUDED.budget_class,
            router_action = EXCLUDED.router_action,
            confidence = EXCLUDED.confidence,
            reason = EXCLUDED.reason,
            source_decided_at = EXCLUDED.source_decided_at,
            shadow_created_at = now(),
            applied = false
        """,
        rows,
    )
    return len(rows)


def write_report(predictions: list[LifecyclePrediction], path: Path, hours: int, source_count: int) -> None:
    state_counts = Counter(p.predicted_state for p in predictions)
    agent_counts = Counter(p.agent_name for p in predictions)
    runtime_counts = Counter(p.runtime for p in predictions)
    action_counts = Counter(p.router_action for p in predictions)

    lines = [
        "# Router Lifecycle Shadow Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Window: last {hours} hours",
        f"Router decisions read: {source_count}",
        f"Lifecycle predictions written: {len(predictions)}",
        "",
        "## State Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in state_counts.most_common()],
        "",
        "## Agent Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in agent_counts.most_common()],
        "",
        "## Runtime Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in runtime_counts.most_common()],
        "",
        "## Router Action Counts",
        "",
        *[f"- `{k}`: {v}" for k, v in action_counts.most_common()],
        "",
        "## Safety",
        "",
        "- Shadow mode only: `soul_v3.agent_lifecycle` is not modified.",
        "- `applied=false` for every row.",
        "- ADA runtime is Codex; sibling runtimes are Claude.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"router_lifecycle_shadow_{args.hours}h_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"report_router_lifecycle_shadow_{args.hours}h_{ts}.md"

    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_shadow_table(conn)
        decisions = await fetch_router_decisions(conn, args.hours, args.limit)
        predictions = [p for row in decisions for p in predict_from_decision(row)]
        if args.persist:
            written = await persist_predictions(conn, predictions)
        else:
            written = 0
    finally:
        await conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hours": args.hours,
        "router_decisions_read": len(decisions),
        "predictions": [asdict(p) for p in predictions],
        "persisted": args.persist,
        "rows_written": written,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(predictions, md_path, args.hours, len(decisions))

    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    print(f"Router decisions read: {len(decisions)}")
    print(f"Lifecycle predictions: {len(predictions)}")
    if args.persist:
        print(f"Shadow rows upserted: {written}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--persist", action="store_true", help="Write predictions to soul_v3.router_lifecycle_shadow.")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
