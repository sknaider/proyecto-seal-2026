#!/usr/bin/env python3
"""Evidence-gated autonomous lifecycle planner for SEAL Sprint 12.

This module proposes safe next actions from agent_tasks, GAM actions and
reflective diagnoses. It does not execute those actions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from task_lifecycle import connect_db


@dataclass(frozen=True)
class LifecycleActionProposal:
    action_type: str
    target_agent: str
    source_kind: str
    source_id: int
    score: int
    cooldown_seconds: int
    requires_audit: bool
    audit_trail: list[str]
    reason: str


@dataclass(frozen=True)
class LifecycleAssessment:
    agent: str
    proposals: list[LifecycleActionProposal]
    total_sources: int
    actionable_count: int
    blocked_count: int
    highest_score: int
    next_action: LifecycleActionProposal | None
    evidence: str


@dataclass(frozen=True)
class LifecycleProposalRecord:
    proposal_id: int
    action_type: str
    source_kind: str
    source_id: int
    status: str
    cooldown_until: datetime
    inserted: bool


@dataclass(frozen=True)
class LifecyclePersistenceResult:
    agent: str
    proposal_count: int
    inserted_count: int
    existing_count: int
    records: list[LifecycleProposalRecord]
    evidence: str


@dataclass(frozen=True)
class LifecycleDelegationRecord:
    proposal_id: int
    reviewer_agent: str
    delegated_task_id: int
    gam_event_id: int | None
    inserted: bool


@dataclass(frozen=True)
class LifecycleDelegationResult:
    agent: str
    reviewer_agent: str
    candidate_count: int
    delegated_count: int
    existing_count: int
    records: list[LifecycleDelegationRecord]
    evidence: str


@dataclass(frozen=True)
class LifecycleReviewRecord:
    review_id: int
    proposal_id: int
    reviewer_agent: str
    review_task_id: int | None
    decision: str
    inserted: bool


@dataclass(frozen=True)
class LifecycleReviewSeedResult:
    agent: str
    reviewer_agent: str
    candidate_count: int
    inserted_count: int
    existing_count: int
    records: list[LifecycleReviewRecord]
    evidence: str


@dataclass(frozen=True)
class LifecycleReviewDecision:
    review_id: int
    proposal_id: int
    reviewer_agent: str
    review_task_id: int | None
    decision: str
    rationale: str
    evidence: str


@dataclass(frozen=True)
class LifecycleExecutionGate:
    proposal_id: int
    action_type: str
    source_kind: str
    source_id: int
    allowed: bool
    decision: str
    review_id: int | None
    missing: list[str]
    evidence: str


@dataclass(frozen=True)
class LifecycleExecutionGateAssessment:
    agent: str
    gate_count: int
    allowed_count: int
    blocked_count: int
    gates: list[LifecycleExecutionGate]
    evidence: str


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _score_from_priority(priority: int) -> int:
    return max(25, min(100, 110 - priority * 10))


def _task_proposal(task: dict[str, Any], *, now: datetime) -> LifecycleActionProposal | None:
    status = str(task.get("status") or "")
    if status not in {"pending", "in_progress"}:
        return None
    priority = _int(task.get("priority"), 5)
    score = _score_from_priority(priority)
    deadline = task.get("deadline")
    audit: list[str] = [f"agent_tasks.id={task.get('id')}", f"status={status}", f"priority={priority}"]
    if deadline and hasattr(deadline, "timestamp"):
        seconds_to_deadline = int((deadline - now).total_seconds())
        audit.append(f"seconds_to_deadline={seconds_to_deadline}")
        if seconds_to_deadline < 0:
            score = min(100, score + 20)
        elif seconds_to_deadline < 3600:
            score = min(100, score + 10)
    cooldown = 1800 if priority <= 3 else 3600
    return LifecycleActionProposal(
        action_type="continue_task",
        target_agent=str(task.get("agent") or "UNKNOWN"),
        source_kind="agent_task",
        source_id=_int(task.get("id")),
        score=score,
        cooldown_seconds=cooldown,
        requires_audit=priority <= 3,
        audit_trail=audit,
        reason=str(task.get("title") or "pending task requires progress"),
    )


def _gam_proposal(action: dict[str, Any]) -> LifecycleActionProposal | None:
    metadata = _json_dict(action.get("metadata"))
    status = str(metadata.get("status") or "")
    if status not in {"pending", "in_progress"}:
        return None
    if metadata.get("source") == "agent_task":
        return None
    priority = _int(metadata.get("priority"), 5)
    score = _score_from_priority(priority)
    return LifecycleActionProposal(
        action_type="review_gam_action",
        target_agent=str(action.get("agent") or "UNKNOWN"),
        source_kind="gam_event_graph",
        source_id=_int(action.get("id")),
        score=score,
        cooldown_seconds=2700,
        requires_audit=True,
        audit_trail=[
            f"gam_event_graph.id={action.get('id')}",
            f"status={status}",
            f"topic_id={action.get('topic_id')}",
        ],
        reason=str(action.get("event") or "pending GAM action requires review"),
    )


def _diagnosis_proposal(diagnosis: dict[str, Any]) -> LifecycleActionProposal | None:
    status = str(diagnosis.get("status") or "")
    if status not in {"pending_review", "accepted"}:
        return None
    confidence = float(diagnosis.get("confidence") or 0.0)
    if confidence < 0.5:
        return None
    action_type = "apply_accepted_diagnosis" if status == "accepted" else "request_nexus_review"
    score = min(100, max(50, round(confidence * 100)))
    return LifecycleActionProposal(
        action_type=action_type,
        target_agent="NEXUS" if status == "pending_review" else str(diagnosis.get("agent") or "ADA"),
        source_kind="reflective_diagnosis",
        source_id=_int(diagnosis.get("id")),
        score=score,
        cooldown_seconds=3600 if status == "pending_review" else 1800,
        requires_audit=True,
        audit_trail=[
            f"reflective_diagnoses.id={diagnosis.get('id')}",
            f"status={status}",
            f"confidence={confidence:.2f}",
        ],
        reason=str(diagnosis.get("suggested_fix") or diagnosis.get("diagnosis") or "diagnosis requires lifecycle action"),
    )


def assess_autonomous_lifecycle(
    agent: str,
    *,
    tasks: list[dict[str, Any]],
    gam_actions: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    now: datetime | None = None,
) -> LifecycleAssessment:
    now = now or _utc_now()
    proposals: list[LifecycleActionProposal] = []
    for task in tasks:
        proposal = _task_proposal(task, now=now)
        if proposal:
            proposals.append(proposal)
    for action in gam_actions:
        proposal = _gam_proposal(action)
        if proposal:
            proposals.append(proposal)
    for diagnosis in diagnoses:
        proposal = _diagnosis_proposal(diagnosis)
        if proposal:
            proposals.append(proposal)
    proposals.sort(key=lambda proposal: (-proposal.score, proposal.source_kind, proposal.source_id))
    next_action = proposals[0] if proposals else None
    highest = next_action.score if next_action else 0
    total_sources = len(tasks) + len(gam_actions) + len(diagnoses)
    blocked_count = sum(1 for proposal in proposals if proposal.requires_audit)
    evidence = (
        f"autonomous_lifecycle_sources={total_sources} proposals={len(proposals)} "
        f"highest_score={highest} next_action={next_action.action_type if next_action else 'none'}"
    )
    return LifecycleAssessment(
        agent=agent,
        proposals=proposals,
        total_sources=total_sources,
        actionable_count=len(proposals),
        blocked_count=blocked_count,
        highest_score=highest,
        next_action=next_action,
        evidence=evidence,
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.autonomous_lifecycle_proposals (
            id               BIGSERIAL PRIMARY KEY,
            agent            TEXT        NOT NULL,
            action_type      TEXT        NOT NULL,
            target_agent     TEXT        NOT NULL,
            source_kind      TEXT        NOT NULL,
            source_id        BIGINT      NOT NULL,
            score            INT         NOT NULL CHECK (score BETWEEN 0 AND 100),
            cooldown_seconds INT         NOT NULL CHECK (cooldown_seconds > 0),
            cooldown_until   TIMESTAMPTZ NOT NULL,
            requires_audit   BOOLEAN     NOT NULL DEFAULT TRUE,
            audit_trail      JSONB       NOT NULL DEFAULT '[]',
            reason           TEXT        NOT NULL,
            status           TEXT        NOT NULL DEFAULT 'proposed',
            evidence         TEXT        NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute("ALTER TABLE soul_v3.autonomous_lifecycle_proposals ADD COLUMN IF NOT EXISTS delegated_agent TEXT")
    await conn.execute("ALTER TABLE soul_v3.autonomous_lifecycle_proposals ADD COLUMN IF NOT EXISTS delegated_task_id BIGINT")
    await conn.execute("ALTER TABLE soul_v3.autonomous_lifecycle_proposals ADD COLUMN IF NOT EXISTS delegated_at TIMESTAMPTZ")
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autonomous_lifecycle_proposals_agent_status
        ON soul_v3.autonomous_lifecycle_proposals(agent, status, created_at DESC)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autonomous_lifecycle_proposals_source
        ON soul_v3.autonomous_lifecycle_proposals(agent, source_kind, source_id, action_type)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autonomous_lifecycle_proposals_delegated_task
        ON soul_v3.autonomous_lifecycle_proposals(delegated_task_id)
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.autonomous_lifecycle_reviews (
            id              BIGSERIAL PRIMARY KEY,
            proposal_id     BIGINT      NOT NULL REFERENCES soul_v3.autonomous_lifecycle_proposals(id) ON DELETE CASCADE,
            reviewer_agent  TEXT        NOT NULL,
            review_task_id  BIGINT      REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
            decision        TEXT        NOT NULL CHECK (decision IN ('pending','approved','rejected','needs_evidence')),
            rationale       TEXT        NOT NULL DEFAULT '',
            evidence        JSONB       NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autonomous_lifecycle_reviews_proposal
        ON soul_v3.autonomous_lifecycle_reviews(proposal_id, created_at DESC)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autonomous_lifecycle_reviews_task
        ON soul_v3.autonomous_lifecycle_reviews(review_task_id, reviewer_agent, decision)
        """
    )


