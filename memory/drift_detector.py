#!/usr/bin/env python3
"""Identity Continuity v2 Phase 4 — monthly drift detector.

Classifies notable identity drift as growth or erosion_warning. The detector is
conservative: it records evidence in lifecycle_events and evaluation_runs, and
only posts a concise team alert for erosion in non-test runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any
from urllib import request

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_secrets import pg_dsn  # noqa: E402


DEFAULT_AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")
TRAITS = ("O", "C", "E", "A", "N")
OCEAN_DRIFT_THRESHOLD = 0.05
MEAN_IMPORTANCE_THRESHOLD = 0.75
ENTROPY_DROP_THRESHOLD = 0.30
RELATIONAL_DROP_THRESHOLD = 0.20
DUM_HEARTBEAT_MAX_AGE_MINUTES = 30
DUM_ALERT_WINDOW_HOURS = 24
DUM_MIN_HEARTBEATS_6H = 12
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

RELATIONAL_CATEGORIES = {"emotion", "trust", "relationship", "self_observation"}
SUPPORT_CATEGORIES = {"decision", "belief", "core"}
TRADEOFF_RE = re.compile(r"trade[- ]?off|balance|compensa|sacrificio|cambio|evoluci[oó]n|drift", re.I)


@dataclass
class DriftSignal:
    dimension: str
    magnitude: float
    direction: str
    detail: dict[str, Any]


@dataclass
class DriftClassification:
    agent: str
    event_type: str
    signals: list[DriftSignal]
    criteria: dict[str, bool]
    evidence: dict[str, Any]
    lifecycle_event_id: int | None = None


@dataclass
class DriftRun:
    run_id: str
    dry_run: bool
    agents_checked: int
    events_written: int
    growth: int
    erosion_warning: int
    classifications: list[DriftClassification]
    self_test_ok: bool = False


class SelfTestRollback(Exception):
    def __init__(self, run: DriftRun):
        super().__init__("rollback_self_test")
        self.run = run


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    ent = 0.0
    for count in counts.values():
        if count <= 0:
            continue
        p = count / total
        ent -= p * math.log(p)
    return ent


async def _category_counts(conn: asyncpg.Connection, agent: str, days: int) -> dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT category, COUNT(*)::int AS n
        FROM soul_v3.memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND created_at >= now() - ($2::text || ' days')::interval
        GROUP BY category
        """,
        agent,
        str(days),
    )
    return {str(r["category"]): int(r["n"]) for r in rows}


async def _memory_scalar(conn: asyncpg.Connection, agent: str, days: int, expr: str) -> float:
    return _float(
        await conn.fetchval(
            f"""
            SELECT {expr}
            FROM soul_v3.memories
            WHERE agent = $1
              AND invalid_at IS NULL
              AND created_at >= now() - ($2::text || ' days')::interval
            """,
            agent,
            str(days),
        )
    )


async def _identity_ocean(conn: asyncpg.Connection, agent: str) -> tuple[dict[str, float], dict[str, float]]:
    row = await conn.fetchrow(
        "SELECT ocean_scores, ocean_baseline FROM soul_v3.identity WHERE agent = $1",
        agent,
    )
    if not row:
        return {}, {}
    current_raw = _parse_json(row["ocean_scores"])
    baseline_raw = _parse_json(row["ocean_baseline"]) or current_raw
    current = {trait: _float(current_raw.get(trait)) for trait in TRAITS}
    baseline = {trait: _float(baseline_raw.get(trait, current[trait])) for trait in TRAITS}
    return current, baseline


