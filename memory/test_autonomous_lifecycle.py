from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autonomous_lifecycle import (
    assess_execution_gates,
    assess_autonomous_lifecycle,
    build_parser,
    delegate_audit_proposals,
    persist_lifecycle_proposals,
    record_review_decision,
    seed_pending_reviews,
)


class FakeLifecycleConn:
    def __init__(self, *, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.execute_calls: list[str] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.insert_args: tuple[Any, ...] | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append(query)
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        if "FROM soul_v3.autonomous_lifecycle_proposals p" in query:
            return self.existing
        if "INSERT INTO soul_v3.autonomous_lifecycle_proposals" in query:
            self.insert_args = args
            return {"id": 900, "status": "proposed", "cooldown_until": args[7]}
        raise AssertionError(f"unexpected query: {query}")


class FakeDelegationConn:
    def __init__(self, *, existing_task: dict[str, Any] | None = None) -> None:
        self.existing_task = existing_task
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted_task_args: tuple[Any, ...] | None = None
        self.updated_proposal_args: tuple[Any, ...] | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        if "UPDATE soul_v3.autonomous_lifecycle_proposals" in query:
            self.updated_proposal_args = args
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM soul_v3.autonomous_lifecycle_proposals" in query:
            return [
                {
                    "id": 900,
                    "agent": "ADA",
                    "action_type": "continue_task",
                    "target_agent": "ADA",
                    "source_kind": "agent_task",
                    "source_id": 438,
                    "score": 80,
                    "cooldown_seconds": 1800,
                    "cooldown_until": datetime(2026, 5, 20, 4, 25, tzinfo=timezone.utc),
                    "requires_audit": True,
                    "audit_trail": ["agent_tasks.id=438", "status=pending", "priority=3"],
                    "reason": "Sprint 12",
                    "status": "proposed",
                    "evidence": "autonomous_lifecycle_sources=1 proposals=1",
                    "delegated_agent": None,
                    "delegated_task_id": None,
                    "delegated_at": None,
                }
            ]
        raise AssertionError(f"unexpected query: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        if "SELECT id FROM soul_v3.gam_topics" in query:
            return {"id": 22}
        if "FROM soul_v3.agent_tasks" in query and "description LIKE" in query:
            return self.existing_task
        if "INSERT INTO soul_v3.agent_tasks" in query:
            self.inserted_task_args = args
            return {
                "id": 1200,
                "agent": args[0],
                "title": args[1],
                "description": args[2],
                "status": "pending",
                "priority": args[3],
                "deadline": None,
                "created_at": datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc),
                "completed_at": None,
            }
        raise AssertionError(f"unexpected query: {query}")

    async def fetchval(self, query: str, *args: Any) -> int | None:
        self.fetchval_calls.append((query, args))
        if "SELECT id FROM soul_v3.gam_event_graph" in query:
            return None
        if "INSERT INTO soul_v3.gam_event_graph" in query:
            return 3300
        raise AssertionError(f"unexpected query: {query}")


class FakeReviewConn:
    def __init__(self, *, existing_review: dict[str, Any] | None = None) -> None:
        self.existing_review = existing_review
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.insert_review_args: tuple[Any, ...] | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM soul_v3.autonomous_lifecycle_proposals p" in query:
            return [
                {
                    "id": 3,
                    "agent": "ADA",
                    "action_type": "continue_task",
                    "source_kind": "agent_task",
                    "source_id": 438,
                    "requires_audit": True,
                    "delegated_agent": "NEXUS",
                    "delegated_task_id": 448,
                    "delegated_task_status": "pending",
                }
            ]
        raise AssertionError(f"unexpected query: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM soul_v3.autonomous_lifecycle_reviews" in query:
            return self.existing_review
        if "INSERT INTO soul_v3.autonomous_lifecycle_reviews" in query:
            self.insert_review_args = args
            return {
                "id": 120,
                "proposal_id": args[0],
                "reviewer_agent": args[1],
                "review_task_id": args[2],
                "decision": "pending",
            }
        raise AssertionError(f"unexpected query: {query}")


class FakeGateConn:
    def __init__(self, review: dict[str, Any] | None) -> None:
        self.review = review
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM soul_v3.autonomous_lifecycle_proposals p" in query:
            return [
                {
                    "id": 3,
                    "action_type": "continue_task",
                    "source_kind": "agent_task",
                    "source_id": 438,
                    "requires_audit": True,
                    "delegated_agent": "NEXUS",
                    "delegated_task_id": 448,
                }
            ]
        raise AssertionError(f"unexpected query: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM soul_v3.autonomous_lifecycle_reviews" in query:
            return self.review
        raise AssertionError(f"unexpected query: {query}")


class FakeDecisionConn:
    def __init__(self, *, proposal: dict[str, Any] | None = None) -> None:
        self.proposal = proposal or {"id": 3, "delegated_agent": "NEXUS", "delegated_task_id": 447}
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.insert_args: tuple[Any, ...] | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM soul_v3.autonomous_lifecycle_proposals" in query:
            return self.proposal
        if "INSERT INTO soul_v3.autonomous_lifecycle_reviews" in query:
            self.insert_args = args
            return {
                "id": 130,
                "proposal_id": args[0],
                "reviewer_agent": args[1],
                "review_task_id": args[2],
                "decision": args[3],
                "rationale": args[4],
                "evidence": args[5],
            }
        raise AssertionError(f"unexpected query: {query}")


def test_assessment_proposes_scored_task_with_cooldown_and_audit_trail() -> None:
    now = datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)
    tasks = [
        {
            "id": 438,
            "agent": "ADA",
            "title": "Sprint 12 — Autonomous Cognitive Lifecycle Gap",
            "status": "pending",
            "priority": 3,
            "deadline": now + timedelta(minutes=30),
        }
    ]

    assessment = assess_autonomous_lifecycle("ADA", tasks=tasks, gam_actions=[], diagnoses=[], now=now)

    assert assessment.actionable_count == 1
    assert assessment.next_action is not None
    assert assessment.next_action.action_type == "continue_task"
    assert assessment.next_action.score >= 80
    assert assessment.next_action.cooldown_seconds == 1800
    assert assessment.next_action.requires_audit is True
    assert "agent_tasks.id=438" in assessment.next_action.audit_trail


def test_assessment_filters_agent_task_gam_mirrors_and_keeps_native_gam_actions() -> None:
    gam_actions = [
        {
            "id": 504,
            "agent": "ADA",
            "topic_id": 1135,
            "event": "Sprint 12 mirror",
            "metadata": {"source": "agent_task", "status": "pending", "priority": 3},
        },
        {
            "id": 600,
            "agent": "ADA",
            "topic_id": 1200,
            "event": "Investigate autonomous lifecycle drift",
            "metadata": {"status": "pending", "priority": 4},
        },
    ]

    assessment = assess_autonomous_lifecycle("ADA", tasks=[], gam_actions=gam_actions, diagnoses=[])

    assert assessment.actionable_count == 1
    assert assessment.next_action is not None
    assert assessment.next_action.source_kind == "gam_event_graph"
    assert assessment.next_action.source_id == 600
    assert "gam_event_graph.id=600" in assessment.next_action.audit_trail


def test_assessment_routes_pending_diagnosis_to_nexus_review() -> None:
    diagnoses = [
        {
            "id": 77,
            "agent": "ADA",
            "diagnosis": "stale working_state after run-all",
            "confidence": 0.82,
            "status": "pending_review",
            "suggested_fix": "update working_state projection",
        }
    ]

    assessment = assess_autonomous_lifecycle("ADA", tasks=[], gam_actions=[], diagnoses=diagnoses)

    assert assessment.actionable_count == 1
    assert assessment.next_action is not None
    assert assessment.next_action.action_type == "request_nexus_review"
    assert assessment.next_action.target_agent == "NEXUS"
    assert assessment.next_action.score == 82
    assert assessment.next_action.requires_audit is True
    assert "reflective_diagnoses.id=77" in assessment.next_action.audit_trail


def test_assessment_prioritizes_highest_score() -> None:
    tasks = [{"id": 1, "agent": "ADA", "title": "low", "status": "pending", "priority": 8}]
    diagnoses = [{"id": 2, "agent": "ADA", "diagnosis": "high", "confidence": 0.95, "status": "accepted"}]

    assessment = assess_autonomous_lifecycle("ADA", tasks=tasks, gam_actions=[], diagnoses=diagnoses)

    assert assessment.next_action is not None
    assert assessment.next_action.source_kind == "reflective_diagnosis"
    assert assessment.highest_score == 95


def test_cli_parser_accepts_assess() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "--limit", "5", "assess"])

    assert args.agent == "ADA"
    assert args.limit == 5
    assert args.command == "assess"