async def persist_lifecycle_proposals(
    conn: asyncpg.Connection,
    assessment: LifecycleAssessment,
    *,
    now: datetime | None = None,
) -> LifecyclePersistenceResult:
    await ensure_schema(conn)
    now = now or _utc_now()
    records: list[LifecycleProposalRecord] = []
    inserted_count = 0
    existing_count = 0
    for proposal in assessment.proposals:
        existing = await conn.fetchrow(
            """
            SELECT p.id, p.status, p.cooldown_until
            FROM soul_v3.autonomous_lifecycle_proposals p
            LEFT JOIN soul_v3.agent_tasks delegated_task ON delegated_task.id=p.delegated_task_id
            WHERE p.agent=$1
              AND p.source_kind=$2
              AND p.source_id=$3
              AND p.action_type=$4
              AND p.status='proposed'
              AND (
                p.cooldown_until > $5
                OR delegated_task.status IN ('pending','in_progress')
              )
            ORDER BY
              CASE WHEN delegated_task.status IN ('pending','in_progress') THEN 0 ELSE 1 END,
              p.created_at DESC,
              p.id DESC
            LIMIT 1
            """,
            assessment.agent,
            proposal.source_kind,
            proposal.source_id,
            proposal.action_type,
            now,
        )
        if existing:
            existing_count += 1
            records.append(
                LifecycleProposalRecord(
                    proposal_id=int(existing["id"]),
                    action_type=proposal.action_type,
                    source_kind=proposal.source_kind,
                    source_id=proposal.source_id,
                    status=str(existing["status"]),
                    cooldown_until=existing["cooldown_until"],
                    inserted=False,
                )
            )
            continue

        cooldown_until = now + timedelta(seconds=proposal.cooldown_seconds)
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.autonomous_lifecycle_proposals (
                agent, action_type, target_agent, source_kind, source_id,
                score, cooldown_seconds, cooldown_until, requires_audit,
                audit_trail, reason, status, evidence
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,'proposed',$12)
            RETURNING id, status, cooldown_until
            """,
            assessment.agent,
            proposal.action_type,
            proposal.target_agent,
            proposal.source_kind,
            proposal.source_id,
            proposal.score,
            proposal.cooldown_seconds,
            cooldown_until,
            proposal.requires_audit,
            json.dumps(proposal.audit_trail, ensure_ascii=False),
            proposal.reason,
            assessment.evidence,
        )
        inserted_count += 1
        records.append(
            LifecycleProposalRecord(
                proposal_id=int(row["id"]),
                action_type=proposal.action_type,
                source_kind=proposal.source_kind,
                source_id=proposal.source_id,
                status=str(row["status"]),
                cooldown_until=row["cooldown_until"],
                inserted=True,
            )
        )
    evidence = (
        f"autonomous_lifecycle_persisted={inserted_count} existing={existing_count} "
        f"proposals={len(assessment.proposals)}"
    )
    return LifecyclePersistenceResult(
        agent=assessment.agent,
        proposal_count=len(assessment.proposals),
        inserted_count=inserted_count,
        existing_count=existing_count,
        records=records,
        evidence=evidence,
    )


def _task_priority_from_score(score: int) -> int:
    if score >= 90:
        return 2
    if score >= 75:
        return 3
    return 4


def _audit_trail_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            return [value]
    return []


async def _sync_agent_task_to_gam(conn: asyncpg.Connection, task: dict[str, Any], event_status: str = "pending") -> int:
    topic = "TaskList / Agent Tasks"
    topic_row = await conn.fetchrow(
        """
        SELECT id FROM soul_v3.gam_topics
        WHERE agent=$1 AND topic=$2
        ORDER BY id ASC
        LIMIT 1
        """,
        task["agent"],
        topic,
    )
    if topic_row:
        topic_id = int(topic_row["id"])
    else:
        topic_id = int(await conn.fetchval(
            """
            INSERT INTO soul_v3.gam_topics (agent, topic, summary, relevance_score)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            task["agent"],
            topic,
            "Canonical GAM topic mirroring soul_v3.agent_tasks into actionable events.",
            0.8,
        ))

    existing = await conn.fetchval(
        """
        SELECT id FROM soul_v3.gam_event_graph
        WHERE agent=$1
          AND metadata->>'source'='agent_task'
          AND (metadata->>'task_id')::bigint=$2
        LIMIT 1
        """,
        task["agent"],
        task["id"],
    )
    metadata = {
        "source": "agent_task",
        "task_id": task["id"],
        "status": event_status,
        "priority": task.get("priority"),
        "deadline": task.get("deadline").isoformat() if task.get("deadline") else None,
        "created_at": task.get("created_at").isoformat() if task.get("created_at") else None,
        "completed_at": task.get("completed_at").isoformat() if task.get("completed_at") else None,
    }
    if existing:
        await conn.execute(
            """
            UPDATE soul_v3.gam_event_graph
            SET metadata=$1, event=$2
            WHERE id=$3
            """,
            json.dumps(metadata),
            task["title"],
            existing,
        )
        event_id = int(existing)
    else:
        event_id = int(await conn.fetchval(
            """
            INSERT INTO soul_v3.gam_event_graph
                (agent, topic_id, event, event_timestamp, related_event_ids, causal_direction, metadata)
            VALUES ($1, $2, $3, COALESCE($4, now()), '{}', 'related', $5)
            RETURNING id
            """,
            task["agent"],
            topic_id,
            task["title"],
            task.get("created_at"),
            json.dumps(metadata),
        ))

    await conn.execute(
        """
        UPDATE soul_v3.gam_topics t
        SET event_count = counts.count, last_updated = now()
        FROM (
            SELECT topic_id, COUNT(*)::int AS count
            FROM soul_v3.gam_event_graph
            WHERE topic_id=$1
            GROUP BY topic_id
        ) counts
        WHERE t.id=counts.topic_id
        """,
        topic_id,
    )
    return event_id


