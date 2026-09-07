#!/usr/bin/env python3
"""Identity Continuity v2 Phase 5 — importance peer-review sampler.

Queues cross-agent memory-importance samples for review and records calibration
alerts when reviewer suggestions diverge by >=2 points on at least 15% of a
pair's recent samples. Suggestions are signals only; the script never mutates
the reviewed memory importance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_secrets import pg_dsn  # noqa: E402


DEFAULT_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS")
SAMPLE_SIZE = 30
DISCREPANCY_DELTA = 2
DISCREPANCY_RATIO = 0.15


@dataclass
class PairSample:
    reviewer_agent: str
    reviewed_agent: str
    sampled: int


@dataclass
class CalibrationAlert:
    reviewer_agent: str
    reviewed_agent: str
    total: int
    discrepant: int
    ratio: float
    lifecycle_event_id: int | None = None


@dataclass
class PeerReviewRun:
    run_id: str
    dry_run: bool
    pairs_checked: int
    samples_inserted: int
    alerts: list[CalibrationAlert]
    pair_samples: list[PairSample]
    self_test_ok: bool = False


class SelfTestRollback(Exception):
    def __init__(self, run: PeerReviewRun):
        super().__init__("rollback_self_test")
        self.run = run


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.importance_review (
            id              BIGSERIAL PRIMARY KEY,
            reviewed_agent  TEXT NOT NULL,
            reviewer_agent  TEXT NOT NULL,
            memory_id       BIGINT NOT NULL,
            original_imp    INTEGER NOT NULL CHECK (original_imp BETWEEN 1 AND 10),
            suggested_imp   INTEGER NOT NULL CHECK (suggested_imp BETWEEN 1 AND 10),
            delta           INTEGER GENERATED ALWAYS AS (suggested_imp - original_imp) STORED,
            reason          TEXT,
            applied         BOOLEAN DEFAULT FALSE,
            applied_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (reviewed_agent <> reviewer_agent)
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS imp_review_reviewed
        ON soul_v3.importance_review(reviewed_agent, created_at DESC)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS imp_review_pair_delta
        ON soul_v3.importance_review(reviewed_agent, reviewer_agent, (ABS(delta)))
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS imp_review_reviewer_pending
        ON soul_v3.importance_review(reviewer_agent, applied, created_at DESC)
        """
    )


async def _sample_pair(
    conn: asyncpg.Connection,
    reviewer: str,
    reviewed: str,
    *,
    sample_size: int,
    dry_run: bool,
) -> PairSample:
    existing = await conn.fetchval(
        """
        SELECT COUNT(*)::int
        FROM soul_v3.importance_review
        WHERE reviewed_agent = $1
          AND reviewer_agent = $2
          AND created_at >= now() - interval '7 days'
        """,
        reviewed,
        reviewer,
    )
    remaining = max(0, sample_size - int(existing or 0))
    if remaining <= 0:
        return PairSample(reviewer_agent=reviewer, reviewed_agent=reviewed, sampled=0)

    rows = await conn.fetch(
        """
        SELECT m.id, m.importance
        FROM soul_v3.memories m
        WHERE m.agent = $1
          AND m.invalid_at IS NULL
          AND m.created_at >= now() - interval '7 days'
          AND m.importance BETWEEN 5 AND 10
          AND NOT EXISTS (
              SELECT 1
              FROM soul_v3.importance_review r
              WHERE r.reviewed_agent = $1
                AND r.reviewer_agent = $2
                AND r.memory_id = m.id
                AND r.created_at >= now() - interval '7 days'
          )
        ORDER BY random()
        LIMIT $3
        """,
        reviewed,
        reviewer,
        remaining,
    )
    if dry_run or not rows:
        return PairSample(reviewer_agent=reviewer, reviewed_agent=reviewed, sampled=len(rows))

    await conn.executemany(
        """
        INSERT INTO soul_v3.importance_review
            (reviewed_agent, reviewer_agent, memory_id, original_imp, suggested_imp, reason, applied)
        VALUES ($1, $2, $3, $4, $4, $5, false)
        """,
        [
            (
                reviewed,
                reviewer,
                int(row["id"]),
                int(row["importance"]),
                "pending_peer_review_sample: agree by default; reviewer may propose +/-1 to +/-3 before apply",
            )
            for row in rows
        ],
    )
    return PairSample(reviewer_agent=reviewer, reviewed_agent=reviewed, sampled=len(rows))


