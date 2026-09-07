#!/usr/bin/env python3
"""Cross-agent governance automation for SEAL.

Turns governance/challenge records into auditable reviewer tasks and gates them
on an explicit reviewer decision. It does not execute the requested action.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
from task_lifecycle import connect_db
from autonomous_lifecycle import _sync_agent_task_to_gam


@dataclass(frozen=True)
class GovernanceWorkItem:
    source_kind: str
    source_id: int
    challenger_agent: str
    target_agent: str | None
    category: str
    score: int
    reason: str
    audit_trail: list[str]


@dataclass(frozen=True)
class GovernanceAssessment:
    agent: str
    reviewer_agent: str
    items: list[GovernanceWorkItem]
    total_sources: int
    actionable_count: int
    highest_score: int
    evidence: str


@dataclass(frozen=True)
class GovernanceReviewRecord:
    review_id: int
    source_kind: str
    source_id: int
    reviewer_agent: str
    review_task_id: int | None
    status: str
    inserted: bool


@dataclass(frozen=True)
class GovernanceAutomationResult:
    agent: str
    reviewer_agent: str
    candidate_count: int
    inserted_count: int
    existing_count: int
    delegated_count: int
    records: list[GovernanceReviewRecord]
    evidence: str


@dataclass(frozen=True)
class GovernanceGate:
    review_id: int
    source_kind: str
    source_id: int
    allowed: bool
    decision: str
    missing: list[str]
    evidence: str


@dataclass(frozen=True)
class GovernanceGateAssessment:
    agent: str
    reviewer_agent: str
    gate_count: int
    allowed_count: int
    blocked_count: int
    gates: list[GovernanceGate]
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


def _category_from_row(row: dict[str, Any]) -> str:
    response = _json_dict(row.get("response"))
    decision = response.get("category") or response.get("decision", {}).get("category")
    if decision:
        return str(decision)
    topic = str(row.get("topic") or "")
    if topic.startswith("governance:"):
        parts = topic.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return "cross_agent_governance"


def _score_from_category(category: str) -> int:
    return 95 if category in {"destructive_operation", "memory_poisoning", "identity_drift"} else 85


def assess_cross_agent_governance(
    agent: str,
    *,
    challenge_rows: list[dict[str, Any]],
    reviewer_agent: str = "NEXUS",
) -> GovernanceAssessment:
    items: list[GovernanceWorkItem] = []
    for row in challenge_rows:
        source_id = int(row.get("id") or 0)
        if source_id <= 0:
            continue
        category = _category_from_row(row)
        challenger = str(row.get("challenger_agent") or agent)
        target = row.get("target_agent")
        reason = str(row.get("challenge") or row.get("topic") or "governance item requires review")
        score = _score_from_category(category)
        items.append(
            GovernanceWorkItem(
                source_kind="agent_challenge",
                source_id=source_id,
                challenger_agent=challenger,
                target_agent=str(target) if target is not None else None,
                category=category,
                score=score,
                reason=reason,
                audit_trail=[
                    f"agent_challenges.id={source_id}",
                    f"category={category}",
                    f"challenger={challenger}",
                    f"target={target or 'none'}",
                ],
            )
        )
    items.sort(key=lambda item: (-item.score, item.source_id))
    evidence = (
        f"cross_agent_governance_sources={len(challenge_rows)} actionable={len(items)} "
        f"highest_score={items[0].score if items else 0} reviewer={reviewer_agent}"
    )
    return GovernanceAssessment(
        agent=agent,
        reviewer_agent=reviewer_agent,
        items=items,
        total_sources=len(challenge_rows),
        actionable_count=len(items),
        highest_score=items[0].score if items else 0,
        evidence=evidence,
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.cross_agent_governance_reviews (
            id              BIGSERIAL PRIMARY KEY,
            agent           TEXT        NOT NULL,
            reviewer_agent  TEXT        NOT NULL,
            source_kind     TEXT        NOT NULL,
            source_id       BIGINT      NOT NULL,
            category        TEXT        NOT NULL,
            status          TEXT        NOT NULL CHECK (status IN ('pending','approved','rejected','needs_evidence')) DEFAULT 'pending',
            review_task_id  BIGINT      REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
            rationale       TEXT        NOT NULL DEFAULT '',
            evidence        JSONB       NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at      TIMESTAMPTZ
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cross_agent_governance_reviews_source
        ON soul_v3.cross_agent_governance_reviews(agent, source_kind, source_id, reviewer_agent)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cross_agent_governance_reviews_status
        ON soul_v3.cross_agent_governance_reviews(reviewer_agent, status, created_at DESC)
        """
    )


