#!/usr/bin/env python3
"""Intent contracts and task lifecycle gates for SEAL Sprint 1."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
from seal_secrets import pg_dsn


DEFAULT_AGENT = "ADA"
LIFECYCLE_SEQUENCE = ("intent_recorded", "executing", "verified", "audited", "closed")
REQUIRED_EVIDENCE_TYPES = {"command", "output", "artifact"}
NON_VACUOUS_EVIDENCE_TYPES = {
    "subject",
    "observed_at",
    "opportunity",
    "expected",
    "actual",
    "positive_control",
    "negative_control",
    "by_effect",
}


def _nonempty_mapping(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def _observed_value(value: Any) -> bool:
    """Reject labels/booleans that only *claim* an observation exists."""
    return value is not None and not isinstance(value, bool) and value != ""


def _valid_observed_at(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_opportunity(value: Any) -> bool:
    return (
        _nonempty_mapping(value)
        and value.get("exercised") is True
        and _observed_value(value.get("operation"))
    )


def _valid_control(value: Any) -> bool:
    """A control needs machine-readable expected/observed values and a verdict."""
    return (
        _nonempty_mapping(value)
        and value.get("passed") is True
        and _observed_value(value.get("expected"))
        and _observed_value(value.get("observed"))
    )


def _valid_by_effect(value: Any) -> bool:
    """Proof by effect must expose a real transition, not ``"ok"``."""
    if not _nonempty_mapping(value) or value.get("changed") is not True:
        return False
    before = value.get("before")
    after = value.get("after")
    return _observed_value(before) and _observed_value(after) and before != after


def validate_non_vacuous_evidence(evidence_type: str, value: Any) -> bool:
    """Validate the content of opt-in strong evidence fields.

    Legacy contracts remain compatible because this is called only for fields
    explicitly named in ``expected_evidence``.
    """
    if evidence_type == "observed_at":
        return _valid_observed_at(value)
    if evidence_type == "opportunity":
        return _valid_opportunity(value)
    if evidence_type in {"positive_control", "negative_control"}:
        return _valid_control(value)
    if evidence_type == "by_effect":
        return _valid_by_effect(value)
    if evidence_type in {"subject", "expected", "actual"}:
        return _nonempty_mapping(value) and any(
            _observed_value(item) for item in value.values()
        )
    return False


@dataclass(frozen=True)
class IntentContract:
    agent: str
    objective: str
    scope: list[str]
    out_of_scope: list[str]
    risks: list[str]
    expected_evidence: list[str]
    task_id: int | None = None
    audit_required: bool = True
    temporary: bool = False


@dataclass(frozen=True)
class LifecycleEvent:
    event_type: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class LifecycleAssessment:
    allowed_to_close: bool
    score: int
    missing: list[str]
    observed_events: list[str]


@dataclass(frozen=True)
class LifecycleRecord:
    contract_id: int
    task_id: int | None
    event_ids: list[int]
    assessment: LifecycleAssessment
    evidence: str


@dataclass(frozen=True)
class ClosureGateResult:
    task_id: int
    contract_id: int | None
    allowed_to_close: bool
    score: int
    missing: list[str]
    observed_events: list[str]
    evidence: str


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(resolve_db_dsn())


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.task_intent_contracts (
            id                BIGSERIAL PRIMARY KEY,
            task_id           BIGINT REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
            agent             TEXT        NOT NULL,
            objective         TEXT        NOT NULL,
            scope             JSONB       NOT NULL DEFAULT '[]',
            out_of_scope      JSONB       NOT NULL DEFAULT '[]',
            risks             JSONB       NOT NULL DEFAULT '[]',
            expected_evidence JSONB       NOT NULL DEFAULT '[]',
            audit_required    BOOLEAN     NOT NULL DEFAULT TRUE,
            temporary         BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.task_lifecycle_events (
            id          BIGSERIAL PRIMARY KEY,
            contract_id BIGINT      NOT NULL REFERENCES soul_v3.task_intent_contracts(id) ON DELETE CASCADE,
            event_type  TEXT        NOT NULL,
            evidence    JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_task_intent_contracts_agent ON soul_v3.task_intent_contracts(agent)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_task_lifecycle_events_contract ON soul_v3.task_lifecycle_events(contract_id)")


def validate_intent_contract(contract: IntentContract) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not contract.agent.strip():
        missing.append("agent")
    if not contract.objective.strip():
        missing.append("objective")
    if not contract.scope:
        missing.append("scope")
    if not contract.out_of_scope:
        missing.append("out_of_scope")
    if not contract.risks:
        missing.append("risks")
    evidence = {item.strip().lower() for item in contract.expected_evidence if item.strip()}
    missing_evidence = sorted(REQUIRED_EVIDENCE_TYPES - evidence)
    missing.extend(f"expected_evidence:{item}" for item in missing_evidence)
    return not missing, missing


def assess_lifecycle(contract: IntentContract, events: list[LifecycleEvent]) -> LifecycleAssessment:
    valid_contract, missing = validate_intent_contract(contract)
    if not valid_contract:
        missing = [f"contract:{item}" for item in missing]

    observed = [event.event_type for event in events]
    required = list(LIFECYCLE_SEQUENCE if contract.audit_required else ("intent_recorded", "executing", "verified", "closed"))
    for event_type in required:
        if event_type not in observed:
            missing.append(f"event:{event_type}")

    indexes = [observed.index(event_type) for event_type in required if event_type in observed]
    if indexes != sorted(indexes):
        missing.append("event_order")

    evidence_by_event = {event.event_type: event.evidence for event in events}
    verified = evidence_by_event.get("verified", {})
    if not verified.get("command") or not verified.get("output") or not verified.get("artifact"):
        missing.append("verified_evidence")

    # Strong evidence is opt-in and backwards compatible. A contract that
    # names any non-vacuous field must provide structurally valid measured
    # content, not merely a truthy label such as ``"ok"`` or ``"yes"``.
    expected_evidence = {
        item.strip().lower() for item in contract.expected_evidence if item.strip()
    }
    for evidence_type in sorted(expected_evidence & NON_VACUOUS_EVIDENCE_TYPES):
        if not validate_non_vacuous_evidence(
            evidence_type,
            verified.get(evidence_type),
        ):
            missing.append(f"verified_evidence:{evidence_type}")

    if contract.audit_required:
        audited = evidence_by_event.get("audited", {})
        if not audited.get("reviewer") or not audited.get("verdict"):
            missing.append("audit_evidence")

    allowed = not missing
    score = 100 if allowed else max(0, 100 - len(set(missing)) * 15)
    return LifecycleAssessment(
        allowed_to_close=allowed,
        score=score,
        missing=sorted(set(missing)),
        observed_events=observed,
    )


async def create_contract(conn: asyncpg.Connection, contract: IntentContract) -> int:
    valid, missing = validate_intent_contract(contract)
    if not valid:
        raise ValueError(f"Invalid intent contract: {missing}")
    await ensure_schema(conn)
    return int(
        await conn.fetchval(
            """
            INSERT INTO soul_v3.task_intent_contracts
                (task_id, agent, objective, scope, out_of_scope, risks, expected_evidence, audit_required, temporary)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9)
            RETURNING id
            """,
            contract.task_id,
            contract.agent,
            contract.objective,
            json.dumps(contract.scope, sort_keys=True),
            json.dumps(contract.out_of_scope, sort_keys=True),
            json.dumps(contract.risks, sort_keys=True),
            json.dumps(contract.expected_evidence, sort_keys=True),
            contract.audit_required,
            contract.temporary,
        )
    )


async def append_lifecycle_event(conn: asyncpg.Connection, contract_id: int, event: LifecycleEvent) -> int:
    if event.event_type not in LIFECYCLE_SEQUENCE:
        raise ValueError(f"Invalid lifecycle event {event.event_type!r}")
    await ensure_schema(conn)
    return int(
        await conn.fetchval(
            """
            INSERT INTO soul_v3.task_lifecycle_events (contract_id, event_type, evidence)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id
            """,
            contract_id,
            event.event_type,
            json.dumps(event.evidence, sort_keys=True),
        )
    )


async def fetch_lifecycle(conn: asyncpg.Connection, contract_id: int) -> tuple[IntentContract, list[LifecycleEvent]]:
    row = await conn.fetchrow(
        """
        SELECT id, task_id, agent, objective, scope, out_of_scope, risks,
               expected_evidence, audit_required, temporary
        FROM soul_v3.task_intent_contracts
        WHERE id=$1
        """,
        contract_id,
    )
    if row is None:
        raise KeyError(f"contract {contract_id} not found")

    def json_field(name: str) -> list[str]:
        value = row[name]
        if isinstance(value, str):
            value = json.loads(value)
        return list(value or [])

    contract = IntentContract(
        task_id=row["task_id"],
        agent=row["agent"],
        objective=row["objective"],
        scope=json_field("scope"),
        out_of_scope=json_field("out_of_scope"),
        risks=json_field("risks"),
        expected_evidence=json_field("expected_evidence"),
        audit_required=bool(row["audit_required"]),
        temporary=bool(row["temporary"]),
    )
    event_rows = await conn.fetch(
        """
        SELECT event_type, evidence
        FROM soul_v3.task_lifecycle_events
        WHERE contract_id=$1
        ORDER BY id ASC
        """,
        contract_id,
    )
    events = []
    for event_row in event_rows:
        evidence = event_row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        events.append(LifecycleEvent(event_row["event_type"], dict(evidence or {})))
    return contract, events


async def latest_contract_id_for_task(conn: asyncpg.Connection, task_id: int, agent: str | None = None) -> int | None:
    await ensure_schema(conn)
    return await conn.fetchval(
        """
        SELECT id
        FROM soul_v3.task_intent_contracts
        WHERE task_id=$1
          AND ($2::text IS NULL OR agent=$2)
        ORDER BY id DESC
        LIMIT 1
        """,
        task_id,
        agent,
    )


async def assess_task_closure(conn: asyncpg.Connection, task_id: int, agent: str | None = None) -> ClosureGateResult:
    await ensure_schema(conn)
    task = await conn.fetchrow(
        """
        SELECT id, agent, title, status
        FROM soul_v3.agent_tasks
        WHERE id=$1
        """,
        task_id,
    )
    if task is None:
        return ClosureGateResult(
            task_id=task_id,
            contract_id=None,
            allowed_to_close=False,
            score=0,
            missing=["task:not_found"],
            observed_events=[],
            evidence=f"task_id={task_id} allowed_to_close=False missing=task:not_found",
        )
    if agent is not None and task["agent"] != agent:
        return ClosureGateResult(
            task_id=task_id,
            contract_id=None,
            allowed_to_close=False,
            score=0,
            missing=["task:agent_mismatch"],
            observed_events=[],
            evidence=f"task_id={task_id} allowed_to_close=False missing=task:agent_mismatch",
        )

    contract_id = await latest_contract_id_for_task(conn, task_id, agent=agent)
    if contract_id is None:
        return ClosureGateResult(
            task_id=task_id,
            contract_id=None,
            allowed_to_close=False,
            score=0,
            missing=["contract:not_found"],
            observed_events=[],
            evidence=f"task_id={task_id} allowed_to_close=False missing=contract:not_found",
        )

    contract, events = await fetch_lifecycle(conn, int(contract_id))
    assessment = assess_lifecycle(contract, events)
    return ClosureGateResult(
        task_id=task_id,
        contract_id=int(contract_id),
        allowed_to_close=assessment.allowed_to_close,
        score=assessment.score,
        missing=assessment.missing,
        observed_events=assessment.observed_events,
        evidence=(
            f"task_id={task_id} contract_id={contract_id} "
            f"allowed_to_close={assessment.allowed_to_close} score={assessment.score}"
        ),
    )


async def cleanup_temporary_lifecycle(conn: asyncpg.Connection, contract_id: int, task_id: int | None = None) -> int:
    deleted_events = int(
        await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.task_lifecycle_events
                WHERE contract_id=$1
                RETURNING id
            )
            SELECT COUNT(*) FROM deleted
            """,
            contract_id,
        )
    )
    deleted_contracts = int(
        await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.task_intent_contracts
                WHERE id=$1 AND temporary=true
                RETURNING id
            )
            SELECT COUNT(*) FROM deleted
            """,
            contract_id,
        )
    )
    deleted_tasks = 0
    if task_id is not None:
        deleted_tasks = int(
            await conn.fetchval(
                """
                WITH deleted AS (
                    DELETE FROM soul_v3.agent_tasks
                    WHERE id=$1
                      AND title LIKE 'evaluation_spine_task_lifecycle_%'
                      AND agent IN ('ADA', 'NEXUS')
                    RETURNING id
                )
                SELECT COUNT(*) FROM deleted
                """,
                task_id,
            )
        )
    return deleted_events + deleted_contracts + deleted_tasks


async def cleanup_temporary_task(conn: asyncpg.Connection, task_id: int) -> int:
    return int(
        await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.agent_tasks
                WHERE id=$1
                  AND title LIKE 'evaluation_spine_task_lifecycle_%'
                  AND agent IN ('ADA', 'NEXUS')
                RETURNING id
            )
            SELECT COUNT(*) FROM deleted
            """,
            task_id,
        )
    )


