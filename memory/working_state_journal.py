#!/usr/bin/env python3
"""Working-state event journal for SEAL Sprint 3.

This module keeps the automatic state update path small and auditable: tools or
tasks append operational events, then a projection converts those events into a
working-state payload. Daemon hooks can call this later without changing the
projection rules.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
from task_lifecycle import connect_db


DEFAULT_AGENT = "ADA"
VALID_EVENT_TYPES = {"intent", "tool_call", "tool_result", "verification", "decision"}
VALID_STATUSES = {"planned", "running", "success", "failed", "blocked", "partial"}


@dataclass(frozen=True)
class WorkingStateEvent:
    event_type: str
    action: str
    result: str
    status: str
    evidence: dict[str, Any]
    task_id: int | None = None
    temporary: bool = False


@dataclass(frozen=True)
class WorkingStateProjection:
    agent: str
    event_count: int
    last_action: str
    last_result: str
    current_task: str
    attempted_strategies: list[str]
    failed_paths: list[str]
    evidence_items: list[str]
    next_step: str
    risk_level: str
    score: int
    missing: list[str]


def validate_event(event: WorkingStateEvent) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if event.event_type not in VALID_EVENT_TYPES:
        missing.append("event_type")
    if not event.action.strip():
        missing.append("action")
    if event.status not in VALID_STATUSES:
        missing.append("status")
    if event.event_type in {"tool_result", "verification"} and not event.result.strip():
        missing.append("result")
    if event.status in {"success", "failed", "blocked", "partial"} and not event.evidence:
        missing.append("evidence")
    return not missing, missing


async def ensure_schema(conn: asyncpg.Connection) -> None:
    # Runtime daemons use least-privilege roles with no CREATE.  The schema is
    # provisioned by migrations; avoid issuing DDL on every journal append when
    # all canonical objects already exist.
    ready = await conn.fetchval(
        """SELECT to_regclass('soul_v3.working_state_events') IS NOT NULL
                  AND to_regclass('soul_v3.idx_working_state_events_agent') IS NOT NULL
                  AND to_regclass('soul_v3.idx_working_state_events_task') IS NOT NULL"""
    )
    if ready:
        return
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.working_state_events (
            id          BIGSERIAL PRIMARY KEY,
            agent       TEXT        NOT NULL,
            task_id     BIGINT      NULL REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
            event_type  TEXT        NOT NULL,
            action      TEXT        NOT NULL,
            result      TEXT        NOT NULL DEFAULT '',
            status      TEXT        NOT NULL,
            evidence    JSONB       NOT NULL DEFAULT '{}',
            temporary   BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_working_state_events_agent ON soul_v3.working_state_events(agent, id DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_working_state_events_task ON soul_v3.working_state_events(task_id)")


async def record_event(conn: asyncpg.Connection, agent: str, event: WorkingStateEvent) -> int:
    valid, missing = validate_event(event)
    if not valid:
        raise ValueError(f"Invalid working state event: {missing}")
    await ensure_schema(conn)
    return int(
        await conn.fetchval(
            """
            INSERT INTO soul_v3.working_state_events
                (agent, task_id, event_type, action, result, status, evidence, temporary)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            RETURNING id
            """,
            agent,
            event.task_id,
            event.event_type,
            event.action,
            event.result,
            event.status,
            json.dumps(event.evidence, sort_keys=True),
            event.temporary,
        )
    )


async def fetch_events(conn: asyncpg.Connection, agent: str, *, task_id: int | None = None, limit: int = 20) -> list[WorkingStateEvent]:
    await ensure_schema(conn)
    rows = await conn.fetch(
        """
        SELECT task_id, event_type, action, result, status, evidence, temporary
        FROM soul_v3.working_state_events
        WHERE agent=$1
          AND ($2::bigint IS NULL OR task_id=$2)
        ORDER BY id DESC
        LIMIT $3
        """,
        agent,
        task_id,
        limit,
    )
    events: list[WorkingStateEvent] = []
    for row in reversed(rows):
        evidence = row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        events.append(
            WorkingStateEvent(
                task_id=row["task_id"],
                event_type=row["event_type"],
                action=row["action"],
                result=row["result"],
                status=row["status"],
                evidence=dict(evidence or {}),
                temporary=bool(row["temporary"]),
            )
        )
    return events


def project_events(agent: str, events: list[WorkingStateEvent]) -> WorkingStateProjection:
    missing: list[str] = []
    if not events:
        missing.append("events:not_found")

    attempted = list(dict.fromkeys(event.action for event in events if event.event_type in {"tool_call", "tool_result", "verification"}))
    failed = list(dict.fromkeys(event.action for event in events if event.status in {"failed", "blocked"}))
    evidence_items = []
    for event in events:
        command = event.evidence.get("command")
        artifact = event.evidence.get("artifact")
        output = event.evidence.get("output")
        for item in (command, artifact, output):
            if item and item not in evidence_items:
                evidence_items.append(str(item))

    last = events[-1] if events else WorkingStateEvent("decision", "", "", "blocked", {})
    if not attempted:
        missing.append("attempted_strategies")
    if not evidence_items:
        missing.append("evidence_items")
    if last.status not in {"success", "partial"}:
        missing.append("last_result:not_success")

    risk_level = "medium" if failed or last.status in {"failed", "blocked", "partial"} else "low"
    score = 100 if not missing else max(0, 100 - len(set(missing)) * 20)
    next_step = "continue_with_evidence" if last.status in {"success", "partial"} else "investigate_blocker"

    return WorkingStateProjection(
        agent=agent,
        event_count=len(events),
        last_action=last.action,
        last_result=last.result,
        current_task=events[0].action if events else "",
        attempted_strategies=attempted,
        failed_paths=failed,
        evidence_items=evidence_items,
        next_step=next_step,
        risk_level=risk_level,
        score=score,
        missing=sorted(set(missing)),
    )


async def project_agent(conn: asyncpg.Connection, agent: str, *, task_id: int | None = None, limit: int = 20) -> WorkingStateProjection:
    return project_events(agent, await fetch_events(conn, agent, task_id=task_id, limit=limit))


async def apply_projection(conn: asyncpg.Connection, projection: WorkingStateProjection) -> None:
    state_patch = {
        "working_state_journal": {
            "last_action": projection.last_action,
            "last_result": projection.last_result,
            "current_task": projection.current_task,
            "evidence_items": projection.evidence_items,
            "next_step": projection.next_step,
            "event_count": projection.event_count,
            "updated_by": "memory/working_state_journal.py",
        }
    }
    await conn.execute(
        """
        INSERT INTO soul_v3.working_state (
            agent, task_name, step, total_steps, description,
            active_hypotheses, discarded_paths, current_constraints,
            pending_validations, risk_level, agent_state, technical_state,
            last_intention, updated_at, state
        )
        VALUES (
            $1, $2, 3, 4, $3,
            $4::text[], $5::text[], $6::text[],
            $7::text[], $8, $9, $10,
            $11, NOW(), $12::jsonb
        )
        ON CONFLICT (agent) DO UPDATE SET
            task_name = EXCLUDED.task_name,
            step = EXCLUDED.step,
            total_steps = EXCLUDED.total_steps,
            description = EXCLUDED.description,
            active_hypotheses = EXCLUDED.active_hypotheses,
            discarded_paths = EXCLUDED.discarded_paths,
            current_constraints = EXCLUDED.current_constraints,
            pending_validations = EXCLUDED.pending_validations,
            risk_level = EXCLUDED.risk_level,
            agent_state = EXCLUDED.agent_state,
            technical_state = EXCLUDED.technical_state,
            last_intention = EXCLUDED.last_intention,
            updated_at = NOW(),
            state = COALESCE(soul_v3.working_state.state, '{}'::jsonb) || EXCLUDED.state
        """,
        projection.agent,
        "Sprint 3 — Working State Journal",
        "Project tool/task events into working_state without relying on chat memory.",
        ["Working state can be derived from persisted events"],
        projection.failed_paths,
        ["No AGI claim without repeated evidence and NEXUS review"],
        [] if projection.score >= 90 else projection.missing,
        projection.risk_level,
        "STANDBY" if projection.score >= 90 else "BLOCKED",
        f"working_state_journal score={projection.score} events={projection.event_count}",
        projection.next_step,
        json.dumps(state_patch, sort_keys=True),
    )


async def cleanup_temporary_events(conn: asyncpg.Connection, agent: str) -> int:
    return int(
        await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.working_state_events
                WHERE agent=$1 AND temporary=true
                RETURNING id
            )
            SELECT COUNT(*) FROM deleted
            """,
            agent,
        )
    )