def test_cli_parser_accepts_persist_proposals() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "--limit", "5", "persist-proposals"])

    assert args.agent == "ADA"
    assert args.limit == 5
    assert args.command == "persist-proposals"


def test_cli_parser_accepts_delegate_audit() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "--reviewer-agent", "NEXUS", "--limit", "5", "delegate-audit"])

    assert args.agent == "ADA"
    assert args.reviewer_agent == "NEXUS"
    assert args.limit == 5
    assert args.command == "delegate-audit"


def test_cli_parser_accepts_review_gate_commands() -> None:
    seed = build_parser().parse_args(["--agent", "ADA", "--reviewer-agent", "NEXUS", "seed-reviews"])
    gate = build_parser().parse_args(["--agent", "ADA", "--reviewer-agent", "NEXUS", "review-gate"])

    assert seed.command == "seed-reviews"
    assert gate.command == "review-gate"
    assert gate.reviewer_agent == "NEXUS"


def test_cli_parser_accepts_record_review() -> None:
    args = build_parser().parse_args(
        [
            "--agent",
            "ADA",
            "--reviewer-agent",
            "NEXUS",
            "--proposal-id",
            "3",
            "--review-task-id",
            "447",
            "--decision",
            "needs_evidence",
            "--rationale",
            "missing proof",
            "record-review",
        ]
    )

    assert args.command == "record-review"
    assert args.proposal_id == 3
    assert args.review_task_id == 447
    assert args.decision == "needs_evidence"
    assert args.rationale == "missing proof"


