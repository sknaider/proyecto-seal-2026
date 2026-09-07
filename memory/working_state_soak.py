#!/usr/bin/env python3
"""Post-activation soak metrics for the working-state hook."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg

from task_lifecycle import connect_db
from working_state_hook import ingest_hook_payload
from working_state_journal import WorkingStateEvent, cleanup_temporary_events, project_events


DEFAULT_AGENT = "ADA"


@dataclass(frozen=True)
class SoakMetrics:
    agent: str
    total_events: int
    success_events: int
    failed_events: int
    blocked_events: int
    temporary_events: int
    failure_rate: float
    success_rate: float
    projection_score: int
    projection_next_step: str
    latest_event_at: str | None
    event_types: list[str]
    evidence_items: list[str]
    missing: list[str]


@dataclass(frozen=True)
class ProductionReadiness:
    agent: str
    ready_for_real_soak: bool
    real_soak_claim_allowed: bool
    real_event_count: int
    synthetic_supported: bool
    reason: str
    missing: list[str]
    evidence: str


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 3)


async def collect_soak_metrics(
    conn: asyncpg.Connection,
    agent: str,
    *,
    limit: int = 50,
    include_temporary: bool = False,
) -> SoakMetrics:
    rows = await conn.fetch(
        """
        SELECT event_type, status, evidence, temporary, created_at
        FROM soul_v3.working_state_events
        WHERE agent=$1
          AND ($2::boolean OR temporary=false)
        ORDER BY id DESC
        LIMIT $3
        """,
        agent,
        include_temporary,
        limit,
    )
    ordered = list(reversed(rows))
    events = [
        WorkingStateEvent(
            event_type=str(row["event_type"]),
            action="metrics_snapshot",
            result=str(row["status"]),
            status=str(row["status"]),
            evidence=dict(json.loads(row["evidence"]) if isinstance(row["evidence"], str) else (row["evidence"] or {})),
            temporary=bool(row["temporary"]),
        )
        for row in ordered
    ]
    total = len(ordered)
    success = sum(1 for row in ordered if row["status"] == "success")
    failed = sum(1 for row in ordered if row["status"] == "failed")
    blocked = sum(1 for row in ordered if row["status"] == "blocked")
    temporary = sum(1 for row in ordered if row["temporary"])
    event_types = list(dict.fromkeys(str(row["event_type"]) for row in ordered))
    latest = ordered[-1]["created_at"].isoformat() if ordered else None
    projection = project_events(agent, events)

    evidence_items: list[str] = []
    for row in ordered:
        evidence = row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        for key in ("command", "artifact", "output"):
            value = (evidence or {}).get(key)
            if value and value not in evidence_items:
                evidence_items.append(str(value))

    missing: list[str] = []
    if total == 0:
        missing.append("events:not_found")
    if success == 0:
        missing.append("success_events")
    if not evidence_items:
        missing.append("evidence_items")
    if projection.score < 90:
        missing.append("projection_score")

    return SoakMetrics(
        agent=agent,
        total_events=total,
        success_events=success,
        failed_events=failed,
        blocked_events=blocked,
        temporary_events=temporary,
        failure_rate=_rate(failed + blocked, total),
        success_rate=_rate(success, total),
        projection_score=projection.score,
        projection_next_step=projection.next_step,
        latest_event_at=latest,
        event_types=event_types,
        evidence_items=evidence_items,
        missing=sorted(set(missing + projection.missing)),
    )


def assess_production_readiness(metrics: SoakMetrics, *, synthetic_supported: bool) -> ProductionReadiness:
    missing = set(metrics.missing)
    ready_for_real_soak = metrics.total_events > 0 and metrics.temporary_events == 0 and metrics.projection_score >= 90 and synthetic_supported
    real_soak_claim_allowed = ready_for_real_soak
    if metrics.total_events == 0:
        missing.add("real_events:not_observed")
    if metrics.temporary_events:
        missing.add("real_events:temporary_only")
    if not synthetic_supported:
        missing.add("synthetic_support")

    if ready_for_real_soak:
        reason = "real_events_observed"
    elif metrics.total_events == 0:
        reason = "real_events_not_observed"
    elif metrics.temporary_events:
        reason = "temporary_events_do_not_count_as_real_soak"
    else:
        reason = "readiness_gates_missing"

    evidence = (
        f"real_events={metrics.total_events} temporary_events={metrics.temporary_events} "
        f"synthetic_supported={synthetic_supported} real_soak_claim_allowed={real_soak_claim_allowed}"
    )
    return ProductionReadiness(
        agent=metrics.agent,
        ready_for_real_soak=ready_for_real_soak,
        real_soak_claim_allowed=real_soak_claim_allowed,
        real_event_count=metrics.total_events,
        synthetic_supported=synthetic_supported,
        reason=reason,
        missing=sorted(missing),
        evidence=evidence,
    )


def synthetic_soak_payloads() -> list[dict[str, Any]]:
    return [
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q memory/test_working_state_soak.py"},
            "tool_response": {"exit_code": 1, "stderr": "failed"},
        },
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "memory/working_state_soak.py"},
            "tool_response": {"success": True, "output": "updated"},
        },
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q memory/test_working_state_soak.py"},
            "tool_response": {"exit_code": 0, "stdout": "passed"},
        },
        {
            "hook_event_name": "TaskCompleted",
            "task_id": "sprint6-soak",
            "task_subject": "Sprint 6 working state hook soak",
            "result": "soak metrics verified",
        },
    ]


def live_probe_payload(marker: str) -> dict[str, Any]:
    command = f"seal-working-state-live-probe {marker}"
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {
            "exit_code": 0,
            "stdout": f"live_capture marker={marker} passed",
        },
    }


async def run_synthetic_soak(conn: asyncpg.Connection, agent: str) -> tuple[list[int], SoakMetrics, int]:
    event_ids: list[int] = []
    for payload in synthetic_soak_payloads():
        result = await ingest_hook_payload(payload, agent=agent, temporary=True, apply=False)
        if result.get("event_id"):
            event_ids.append(int(result["event_id"]))
    metrics = await collect_soak_metrics(conn, agent, include_temporary=True)
    cleanup_deleted = await cleanup_temporary_events(conn, agent)
    return event_ids, metrics, cleanup_deleted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Working-state hook soak metrics")
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--include-temporary", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("metrics")
    sub.add_parser("readiness")
    sub.add_parser("synthetic-soak")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        if args.command == "metrics":
            metrics = await collect_soak_metrics(conn, args.agent, include_temporary=args.include_temporary)
            print(json.dumps(asdict(metrics), indent=2, ensure_ascii=False))
            return 0 if not metrics.missing else 2
        if args.command == "readiness":
            metrics = await collect_soak_metrics(conn, args.agent, include_temporary=args.include_temporary)
            readiness = assess_production_readiness(metrics, synthetic_supported=True)
            print(json.dumps(asdict(readiness), indent=2, ensure_ascii=False))
            return 0 if readiness.real_soak_claim_allowed else 2
        if args.command == "synthetic-soak":
            agent = f"{args.agent[:3]}_SK{int(time.time() * 1000) % 100000}"
            event_ids, metrics, cleanup_deleted = await run_synthetic_soak(conn, agent)
            print(json.dumps({"agent": agent, "event_ids": event_ids, "metrics": asdict(metrics), "cleanup_deleted": cleanup_deleted}, indent=2, ensure_ascii=False))
            return 0 if metrics.projection_score >= 90 and cleanup_deleted == len(event_ids) else 2
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
