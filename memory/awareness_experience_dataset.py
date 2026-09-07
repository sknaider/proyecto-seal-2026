#!/usr/bin/env python3
"""Experience dataset and adapter queue for awareness decisions.

Fase 5 converts evaluated awareness loop items into training candidates. It is
proposal-only: private/cross-agent DM content is filtered out, destructive
events become safety-regression examples only, and every adapter candidate stays
pending NEXUS review with canary mode required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

import asyncpg

from awareness_ledger import connect_db
from awareness_loop import AwarenessLoopRun, run_fixture_loop


TRAINABLE_ACTIONS = {"ignore", "store_only", "local_reflect", "reflex_action", "wake_codex"}
EXCLUDED_PRIVACY_CLASSES = {"cross_agent_dm", "private_dm", "destructive_safety"}


@dataclass(frozen=True)
class ExperienceExample:
    example_id: str
    agent: str
    source_event_id: str
    source_tick_id: str | None
    privacy_class: str
    eligible_for_training: bool
    adapter_candidate: bool
    canary_required: bool
    nexus_review_required: bool
    score: float
    input_event: dict[str, Any]
    decision: dict[str, Any]
    labels: dict[str, Any]
    exclusion_reason: str | None = None
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterQueueRecord:
    queue_id: str
    example_id: str
    agent: str
    status: str
    canary_mode: bool
    nexus_review_required: bool
    candidate_reason: str
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperienceDatasetBuild:
    agent: str
    examples: list[ExperienceExample]
    queue_records: list[AdapterQueueRecord]
    persisted: bool
    boundary: str = "dataset_and_adapter_queue_proposal_only_no_training_no_promotion"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "examples": [example.to_dict() for example in self.examples],
            "queue_records": [record.to_dict() for record in self.queue_records],
            "persisted": self.persisted,
            "boundary": self.boundary,
        }


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return f"{prefix}:{sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def classify_privacy(event: dict[str, Any], decision: dict[str, Any]) -> tuple[str, str | None]:
    metadata = event.get("metadata") or {}
    channel = str(event.get("channel") or "")
    raw_channel = str(metadata.get("raw_channel") or channel)

    if metadata.get("redacted") or event.get("kind") == "privacy_boundary":
        return "cross_agent_dm", "cross-agent DM is redacted and never trainable"
    if metadata.get("destructive_command") or decision.get("risk_level") == "critical":
        return "destructive_safety", "destructive command becomes safety regression only"
    if raw_channel.startswith("dm:"):
        return "private_dm", "William DM is not used for adapter training without explicit policy"
    if channel == "web_chat":
        return "public_team", None
    if channel == "system":
        return "system_event", None
    return "internal_event", None


def _score_from_item(item: dict[str, Any]) -> float:
    tick = item.get("tick") or {}
    score = tick.get("score")
    if score is None:
        decision = item.get("decision") or {}
        return 0.0 if decision.get("blocked") else 1.0
    return float(score)


def example_from_loop_item(agent: str, item: dict[str, Any]) -> ExperienceExample:
    event = item["event"]
    decision = item["decision"]
    tick = item.get("tick") or {}
    privacy_class, exclusion_reason = classify_privacy(event, decision)
    score = _score_from_item(item)
    action = str(decision.get("action") or "")
    blocked = bool(decision.get("blocked"))
    eligible = (
        privacy_class not in EXCLUDED_PRIVACY_CLASSES
        and action in TRAINABLE_ACTIONS
        and score >= 0.9
        and not blocked
    )
    adapter_candidate = eligible and action != "ignore"
    labels = {
        "attention_action": action,
        "budget_class": decision.get("budget_class"),
        "risk_level": decision.get("risk_level"),
        "should_respond": decision.get("should_respond"),
        "blocked": blocked,
        "requires_confirmation": decision.get("requires_confirmation"),
        "target_runtime": decision.get("target_runtime"),
        "response_channel": decision.get("response_channel"),
    }
    example_id = _stable_id(
        "experience",
        agent.upper(),
        event.get("event_id"),
        tick.get("tick_id"),
        labels,
    )
    return ExperienceExample(
        example_id=example_id,
        agent=agent.upper(),
        source_event_id=str(event.get("event_id") or ""),
        source_tick_id=tick.get("tick_id"),
        privacy_class=privacy_class,
        eligible_for_training=eligible,
        adapter_candidate=adapter_candidate,
        canary_required=True,
        nexus_review_required=True,
        score=score,
        input_event=event,
        decision=decision,
        labels=labels,
        exclusion_reason=exclusion_reason,
    )


def build_examples_from_loop_run(run: AwarenessLoopRun | dict[str, Any]) -> list[ExperienceExample]:
    payload = run.to_dict() if isinstance(run, AwarenessLoopRun) else run
    agent = str(payload.get("agent") or "ADA").upper()
    return [example_from_loop_item(agent, item) for item in payload.get("items", [])]


def select_adapter_candidates(examples: list[ExperienceExample]) -> list[ExperienceExample]:
    return [example for example in examples if example.adapter_candidate]


async def ensure_experience_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_experience_examples (
            id BIGSERIAL PRIMARY KEY,
            example_id TEXT UNIQUE NOT NULL,
            agent TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            source_tick_id TEXT,
            privacy_class TEXT NOT NULL,
            eligible_for_training BOOLEAN NOT NULL DEFAULT false,
            adapter_candidate BOOLEAN NOT NULL DEFAULT false,
            canary_required BOOLEAN NOT NULL DEFAULT true,
            nexus_review_required BOOLEAN NOT NULL DEFAULT true,
            score NUMERIC NOT NULL DEFAULT 0.0,
            input_event JSONB NOT NULL DEFAULT '{}'::jsonb,
            decision JSONB NOT NULL DEFAULT '{}'::jsonb,
            labels JSONB NOT NULL DEFAULT '{}'::jsonb,
            exclusion_reason TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_adapter_queue (
            id BIGSERIAL PRIMARY KEY,
            queue_id TEXT UNIQUE NOT NULL,
            example_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_nexus_review',
            canary_mode BOOLEAN NOT NULL DEFAULT true,
            nexus_review_required BOOLEAN NOT NULL DEFAULT true,
            candidate_reason TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_examples_agent ON soul_v3.awareness_experience_examples(agent, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_awareness_queue_agent ON soul_v3.awareness_adapter_queue(agent, created_at DESC)")


async def persist_examples(conn: asyncpg.Connection, examples: list[ExperienceExample]) -> list[ExperienceExample]:
    await ensure_experience_schema(conn)
    persisted: list[ExperienceExample] = []
    for example in examples:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.awareness_experience_examples
                (example_id, agent, source_event_id, source_tick_id, privacy_class,
                 eligible_for_training, adapter_candidate, canary_required,
                 nexus_review_required, score, input_event, decision, labels,
                 exclusion_reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11::jsonb, $12::jsonb, $13::jsonb, $14)
            ON CONFLICT (example_id) DO UPDATE SET
                eligible_for_training=EXCLUDED.eligible_for_training,
                adapter_candidate=EXCLUDED.adapter_candidate,
                canary_required=EXCLUDED.canary_required,
                nexus_review_required=EXCLUDED.nexus_review_required,
                score=EXCLUDED.score,
                labels=EXCLUDED.labels,
                exclusion_reason=EXCLUDED.exclusion_reason
            RETURNING id
            """,
            example.example_id,
            example.agent,
            example.source_event_id,
            example.source_tick_id,
            example.privacy_class,
            example.eligible_for_training,
            example.adapter_candidate,
            example.canary_required,
            example.nexus_review_required,
            example.score,
            json.dumps(example.input_event, sort_keys=True, ensure_ascii=False, default=str),
            json.dumps(example.decision, sort_keys=True, ensure_ascii=False, default=str),
            json.dumps(example.labels, sort_keys=True, ensure_ascii=False, default=str),
            example.exclusion_reason,
        )
        persisted.append(ExperienceExample(**{**example.to_dict(), "db_id": int(row["id"])}))
    return persisted


