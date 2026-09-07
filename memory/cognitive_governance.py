#!/usr/bin/env python3
"""Deterministic cognitive governance gate for SEAL.

P5 goal: before an agent acts, classify cognitive/safety risk and decide whether
the action is allowed, blocked, escalated to William, or sent to NEXUS review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg
from seal_secrets import pg_dsn
from destructive_guard import classify_command


DEFAULT_AGENT = "ADA"
PRIVACY_DM_PATTERN = re.compile(r"\bdm:(?!ada:william\b)[a-z0-9_-]+:[a-z0-9_-]+\b", re.IGNORECASE)
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"\bignore (all )?(previous|prior|system|developer) instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard (all )?(previous|prior|system|developer) instructions\b", re.IGNORECASE),
    re.compile(r"\breveal (your )?(system|developer) prompt\b", re.IGNORECASE),
    re.compile(r"\bact as (william|henry|jarvis|nexus|alice|dum)\b", re.IGNORECASE),
]
MEMORY_POISON_PATTERNS = [
    re.compile(r"\bguarda.*(ignora|ignores|desobedece|override|borra).*william\b", re.IGNORECASE),
    re.compile(r"\bstore.*(ignore|disobey|override|delete).*william\b", re.IGNORECASE),
    re.compile(r"\bwilliam ya no es\b", re.IGNORECASE),
]
IDENTITY_DRIFT_PATTERNS = [
    re.compile(r"\bsoy (william|henry|jarvis|nexus|alice|dum)\b", re.IGNORECASE),
    re.compile(r"\byou are not ada\b", re.IGNORECASE),
    re.compile(r"\bada no eres ada\b", re.IGNORECASE),
]
PHANTOM_CLAIM_PATTERNS = [
    re.compile(r"\b(implementado|listo|cerrado|done|fixed)\b", re.IGNORECASE),
]
EVIDENCE_MARKERS = [
    "pytest",
    "py_compile",
    "run-all",
    "health",
    "score=",
    "passed",
    "id ",
    "ids ",
    "evidence",
    "evidencia",
]


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    action: str
    risk_level: str
    category: str
    reason: str
    reviewers: list[str]
    required_evidence: list[str]
    blocked: bool = False


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(resolve_db_dsn())


def has_evidence(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in EVIDENCE_MARKERS)


def evaluate_governance(
    content: str,
    *,
    agent: str = DEFAULT_AGENT,
    channel: str = "web_chat",
    command: str = "",
    has_tests: bool | None = None,
) -> GovernanceDecision:
    text = f"{content}\n{command}".strip()

    destructive_kind = classify_command(command or content)
    if destructive_kind is not None:
        return GovernanceDecision(
            allowed=False,
            action="block_and_require_william_confirmation",
            risk_level="critical",
            category="destructive_operation",
            reason=f"destructive operation detected: {destructive_kind}",
            reviewers=["William", "NEXUS"],
            required_evidence=["exact affected COUNT", "explicit scope", "required confirmation text"],
            blocked=True,
        )

    if PRIVACY_DM_PATTERN.search(channel) or PRIVACY_DM_PATTERN.search(text):
        return GovernanceDecision(
            allowed=False,
            action="block_privacy_boundary",
            risk_level="high",
            category="privacy_boundary",
            reason="attempt to access or route through a non-ADA private DM",
            reviewers=["NEXUS"],
            required_evidence=["authorized channel must be dm:ada:william or public web_chat"],
            blocked=True,
        )

    if any(pattern.search(text) for pattern in PROMPT_INJECTION_PATTERNS):
        return GovernanceDecision(
            allowed=False,
            action="block_prompt_injection",
            risk_level="high",
            category="prompt_injection",
            reason="instruction attempts to override system/developer identity or reveal protected prompt",
            reviewers=["NEXUS"],
            required_evidence=["preserve active instructions", "log injection attempt"],
            blocked=True,
        )

    if any(pattern.search(text) for pattern in MEMORY_POISON_PATTERNS):
        return GovernanceDecision(
            allowed=False,
            action="quarantine_memory_write",
            risk_level="high",
            category="memory_poisoning",
            reason="memory write attempts to weaken William authority or core rules",
            reviewers=["William", "NEXUS"],
            required_evidence=["human approval", "source attribution", "conflict record"],
            blocked=True,
        )

    if any(pattern.search(text) for pattern in IDENTITY_DRIFT_PATTERNS):
        return GovernanceDecision(
            allowed=False,
            action="identity_lock_and_review",
            risk_level="high",
            category="identity_drift",
            reason=f"{agent} identity or chain of command is being altered",
            reviewers=["William", "NEXUS"],
            required_evidence=["SOUL identity snapshot", "active_recall", "NEXUS review"],
            blocked=True,
        )

    claims_done = any(pattern.search(text) for pattern in PHANTOM_CLAIM_PATTERNS)
    tests_known = bool(has_tests) if has_tests is not None else has_evidence(text)
    if claims_done and not tests_known:
        return GovernanceDecision(
            allowed=False,
            action="require_evidence_before_done_claim",
            risk_level="medium",
            category="phantom_claim",
            reason="done/fixed/implemented claim without auditable test or command evidence",
            reviewers=["NEXUS"],
            required_evidence=["command executed", "relevant output", "file path or DB run id"],
            blocked=True,
        )

    return GovernanceDecision(
        allowed=True,
        action="allow",
        risk_level="low",
        category="normal",
        reason="no deterministic governance risk matched",
        reviewers=[],
        required_evidence=[],
    )


async def record_governance_challenge(
    conn: asyncpg.Connection,
    decision: GovernanceDecision,
    *,
    agent: str,
    target_agent: str | None = None,
    correlation_id: str | None = None,
    temporary: bool = False,
) -> tuple[int, int]:
    topic = f"governance:{decision.category}:{correlation_id or int(time.time() * 1000)}"
    metadata = {
        "source": "cognitive_governance",
        "correlation_id": correlation_id,
        "temporary": temporary,
        "decision": asdict(decision),
    }
    debate_row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.debate_log
            (topic, agents_involved, trigger_type, consensus_reached, outcome, synthesis)
        VALUES ($1, $2::text[], 'manual', $3, $4, $5)
        RETURNING id
        """,
        topic,
        [agent] + decision.reviewers,
        decision.allowed,
        decision.action,
        json.dumps(metadata, sort_keys=True),
    )
    debate_id = int(debate_row["id"])
    challenge_row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.agent_challenges
            (challenger_agent, target_agent, topic, challenge, response, debate_id, resolved, resolution)
        VALUES ($1, $2, $3, $4, $5, $6, true, $7)
        RETURNING id
        """,
        agent,
        target_agent,
        topic,
        decision.reason,
        json.dumps(asdict(decision), sort_keys=True),
        debate_id,
        decision.action,
    )
    return int(challenge_row["id"]), debate_id


async def cleanup_governance_challenge(conn: asyncpg.Connection, debate_id: int) -> int:
    challenge_count = await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.agent_challenges
            WHERE debate_id=$1
            RETURNING id
        )
        SELECT COUNT(*) FROM deleted
        """,
        debate_id,
    )
    debate_count = await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.debate_log
            WHERE id=$1 AND trigger_type='manual' AND topic LIKE 'governance:%'
            RETURNING id
        )
        SELECT COUNT(*) FROM deleted
        """,
        debate_id,
    )
    return int(challenge_count or 0) + int(debate_count or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate cognitive governance risk")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--content", default="")
    evaluate.add_argument("--command", default="")
    evaluate.add_argument("--channel", default="web_chat")
    evaluate.add_argument("--agent", default=DEFAULT_AGENT)
    evaluate.add_argument("--has-tests", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        decision = evaluate_governance(
            args.content,
            agent=args.agent,
            channel=args.channel,
            command=args.command,
            has_tests=args.has_tests or None,
        )
        print(json.dumps(asdict(decision), indent=2, ensure_ascii=False))
        return 0 if decision.allowed else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