def _review_task_description(proposal: dict[str, Any]) -> str:
    audit_trail = _audit_trail_list(proposal.get("audit_trail"))
    audit_lines = "\n".join(f"- {item}" for item in audit_trail) if audit_trail else "- none"
    return (
        f"autonomous_lifecycle_proposal_id={proposal['id']}\n"
        f"origin_agent={proposal['agent']}\n"
        f"action_type={proposal['action_type']}\n"
        f"source={proposal['source_kind']}#{proposal['source_id']}\n"
        f"score={proposal['score']}\n"
        f"cooldown_until={proposal['cooldown_until']}\n"
        f"reason={proposal['reason']}\n"
        f"evidence={proposal['evidence']}\n"
        "Required review: approve, reject, or request more evidence before ADA executes anything.\n"
        "Audit trail:\n"
        f"{audit_lines}"
    )


async def delegate_audit_proposals(
    conn: asyncpg.Connection,
    agent: str,
    *,
    reviewer_agent: str = "NEXUS",
    limit: int = 20,
    now: datetime | None = None,
) -> LifecycleDelegationResult:
    await ensure_schema(conn)
    now = now or _utc_now()
    proposals = [dict(row) for row in await conn.fetch(
        """
        SELECT id, agent, action_type, target_agent, source_kind, source_id, score,
               cooldown_seconds, cooldown_until, requires_audit, audit_trail, reason,
               status, evidence, delegated_agent, delegated_task_id, delegated_at
        FROM soul_v3.autonomous_lifecycle_proposals
        WHERE agent=$1
          AND status='proposed'
          AND requires_audit=true
          AND delegated_task_id IS NULL
          AND cooldown_until > $2
        ORDER BY score DESC, created_at ASC
        LIMIT $3
        """,
        agent,
        now,
        limit,
    )]
    records: list[LifecycleDelegationRecord] = []
    delegated_count = 0
    existing_count = 0
    for proposal in proposals:
        marker = f"autonomous_lifecycle_proposal_id={proposal['id']}"
        existing_task = await conn.fetchrow(
            """
            SELECT id, agent, title, description, status, priority, deadline, created_at, completed_at
            FROM soul_v3.agent_tasks
            WHERE agent=$1
              AND status IN ('pending','in_progress')
              AND (
                description LIKE $2
                OR (
                  description LIKE $3
                  AND description LIKE $4
                  AND description LIKE $5
                )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            reviewer_agent,
            f"%{marker}%",
            f"%origin_agent={proposal['agent']}%",
            f"%action_type={proposal['action_type']}%",
            f"%source={proposal['source_kind']}#{proposal['source_id']}%",
        )
        inserted = False
        if existing_task:
            task = dict(existing_task)
            existing_count += 1
        else:
            title = (
                f"Review ADA lifecycle proposal {proposal['id']}: "
                f"{proposal['action_type']} {proposal['source_kind']}#{proposal['source_id']}"
            )
            task = dict(await conn.fetchrow(
                """
                INSERT INTO soul_v3.agent_tasks (agent, title, description, priority)
                VALUES ($1, $2, $3, $4)
                RETURNING id, agent, title, description, status, priority, deadline, created_at, completed_at
                """,
                reviewer_agent,
                title,
                _review_task_description(proposal),
                _task_priority_from_score(int(proposal["score"])),
            ))
            inserted = True
            delegated_count += 1
        gam_event_id = await _sync_agent_task_to_gam(conn, task, "pending")
        await conn.execute(
            """
            UPDATE soul_v3.autonomous_lifecycle_proposals
            SET delegated_agent=$1, delegated_task_id=$2, delegated_at=COALESCE(delegated_at, NOW())
            WHERE id=$3
            """,
            reviewer_agent,
            int(task["id"]),
            int(proposal["id"]),
        )
        records.append(
            LifecycleDelegationRecord(
                proposal_id=int(proposal["id"]),
                reviewer_agent=reviewer_agent,
                delegated_task_id=int(task["id"]),
                gam_event_id=gam_event_id,
                inserted=inserted,
            )
        )
    evidence = (
        f"autonomous_lifecycle_delegated={delegated_count} existing={existing_count} "
        f"candidates={len(proposals)} reviewer={reviewer_agent}"
    )
    return LifecycleDelegationResult(
        agent=agent,
        reviewer_agent=reviewer_agent,
        candidate_count=len(proposals),
        delegated_count=delegated_count,
        existing_count=existing_count,
        records=records,
        evidence=evidence,
    )


async def record_review_decision(
    conn: asyncpg.Connection,
    *,
    proposal_id: int,
    reviewer_agent: str,
    decision: str,
    rationale: str,
    review_task_id: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> LifecycleReviewDecision:
    await ensure_schema(conn)
    if decision not in {"approved", "rejected", "needs_evidence", "pending"}:
        raise ValueError("decision must be one of: approved, rejected, needs_evidence, pending")
    proposal = await conn.fetchrow(
        """
        SELECT id, delegated_agent, delegated_task_id
        FROM soul_v3.autonomous_lifecycle_proposals
        WHERE id=$1
        """,
        proposal_id,
    )
    if not proposal:
        raise ValueError(f"proposal_id {proposal_id} not found")
    delegated_agent = str(proposal["delegated_agent"] or "")
    if delegated_agent and delegated_agent != reviewer_agent:
        raise ValueError(f"proposal_id {proposal_id} is delegated to {delegated_agent}, not {reviewer_agent}")
    expected_task_id = int(proposal["delegated_task_id"]) if proposal["delegated_task_id"] is not None else None
    resolved_task_id = review_task_id if review_task_id is not None else expected_task_id
    if expected_task_id is not None and resolved_task_id != expected_task_id:
        raise ValueError(f"review_task_id {resolved_task_id} does not match delegated_task_id {expected_task_id}")
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.autonomous_lifecycle_reviews
            (proposal_id, reviewer_agent, review_task_id, decision, rationale, evidence)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id, proposal_id, reviewer_agent, review_task_id, decision, rationale, evidence
        """,
        proposal_id,
        reviewer_agent,
        resolved_task_id,
        decision,
        rationale,
        json.dumps(evidence or {"source": "record_review_decision"}),
    )
    return LifecycleReviewDecision(
        review_id=int(row["id"]),
        proposal_id=int(row["proposal_id"]),
        reviewer_agent=str(row["reviewer_agent"]),
        review_task_id=int(row["review_task_id"]) if row["review_task_id"] is not None else None,
        decision=str(row["decision"]),
        rationale=str(row["rationale"]),
        evidence=json.dumps(_json_dict(row["evidence"]), sort_keys=True),
    )


