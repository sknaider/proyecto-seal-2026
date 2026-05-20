#!/usr/bin/env python3
"""Skill/instinct factory for SEAL.

Repeated success can promote a skill; repeated failure creates a guardrail
instinct. Records carry keep/revert evidence and are reviewable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
from task_lifecycle import connect_db


@dataclass(frozen=True)
class FactoryCandidate:
    kind: str
    agent: str
    source_suite: str
    count: int
    score: int
    name: str
    description: str
    keep_revert_evidence: str


@dataclass(frozen=True)
class FactoryAssessment:
    agent: str
    candidates: list[FactoryCandidate]
    total_runs: int
    skill_candidate_count: int
    guardrail_candidate_count: int
    evidence: str


@dataclass(frozen=True)
class FactoryRecord:
    record_kind: str
    record_id: int
    candidate_kind: str
    source_suite: str
    inserted: bool
    evidence: str


@dataclass(frozen=True)
class FactoryPersistenceResult:
    agent: str
    candidate_count: int
    inserted_count: int
    existing_count: int
    records: list[FactoryRecord]
    evidence: str


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:32] or "unknown"


def assess_skill_instinct_factory(
    agent: str,
    *,
    evaluation_rows: list[dict[str, Any]],
    min_success_repetitions: int = 3,
    min_failure_repetitions: int = 2,
) -> FactoryAssessment:
    successes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluation_rows:
        if str(row.get("agent") or agent) != agent:
            continue
        suite = str(row.get("suite_name") or "")
        if not suite:
            continue
        if bool(row.get("passed")):
            successes[suite].append(row)
        else:
            failures[suite].append(row)
    candidates: list[FactoryCandidate] = []
    for suite, rows in sorted(successes.items()):
        if len(rows) >= min_success_repetitions:
            score = round(sum(int(row.get("score") or 0) for row in rows) / len(rows))
            name = f"auto_skill_{agent.lower()}_{_slug(suite)}"[:64]
            candidates.append(
                FactoryCandidate(
                    kind="skill",
                    agent=agent,
                    source_suite=suite,
                    count=len(rows),
                    score=score,
                    name=name,
                    description=f"Promoted from {len(rows)} repeated successful evaluation runs for {suite}.",
                    keep_revert_evidence=f"keep if future {suite} score>=90; revert if two consecutive failures or NEXUS rejects",
                )
            )
    for suite, rows in sorted(failures.items()):
        if len(rows) >= min_failure_repetitions:
            worst = min(int(row.get("score") or 0) for row in rows)
            name = f"guardrail_{agent.lower()}_{_slug(suite)}"[:64]
            candidates.append(
                FactoryCandidate(
                    kind="guardrail_instinct",
                    agent=agent,
                    source_suite=suite,
                    count=len(rows),
                    score=worst,
                    name=name,
                    description=f"When {suite} repeatedly fails, stop closure and require focused fix + test evidence.",
                    keep_revert_evidence=f"keep until {suite} has three consecutive passing runs; revert if false-positive blocks work",
                )
            )
    candidates.sort(key=lambda c: (c.kind, c.source_suite))
    evidence = (
        f"skill_instinct_factory_runs={len(evaluation_rows)} candidates={len(candidates)} "
        f"skills={sum(1 for c in candidates if c.kind == 'skill')} "
        f"guardrails={sum(1 for c in candidates if c.kind == 'guardrail_instinct')}"
    )
    return FactoryAssessment(
        agent=agent,
        candidates=candidates,
        total_runs=len(evaluation_rows),
        skill_candidate_count=sum(1 for c in candidates if c.kind == "skill"),
        guardrail_candidate_count=sum(1 for c in candidates if c.kind == "guardrail_instinct"),
        evidence=evidence,
    )


async def fetch_factory_assessment(
    conn: asyncpg.Connection,
    agent: str,
    *,
    limit: int = 200,
) -> FactoryAssessment:
    rows = [dict(row) for row in await conn.fetch(
        """
        SELECT id, suite_name, score, passed, evidence, details, agent, run_at, notes
        FROM soul_v3.evaluation_runs
        WHERE agent=$1
        ORDER BY run_at DESC, id DESC
        LIMIT $2
        """,
        agent,
        limit,
    )]
    return assess_skill_instinct_factory(agent, evaluation_rows=rows)


async def persist_factory_candidates(
    conn: asyncpg.Connection,
    assessment: FactoryAssessment,
    *,
    temporary: bool = False,
    marker: str | None = None,
) -> FactoryPersistenceResult:
    records: list[FactoryRecord] = []
    inserted_count = 0
    existing_count = 0
    for candidate in assessment.candidates:
        metadata = {
            "source": "skill_instinct_factory",
            "candidate_kind": candidate.kind,
            "source_suite": candidate.source_suite,
            "count": candidate.count,
            "score": candidate.score,
            "keep_revert_evidence": candidate.keep_revert_evidence,
            "temporary": temporary,
            "temporary_marker": marker,
        }
        if candidate.kind == "skill":
            existing = await conn.fetchrow(
                """
                SELECT id FROM soul_v3.skills
                WHERE agent=$1 AND name=$2 AND invalid_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                candidate.agent,
                candidate.name,
            )
            if existing:
                existing_count += 1
                records.append(FactoryRecord("skill", int(existing["id"]), candidate.kind, candidate.source_suite, False, "existing skill"))
                continue
            row = await conn.fetchrow(
                """
                INSERT INTO soul_v3.skills
                    (agent, name, description, skill_path, type, success_count, failure_count,
                     pending_review, execution_environment, metadata, boot_load)
                VALUES ($1,$2,$3,$4,'guiding', $5, 0, true, 'sandbox', $6::jsonb, false)
                RETURNING id
                """,
                candidate.agent,
                candidate.name,
                candidate.description,
                f"generated://skill_instinct_factory/{candidate.name}",
                candidate.count,
                json.dumps(metadata, ensure_ascii=False),
            )
            inserted_count += 1
            records.append(FactoryRecord("skill", int(row["id"]), candidate.kind, candidate.source_suite, True, candidate.keep_revert_evidence))
        else:
            trigger = f"evaluation suite {candidate.source_suite} repeatedly fails"
            existing = await conn.fetchrow(
                """
                SELECT id FROM soul_v3.instincts
                WHERE agent=$1 AND trigger_condition=$2 AND invalid_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                candidate.agent,
                trigger,
            )
            if existing:
                existing_count += 1
                records.append(FactoryRecord("instinct", int(existing["id"]), candidate.kind, candidate.source_suite, False, "existing guardrail"))
                continue
            row = await conn.fetchrow(
                """
                INSERT INTO soul_v3.instincts
                    (agent, trigger_condition, action, strength, success_count, failure_count, metadata)
                VALUES ($1,$2,$3,$4,0,$5,$6::jsonb)
                RETURNING id
                """,
                candidate.agent,
                trigger,
                candidate.description,
                0.72,
                candidate.count,
                json.dumps(metadata, ensure_ascii=False),
            )
            inserted_count += 1
            records.append(FactoryRecord("instinct", int(row["id"]), candidate.kind, candidate.source_suite, True, candidate.keep_revert_evidence))
    evidence = (
        f"skill_instinct_factory_persisted inserted={inserted_count} existing={existing_count} "
        f"candidates={len(assessment.candidates)} temporary={temporary}"
    )
    return FactoryPersistenceResult(
        agent=assessment.agent,
        candidate_count=len(assessment.candidates),
        inserted_count=inserted_count,
        existing_count=existing_count,
        records=records,
        evidence=evidence,
    )


async def cleanup_temporary_factory(conn: asyncpg.Connection, marker: str) -> int:
    deleted_skills = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.skills
            WHERE metadata->>'source'='skill_instinct_factory'
              AND metadata->>'temporary'='true'
              AND metadata->>'temporary_marker'=$1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        marker,
    ))
    deleted_instincts = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.instincts
            WHERE metadata->>'source'='skill_instinct_factory'
              AND metadata->>'temporary'='true'
              AND metadata->>'temporary_marker'=$1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        marker,
    ))
    deleted_runs = int(await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM soul_v3.evaluation_runs
            WHERE details->>'temporary_marker'=$1
            RETURNING id
        ) SELECT COUNT(*) FROM deleted
        """,
        marker,
    ))
    return deleted_skills + deleted_instincts + deleted_runs


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect_db()
    try:
        assessment = await fetch_factory_assessment(conn, args.agent, limit=args.limit)
        payload: dict[str, Any] = {"assessment": asdict(assessment)}
        if args.command == "persist":
            payload["persistence"] = asdict(await persist_factory_candidates(conn, assessment))
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        return 0 if assessment.candidates else 2
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL skill/instinct factory")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("command", choices=["assess", "persist"])
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