def default_contract(agent: str = DEFAULT_AGENT, *, task_id: int | None = None, temporary: bool = False) -> IntentContract:
    return IntentContract(
        agent=agent,
        task_id=task_id,
        objective="Implement Sprint 1 Intent Contract with measurable lifecycle gates.",
        scope=["memory/task_lifecycle.py", "memory/evaluation_spine.py", "tests", "SOUL DB evidence"],
        out_of_scope=["daemon restart", "AGI declaration", "destructive cleanup outside exact temporary IDs"],
        risks=["phantom done claim", "missing audit evidence", "task closure without verified output"],
        expected_evidence=["command", "output", "artifact"],
        audit_required=True,
        temporary=temporary,
    )


def default_events() -> list[LifecycleEvent]:
    return [
        LifecycleEvent("intent_recorded", {"artifact": "task_intent_contracts"}),
        LifecycleEvent("executing", {"artifact": "memory/task_lifecycle.py"}),
        LifecycleEvent("verified", {"command": "pytest task lifecycle", "output": "passed", "artifact": "memory/test_task_lifecycle.py"}),
        LifecycleEvent("audited", {"reviewer": "evaluation_spine", "verdict": "pass"}),
        LifecycleEvent("closed", {"artifact": "evaluation_run_id"}),
    ]