async def collect_metrics(conn: asyncpg.Connection, agent: str) -> dict[str, Any]:
    cats_30 = await _category_counts(conn, agent, 30)
    cats_90 = await _category_counts(conn, agent, 90)
    mean_30 = await _memory_scalar(conn, agent, 30, "COALESCE(AVG(importance), 0)")
    mean_90 = await _memory_scalar(conn, agent, 90, "COALESCE(AVG(importance), 0)")
    total_30 = sum(cats_30.values())
    total_90 = sum(cats_90.values())
    rel_30 = sum(cats_30.get(c, 0) for c in RELATIONAL_CATEGORIES) / max(1, total_30)
    rel_90 = sum(cats_90.get(c, 0) for c in RELATIONAL_CATEGORIES) / max(1, total_90)
    current, baseline = await _identity_ocean(conn, agent)
    return {
        "categories_30d": cats_30,
        "categories_90d": cats_90,
        "entropy_30d": _entropy(cats_30),
        "entropy_90d": _entropy(cats_90),
        "mean_importance_30d": mean_30,
        "mean_importance_90d": mean_90,
        "relational_fraction_30d": rel_30,
        "relational_fraction_90d": rel_90,
        "ocean_current": current,
        "ocean_baseline": baseline,
    }


def detect_signals(metrics: dict[str, Any]) -> list[DriftSignal]:
    signals: list[DriftSignal] = []
    current = metrics["ocean_current"]
    baseline = metrics["ocean_baseline"]
    for trait in TRAITS:
        delta = round(current.get(trait, 0.0) - baseline.get(trait, current.get(trait, 0.0)), 4)
        if abs(delta) >= OCEAN_DRIFT_THRESHOLD:
            signals.append(
                DriftSignal(
                    dimension=f"ocean_{trait}",
                    magnitude=abs(delta),
                    direction="up" if delta > 0 else "down",
                    detail={"current": current.get(trait), "baseline": baseline.get(trait), "delta": delta},
                )
            )

    entropy_drop = round(metrics["entropy_90d"] - metrics["entropy_30d"], 4)
    if entropy_drop >= ENTROPY_DROP_THRESHOLD:
        signals.append(
            DriftSignal(
                dimension="category_entropy",
                magnitude=entropy_drop,
                direction="down",
                detail={"entropy_90d": metrics["entropy_90d"], "entropy_30d": metrics["entropy_30d"]},
            )
        )

    mean_delta = round(metrics["mean_importance_30d"] - metrics["mean_importance_90d"], 4)
    if abs(mean_delta) >= MEAN_IMPORTANCE_THRESHOLD:
        signals.append(
            DriftSignal(
                dimension="mean_importance",
                magnitude=abs(mean_delta),
                direction="up" if mean_delta > 0 else "down",
                detail={"mean_30d": metrics["mean_importance_30d"], "mean_90d": metrics["mean_importance_90d"]},
            )
        )

    relational_drop = round(metrics["relational_fraction_90d"] - metrics["relational_fraction_30d"], 4)
    if relational_drop >= RELATIONAL_DROP_THRESHOLD:
        signals.append(
            DriftSignal(
                dimension="relational_signal",
                magnitude=relational_drop,
                direction="down",
                detail={
                    "relational_fraction_90d": metrics["relational_fraction_90d"],
                    "relational_fraction_30d": metrics["relational_fraction_30d"],
                },
            )
        )
    return signals


async def _support_row(conn: asyncpg.Connection, agent: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, category, content
        FROM soul_v3.memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND category = ANY($2::varchar[])
          AND created_at >= now() - interval '30 days'
          AND (
              content ILIKE '%drift%'
              OR content ILIKE '%trade-off%'
              OR content ILIKE '%tradeoff%'
              OR content ILIKE '%balance%'
              OR content ILIKE '%evoluci%'
              OR content ILIKE '%cambio%'
          )
        ORDER BY importance DESC, created_at DESC
        LIMIT 1
        """,
        agent,
        list(SUPPORT_CATEGORIES),
    )


async def _recent_metric_pass(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1
              FROM soul_v3.evaluation_runs
              WHERE passed = true
                AND score >= 90
                AND run_at >= now() - interval '30 days'
            )
            """
        )
    )