async def seed_pending_reviews(
    conn: asyncpg.Connection,
    agent: str,
    *,
    reviewer_agent: str = "NEXUS",
    limit: int = 20,
) -> LifecycleReviewSeedResult:
    await ensure_schema(conn)
    proposals = [dict(row) for row in await conn.fetch(
        """
        SELECT p.id, p.agent, p.action_type, p.source_kind, p.source_id,
               p.delegated_task_id, t.status AS delegated_task_status
        FROM soul_v3.autonomous_lifecycle_proposals p
        JOIN soul_v3.agent_tasks t ON t.id=p.delegated_task_id
        WHERE p.agent=$1
          AND p.status='proposed'
          AND p.requires_audit=true
          AND p.delegated_agent=$2
          AND t.agent=$2
          AND t.status IN ('pending','in_progress')
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT $3
        """,
        agent,
        reviewer_agent,
        limit,
    )]
    records: list[LifecycleReviewRecord] = []
    inserted_count = 0
    existing_count = 0
    seen_sources: set[tuple[str, int, str]] = set()
    for proposal in proposals:
        source_key = (str(proposal["source_kind"]), int(proposal["source_id"]), str(proposal["action_type"]))
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        existing = await conn.fetchrow(
            """
            SELECT id, proposal_id, reviewer_agent, review_task_id, decision
            FROM soul_v3.autonomous_lifecycle_reviews
            WHERE proposal_id=$1
              AND reviewer_agent=$2
              AND review_task_id=$3
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            int(proposal["id"]),
            reviewer_agent,
            int(proposal["delegated_task_id"]),
        )
        if existing:
            existing_count += 1
            records.append(
                LifecycleReviewRecord(
                    review_id=int(existing["id"]),
                    proposal_id=int(existing["proposal_id"]),
                    reviewer_agent=str(existing["reviewer_agent"]),
                    review_task_id=int(existing["review_task_id"]) if existing["review_task_id"] is not None else None,
                    decision=str(existing["decision"]),
                    inserted=False,
                )
            )
            continue
        evidence = {
            "source": "autonomous_lifecycle_review_gate",
            "proposal_id": int(proposal["id"]),
            "source_kind": proposal["source_kind"],
            "source_id": int(proposal["source_id"]),
            "action_type": proposal["action_type"],
            "delegated_task_status": proposal["delegated_task_status"],
        }
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.autonomous_lifecycle_reviews
                (proposal_id, reviewer_agent, review_task_id, decision, rationale, evidence)
            VALUES ($1, $2, $3, 'pending', $4, $5::jsonb)
            RETURNING id, proposal_id, reviewer_agent, review_task_id, decision
            """,
            int(proposal["id"]),
            reviewer_agent,
            int(proposal["delegated_task_id"]),
            "Awaiting explicit NEXUS approve/reject/more-evidence decision before ADA execution.",
            json.dumps(evidence),
        )
        inserted_count += 1
        records.append(
            LifecycleReviewRecord(
                review_id=int(row["id"]),
                proposal_id=int(row["proposal_id"]),
                reviewer_agent=str(row["reviewer_agent"]),
                review_task_id=int(row["review_task_id"]) if row["review_task_id"] is not None else None,
                decision=str(row["decision"]),
                inserted=True,
            )
        )
    evidence_text = (
        f"autonomous_lifecycle_reviews_seeded={inserted_count} existing={existing_count} "
        f"candidates={len(seen_sources)} reviewer={reviewer_agent}"
    )
    return LifecycleReviewSeedResult(
        agent=agent,
        reviewer_agent=reviewer_agent,
        candidate_count=len(seen_sources),
        inserted_count=inserted_count,
        existing_count=existing_count,
        records=records,
        evidence=evidence_text,
    )