def test_persist_lifecycle_proposals_inserts_with_cooldown_and_audit_trail() -> None:
    now = datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)
    assessment = assess_autonomous_lifecycle(
        "ADA",
        tasks=[
            {
                "id": 438,
                "agent": "ADA",
                "title": "Sprint 12",
                "status": "pending",
                "priority": 3,
            }
        ],
        gam_actions=[],
        diagnoses=[],
        now=now,
    )
    conn = FakeLifecycleConn()

    result = asyncio.run(persist_lifecycle_proposals(conn, assessment, now=now))  # type: ignore[arg-type]

    assert result.inserted_count == 1
    assert result.existing_count == 0
    assert result.records[0].proposal_id == 900
    assert result.records[0].inserted is True
    assert result.records[0].cooldown_until == now + timedelta(seconds=1800)
    assert any("CREATE TABLE IF NOT EXISTS soul_v3.autonomous_lifecycle_proposals" in call for call in conn.execute_calls)
    assert conn.insert_args is not None
    assert conn.insert_args[0] == "ADA"
    assert conn.insert_args[1] == "continue_task"
    assert conn.insert_args[4] == 438
    assert conn.insert_args[7] == now + timedelta(seconds=1800)
    assert "agent_tasks.id=438" in conn.insert_args[9]
    assert "autonomous_lifecycle_sources=1 proposals=1" in conn.insert_args[11]


def test_persist_lifecycle_proposals_reuses_existing_open_proposal() -> None:
    now = datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)
    existing_until = now + timedelta(minutes=12)
    assessment = assess_autonomous_lifecycle(
        "ADA",
        tasks=[{"id": 438, "agent": "ADA", "title": "Sprint 12", "status": "pending", "priority": 3}],
        gam_actions=[],
        diagnoses=[],
        now=now,
    )
    conn = FakeLifecycleConn(existing={"id": 901, "status": "proposed", "cooldown_until": existing_until})

    result = asyncio.run(persist_lifecycle_proposals(conn, assessment, now=now))  # type: ignore[arg-type]

    assert result.inserted_count == 0
    assert result.existing_count == 1
    assert result.records[0].proposal_id == 901
    assert result.records[0].cooldown_until == existing_until
    assert conn.insert_args is None


