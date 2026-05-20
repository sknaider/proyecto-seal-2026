#!/usr/bin/env python3
"""Observe natural working-state events emitted by the ADA bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

import asyncpg
from task_lifecycle import connect_db


BRIDGE_ARTIFACT = "messages/ada_codex_remote_bridge.py"
BRIDGE_STAGES = {"turn_started", "final_published", "final_suppressed", "turn_failed"}
HUMAN_SENDERS = {"william", "henry"}


@dataclass(frozen=True)
class BridgeNaturalJournalObservation:
    agent: str
    observed: bool
    natural_event_count: int
    natural_event_claim_allowed: bool
    event_ids: list[int]
    stages: list[str]
    chat_ids: list[int]
    latest_event_at: str | None
    missing: list[str]
    evidence: str


@dataclass(frozen=True)
class BridgeNaturalJournalClosure:
    agent: str
    task_id: int
    allowed_to_close: bool
    closed: bool
    task_status_before: str | None
    task_status_after: str | None
    gam_event_id: int | None
    missing: list[str]
    evidence: str
    observation: BridgeNaturalJournalObservation


def _decode_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def is_bridge_natural_event(row: Any) -> bool:
    action = str(row.get("action") if isinstance(row, dict) else row["action"])
    temporary = bool(row.get("temporary") if isinstance(row, dict) else row["temporary"])
    evidence = _decode_evidence(row.get("evidence") if isinstance(row, dict) else row["evidence"])
    sender = str(evidence.get("sender") or "").lower()
    return (
        action.startswith("bridge:")
        and not temporary
        and evidence.get("artifact") == BRIDGE_ARTIFACT
        and evidence.get("stage") in BRIDGE_STAGES
        and sender in HUMAN_SENDERS
        and bool(evidence.get("chat_id"))
    )


def observation_from_rows(agent: str, rows: list[Any]) -> BridgeNaturalJournalObservation:
    natural_rows = [row for row in rows if is_bridge_natural_event(row)]
    event_ids: list[int] = []
    stages: list[str] = []
    chat_ids: list[int] = []
    latest_event_at = None
    for row in natural_rows:
        evidence = _decode_evidence(row.get("evidence") if isinstance(row, dict) else row["evidence"])
        event_ids.append(int(row.get("id") if isinstance(row, dict) else row["id"]))
        stage = str(evidence.get("stage") or "")
        if stage and stage not in stages:
            stages.append(stage)
        try:
            chat_id = int(evidence.get("chat_id"))
        except (TypeError, ValueError):
            continue
        if chat_id not in chat_ids:
            chat_ids.append(chat_id)
        created_at = row.get("created_at") if isinstance(row, dict) else row["created_at"]
        if created_at is not None:
            latest_event_at = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)

    missing: list[str] = []
    if not natural_rows:
        missing.append("bridge_natural_event:not_observed")
    observed = bool(natural_rows)
    evidence_text = (
        f"bridge_natural_events={len(natural_rows)} "
        f"natural_event_claim_allowed={observed} stages={stages or 'none'}"
    )
    return BridgeNaturalJournalObservation(
        agent=agent,
        observed=observed,
        natural_event_count=len(natural_rows),
        natural_event_claim_allowed=observed,
        event_ids=event_ids,
        stages=stages,
        chat_ids=chat_ids,
        latest_event_at=latest_event_at,
        missing=missing,
        evidence=evidence_text,
    )


async def fetch_bridge_natural_observation(
    conn: asyncpg.Connection,
    agent: str,
    *,
    limit: int = 50,
) -> BridgeNaturalJournalObservation:
    rows = await conn.fetch(
        """
        SELECT id, action, status, evidence, temporary, created_at
        FROM soul_v3.working_state_events
        WHERE agent=$1
          AND temporary=false
          AND action LIKE 'bridge:%'
        ORDER BY id DESC
        LIMIT $2
        """,
        agent,
        limit,
    )
    return observation_from_rows(agent, list(reversed(rows)))


async def wait_for_bridge_natural_observation(
    conn: asyncpg.Connection,
    agent: str,
    *,
    limit: int = 50,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 2.0,
) -> BridgeNaturalJournalObservation:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    interval = max(0.1, poll_interval_seconds)
    while True:
        observation = await fetch_bridge_natural_observation(conn, agent, limit=limit)
        if observation.observed or time.monotonic() >= deadline:
            return observation
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))


async def close_task_if_observed(
    conn: asyncpg.Connection,
    observation: BridgeNaturalJournalObservation,
    *,
    task_id: int,
) -> BridgeNaturalJournalClosure:
    missing: list[str] = []
    if not observation.observed:
        missing.append("bridge_natural_event:not_observed")

    task = await conn.fetchrow(
        """
        SELECT id, agent, title, status, priority, deadline, created_at, completed_at
        FROM soul_v3.agent_tasks
        WHERE id=$1
        """,
        task_id,
    )
    if task is None:
        missing.append("task:not_found")
        return BridgeNaturalJournalClosure(
            agent=observation.agent,
            task_id=task_id,
            allowed_to_close=False,
            closed=False,
            task_status_before=None,
            task_status_after=None,
            gam_event_id=None,
            missing=missing,
            evidence=f"task_id={task_id} closed=False missing={missing}",
            observation=observation,
        )

    status_before = str(task["status"])
    if str(task["agent"]) != observation.agent:
        missing.append("task:agent_mismatch")
    allowed = not missing
    if not allowed:
        return BridgeNaturalJournalClosure(
            agent=observation.agent,
            task_id=task_id,
            allowed_to_close=False,
            closed=False,
            task_status_before=status_before,
            task_status_after=status_before,
            gam_event_id=None,
            missing=missing,
            evidence=f"task_id={task_id} closed=False missing={missing}",
            observation=observation,
        )

    if status_before != "completed":
        await conn.execute(
            """
            UPDATE soul_v3.agent_tasks
            SET status='completed', completed_at=NOW()
            WHERE id=$1 AND agent=$2
            """,
            task_id,
            observation.agent,
        )

    updated_task = await conn.fetchrow(
        """
        SELECT id, agent, title, status, priority, deadline, created_at, completed_at
        FROM soul_v3.agent_tasks
        WHERE id=$1
        """,
        task_id,
    )
    gam_event_id = await _sync_agent_task_gam_event(conn, updated_task) if updated_task else None
    await _close_working_state(conn, observation, task_id=task_id, gam_event_id=gam_event_id)
    status_after = str(updated_task["status"]) if updated_task else None
    return BridgeNaturalJournalClosure(
        agent=observation.agent,
        task_id=task_id,
        allowed_to_close=True,
        closed=status_after == "completed",
        task_status_before=status_before,
        task_status_after=status_after,
        gam_event_id=gam_event_id,
        missing=[],
        evidence=(
            f"task_id={task_id} closed={status_after == 'completed'} "
            f"natural_events={observation.natural_event_count} event_ids={observation.event_ids}"
        ),
        observation=observation,
    )


async def watch_and_close_task(
    conn: asyncpg.Connection,
    agent: str,
    *,
    task_id: int,
    limit: int = 50,
    poll_interval_seconds: float = 5.0,
    heartbeat_seconds: float = 300.0,
) -> BridgeNaturalJournalClosure:
    interval = max(0.1, poll_interval_seconds)
    heartbeat = max(interval, heartbeat_seconds)
    next_heartbeat = time.monotonic()
    while True:
        observation = await fetch_bridge_natural_observation(conn, agent, limit=limit)
        closure = await close_task_if_observed(conn, observation, task_id=task_id)
        if closure.closed:
            print(json.dumps(asdict(closure), indent=2, ensure_ascii=False), flush=True)
            return closure
        now = time.monotonic()
        if now >= next_heartbeat:
            print(watcher_heartbeat_line(closure), flush=True)
            next_heartbeat = now + heartbeat
        await asyncio.sleep(interval)


def watcher_heartbeat_line(closure: BridgeNaturalJournalClosure) -> str:
    observation = closure.observation
    return (
        "bridge_natural_close_watch "
        f"agent={closure.agent} task_id={closure.task_id} closed={closure.closed} "
        f"allowed_to_close={closure.allowed_to_close} observed={observation.observed} "
        f"natural_events={observation.natural_event_count} "
        f"task_status={closure.task_status_after} missing={','.join(closure.missing) or 'none'}"
    )


async def _sync_agent_task_gam_event(conn: asyncpg.Connection, task: Any) -> int | None:
    existing = await conn.fetchval(
        """
        SELECT id FROM soul_v3.gam_event_graph
        WHERE agent=$1
          AND metadata->>'source'='agent_task'
          AND (metadata->>'task_id')::bigint=$2
        ORDER BY id ASC
        LIMIT 1
        """,
        task["agent"],
        task["id"],
    )
    if existing is None:
        return None
    metadata = {
        "source": "agent_task",
        "task_id": task["id"],
        "status": task["status"],
        "priority": task["priority"],
        "deadline": task["deadline"].isoformat() if task["deadline"] else None,
        "created_at": task["created_at"].isoformat() if task["created_at"] else None,
        "completed_at": task["completed_at"].isoformat() if task["completed_at"] else None,
    }
    await conn.execute(
        """
        UPDATE soul_v3.gam_event_graph
        SET event=$1, event_timestamp=COALESCE($2, event_timestamp), metadata=$3::jsonb
        WHERE id=$4
        """,
        task["title"],
        task["created_at"],
        json.dumps(metadata, sort_keys=True),
        existing,
    )
    return int(existing)


async def _close_working_state(
    conn: asyncpg.Connection,
    observation: BridgeNaturalJournalObservation,
    *,
    task_id: int,
    gam_event_id: int | None,
) -> None:
    await conn.execute(
        """
        UPDATE soul_v3.working_state
        SET agent_state='STANDBY',
            pending_validations=ARRAY[]::text[],
            technical_state=$1,
            last_intention=$2,
            updated_at=NOW()
        WHERE agent=$3
        """,
        (
            "Sprint 11 closed: first natural bridge event observed; "
            f"task {task_id} completed; GAM {gam_event_id}; event_ids={observation.event_ids}"
        ),
        "Sprint 11 closed. Continue next AGI plan sprint only with explicit evidence gates.",
        observation.agent,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe natural ADA bridge working-state events")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--limit", type=int, default=50)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("observe")
    sub.add_parser("require-observed")
    wait_parser = sub.add_parser("wait-observed")
    wait_parser.add_argument("--timeout-seconds", type=float, default=30.0)
    wait_parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    close_parser = sub.add_parser("close-task")
    close_parser.add_argument("--task-id", type=int, required=True)
    close_parser.add_argument("--timeout-seconds", type=float, default=0.0)
    close_parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    watch_parser = sub.add_parser("watch-close-task")
    watch_parser.add_argument("--task-id", type=int, required=True)
    watch_parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    watch_parser.add_argument("--heartbeat-seconds", type=float, default=300.0)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        if args.command == "watch-close-task":
            closure = await watch_and_close_task(
                conn,
                args.agent,
                task_id=args.task_id,
                limit=args.limit,
                poll_interval_seconds=args.poll_interval_seconds,
                heartbeat_seconds=args.heartbeat_seconds,
            )
            return 0 if closure.closed else 2
        if args.command in {"wait-observed", "close-task"}:
            observation = await wait_for_bridge_natural_observation(
                conn,
                args.agent,
                limit=args.limit,
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
            )
        else:
            observation = await fetch_bridge_natural_observation(conn, args.agent, limit=args.limit)
        if args.command == "close-task":
            closure = await close_task_if_observed(conn, observation, task_id=args.task_id)
            print(json.dumps(asdict(closure), indent=2, ensure_ascii=False))
            return 0 if closure.closed else 2
        print(json.dumps(asdict(observation), indent=2, ensure_ascii=False))
        if args.command == "observe":
            return 0
        if args.command in {"require-observed", "wait-observed"}:
            return 0 if observation.observed else 2
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