async def fetch_governance_assessment(
    conn: asyncpg.Connection,
    agent: str,
    *,
    reviewer_agent: str = "NEXUS",
    limit: int = 20,
) -> GovernanceAssessment:
    await ensure_schema(conn)
    rows = [dict(row) for row in await conn.fetch(
        """
        SELECT c.id, c.challenger_agent, c.target_agent, c.topic, c.challenge, c.response,
               c.resolved, c.resolution, c.debate_id, c.created_at
        FROM soul_v3.agent_challenges c
        LEFT JOIN soul_v3.cross_agent_governance_reviews r
          ON r.source_kind='agent_challenge'
         AND r.source_id=c.id
         AND r.agent=$1
         AND r.reviewer_agent=$2
         AND r.status IN ('pending','approved','rejected','needs_evidence')
        WHERE (c.challenger_agent=$1 OR c.target_agent=$1 OR c.challenger_agent=$2 OR c.target_agent=$2)
          AND (c.resolved=false OR c.topic LIKE 'governance:%')
          AND r.id IS NULL
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT $3
        """,
        agent,
        reviewer_agent,
        limit,
    )]
    return assess_cross_agent_governance(agent, challenge_rows=rows, reviewer_agent=reviewer_agent)


def _review_task_description(item: GovernanceWorkItem, assessment: GovernanceAssessment) -> str:
    audit_lines = "\n".join(f"- {entry}" for entry in item.audit_trail)
    return (
        f"cross_agent_governance_source={item.source_kind}#{item.source_id}\n"
        f"origin_agent={assessment.agent}\n"
        f"reviewer_agent={assessment.reviewer_agent}\n"
        f"category={item.category}\n"
        f"score={item.score}\n"
        f"reason={item.reason}\n"
        f"evidence={assessment.evidence}\n"
        "Required review: approve, reject, or request more evidence before any cross-agent action proceeds.\n"
        "Audit trail:\n"
        f"{audit_lines}"
    )