def test_delegate_audit_proposals_creates_nexus_task_and_links_proposal() -> None:
    conn = FakeDelegationConn()

    result = asyncio.run(delegate_audit_proposals(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert result.candidate_count == 1
    assert result.delegated_count == 1
    assert result.existing_count == 0
    assert result.records[0].proposal_id == 900
    assert result.records[0].delegated_task_id == 1200
    assert result.records[0].gam_event_id == 3300
    assert conn.inserted_task_args is not None
    assert conn.inserted_task_args[0] == "NEXUS"
    assert "Review ADA lifecycle proposal 900" in conn.inserted_task_args[1]
    assert "autonomous_lifecycle_proposal_id=900" in conn.inserted_task_args[2]
    assert "Required review: approve, reject, or request more evidence" in conn.inserted_task_args[2]
    assert conn.inserted_task_args[3] == 3
    assert conn.updated_proposal_args == ("NEXUS", 1200, 900)


def test_delegate_audit_proposals_reuses_existing_nexus_task() -> None:
    existing_task = {
        "id": 1201,
        "agent": "NEXUS",
        "title": "Review ADA lifecycle proposal 900",
        "description": "autonomous_lifecycle_proposal_id=900",
        "status": "pending",
        "priority": 3,
        "deadline": None,
        "created_at": datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc),
        "completed_at": None,
    }
    conn = FakeDelegationConn(existing_task=existing_task)

    result = asyncio.run(delegate_audit_proposals(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert result.delegated_count == 0
    assert result.existing_count == 1
    assert result.records[0].delegated_task_id == 1201
    assert conn.inserted_task_args is None
    assert conn.updated_proposal_args == ("NEXUS", 1201, 900)


def test_seed_pending_reviews_creates_pending_review_for_delegated_proposal() -> None:
    conn = FakeReviewConn()

    result = asyncio.run(seed_pending_reviews(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert result.candidate_count == 1
    assert result.inserted_count == 1
    assert result.existing_count == 0
    assert result.records[0].proposal_id == 3
    assert result.records[0].review_task_id == 448
    assert result.records[0].decision == "pending"
    assert conn.insert_review_args is not None
    assert conn.insert_review_args[0] == 3
    assert conn.insert_review_args[1] == "NEXUS"
    assert conn.insert_review_args[2] == 448


def test_seed_pending_reviews_reuses_existing_review() -> None:
    conn = FakeReviewConn(
        existing_review={
            "id": 121,
            "proposal_id": 3,
            "reviewer_agent": "NEXUS",
            "review_task_id": 448,
            "decision": "pending",
        }
    )

    result = asyncio.run(seed_pending_reviews(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert result.inserted_count == 0
    assert result.existing_count == 1
    assert result.records[0].review_id == 121
    assert conn.insert_review_args is None


def test_execution_gate_blocks_pending_review() -> None:
    conn = FakeGateConn({"id": 121, "decision": "pending", "reviewer_agent": "NEXUS", "review_task_id": 448, "rationale": ""})

    gate = asyncio.run(assess_execution_gates(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert gate.gate_count == 1
    assert gate.allowed_count == 0
    assert gate.blocked_count == 1
    assert gate.gates[0].allowed is False
    assert gate.gates[0].decision == "pending"
    assert "nexus_review:pending" in gate.gates[0].missing


def test_execution_gate_allows_explicit_approval() -> None:
    conn = FakeGateConn({"id": 122, "decision": "approved", "reviewer_agent": "NEXUS", "review_task_id": 448, "rationale": "ok"})

    gate = asyncio.run(assess_execution_gates(conn, "ADA", reviewer_agent="NEXUS"))  # type: ignore[arg-type]

    assert gate.allowed_count == 1
    assert gate.blocked_count == 0
    assert gate.gates[0].allowed is True
    assert gate.gates[0].missing == []


def test_record_review_decision_inserts_terminal_decision() -> None:
    conn = FakeDecisionConn()

    result = asyncio.run(
        record_review_decision(
            conn,  # type: ignore[arg-type]
            proposal_id=3,
            reviewer_agent="NEXUS",
            review_task_id=447,
            decision="needs_evidence",
            rationale="missing observation",
            evidence={"probe": "review"},
        )
    )

    assert result.review_id == 130
    assert result.proposal_id == 3
    assert result.reviewer_agent == "NEXUS"
    assert result.review_task_id == 447
    assert result.decision == "needs_evidence"
    assert "missing observation" in result.rationale
    assert conn.insert_args is not None
    assert conn.insert_args[3] == "needs_evidence"


def test_record_review_decision_rejects_reviewer_mismatch() -> None:
    conn = FakeDecisionConn(proposal={"id": 3, "delegated_agent": "NEXUS", "delegated_task_id": 447})

    try:
        asyncio.run(
            record_review_decision(
                conn,  # type: ignore[arg-type]
                proposal_id=3,
                reviewer_agent="ADA",
                review_task_id=447,
                decision="approved",
                rationale="bad reviewer",
            )
        )
    except ValueError as exc:
        assert "delegated to NEXUS" in str(exc)
    else:
        raise AssertionError("expected reviewer mismatch to fail")