async def record_default_lifecycle(conn: asyncpg.Connection, agent: str = DEFAULT_AGENT, temporary: bool = False) -> LifecycleRecord:
    marker = f"evaluation_spine_task_lifecycle_{agent}_{int(time.time() * 1000)}"
    task_id = int(
        await conn.fetchval(
            """
            INSERT INTO soul_v3.agent_tasks (agent, title, description, priority)
            VALUES ($1, $2, $3, 5)
            RETURNING id
            """,
            agent,
            marker,
            "temporary lifecycle suite task",
        )
    )
    contract = default_contract(agent, task_id=task_id, temporary=temporary)
    contract_id = await create_contract(conn, contract)
    event_ids = [await append_lifecycle_event(conn, contract_id, event) for event in default_events()]
    fetched_contract, fetched_events = await fetch_lifecycle(conn, contract_id)
    assessment = assess_lifecycle(fetched_contract, fetched_events)
    evidence = (
        f"contract_id={contract_id} task_id={task_id} events={len(event_ids)} "
        f"allowed_to_close={assessment.allowed_to_close} score={assessment.score}"
    )
    return LifecycleRecord(contract_id, task_id, event_ids, assessment, evidence)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL task intent/lifecycle gate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-schema")
    record = sub.add_parser("record-default")
    record.add_argument("--agent", default=DEFAULT_AGENT)
    record.add_argument("--temporary", action="store_true")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        if args.command == "init-schema":
            await ensure_schema(conn)
            print("schema_ok soul_v3.task_intent_contracts soul_v3.task_lifecycle_events")
            return 0
        if args.command == "record-default":
            record = await record_default_lifecycle(conn, args.agent, temporary=args.temporary)
            print(json.dumps(asdict(record), indent=2, ensure_ascii=False))
            return 0 if record.assessment.allowed_to_close else 2
    finally:
        await conn.close()
    raise AssertionError(f"Unhandled command: {args.command}")


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