async def persist_and_delegate_governance_reviews(
    conn: asyncpg.Connection,
    assessment: GovernanceAssessment,
    *,
    temporary: bool = False,
    marker: str | None = None,
) -> GovernanceAutomationResult:
    await ensure_schema(conn)
    records: list[GovernanceReviewRecord] = []
    inserted_count = 0
    existing_count = 0
    delegated_count = 0
    for item in assessment.items:
        existing = await conn.fetchrow(
            """
            SELECT id, source_kind, source_id, reviewer_agent, review_task_id, status
            FROM soul_v3.cross_agent_governance_reviews
            WHERE agent=$1 AND reviewer_agent=$2 AND source_kind=$3 AND source_id=$4
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            assessment.agent,
            assessment.reviewer_agent,
            item.source_kind,
            item.source_id,
        )
        inserted = False
        if existing:
            review = dict(existing)
            existing_count += 1
        else:
            review = dict(await conn.fetchrow(
                """
                INSERT INTO soul_v3.cross_agent_governance_reviews
                    (agent, reviewer_agent, source_kind, source_id, category, status, evidence)
                VALUES ($1,$2,$3,$4,$5,'pending',$6::jsonb)
                RETURNING id, source_kind, source_id, reviewer_agent, review_task_id, status
                """,
                assessment.agent,
                assessment.reviewer_agent,
                item.source_kind,
                item.source_id,
                item.category,
                json.dumps(
                    {
                        "source": "cross_agent_governance",
                        "audit_trail": item.audit_trail,
                        "temporary": temporary,
                        "temporary_marker": marker,
                    },
                    ensure_ascii=False,
                ),
            ))
            inserted = True
            inserted_count += 1
        review_task_id = review.get("review_task_id")
        if review_task_id is None and review.get("status") == "pending":
            title = f"Review cross-agent governance {item.source_kind}#{item.source_id}: {item.category}"
            task = dict(await conn.fetchrow(
                """
                INSERT INTO soul_v3.agent_tasks (agent, title, description, priority)
                VALUES ($1,$2,$3,$4)
                RETURNING id, agent, title, description, status, priority, deadline, created_at, completed_at
                """,
                assessment.reviewer_agent,
                title,
                _review_task_description(item, assessment)
                + (f"\ntemporary_marker={marker}" if marker else ""),
                2 if item.score >= 90 else 3,
            ))
            await _sync_agent_task_to_gam(conn, task, "pending")
            await conn.execute(
                """
                UPDATE soul_v3.cross_agent_governance_reviews
                SET review_task_id=$1
                WHERE id=$2
                """,
                int(task["id"]),
                int(review["id"]),
            )
            review_task_id = int(task["id"])
            delegated_count += 1
        records.append(
            GovernanceReviewRecord(
                review_id=int(review["id"]),
                source_kind=str(review["source_kind"]),
                source_id=int(review["source_id"]),
                reviewer_agent=str(review["reviewer_agent"]),
                review_task_id=int(review_task_id) if review_task_id is not None else None,
                status=str(review["status"]),
                inserted=inserted,
            )
        )
    evidence = (
        f"cross_agent_governance_reviews inserted={inserted_count} existing={existing_count} "
        f"delegated={delegated_count} candidates={len(assessment.items)} reviewer={assessment.reviewer_agent}"
    )
    return GovernanceAutomationResult(
        agent=assessment.agent,
        reviewer_agent=assessment.reviewer_agent,
        candidate_count=len(assessment.items),
        inserted_count=inserted_count,
        existing_count=existing_count,
        delegated_count=delegated_count,
        records=records,
        evidence=evidence,
    )


async def record_governance_review_decision(
    conn: asyncpg.Connection,
    *,
    review_id: int,
    reviewer_agent: str,
    decision: str,
    rationale: str,
    evidence: dict[str, Any] | None = None,
) -> GovernanceReviewRecord:
    await ensure_schema(conn)
    if decision not in {"approved", "rejected", "needs_evidence", "pending"}:
        raise ValueError("decision must be pending, approved, rejected, or needs_evidence")
    row = await conn.fetchrow(
        """
        UPDATE soul_v3.cross_agent_governance_reviews
        SET status=$1, rationale=$2, evidence=COALESCE(evidence, '{}'::jsonb) || $3::jsonb,
            decided_at=CASE WHEN $1='pending' THEN NULL ELSE NOW() END
        WHERE id=$4 AND reviewer_agent=$5
        RETURNING id, source_kind, source_id, reviewer_agent, review_task_id, status
        """,
        decision,
        rationale,
        json.dumps(evidence or {"source": "record_governance_review_decision"}, ensure_ascii=False),
        review_id,
        reviewer_agent,
    )
    if not row:
        raise ValueError(f"review_id {review_id} not found for reviewer {reviewer_agent}")
    task_id = row["review_task_id"]
    if task_id is not None and decision in {"approved", "rejected", "needs_evidence"}:
        await conn.execute(
            """
            UPDATE soul_v3.agent_tasks
            SET status='completed', completed_at=COALESCE(completed_at, NOW())
            WHERE id=$1 AND agent=$2
            """,
            int(task_id),
            reviewer_agent,
        )
    return GovernanceReviewRecord(
        review_id=int(row["id"]),
        source_kind=str(row["source_kind"]),
        source_id=int(row["source_id"]),
        reviewer_agent=str(row["reviewer_agent"]),
        review_task_id=int(task_id) if task_id is not None else None,
        status=str(row["status"]),
        inserted=False,
    )


async def assess_governance_gates(
    conn: asyncpg.Connection,
    agent: str,
    *,
    reviewer_agent: str = "NEXUS",
    limit: int = 20,
) -> GovernanceGateAssessment:
    await ensure_schema(conn)
    rows = [dict(row) for row in await conn.fetch(
        """
        SELECT id, source_kind, source_id, reviewer_agent, review_task_id, status
        FROM soul_v3.cross_agent_governance_reviews
        WHERE agent=$1 AND reviewer_agent=$2
        ORDER BY created_at DESC, id DESC
        LIMIT $3
        """,
        agent,
        reviewer_agent,
        limit,
    )]
    gates: list[GovernanceGate] = []
    for row in rows:
        missing: list[str] = []
        if row.get("review_task_id") is None:
            missing.append("review_task:missing")
        decision = str(row.get("status") or "missing")
        if decision != "approved":
            missing.append(f"review:{decision}")
        allowed = not missing
        gates.append(
            GovernanceGate(
                review_id=int(row["id"]),
                source_kind=str(row["source_kind"]),
                source_id=int(row["source_id"]),
                allowed=allowed,
                decision=decision,
                missing=missing,
                evidence=f"review_id={row['id']} allowed={allowed} decision={decision} missing={','.join(missing) or 'none'}",
            )
        )
    allowed_count = sum(1 for gate in gates if gate.allowed)
    evidence = f"cross_agent_governance_gates={len(gates)} allowed={allowed_count} blocked={len(gates)-allowed_count}"
    return GovernanceGateAssessment(
        agent=agent,
        reviewer_agent=reviewer_agent,
        gate_count=len(gates),
        allowed_count=allowed_count,
        blocked_count=len(gates) - allowed_count,
        gates=gates,
        evidence=evidence,
    )


async def cleanup_temporary_governance(conn: asyncpg.Connection, marker: str) -> int:
    await ensure_schema(conn)
    review_task_ids = [int(row["review_task_id"]) for row in await conn.fetch(
        """
        SELECT review_task_id
        FROM soul_v3.cross_agent_governance_reviews
        WHERE evidence->>'temporary_marker'=$1 AND review_task_id IS NOT NULL
        """,
        marker,
    )]
    deleted_reviews = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.cross_agent_governance_reviews
            WHERE evidence->>'temporary_marker'=$1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        marker,
    ))
    deleted_tasks = 0
    if review_task_ids:
        await conn.execute(
            """
            DELETE FROM soul_v3.gam_event_graph
            WHERE agent='NEXUS'
              AND metadata->>'source'='agent_task'
              AND (metadata->>'task_id')::bigint = ANY($1::bigint[])
            """,
            review_task_ids,
        )
        deleted_tasks = int(await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM soul_v3.agent_tasks
                WHERE id = ANY($1::bigint[]) AND description LIKE $2
                RETURNING id
            ) SELECT COUNT(*) FROM deleted
            """,
            review_task_ids,
            f"%temporary_marker={marker}%",
        ))
    deleted_challenges = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.agent_challenges
            WHERE topic LIKE $1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        f"%{marker}%",
    ))
    deleted_debates = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.debate_log
            WHERE topic LIKE $1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        f"%{marker}%",
    ))
    return deleted_reviews + deleted_tasks + deleted_challenges + deleted_debates


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        assessment = await fetch_governance_assessment(conn, args.agent, reviewer_agent=args.reviewer_agent, limit=args.limit)
        payload: dict[str, Any] = {"assessment": asdict(assessment)}
        if args.command == "delegate":
            payload["automation"] = asdict(await persist_and_delegate_governance_reviews(conn, assessment))
        if args.command == "gates":
            payload["gates"] = asdict(await assess_governance_gates(conn, args.agent, reviewer_agent=args.reviewer_agent, limit=args.limit))
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        return 0
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL cross-agent governance automation")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--reviewer-agent", default="NEXUS")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("command", choices=["assess", "delegate", "gates"])
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