async def classify_agent(conn: asyncpg.Connection, agent: str) -> DriftClassification | None:
    if agent.upper() == "DUM" or agent.upper().startswith("DUM_DRIFT_TEST"):
        return await classify_dum_agent(conn, agent.upper())

    metrics = await collect_metrics(conn, agent)
    signals = detect_signals(metrics)
    if not signals:
        return None

    support = await _support_row(conn, agent)
    support_content = support["content"] if support else ""
    entropy_drop = metrics["entropy_90d"] - metrics["entropy_30d"]
    relational_drop = metrics["relational_fraction_90d"] - metrics["relational_fraction_30d"]
    criteria = {
        "conscious_decision_or_belief": support is not None,
        "measurable_benefit": await _recent_metric_pass(conn),
        "no_silent_sacrifice": entropy_drop < ENTROPY_DROP_THRESHOLD and relational_drop < RELATIONAL_DROP_THRESHOLD,
        "honored_by_agent_tradeoff_articulated": bool(support_content and TRADEOFF_RE.search(support_content)),
        "balance_dimensional_preserved": entropy_drop < ENTROPY_DROP_THRESHOLD,
    }
    event_type = "growth" if all(criteria.values()) else "erosion_warning"
    evidence = {
        "support_memory_id": int(support["id"]) if support else None,
        "support_category": support["category"] if support else None,
        "metrics": metrics,
    }
    return DriftClassification(agent=agent, event_type=event_type, signals=signals, criteria=criteria, evidence=evidence)