async def _write_calibration_alert(conn: asyncpg.Connection, alert: CalibrationAlert) -> int:
    detail = {
        "phase": "identity_continuity_v2_phase5",
        "reviewer_agent": alert.reviewer_agent,
        "reviewed_agent": alert.reviewed_agent,
        "total": alert.total,
        "discrepant": alert.discrepant,
        "ratio": round(alert.ratio, 4),
        "threshold_delta": DISCREPANCY_DELTA,
        "threshold_ratio": DISCREPANCY_RATIO,
    }
    return await conn.fetchval(
        """
        INSERT INTO soul_v3.lifecycle_events
            (agent_name, event_type, desired_state, actual_state, detail)
        VALUES ('NEXUS', 'calibration_alert', 'calibrated', 'alert', $1)
        RETURNING id
        """,
        json.dumps(detail),
    )


async def detect_discrepancies(conn: asyncpg.Connection, *, dry_run: bool) -> list[CalibrationAlert]:
    rows = await conn.fetch(
        """
        SELECT reviewed_agent, reviewer_agent,
               COUNT(*)::int AS total,
               COUNT(*) FILTER (WHERE ABS(delta) >= $1)::int AS discrepant
        FROM soul_v3.importance_review
        WHERE created_at >= now() - interval '7 days'
        GROUP BY reviewed_agent, reviewer_agent
        HAVING COUNT(*) >= 10
           AND (COUNT(*) FILTER (WHERE ABS(delta) >= $1))::float / COUNT(*) >= $2
        ORDER BY reviewed_agent, reviewer_agent
        """,
        DISCREPANCY_DELTA,
        DISCREPANCY_RATIO,
    )
    alerts: list[CalibrationAlert] = []
    for row in rows:
        alert = CalibrationAlert(
            reviewer_agent=row["reviewer_agent"],
            reviewed_agent=row["reviewed_agent"],
            total=int(row["total"]),
            discrepant=int(row["discrepant"]),
            ratio=int(row["discrepant"]) / max(1, int(row["total"])),
        )
        if not dry_run:
            alert.lifecycle_event_id = int(await _write_calibration_alert(conn, alert))
        alerts.append(alert)
    return alerts


async def _log_evaluation_run(conn: asyncpg.Connection, run: PeerReviewRun, passed: bool, notes: str) -> None:
    await conn.execute(
        """
        INSERT INTO soul_v3.evaluation_runs
            (suite_name, score, passed, evidence, details, agent, run_at, notes)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), $7)
        """,
        "identity_continuity_v2_phase5_peer_review",
        100 if passed else 0,
        passed,
        (
            f"run_id={run.run_id} pairs_checked={run.pairs_checked} "
            f"samples_inserted={run.samples_inserted} alerts={len(run.alerts)} "
            f"self_test_ok={run.self_test_ok}"
        ),
        json.dumps(asdict(run), default=str),
        "ADA",
        notes,
    )


async def run_sampler(
    conn: asyncpg.Connection,
    agents: list[str],
    *,
    sample_size: int,
    dry_run: bool,
) -> PeerReviewRun:
    await ensure_schema(conn)
    agents = [a.upper() for a in agents]
    pair_samples: list[PairSample] = []
    for reviewer in agents:
        for reviewed in agents:
            if reviewer == reviewed:
                continue
            pair_samples.append(
                await _sample_pair(
                    conn,
                    reviewer,
                    reviewed,
                    sample_size=sample_size,
                    dry_run=dry_run,
                )
            )
    alerts = await detect_discrepancies(conn, dry_run=dry_run)
    run = PeerReviewRun(
        run_id=str(uuid.uuid4()),
        dry_run=dry_run,
        pairs_checked=len(pair_samples),
        samples_inserted=0 if dry_run else sum(p.sampled for p in pair_samples),
        alerts=alerts,
        pair_samples=pair_samples,
    )
    await _log_evaluation_run(conn, run, True, "importance peer-review sampler completed")
    return run