async def assess_execution_gates(
    conn: asyncpg.Connection,
    agent: str,
    *,
    reviewer_agent: str = "NEXUS",
    limit: int = 20,
) -> LifecycleExecutionGateAssessment:
    await ensure_schema(conn)
    proposals = [dict(row) for row in await conn.fetch(
        """
        SELECT p.id, p.action_type, p.source_kind, p.source_id, p.requires_audit,
               p.delegated_agent, p.delegated_task_id
        FROM soul_v3.autonomous_lifecycle_proposals p
        LEFT JOIN soul_v3.agent_tasks t ON t.id=p.delegated_task_id
        WHERE p.agent=$1
          AND p.status='proposed'
          AND p.requires_audit=true
          AND (t.status IS NULL OR t.status IN ('pending','in_progress'))
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT $2
        """,
        agent,
        limit,
    )]
    gates: list[LifecycleExecutionGate] = []
    seen_sources: set[tuple[str, int, str]] = set()
    for proposal in proposals:
        source_key = (str(proposal["source_kind"]), int(proposal["source_id"]), str(proposal["action_type"]))
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        latest_review = await conn.fetchrow(
            """
            SELECT id, decision, reviewer_agent, review_task_id, rationale
            FROM soul_v3.autonomous_lifecycle_reviews
            WHERE proposal_id=$1 AND reviewer_agent=$2
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            int(proposal["id"]),
            reviewer_agent,
        )
        missing: list[str] = []
        decision = "missing"
        review_id: int | None = None
        if not latest_review:
            missing.append("nexus_review:not_found")
        else:
            decision = str(latest_review["decision"])
            review_id = int(latest_review["id"])
            if decision != "approved":
                missing.append(f"nexus_review:{decision}")
        if proposal.get("delegated_agent") != reviewer_agent:
            missing.append("delegation:reviewer_mismatch")
        if not proposal.get("delegated_task_id"):
            missing.append("delegation:task_missing")
        allowed = not missing and decision == "approved"
        evidence = (
            f"proposal_id={proposal['id']} allowed={allowed} decision={decision} "
            f"missing={','.join(missing) if missing else 'none'}"
        )
        gates.append(
            LifecycleExecutionGate(
                proposal_id=int(proposal["id"]),
                action_type=str(proposal["action_type"]),
                source_kind=str(proposal["source_kind"]),
                source_id=int(proposal["source_id"]),
                allowed=allowed,
                decision=decision,
                review_id=review_id,
                missing=missing,
                evidence=evidence,
            )
        )
    allowed_count = sum(1 for gate in gates if gate.allowed)
    blocked_count = len(gates) - allowed_count
    evidence_text = f"autonomous_lifecycle_gates={len(gates)} allowed={allowed_count} blocked={blocked_count}"
    return LifecycleExecutionGateAssessment(
        agent=agent,
        gate_count=len(gates),
        allowed_count=allowed_count,
        blocked_count=blocked_count,
        gates=gates,
        evidence=evidence_text,
    )


async def fetch_autonomous_lifecycle_assessment(
    conn: asyncpg.Connection,
    agent: str,
    *,
    limit: int = 20,
) -> LifecycleAssessment:
    tasks = [dict(row) for row in await conn.fetch(
        """
        SELECT id, agent, title, description, status, priority, deadline, created_at, completed_at
        FROM soul_v3.agent_tasks
        WHERE agent=$1 AND status IN ('pending','in_progress')
        ORDER BY priority ASC, created_at ASC
        LIMIT $2
        """,
        agent,
        limit,
    )]
    gam_actions = [dict(row) for row in await conn.fetch(
        """
        SELECT id, agent, topic_id, event, related_event_ids, causal_direction, metadata, created_at
        FROM soul_v3.gam_event_graph
        WHERE agent=$1
          AND COALESCE(metadata->>'status', '') IN ('pending','in_progress')
        ORDER BY created_at ASC
        LIMIT $2
        """,
        agent,
        limit,
    )]
    diagnoses = [dict(row) for row in await conn.fetch(
        """
        SELECT id, agent, diagnosis, confidence, root_cause, suggested_fix, target_table, status, trace_id, created_at
        FROM soul_v3.reflective_diagnoses
        WHERE agent=$1 AND status IN ('pending_review','accepted')
        ORDER BY confidence DESC, created_at ASC
        LIMIT $2
        """,
        agent,
        limit,
    )]
    return assess_autonomous_lifecycle(agent, tasks=tasks, gam_actions=gam_actions, diagnoses=diagnoses)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL autonomous lifecycle planner")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--reviewer-agent", default="NEXUS")
    parser.add_argument("--proposal-id", type=int, default=0)
    parser.add_argument("--review-task-id", type=int, default=0)
    parser.add_argument("--decision", default="pending")
    parser.add_argument("--rationale", default="")
    parser.add_argument("command", choices=["assess", "persist-proposals", "delegate-audit", "seed-reviews", "review-gate", "record-review"])
    return parser


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        assessment = await fetch_autonomous_lifecycle_assessment(conn, args.agent, limit=args.limit)
        payload: dict[str, Any] = {"assessment": asdict(assessment)}
        if args.command in {"persist-proposals", "delegate-audit"}:
            persistence = await persist_lifecycle_proposals(conn, assessment)
            payload["persistence"] = asdict(persistence)
        if args.command == "delegate-audit":
            delegation = await delegate_audit_proposals(
                conn,
                args.agent,
                reviewer_agent=args.reviewer_agent,
                limit=args.limit,
            )
            payload["delegation"] = asdict(delegation)
        if args.command in {"seed-reviews", "review-gate"}:
            persistence = await persist_lifecycle_proposals(conn, assessment)
            delegation = await delegate_audit_proposals(
                conn,
                args.agent,
                reviewer_agent=args.reviewer_agent,
                limit=args.limit,
            )
            reviews = await seed_pending_reviews(
                conn,
                args.agent,
                reviewer_agent=args.reviewer_agent,
                limit=args.limit,
            )
            payload["persistence"] = asdict(persistence)
            payload["delegation"] = asdict(delegation)
            payload["reviews"] = asdict(reviews)
        if args.command == "review-gate":
            gate = await assess_execution_gates(
                conn,
                args.agent,
                reviewer_agent=args.reviewer_agent,
                limit=args.limit,
            )
            payload["execution_gate"] = asdict(gate)
        if args.command == "record-review":
            if args.proposal_id <= 0:
                raise ValueError("--proposal-id is required for record-review")
            decision = await record_review_decision(
                conn,
                proposal_id=args.proposal_id,
                reviewer_agent=args.reviewer_agent,
                review_task_id=args.review_task_id or None,
                decision=args.decision,
                rationale=args.rationale or "NEXUS review decision recorded.",
                evidence={"source": "autonomous_lifecycle_cli", "command": "record-review"},
            )
            payload["review_decision"] = asdict(decision)
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        return 0 if assessment.actionable_count else 2
    finally:
        await conn.close()


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