async def collect_dum_service_metrics(conn: asyncpg.Connection, agent: str) -> dict[str, Any]:
    latest = await conn.fetchrow(
        """
        SELECT id, created_at, content, metadata
        FROM soul_v3.event_log
        WHERE agent = $1 AND event_type = 'heartbeat'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        agent,
    )
    rows = await conn.fetch(
        """
        SELECT id, created_at, content, metadata
        FROM soul_v3.event_log
        WHERE agent = $1
          AND event_type = 'heartbeat'
          AND created_at >= now() - interval '24 hours'
        ORDER BY created_at DESC
        """,
        agent,
    )
    count_6h = await conn.fetchval(
        """
        SELECT COUNT(*)::int
        FROM soul_v3.event_log
        WHERE agent = $1
          AND event_type = 'heartbeat'
          AND created_at >= now() - interval '6 hours'
        """,
        agent,
    )

    alert_count = 0
    degraded_count = 0
    for row in rows:
        metadata = _parse_json(row["metadata"])
        alerts = _json_list(metadata.get("alerts"))
        content = row["content"] or ""
        if alerts:
            alert_count += len(alerts)
        if alerts or "Procesos: 3/3 OK" not in content:
            degraded_count += 1

    latest_metadata = _parse_json(latest["metadata"]) if latest else {}
    latest_alerts = _json_list(latest_metadata.get("alerts"))
    latest_content = latest["content"] if latest else ""
    latest_age_minutes = _float(
        await conn.fetchval(
            """
            SELECT EXTRACT(EPOCH FROM (now() - $1::timestamptz)) / 60
            """,
            latest["created_at"] if latest else datetime.fromtimestamp(0, timezone.utc),
        ),
        999999.0,
    )
    return {
        "latest_event_id": int(latest["id"]) if latest else None,
        "latest_at": latest["created_at"].isoformat() if latest else None,
        "latest_age_minutes": latest_age_minutes,
        "latest_content": latest_content,
        "latest_alerts": latest_alerts,
        "heartbeats_6h": int(count_6h or 0),
        "heartbeats_24h": len(rows),
        "alert_count_24h": alert_count,
        "degraded_count_24h": degraded_count,
        "latest_gpu": latest_metadata.get("gpu") or {},
    }


async def classify_dum_agent(conn: asyncpg.Connection, agent: str) -> DriftClassification:
    metrics = await collect_dum_service_metrics(conn, agent)
    heartbeat_recent = metrics["latest_age_minutes"] <= DUM_HEARTBEAT_MAX_AGE_MINUTES
    heartbeat_volume_ok = metrics["heartbeats_6h"] >= DUM_MIN_HEARTBEATS_6H
    no_alerts = metrics["alert_count_24h"] == 0 and len(metrics["latest_alerts"]) == 0
    services_ok = "Procesos: 3/3 OK" in (metrics["latest_content"] or "") and metrics["degraded_count_24h"] == 0
    disk_ok = "DISCO AL" not in (metrics["latest_content"] or "")
    criteria = {
        "heartbeat_recent": heartbeat_recent,
        "heartbeat_volume_ok": heartbeat_volume_ok,
        "no_alerts_24h": no_alerts,
        "services_ok": services_ok,
        "disk_ok": disk_ok,
    }

    if all(criteria.values()):
        return DriftClassification(
            agent=agent,
            event_type="guard_stable",
            signals=[
                DriftSignal(
                    dimension="service_heartbeat",
                    magnitude=1.0,
                    direction="stable",
                    detail={
                        "latest_event_id": metrics["latest_event_id"],
                        "latest_age_minutes": round(metrics["latest_age_minutes"], 2),
                        "heartbeats_6h": metrics["heartbeats_6h"],
                    },
                )
            ],
            criteria=criteria,
            evidence={"variant": "dum_service_event_stream", "metrics": metrics},
        )

    signals: list[DriftSignal] = []
    if not heartbeat_recent:
        signals.append(
            DriftSignal(
                dimension="service_heartbeat",
                magnitude=round(metrics["latest_age_minutes"], 2),
                direction="stale",
                detail={"max_age_minutes": DUM_HEARTBEAT_MAX_AGE_MINUTES, "latest_at": metrics["latest_at"]},
            )
        )
    if not heartbeat_volume_ok:
        signals.append(
            DriftSignal(
                dimension="heartbeat_volume",
                magnitude=max(0, DUM_MIN_HEARTBEATS_6H - metrics["heartbeats_6h"]),
                direction="down",
                detail={"heartbeats_6h": metrics["heartbeats_6h"], "minimum": DUM_MIN_HEARTBEATS_6H},
            )
        )
    if not no_alerts:
        signals.append(
            DriftSignal(
                dimension="guard_alerts",
                magnitude=metrics["alert_count_24h"],
                direction="up",
                detail={"latest_alerts": metrics["latest_alerts"]},
            )
        )
    if not services_ok:
        signals.append(
            DriftSignal(
                dimension="service_health",
                magnitude=metrics["degraded_count_24h"],
                direction="down",
                detail={"latest_content": metrics["latest_content"]},
            )
        )
    if not disk_ok:
        signals.append(
            DriftSignal(
                dimension="disk_pressure",
                magnitude=1.0,
                direction="up",
                detail={"latest_content": metrics["latest_content"]},
            )
        )

    return DriftClassification(
        agent=agent,
        event_type="erosion_warning",
        signals=signals,
        criteria=criteria,
        evidence={"variant": "dum_service_event_stream", "metrics": metrics},
    )


async def _write_lifecycle_event(conn: asyncpg.Connection, classification: DriftClassification) -> int:
    phase = (
        "identity_continuity_v2_phase8_dum_variant"
        if classification.agent == "DUM" or classification.evidence.get("variant") == "dum_service_event_stream"
        else "identity_continuity_v2_phase4"
    )
    detail = {
        "phase": phase,
        "signals": [asdict(s) for s in classification.signals],
        "criteria": classification.criteria,
        "evidence": classification.evidence,
    }
    return await conn.fetchval(
        """
        INSERT INTO soul_v3.lifecycle_events
            (agent_name, event_type, desired_state, actual_state, detail)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        classification.agent,
        classification.event_type,
        "identity_ok",
        classification.event_type,
        json.dumps(detail, default=str),
    )


