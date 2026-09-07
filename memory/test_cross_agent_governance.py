from __future__ import annotations

import asyncio
from typing import Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cross_agent_governance import (
    assess_cross_agent_governance,
    persist_and_delegate_governance_reviews,
    record_governance_review_decision,
    assess_governance_gates,
)


def test_assessment_turns_challenge_into_review_item() -> None:
    assessment = assess_cross_agent_governance(
        "ADA",
        challenge_rows=[
            {
                "id": 10,
                "challenger_agent": "ADA",
                "target_agent": "ADA",
                "topic": "governance:destructive_operation:temp",
                "challenge": "DELETE requires confirmation",
                "response": {"category": "destructive_operation"},
            }
        ],
    )

    assert assessment.actionable_count == 1
    assert assessment.highest_score == 95
    assert assessment.items[0].category == "destructive_operation"
    assert "agent_challenges.id=10" in assessment.items[0].audit_trail


class FakeConn:
    def __init__(self) -> None:
        self.review: dict[str, Any] | None = None
        self.task = {
            "id": 501,
            "agent": "NEXUS",
            "title": "Review cross-agent governance",
            "description": "temporary_marker=unit",
            "status": "pending",
            "priority": 2,
            "deadline": None,
            "created_at": None,
            "completed_at": None,
        }
        self.task_completed = False
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any):
        self.executed.append((query, args))
        if "UPDATE soul_v3.cross_agent_governance_reviews" in query and "review_task_id" in query:
            assert self.review is not None
            self.review["review_task_id"] = args[0]
        if "UPDATE soul_v3.agent_tasks" in query:
            self.task_completed = True
        return "OK"

    async def fetchrow(self, query: str, *args: Any):
        if "FROM soul_v3.cross_agent_governance_reviews" in query and query.lstrip().startswith("SELECT"):
            return self.review
        if "INSERT INTO soul_v3.cross_agent_governance_reviews" in query:
            self.review = {
                "id": 77,
                "source_kind": args[2],
                "source_id": args[3],
                "reviewer_agent": args[1],
                "review_task_id": None,
                "status": "pending",
            }
            return self.review
        if "INSERT INTO soul_v3.agent_tasks" in query:
            return self.task
        if "SELECT id FROM soul_v3.gam_topics" in query:
            return {"id": 9}
        if "UPDATE soul_v3.cross_agent_governance_reviews" in query and "RETURNING" in query:
            assert self.review is not None
            self.review["status"] = args[0]
            return self.review
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetchval(self, query: str, *args: Any):
        if "SELECT id FROM soul_v3.gam_event_graph" in query:
            return 800
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetch(self, query: str, *args: Any):
        if "FROM soul_v3.cross_agent_governance_reviews" in query:
            return [self.review] if self.review else []
        raise AssertionError(f"unexpected fetch: {query}")


def test_persist_delegate_review_and_gate() -> None:
    assessment = assess_cross_agent_governance(
        "ADA",
        challenge_rows=[{"id": 10, "challenger_agent": "ADA", "target_agent": "ADA", "topic": "governance:memory_poisoning:temp", "challenge": "bad memory"}],
    )
    conn = FakeConn()

    result = asyncio.run(persist_and_delegate_governance_reviews(conn, assessment, temporary=True, marker="unit"))
    pending_gate = asyncio.run(assess_governance_gates(conn, "ADA"))
    decision = asyncio.run(record_governance_review_decision(conn, review_id=77, reviewer_agent="NEXUS", decision="approved", rationale="unit passed"))
    approved_gate = asyncio.run(assess_governance_gates(conn, "ADA"))

    assert result.inserted_count == 1
    assert result.delegated_count == 1
    assert result.records[0].review_task_id == 501
    assert pending_gate.allowed_count == 0
    assert decision.status == "approved"
    assert conn.task_completed is True
    assert approved_gate.allowed_count == 1