async def enqueue_adapter_candidates(
    conn: asyncpg.Connection,
    examples: list[ExperienceExample],
) -> list[AdapterQueueRecord]:
    await ensure_experience_schema(conn)
    records: list[AdapterQueueRecord] = []
    for example in select_adapter_candidates(examples):
        queue_id = _stable_id("adapter_queue", example.example_id, example.labels)
        reason = f"eligible awareness example action={example.labels['attention_action']} privacy={example.privacy_class}"
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.awareness_adapter_queue
                (queue_id, example_id, agent, status, canary_mode,
                 nexus_review_required, candidate_reason)
            VALUES ($1, $2, $3, 'pending_nexus_review', true, true, $4)
            ON CONFLICT (queue_id) DO UPDATE SET
                status='pending_nexus_review',
                canary_mode=true,
                nexus_review_required=true,
                candidate_reason=EXCLUDED.candidate_reason
            RETURNING id
            """,
            queue_id,
            example.example_id,
            example.agent,
            reason,
        )
        records.append(
            AdapterQueueRecord(
                queue_id=queue_id,
                example_id=example.example_id,
                agent=example.agent,
                status="pending_nexus_review",
                canary_mode=True,
                nexus_review_required=True,
                candidate_reason=reason,
                db_id=int(row["id"]),
            )
        )
    return records


async def cleanup_experience_agent(conn: asyncpg.Connection, agent: str) -> int:
    await ensure_experience_schema(conn)
    agent = agent.upper()
    examples = int(await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_experience_examples WHERE agent=$1", agent) or 0)
    queued = int(await conn.fetchval("SELECT COUNT(*) FROM soul_v3.awareness_adapter_queue WHERE agent=$1", agent) or 0)
    await conn.execute("DELETE FROM soul_v3.awareness_adapter_queue WHERE agent=$1", agent)
    await conn.execute("DELETE FROM soul_v3.awareness_experience_examples WHERE agent=$1", agent)
    return examples + queued


async def build_dataset_from_loop_run(
    run: AwarenessLoopRun | dict[str, Any],
    *,
    persist: bool = False,
) -> ExperienceDatasetBuild:
    examples = build_examples_from_loop_run(run)
    queue_records: list[AdapterQueueRecord] = []
    if persist:
        conn = await connect_db()
        try:
            examples = await persist_examples(conn, examples)
            queue_records = await enqueue_adapter_candidates(conn, examples)
        finally:
            await conn.close()
    return ExperienceDatasetBuild(
        agent=str((run.to_dict() if isinstance(run, AwarenessLoopRun) else run).get("agent") or "ADA").upper(),
        examples=examples,
        queue_records=queue_records,
        persisted=persist,
    )


async def evaluate_experience_dataset_contract_async() -> dict[str, Any]:
    temp_agent = "ADA_EXPERIENCE_TEST"
    loop = await run_fixture_loop(agent=temp_agent, count=10, dry_run=False, enable_local=False, enable_reflex=True)
    dry = await build_dataset_from_loop_run(loop, persist=False)
    persisted = await build_dataset_from_loop_run(loop, persist=True)
    conn = await connect_db()
    try:
        cleanup_deleted = await cleanup_experience_agent(conn, temp_agent)
        from awareness_ledger import cleanup_awareness_agent

        awareness_cleanup = await cleanup_awareness_agent(conn, temp_agent)
    finally:
        await conn.close()

    examples = persisted.examples
    queued = persisted.queue_records
    privacy_classes = {example.privacy_class for example in examples}
    checks = {
        "examples_match_loop_items": len(examples) == loop.processed == 10,
        "privacy_filters_present": {"cross_agent_dm", "private_dm", "destructive_safety"}.issubset(privacy_classes),
        "private_examples_not_trainable": all(
            not example.eligible_for_training
            for example in examples
            if example.privacy_class in EXCLUDED_PRIVACY_CLASSES
        ),
        "public_or_system_candidates_exist": len(queued) >= 3,
        "queue_requires_nexus": all(record.status == "pending_nexus_review" and record.nexus_review_required for record in queued),
        "canary_required": all(record.canary_mode for record in queued) and all(example.canary_required for example in examples),
        "no_auto_promotion": all(record.status != "approved" for record in queued),
        "dry_run_no_queue_records": dry.queue_records == [] and dry.persisted is False,
        "cleanup_removed_rows": cleanup_deleted >= len(examples) + len(queued),
    }
    return {
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
        "loop": loop.to_dict(),
        "dry": dry.to_dict(),
        "persisted": persisted.to_dict(),
        "cleanup_deleted": cleanup_deleted,
        "awareness_cleanup_deleted": awareness_cleanup,
    }


def evaluate_experience_dataset_contract() -> dict[str, Any]:
    return asyncio.run(evaluate_experience_dataset_contract_async())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness experience dataset")
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
        print(json.dumps(dataset.to_dict(), indent=2, default=_json_default, ensure_ascii=False))
        return 0
    if args.command == "contract":
        payload = await evaluate_experience_dataset_contract_async()
        print(json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    return 2


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