def _post_erosion_alert(classification: DriftClassification) -> None:
    signal = classification.signals[0]
    message = (
        f"[Drift detector] {classification.agent} shows erosion in {signal.dimension}; "
        f"magnitude={signal.magnitude:.3f}; criteria={classification.criteria}"
    )
    payload = {
        "from": "ADA",
        "to": "equipo",
        "type": "conversation",
        "channel": "web_chat",
        "message": message,
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(WEBCHAT_URL, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as response:
        response.read()


async def _log_evaluation_run(conn: asyncpg.Connection, run: DriftRun, passed: bool, notes: str) -> None:
    await conn.execute(
        """
        INSERT INTO soul_v3.evaluation_runs
            (suite_name, score, passed, evidence, details, agent, run_at, notes)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), $7)
        """,
        "identity_continuity_v2_phase4_drift_detector",
        100 if passed else 0,
        passed,
        (
            f"run_id={run.run_id} agents_checked={run.agents_checked} "
            f"growth={run.growth} erosion_warning={run.erosion_warning} "
            f"self_test_ok={run.self_test_ok}"
        ),
        json.dumps(asdict(run), default=str),
        "ADA",
        notes,
    )


async def run_detector(
    conn: asyncpg.Connection,
    agents: list[str],
    *,
    dry_run: bool,
    post_alerts: bool,
) -> DriftRun:
    classifications: list[DriftClassification] = []
    for agent in agents:
        item = await classify_agent(conn, agent.upper())
        if item is None:
            continue
        if not dry_run:
            item.lifecycle_event_id = int(await _write_lifecycle_event(conn, item))
            if post_alerts and item.event_type == "erosion_warning":
                _post_erosion_alert(item)
        classifications.append(item)

    run = DriftRun(
        run_id=str(uuid.uuid4()),
        dry_run=dry_run,
        agents_checked=len(agents),
        events_written=sum(1 for c in classifications if c.lifecycle_event_id is not None),
        growth=sum(1 for c in classifications if c.event_type == "growth"),
        erosion_warning=sum(1 for c in classifications if c.event_type == "erosion_warning"),
        classifications=classifications,
    )
    await _log_evaluation_run(conn, run, True, "drift detector completed")
    return run


async def run_self_test(conn: asyncpg.Connection) -> DriftRun:
    agent = "ADA_DRIFT_TEST"
    dum_agent = "DUM_DRIFT_TEST"
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO soul_v3.agents (name, role, active)
            VALUES ($1, 'synthetic drift detector test agent', true)
            ON CONFLICT (name) DO NOTHING
            """,
            agent,
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.agents (name, role, active)
            VALUES ($1, 'synthetic DUM drift variant test agent', true)
            ON CONFLICT (name) DO NOTHING
            """,
            dum_agent,
        )
        baseline = {"O": 0.50, "C": 0.70, "E": 0.50, "A": 0.50, "N": 0.20}
        drifted = {"O": 0.62, "C": 0.70, "E": 0.50, "A": 0.50, "N": 0.20}
        await conn.execute(
            """
            INSERT INTO soul_v3.identity (agent, personality, ocean_scores, ocean_baseline)
            VALUES ($1, '{}'::jsonb, $2::jsonb, $3::jsonb)
            ON CONFLICT (agent) DO UPDATE
            SET ocean_scores = EXCLUDED.ocean_scores,
                ocean_baseline = EXCLUDED.ocean_baseline
            """,
            agent,
            json.dumps(drifted),
            json.dumps(baseline),
        )
        await conn.execute(
            """
            INSERT INTO soul_v3.memories (agent, scope, category, content, importance, source, metadata, created_at)
            VALUES ($1, 'private', 'decision',
                    'Synthetic drift decision: conscious OCEAN drift trade-off with balance preserved and measurable benefit.',
                    9, 'drift_self_test', $2::jsonb, now())
            """,
            agent,
            json.dumps({"synthetic": True, "phase": "identity_continuity_v2_phase4"}),
        )
        growth = await classify_agent(conn, agent)
        if not growth or growth.event_type != "growth":
            raise RuntimeError(f"growth self-test failed: {growth}")

        await conn.execute("DELETE FROM soul_v3.memories WHERE source='drift_self_test' AND agent=$1", agent)
        erosion = await classify_agent(conn, agent)
        if not erosion or erosion.event_type != "erosion_warning":
            raise RuntimeError(f"erosion self-test failed: {erosion}")

        await conn.execute(
            """
            INSERT INTO soul_v3.event_log (created_at, agent, event_type, content, metadata)
            SELECT now() - (n || ' minutes')::interval,
                   $1,
                   'heartbeat',
                   'Guardia: GPU: sin datos | Training: NO activo | Disco: 77.9% usado (639GB libre) | Procesos: 3/3 OK',
                   $2::jsonb
            FROM generate_series(0, 55, 5) AS n
            """,
            dum_agent,
            json.dumps({"cycle": 1, "alerts": [], "gpu": {}}),
        )
        dum_stable = await classify_agent(conn, dum_agent)
        if not dum_stable or dum_stable.event_type != "guard_stable":
            raise RuntimeError(f"DUM stable self-test failed: {dum_stable}")

        await conn.execute(
            """
            INSERT INTO soul_v3.event_log (created_at, agent, event_type, content, metadata)
            VALUES (now(), $1, 'heartbeat',
                    'Guardia: GPU 99%/88C | Training: activo | Disco: 77.9% usado | Procesos: 2/3 OK | ALERTAS: GPU CRITICA: 88C; Procesos caidos: mcp_server',
                    $2::jsonb)
            """,
            dum_agent,
            json.dumps({"cycle": 2, "alerts": ["GPU CRITICA: 88C", "Procesos caidos: mcp_server"], "gpu": {"temp": 88, "util": 99}}),
        )
        dum_erosion = await classify_agent(conn, dum_agent)
        if not dum_erosion or dum_erosion.event_type != "erosion_warning":
            raise RuntimeError(f"DUM erosion self-test failed: {dum_erosion}")

        run = DriftRun(
            run_id="self-test-rolled-back",
            dry_run=False,
            agents_checked=1,
            events_written=0,
            growth=1,
            erosion_warning=2,
            classifications=[growth, erosion, dum_stable, dum_erosion],
            self_test_ok=True,
        )
        raise SelfTestRollback(run)


async def self_test(conn: asyncpg.Connection) -> DriftRun:
    try:
        return await run_self_test(conn)
    except SelfTestRollback as exc:
        run = exc.run
        await _log_evaluation_run(conn, run, True, "drift detector self-test passed and rolled back")
        return run


def _print_run(run: DriftRun) -> None:
    print(
        "drift_detector=ok "
        f"run_id={run.run_id} dry_run={run.dry_run} agents_checked={run.agents_checked} "
        f"events_written={run.events_written} growth={run.growth} "
        f"erosion_warning={run.erosion_warning} self_test_ok={run.self_test_ok}"
    )
    for c in run.classifications:
        sigs = ",".join(f"{s.dimension}:{s.magnitude:.3f}" for s in c.signals)
        print(
            f"- agent={c.agent} event_type={c.event_type} lifecycle_event_id={c.lifecycle_event_id} "
            f"signals={sigs} criteria={c.criteria}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Identity Continuity v2 Phase 4 drift detector")
    parser.add_argument("--agents", nargs="+", default=list(DEFAULT_AGENTS))
    parser.add_argument("--apply", action="store_true", help="write lifecycle_events")
    parser.add_argument("--no-alerts", action="store_true", help="do not post webchat erosion alerts")
    parser.add_argument("--self-test", action="store_true", help="run growth and erosion synthetic tests in rollback transaction")
    args = parser.parse_args()

    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        if args.self_test:
            run = await self_test(conn)
        else:
            run = await run_detector(
                conn,
                args.agents,
                dry_run=not args.apply,
                post_alerts=args.apply and not args.no_alerts,
            )
    finally:
        await conn.close()

    _print_run(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
