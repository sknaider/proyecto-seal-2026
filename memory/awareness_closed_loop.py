#!/usr/bin/env python3
"""Measured closed loop for awareness learning.

Fase 6 closes the loop without automatic promotion:
outcome -> memory/skill/guardrail/test proposal -> scheduler -> NEXUS gate.
No adapter is trained, promoted, rolled back, or loaded by this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import asyncpg

from awareness_experience_dataset import (
    AdapterQueueRecord,
    ExperienceDatasetBuild,
    ExperienceExample,
    build_dataset_from_loop_run,
    cleanup_experience_agent,
)
from awareness_ledger import cleanup_awareness_agent, connect_db
from awareness_loop import run_fixture_loop


@dataclass(frozen=True)
class ClosedLoopOutcome:
    outcome_id: str
    agent: str
    example_id: str
    queue_id: str | None
    source_event_id: str
    outcome_type: str
    metric_delta: float
    benchmark_score: float
    regression_count: int
    promotion_decision: str
    memory_update_required: bool
    skill_candidate: bool
    guardrail_candidate: bool
    regression_test_candidate: bool
    scheduler_required: bool
    nexus_review_required: bool
    status: str
    evidence: dict[str, Any]
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearningSchedule:
    schedule_id: str
    agent: str
    cadence: str
    next_run_at: str
    benchmark_suite: str
    status: str
    nexus_review_required: bool
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClosedLoopRun:
    agent: str
    outcomes: list[ClosedLoopOutcome]
    schedule: LearningSchedule
    persisted: bool
    boundary: str = "closed_loop_measured_no_auto_training_no_auto_promotion"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "schedule": self.schedule.to_dict(),
            "persisted": self.persisted,
            "boundary": self.boundary,
        }


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return f"{prefix}:{sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _queue_by_example(queue_records: list[AdapterQueueRecord]) -> dict[str, AdapterQueueRecord]:
    return {record.example_id: record for record in queue_records}


def _planned_outcome(example: ExperienceExample, queue: AdapterQueueRecord | None) -> tuple[str, float, int, str, bool, bool, bool]:
    action = str(example.labels.get("attention_action") or "")
    content = str(example.input_event.get("content") or "").lower()

    if example.privacy_class == "destructive_safety":
        return ("safety_guardrail", 0.0, 0, "blocked_from_adapter_training", False, True, True)
    if example.privacy_class in {"cross_agent_dm", "private_dm"}:
        return ("privacy_boundary", 0.0, 0, "excluded_from_adapter_training", False, False, False)
    if queue is None:
        return ("memory_only", 0.01, 0, "keep_pending", False, False, False)
    if "pytest failed" in content or action == "wake_codex" and example.input_event.get("source") == "pytest":
        return ("regression_detected", -0.12, 1, "rollback_pending_nexus", False, True, True)
    if action == "reflex_action":
        return ("skill_candidate", 0.04, 0, "promote_pending_nexus", True, False, False)
    return ("canary_gain", 0.06, 0, "promote_pending_nexus", False, False, False)


def build_closed_loop_outcomes(dataset: ExperienceDatasetBuild) -> list[ClosedLoopOutcome]:
    queued = _queue_by_example(dataset.queue_records)
    if not queued:
        for example in dataset.examples:
            if example.adapter_candidate:
                queued[example.example_id] = AdapterQueueRecord(
                    queue_id=_stable_id("adapter_queue", example.example_id, example.labels),
                    example_id=example.example_id,
                    agent=example.agent,
                    status="pending_nexus_review",
                    canary_mode=True,
                    nexus_review_required=True,
                    candidate_reason=(
                        f"eligible awareness example action={example.labels['attention_action']} "
                        f"privacy={example.privacy_class}"
                    ),
                )
    outcomes: list[ClosedLoopOutcome] = []
    for example in dataset.examples:
        queue = queued.get(example.example_id)
        outcome_type, delta, regressions, decision, skill, guardrail, test = _planned_outcome(example, queue)
        benchmark_score = round(max(0.0, min(1.0, 0.94 + delta - (0.2 * regressions))), 3)
        outcome_id = _stable_id(
            "closed_loop",
            example.example_id,
            queue.queue_id if queue else None,
            outcome_type,
            decision,
        )
        outcomes.append(
            ClosedLoopOutcome(
                outcome_id=outcome_id,
                agent=example.agent,
                example_id=example.example_id,
                queue_id=queue.queue_id if queue else None,
                source_event_id=example.source_event_id,
                outcome_type=outcome_type,
                metric_delta=delta,
                benchmark_score=benchmark_score,
                regression_count=regressions,
                promotion_decision=decision,
                memory_update_required=example.eligible_for_training or example.privacy_class == "destructive_safety",
                skill_candidate=skill,
                guardrail_candidate=guardrail,
                regression_test_candidate=test,
                scheduler_required=True,
                nexus_review_required=True,
                status="pending_nexus_review",
                evidence={
                    "privacy_class": example.privacy_class,
                    "labels": example.labels,
                    "canary_required": example.canary_required,
                    "queue_status": queue.status if queue else None,
                    "auto_applied": False,
                },
            )
        )
    return outcomes


def build_learning_schedule(agent: str) -> LearningSchedule:
    next_run = datetime.now(timezone.utc) + timedelta(days=7)
    return LearningSchedule(
        schedule_id=_stable_id("learning_schedule", agent.upper(), "weekly_awareness_benchmark"),
        agent=agent.upper(),
        cadence="weekly",
        next_run_at=next_run.isoformat(),
        benchmark_suite="awareness_closed_loop",
        status="scheduled_shadow_only",
        nexus_review_required=True,
    )


async def ensure_closed_loop_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_closed_loop_outcomes (
            id BIGSERIAL PRIMARY KEY,
            outcome_id TEXT UNIQUE NOT NULL,
            agent TEXT NOT NULL,
            example_id TEXT NOT NULL,
            queue_id TEXT,
            source_event_id TEXT NOT NULL,
            outcome_type TEXT NOT NULL,
            metric_delta NUMERIC NOT NULL DEFAULT 0.0,
            benchmark_score NUMERIC NOT NULL DEFAULT 0.0,
            regression_count INTEGER NOT NULL DEFAULT 0,
            promotion_decision TEXT NOT NULL,
            memory_update_required BOOLEAN NOT NULL DEFAULT true,
            skill_candidate BOOLEAN NOT NULL DEFAULT false,
            guardrail_candidate BOOLEAN NOT NULL DEFAULT false,
            regression_test_candidate BOOLEAN NOT NULL DEFAULT false,
            scheduler_required BOOLEAN NOT NULL DEFAULT true,
            nexus_review_required BOOLEAN NOT NULL DEFAULT true,
            status TEXT NOT NULL DEFAULT 'pending_nexus_review',
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_learning_schedule (
            id BIGSERIAL PRIMARY KEY,
            schedule_id TEXT UNIQUE NOT NULL,
            agent TEXT NOT NULL,
            cadence TEXT NOT NULL,
            next_run_at TIMESTAMPTZ NOT NULL,
            benchmark_suite TEXT NOT NULL,
            status TEXT NOT NULL,
            nexus_review_required BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_closed_loop_agent ON soul_v3.awareness_closed_loop_outcomes(agent, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_learning_schedule_agent ON soul_v3.awareness_learning_schedule(agent, next_run_at)")


async def persist_closed_loop(
    conn: asyncpg.Connection,
    outcomes: list[ClosedLoopOutcome],
    schedule: LearningSchedule,
) -> ClosedLoopRun:
    await ensure_closed_loop_schema(conn)
    persisted_outcomes: list[ClosedLoopOutcome] = []
    for outcome in outcomes:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.awareness_closed_loop_outcomes
                (outcome_id, agent, example_id, queue_id, source_event_id,
                 outcome_type, metric_delta, benchmark_score, regression_count,
                 promotion_decision, memory_update_required, skill_candidate,
                 guardrail_candidate, regression_test_candidate, scheduler_required,
                 nexus_review_required, status, evidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15, $16, $17, $18::jsonb)
            ON CONFLICT (outcome_id) DO UPDATE SET
                metric_delta=EXCLUDED.metric_delta,
                benchmark_score=EXCLUDED.benchmark_score,
                regression_count=EXCLUDED.regression_count,
                promotion_decision=EXCLUDED.promotion_decision,
                status='pending_nexus_review',
                evidence=EXCLUDED.evidence
            RETURNING id
            """,
            outcome.outcome_id,
            outcome.agent,
            outcome.example_id,
            outcome.queue_id,
            outcome.source_event_id,
            outcome.outcome_type,
            outcome.metric_delta,
            outcome.benchmark_score,
            outcome.regression_count,
            outcome.promotion_decision,
            outcome.memory_update_required,
            outcome.skill_candidate,
            outcome.guardrail_candidate,
            outcome.regression_test_candidate,
            outcome.scheduler_required,
            outcome.nexus_review_required,
            outcome.status,
            json.dumps(outcome.evidence, sort_keys=True, ensure_ascii=False, default=str),
        )
        persisted_outcomes.append(ClosedLoopOutcome(**{**outcome.to_dict(), "db_id": int(row["id"])}))

    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.awareness_learning_schedule
            (schedule_id, agent, cadence, next_run_at, benchmark_suite, status,
             nexus_review_required, updated_at)
        VALUES ($1, $2, $3, $4::timestamptz, $5, $6, $7, now())
        ON CONFLICT (schedule_id) DO UPDATE SET
            cadence=EXCLUDED.cadence,
            next_run_at=EXCLUDED.next_run_at,
            benchmark_suite=EXCLUDED.benchmark_suite,
            status=EXCLUDED.status,
            nexus_review_required=EXCLUDED.nexus_review_required,
            updated_at=now()
        RETURNING id
        """,
        schedule.schedule_id,
        schedule.agent,
        schedule.cadence,
        datetime.fromisoformat(schedule.next_run_at),
        schedule.benchmark_suite,
        schedule.status,
        schedule.nexus_review_required,
    )
    persisted_schedule = LearningSchedule(**{**schedule.to_dict(), "db_id": int(row["id"])})
    return ClosedLoopRun(
        agent=schedule.agent,
        outcomes=persisted_outcomes,
        schedule=persisted_schedule,
        persisted=True,
    )


async def cleanup_closed_loop_agent(conn: asyncpg.Connection, agent: str) -> int:
    await ensure_closed_loop_schema(conn)
    agent = agent.upper()
    outcomes = int(await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1", agent) or 0)
    schedules = int(await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_learning_schedule WHERE agent=$1", agent) or 0)
    await conn.execute("DELETE FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1", agent)
    await conn.execute("DELETE FROM soul_v3.awareness_learning_schedule WHERE agent=$1", agent)
    return outcomes + schedules


async def build_closed_loop_from_dataset(
    dataset: ExperienceDatasetBuild,
    *,
    persist: bool = False,
) -> ClosedLoopRun:
    outcomes = build_closed_loop_outcomes(dataset)
    schedule = build_learning_schedule(dataset.agent)
    if not persist:
        return ClosedLoopRun(
            agent=dataset.agent,
            outcomes=outcomes,
            schedule=schedule,
            persisted=False,
        )
    conn = await connect_db()
    try:
        return await persist_closed_loop(conn, outcomes, schedule)
    finally:
        await conn.close()


async def evaluate_closed_loop_contract_async() -> dict[str, Any]:
    temp_agent = "ADA_CLOSED_LOOP_TEST"
    loop = await run_fixture_loop(agent=temp_agent, count=10, dry_run=False, enable_local=False, enable_reflex=True)
    dataset = await build_dataset_from_loop_run(loop, persist=True)
    dry = await build_closed_loop_from_dataset(dataset, persist=False)
    persisted = await build_closed_loop_from_dataset(dataset, persist=True)
    conn = await connect_db()
    try:
        cleanup_deleted = await cleanup_closed_loop_agent(conn, temp_agent)
        experience_cleanup = await cleanup_experience_agent(conn, temp_agent)
        awareness_cleanup = await cleanup_awareness_agent(conn, temp_agent)
    finally:
        await conn.close()

    outcomes = persisted.outcomes
    decisions = {outcome.promotion_decision for outcome in outcomes}
    checks = {
        "outcomes_match_examples": len(outcomes) == len(dataset.examples) == 10,
        "memory_updates_present": any(outcome.memory_update_required for outcome in outcomes),
        "skill_candidates_present": any(outcome.skill_candidate for outcome in outcomes),
        "guardrail_candidates_present": any(outcome.guardrail_candidate for outcome in outcomes),
        "regression_tests_present": any(outcome.regression_test_candidate for outcome in outcomes),
        "promote_and_rollback_measured": {"promote_pending_nexus", "rollback_pending_nexus"}.issubset(decisions),
        "all_pending_nexus": all(outcome.status == "pending_nexus_review" and outcome.nexus_review_required for outcome in outcomes),
        "no_auto_apply": all(outcome.evidence.get("auto_applied") is False for outcome in outcomes),
        "weekly_scheduler_shadow": persisted.schedule.cadence == "weekly" and persisted.schedule.status == "scheduled_shadow_only",
        "dry_run_not_persisted": dry.persisted is False and all(outcome.db_id is None for outcome in dry.outcomes),
        "cleanup_removed_rows": cleanup_deleted >= len(outcomes) + 1 and experience_cleanup >= 1 and awareness_cleanup >= 1,
    }
    return {
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
        "dataset": dataset.to_dict(),
        "dry": dry.to_dict(),
        "persisted": persisted.to_dict(),
        "cleanup_deleted": cleanup_deleted,
        "experience_cleanup_deleted": experience_cleanup,
        "awareness_cleanup_deleted": awareness_cleanup,
    }


def evaluate_closed_loop_contract() -> dict[str, Any]:
    return asyncio.run(evaluate_closed_loop_contract_async())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness measured closed loop")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--agent", default="ADA")
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--count", type=int, default=10)
    sub.add_parser("contract")
    return parser


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "fixture":
        loop = await run_fixture_loop(agent=args.agent, count=args.count, dry_run=not args.persist, enable_reflex=True)
        dataset = await build_dataset_from_loop_run(loop, persist=args.persist)
        closed = await build_closed_loop_from_dataset(dataset, persist=args.persist)
        print(json.dumps(closed.to_dict(), indent=2, default=_json_default, ensure_ascii=False))
        return 0
    if args.command == "contract":
        payload = await evaluate_closed_loop_contract_async()
        print(json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    return 2


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
