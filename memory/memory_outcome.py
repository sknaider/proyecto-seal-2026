#!/usr/bin/env python3
"""Outcome scoring for SOUL memories.

P3 goal: memories learn from outcomes. This module updates the existing
``utility_score``, ``confidence_score`` and ``surprise_score`` columns and appends
structured evidence to ``metadata.outcome_history``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from seal_secrets import pg_dsn


DEFAULT_AGENT = "ADA"
OUTCOME_REWARDS = {
    "success": 1.0,
    "partial": 0.5,
    "failure": 0.0,
    "unknown": 0.5,
}


@dataclass(frozen=True)
class OutcomeUpdate:
    memory_id: int
    outcome: str
    reward: float
    old_utility: float
    new_utility: float
    old_confidence: float
    new_confidence: float
    old_surprise: float | None
    new_surprise: float
    evidence: str


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalized_outcome(outcome: str) -> str:
    value = outcome.strip().lower()
    return value if value in OUTCOME_REWARDS else "unknown"


def score_values(
    outcome: str,
    old_utility: float | None,
    old_confidence: float | None,
    old_surprise: float | None,
    evidence_quality: float = 1.0,
    alpha: float = 0.2,
) -> tuple[float, float, float, float]:
    normalized = normalized_outcome(outcome)
    reward = OUTCOME_REWARDS[normalized]
    utility = clamp01(old_utility if old_utility is not None else 0.5)
    confidence = clamp01(old_confidence if old_confidence is not None else 0.7)
    previous_surprise = old_surprise if old_surprise is not None else 0.0
    quality = clamp01(evidence_quality)

    new_utility = clamp01(utility + alpha * (reward - utility))
    if normalized == "success":
        new_confidence = clamp01(confidence + 0.10 * quality * (1.0 - confidence))
    elif normalized == "failure":
        new_confidence = clamp01(confidence - 0.15 * quality * confidence)
    elif normalized == "partial":
        new_confidence = clamp01(confidence + 0.02 * quality * (0.5 - confidence))
    else:
        new_confidence = confidence

    prediction_error = abs(reward - utility)
    new_surprise = clamp01((float(previous_surprise) * 0.7) + (prediction_error * 0.3))
    return reward, new_utility, new_confidence, new_surprise


def _decode_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw_metadata": value}
        return decoded if isinstance(decoded, dict) else {"_raw_metadata": decoded}
    return {"_raw_metadata": str(value)}


def append_outcome_history(
    metadata: dict[str, Any],
    outcome: str,
    reward: float,
    evidence: str,
    agent: str,
    utility_score: float,
    confidence_score: float,
    surprise_score: float,
) -> dict[str, Any]:
    history = metadata.get("outcome_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "outcome": outcome,
            "reward": reward,
            "evidence": evidence[:500],
            "agent": agent,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "utility_score": utility_score,
            "confidence_score": confidence_score,
            "surprise_score": surprise_score,
        }
    )
    metadata["outcome_history"] = history[-20:]
    metadata["last_outcome"] = history[-1]
    return metadata


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(resolve_db_dsn())


async def record_memory_outcome(
    conn: asyncpg.Connection,
    memory_id: int,
    outcome: str,
    evidence: str,
    agent: str = DEFAULT_AGENT,
    evidence_quality: float = 1.0,
) -> OutcomeUpdate:
    row = await conn.fetchrow(
        """
        SELECT id, utility_score, confidence_score, surprise_score, metadata
        FROM soul_v3.memories
        WHERE id=$1 AND invalid_at IS NULL
        """,
        memory_id,
    )
    if row is None:
        raise ValueError(f"Memory {memory_id} not found or invalid")

    normalized = normalized_outcome(outcome)
    reward, new_utility, new_confidence, new_surprise = score_values(
        normalized,
        row["utility_score"],
        row["confidence_score"],
        row["surprise_score"],
        evidence_quality=evidence_quality,
    )
    metadata = append_outcome_history(
        _decode_metadata(row["metadata"]),
        normalized,
        reward,
        evidence,
        agent,
        new_utility,
        new_confidence,
        new_surprise,
    )
    await conn.execute(
        """
        UPDATE soul_v3.memories
        SET utility_score=$1,
            confidence_score=$2,
            surprise_score=$3,
            metadata=$4::jsonb,
            updated_at=NOW()
        WHERE id=$5
        """,
        new_utility,
        new_confidence,
        new_surprise,
        json.dumps(metadata, sort_keys=True),
        memory_id,
    )
    return OutcomeUpdate(
        memory_id=memory_id,
        outcome=normalized,
        reward=reward,
        old_utility=clamp01(row["utility_score"] if row["utility_score"] is not None else 0.5),
        new_utility=new_utility,
        old_confidence=clamp01(row["confidence_score"] if row["confidence_score"] is not None else 0.7),
        new_confidence=new_confidence,
        old_surprise=row["surprise_score"],
        new_surprise=new_surprise,
        evidence=evidence,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record memory outcome scores")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("memory_id", type=int)
    record.add_argument("outcome", choices=sorted(OUTCOME_REWARDS))
    record.add_argument("--agent", default=DEFAULT_AGENT)
    record.add_argument("--evidence", required=True)
    record.add_argument("--evidence-quality", type=float, default=1.0)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "record":
        conn = await connect_db()
        try:
            update = await record_memory_outcome(
                conn,
                args.memory_id,
                args.outcome,
                args.evidence,
                agent=args.agent,
                evidence_quality=args.evidence_quality,
            )
        finally:
            await conn.close()
        print(json.dumps(asdict(update), indent=2, ensure_ascii=False))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