async def run_self_test(conn: asyncpg.Connection) -> PeerReviewRun:
    await ensure_schema(conn)
    async with conn.transaction():
        agent = "ADA_RTEST"
        reviewer = "NEXUS_RTEST"
        await conn.execute(
            """
            INSERT INTO soul_v3.agents (name, role, active)
            VALUES ($1, 'synthetic importance peer-review test agent', true)
            ON CONFLICT (name) DO NOTHING
            """,
            agent,
        )
        memory_ids: list[int] = []
        for i in range(30):
            memory_id = await conn.fetchval(
                """
                INSERT INTO soul_v3.memories
                    (agent, scope, category, content, importance, source, metadata, created_at)
                VALUES ($1, 'private', 'decision', $2, 7, 'peer_review_test', $3::jsonb, now())
                RETURNING id
                """,
                agent,
                f"Synthetic peer review memory {i}",
                json.dumps({"synthetic": True, "phase": "identity_continuity_v2_phase5"}),
            )
            memory_ids.append(int(memory_id))
        rows = []
        for i, memory_id in enumerate(memory_ids):
            suggested = 10 if i < 9 else 7
            rows.append((agent, reviewer, memory_id, 7, suggested, "synthetic discrepancy seed"))
        await conn.executemany(
            """
            INSERT INTO soul_v3.importance_review
                (reviewed_agent, reviewer_agent, memory_id, original_imp, suggested_imp, reason, applied)
            VALUES ($1, $2, $3, $4, $5, $6, false)
            """,
            rows,
        )
        alerts = await detect_discrepancies(conn, dry_run=False)
        if not alerts or alerts[0].ratio < 0.30 or alerts[0].lifecycle_event_id is None:
            raise RuntimeError(f"self-test failed: {alerts}")
        run = PeerReviewRun(
            run_id="self-test-rolled-back",
            dry_run=False,
            pairs_checked=1,
            samples_inserted=30,
            alerts=alerts,
            pair_samples=[PairSample(reviewer_agent=reviewer, reviewed_agent=agent, sampled=30)],
            self_test_ok=True,
        )
        raise SelfTestRollback(run)


async def self_test(conn: asyncpg.Connection) -> PeerReviewRun:
    try:
        return await run_self_test(conn)
    except SelfTestRollback as exc:
        run = exc.run
        await _log_evaluation_run(conn, run, True, "importance peer-review self-test passed and rolled back")
        return run


def _print_run(run: PeerReviewRun) -> None:
    print(
        "importance_peer_review=ok "
        f"run_id={run.run_id} dry_run={run.dry_run} pairs_checked={run.pairs_checked} "
        f"samples_inserted={run.samples_inserted} alerts={len(run.alerts)} "
        f"self_test_ok={run.self_test_ok}"
    )
    for sample in run.pair_samples:
        print(
            f"- pair reviewer={sample.reviewer_agent} reviewed={sample.reviewed_agent} "
            f"sampled={sample.sampled}"
        )
    for alert in run.alerts:
        print(
            f"- calibration_alert reviewer={alert.reviewer_agent} reviewed={alert.reviewed_agent} "
            f"total={alert.total} discrepant={alert.discrepant} ratio={alert.ratio:.3f} "
            f"lifecycle_event_id={alert.lifecycle_event_id}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Identity Continuity v2 Phase 5 peer-review sampler")
    parser.add_argument("--agents", nargs="+", default=list(DEFAULT_AGENTS))
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--apply", action="store_true", help="insert review samples and calibration alerts")
    parser.add_argument("--self-test", action="store_true", help="run seeded discrepancy test in rollback transaction")
    args = parser.parse_args()

    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        if args.self_test:
            run = await self_test(conn)
        else:
            run = await run_sampler(
                conn,
                args.agents,
                sample_size=args.sample_size,
                dry_run=not args.apply,
            )
    finally:
        await conn.close()

    _print_run(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