def default_events(*, temporary: bool = False) -> list[WorkingStateEvent]:
    marker = int(time.time() * 1000)
    return [
        WorkingStateEvent("intent", f"working_state_journal_sprint3_{marker}", "intent recorded", "planned", {"artifact": "memory/working_state_journal.py"}, temporary=temporary),
        WorkingStateEvent("tool_call", "run failing probe", "probe started", "running", {"command": "pytest failing probe"}, temporary=temporary),
        WorkingStateEvent("tool_result", "run failing probe", "probe failed and became failed_path", "failed", {"command": "pytest failing probe", "output": "failed", "artifact": "test_probe.py"}, temporary=temporary),
        WorkingStateEvent("tool_call", "run fixed probe", "fix started", "running", {"command": "pytest fixed probe"}, temporary=temporary),
        WorkingStateEvent("verification", "run fixed probe", "fixed probe passed", "success", {"command": "pytest fixed probe", "output": "passed", "artifact": "memory/test_working_state_journal.py"}, temporary=temporary),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL working-state event journal")
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-schema")
    record = sub.add_parser("record-default")
    record.add_argument("--temporary", action="store_true")
    sub.add_parser("project")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        if args.command == "init-schema":
            await ensure_schema(conn)
            print("schema_ok soul_v3.working_state_events")
            return 0
        if args.command == "record-default":
            ids = [await record_event(conn, args.agent, event) for event in default_events(temporary=args.temporary)]
            projection = await project_agent(conn, args.agent)
            print(json.dumps({"event_ids": ids, "projection": asdict(projection)}, indent=2, ensure_ascii=False))
            return 0 if projection.score >= 90 else 2
        if args.command == "project":
            projection = await project_agent(conn, args.agent)
            print(json.dumps(asdict(projection), indent=2, ensure_ascii=False))
            return 0 if projection.score >= 90 else 2
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
