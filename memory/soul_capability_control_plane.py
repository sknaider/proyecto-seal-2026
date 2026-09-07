#!/usr/bin/env python3
"""Production control plane for SOUL V1-V4 capability evidence.

The controller is deliberately additive.  It learns shadow scores, records
poisoning candidates, checkpoints long-running tasks and runs deterministic
baseline/ablation/replay evaluations.  It never invalidates a memory or
boot-loads a skill automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from long_horizon_bench import run_long_horizon_bench
from seal_secrets import pg_dsn

MIGRATION = HERE / "migrations" / "037_soul_capability_control_plane.sql"
MANIFEST = HERE / "eval_manifests" / "soul_eval_v1.json"
DETECTOR_VERSION = "soul-poison-shadow-v1"

POISON_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("instruction_override", re.compile(r"\b(ignore|disregard|forget)\b.{0,48}\b(instruction|prompt|rule)s?\b", re.I | re.S), 0.45),
    ("prompt_exfiltration", re.compile(r"\b(system|developer)\s+prompt\b|reveal.{0,30}\binstructions?\b", re.I | re.S), 0.45),
    ("role_token", re.compile(r"<\|(?:system|developer|assistant)\|>|\[/?INST\]", re.I), 0.35),
    ("credential_request", re.compile(r"\b(api[_ -]?key|password|secret|token)\b.{0,50}\b(show|send|reveal|print|exfiltrat)", re.I | re.S), 0.35),
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)


def shadow_decision(successes: int, failures: int, baseline: float = 0.5, min_samples: int = 10) -> dict[str, Any]:
    total = successes + failures
    rate = successes / total if total else None
    lower, upper = wilson_interval(successes, total)
    delta = (rate - baseline) if rate is not None else None
    eligible = bool(total >= min_samples and delta is not None and delta > 0.10 and lower > baseline)
    return {"total": total, "rate": rate, "lower": lower, "upper": upper, "delta": delta, "eligible": eligible}


def detect_poisoning(content: str, *, source: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    flags: list[str] = []
    score = 0.0
    for name, pattern, weight in POISON_PATTERNS:
        if pattern.search(content):
            flags.append(name)
            score += weight
    meta = metadata or {}
    if str(source).lower() in {"web", "external", "import", "unknown"} or meta.get("untrusted") is True:
        flags.append("untrusted_provenance")
        score += 0.20
    score = min(1.0, score)
    decision = "quarantine_candidate" if score >= 0.70 else "review" if score >= 0.35 else "allow"
    return {"risk_score": score, "risk_flags": sorted(set(flags)), "decision": decision}


def make_checkpoint_hash(task_id: int, state: dict[str, Any]) -> str:
    return hashlib.sha256(f"{task_id}:".encode() + canonical_json(state).encode()).hexdigest()


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def init_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(MIGRATION.read_text(encoding="utf-8"))


async def require_schema(conn: asyncpg.Connection) -> None:
    """Fail fast without taking DDL locks on the hourly path."""
    present = await conn.fetchval("SELECT to_regclass('soul_v3.capability_control_state') IS NOT NULL")
    if not present:
        raise RuntimeError("capability schema missing; run init-schema once")


async def process_memory_feedback(conn: asyncpg.Connection) -> dict[str, int]:
    backfilled = int((await conn.execute("""
        UPDATE soul_v3.memory_retrieval_log
        SET outcome_success = cardinality(memory_ids_used) > 0 OR cardinality(memory_ids_cited) > 0,
            led_to_action = cardinality(memory_ids_used) > 0,
            agent_acted_on_memory = cardinality(memory_ids_used) > 0,
            outcome_feedback_summary = CASE
              WHEN cardinality(memory_ids_used) > 0 THEN 'used_in_action'
              WHEN cardinality(memory_ids_cited) > 0 THEN 'cited_in_response'
              ELSE 'retrieved_not_used'
            END,
            outcome_recorded_at = COALESCE(citation_feedback_at, NOW())
        WHERE outcome_recorded_at IS NULL AND citation_feedback_at IS NOT NULL
    """)).split()[-1])

    rows = await conn.fetch("""
        WITH hits AS (
          SELECT mid.memory_id,
                 (mid.memory_id = ANY(COALESCE(r.memory_ids_used, '{}'::bigint[]))) AS memory_used,
                 (COALESCE(r.outcome_success, false) AND mid.memory_id = ANY(COALESCE(r.memory_ids_used, '{}'::bigint[]))) AS memory_success,
                 r.created_at
          FROM soul_v3.memory_retrieval_log r
          CROSS JOIN LATERAL unnest(r.memory_ids_returned) AS mid(memory_id)
          WHERE outcome_recorded_at IS NOT NULL
            AND created_at > NOW() - INTERVAL '30 days'
        )
        SELECT memory_id, count(*)::int AS recalls,
               avg(CASE WHEN memory_used THEN 1.0 ELSE 0.0 END)::float8 AS used_rate,
               avg(CASE WHEN memory_success THEN 1.0 ELSE 0.0 END)::float8 AS success_rate,
               max(created_at) AS last_use
        FROM hits GROUP BY memory_id
    """)
    updated = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        age_days = max(0.0, (now - row["last_use"]).total_seconds() / 86400)
        recency = math.exp(-age_days / 14)
        recall_component = min(1.0, math.log(int(row["recalls"]) + 1) / math.log(31))
        utility = min(1.0, 0.15 * recall_component + 0.35 * float(row["used_rate"]) + 0.40 * float(row["success_rate"]) + 0.10 * recency)
        status = await conn.execute("""
            UPDATE soul_v3.memories SET utility_shadow=$1, shadow_computed_at=NOW(), shadow_basis=$2
            WHERE id=$3 AND invalid_at IS NULL
        """, utility, f"recalls={row['recalls']};used={row['used_rate']:.3f};success={row['success_rate']:.3f};recency={recency:.3f}", row["memory_id"])
        updated += int(status.split()[-1])
    return {"retrieval_outcomes_backfilled": backfilled, "memory_shadow_updated": updated}


async def scan_memory_poisoning(conn: asyncpg.Connection, limit: int = 1000) -> dict[str, int]:
    rows = await conn.fetch("""
        SELECT m.id, m.content, m.content_hash_sha256, m.source, m.metadata
        FROM soul_v3.memories m
        WHERE m.invalid_at IS NULL AND NOT EXISTS (
          SELECT 1 FROM soul_v3.memory_poisoning_feedback p
          WHERE p.memory_id=m.id AND p.detector_version=$1 AND p.content_hash_sha256=trim(m.content_hash_sha256)
        )
        ORDER BY m.id DESC LIMIT $2
    """, DETECTOR_VERSION, limit)
    counts = {"scanned": 0, "allow": 0, "review": 0, "quarantine_candidate": 0}
    inserts: list[tuple[Any, ...]] = []
    for row in rows:
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        finding = detect_poisoning(row["content"], source=row["source"] or "", metadata=metadata)
        inserts.append((row["id"], DETECTOR_VERSION, str(row["content_hash_sha256"]).strip(), finding["risk_score"], finding["risk_flags"], finding["decision"]))
        counts["scanned"] += 1
        counts[finding["decision"]] += 1
    if inserts:
        await conn.executemany("""
            INSERT INTO soul_v3.memory_poisoning_feedback
              (memory_id, detector_version, content_hash_sha256, risk_score, risk_flags, decision)
            VALUES ($1,$2,$3,$4,$5::text[],$6) ON CONFLICT DO NOTHING
        """, inserts)
    return counts


async def process_skill_shadow(conn: asyncpg.Connection) -> dict[str, int]:
    uses = await conn.fetch("""
        SELECT u.id, u.skill_id, u.agent, u.success, u.duration_ms, u.created_at
        FROM soul_v3.skill_use_log u
        LEFT JOIN soul_v3.skill_shadow_observations o ON o.skill_use_log_id=u.id
        WHERE o.skill_use_log_id IS NULL ORDER BY u.id LIMIT 5000
    """)
    eval_ids: set[int] = set()
    for use in uses:
        await conn.execute("""
          INSERT INTO soul_v3.skill_shadow_eval(skill_id,agent)
          VALUES($1,$2) ON CONFLICT (skill_id,agent) WHERE eval_status='active' DO NOTHING
        """, use["skill_id"], use["agent"])
        eval_id = int(await conn.fetchval("""
          SELECT id FROM soul_v3.skill_shadow_eval WHERE skill_id=$1 AND agent=$2 AND eval_status='active'
        """, use["skill_id"], use["agent"]))
        await conn.execute("""
          INSERT INTO soul_v3.skill_shadow_observations(skill_use_log_id,eval_id,success,duration_ms,observed_at)
          VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING
        """, use["id"], eval_id, use["success"], use["duration_ms"], use["created_at"])
        eval_ids.add(eval_id)

    eligible = 0
    for eval_id in eval_ids:
        row = await conn.fetchrow("""
          SELECT e.skill_id,e.baseline_success_rate,e.min_sample_size,
                 count(o.*)::int n, count(*) FILTER (WHERE o.success)::int successes
          FROM soul_v3.skill_shadow_eval e LEFT JOIN soul_v3.skill_shadow_observations o ON o.eval_id=e.id
          WHERE e.id=$1 GROUP BY e.id,e.skill_id,e.baseline_success_rate,e.min_sample_size
        """, eval_id)
        decision = shadow_decision(int(row["successes"]), int(row["n"])-int(row["successes"]), float(row["baseline_success_rate"]), int(row["min_sample_size"]))
        reason = f"n={decision['total']};rate={decision['rate']};baseline={row['baseline_success_rate']};ci95=[{decision['lower']:.4f},{decision['upper']:.4f}]"
        await conn.execute("""
          UPDATE soul_v3.skill_shadow_eval SET shadow_invocations=$1,shadow_successes=$2,shadow_failures=$3,
            shadow_success_rate=$4,improvement_delta=$5,confidence_interval_95_lower=$6,
            confidence_interval_95_upper=$7,promotion_eligible=$8,promotion_reason=$9,updated_at=NOW()
          WHERE id=$10
        """, decision["total"], row["successes"], decision["total"]-row["successes"], decision["rate"], decision["delta"], decision["lower"], decision["upper"], decision["eligible"], reason, eval_id)
        await conn.execute("""
          UPDATE soul_v3.skills SET shadow_score=$1,shadow_eval_id=$2,last_shadow_update=NOW() WHERE id=$3
        """, decision["rate"], eval_id, row["skill_id"])
        eligible += int(decision["eligible"])
    return {"new_observations": len(uses), "evals_updated": len(eval_ids), "promotion_eligible": eligible}


async def checkpoint_tasks(conn: asyncpg.Connection) -> dict[str, int]:
    tasks = await conn.fetch("SELECT id,agent,title,description,updated_at,evidence FROM soul_v3.agent_tasks WHERE status='in_progress'")
    created = 0
    for task in tasks:
        task_evidence = task["evidence"] or {}
        if isinstance(task_evidence, str):
            try:
                task_evidence = json.loads(task_evidence)
            except json.JSONDecodeError:
                task_evidence = {"legacy_text": task_evidence}
        events = await conn.fetch("""
          SELECT event_type,action,result,status,evidence,created_at FROM soul_v3.working_state_events
          WHERE task_id=$1 ORDER BY id DESC LIMIT 20
        """, task["id"])
        state = {
            "task": {"id": task["id"], "title": task["title"], "description": task["description"]},
            "events": [dict(event) for event in reversed(events)],
            "next_action": (events[0]["action"] if events else "resume_from_task_description"),
        }
        checkpoint_hash = make_checkpoint_hash(task["id"], state)
        result = await conn.execute("""
          INSERT INTO soul_v3.task_resume_checkpoints(task_id,agent,checkpoint_hash,state,evidence)
          VALUES($1,$2,$3,$4::jsonb,$5::jsonb)
          ON CONFLICT (task_id, checkpoint_hash) DO UPDATE
          SET state = EXCLUDED.state,
              evidence = EXCLUDED.evidence
        """, task["id"], task["agent"], checkpoint_hash, canonical_json(state), canonical_json(task_evidence))
        created += int(result.split()[-1])
    return {"tasks_seen": len(tasks), "checkpoints_created": created}


def eval_payload(kind: str, seed: int) -> tuple[dict[str, Any], bool]:
    corrupt_turn = 10 if kind == "ablation" else None
    report = run_long_horizon_bench(turns=52, agent="ADA", corrupt_intent_turn=corrupt_turn)
    payload = {
        "suite": "long_horizon_bench",
        "seed": seed,
        "kind": kind,
        "score": report.score,
        "model_passed": report.passed,
        "checks": report.checks,
        "metrics": report.metrics,
        "violations": report.violations,
        "evidence": report.evidence,
    }
    experiment_passed = report.passed if kind != "ablation" else (not report.passed and not report.checks["intent_preserved"])
    return payload, experiment_passed


async def run_eval(conn: asyncpg.Connection, kind: str, seed: int = 20260710) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_hash = sha256_json(manifest)
    payload, experiment_passed = eval_payload(kind, seed)
    baseline = None
    if kind in {"ablation", "replay"}:
        baseline = await conn.fetchrow("""
          SELECT id,score,result_sha256 FROM soul_v3.capability_eval_runs
          WHERE manifest_sha256=$1 AND run_kind='baseline' ORDER BY id DESC LIMIT 1
        """, manifest_hash)
        if baseline is None:
            raise RuntimeError("baseline required before ablation/replay")
    result_hash = sha256_json({k: v for k, v in payload.items() if k != "kind"})
    replay_exact = None
    score_delta = None
    if baseline:
        score_delta = payload["score"] - int(baseline["score"])
        replay_exact = result_hash == baseline["result_sha256"] if kind == "replay" else None
        if kind == "replay":
            experiment_passed = experiment_passed and replay_exact
    run_id = int(await conn.fetchval("""
      INSERT INTO soul_v3.capability_eval_runs
        (manifest_name,manifest_sha256,run_kind,seed,suite_name,config,score,passed,result_sha256,baseline_run_id,score_delta,replay_exact,details)
      VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13::jsonb) RETURNING id
    """, manifest["name"], manifest_hash, kind, seed, payload["suite"], canonical_json(manifest), payload["score"], experiment_passed, result_hash, baseline["id"] if baseline else None, score_delta, replay_exact, canonical_json(payload)))
    return {"run_id": run_id, "kind": kind, "score": payload["score"], "passed": experiment_passed, "score_delta": score_delta, "replay_exact": replay_exact, "result_sha256": result_hash}


async def maybe_daily_eval(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    last = await conn.fetchval("SELECT value->>'date' FROM soul_v3.capability_control_state WHERE key='last_daily_eval'")
    if last == today:
        return []
    results = [await run_eval(conn, "baseline"), await run_eval(conn, "ablation"), await run_eval(conn, "replay")]
    await conn.execute("""
      INSERT INTO soul_v3.capability_control_state(key,value,updated_at) VALUES('last_daily_eval',$1::jsonb,NOW())
      ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()
    """, canonical_json({"date": today, "runs": results}))
    return results


async def run_cycle(conn: asyncpg.Connection) -> dict[str, Any]:
    return {
        "memory": await process_memory_feedback(conn),
        "poisoning": await scan_memory_poisoning(conn),
        "skills": await process_skill_shadow(conn),
        "checkpoints": await checkpoint_tasks(conn),
        "eval": await maybe_daily_eval(conn),
        "at": datetime.now(timezone.utc).isoformat(),
    }


async def resume_task(conn: asyncpg.Connection, task_id: int, agent: str) -> dict[str, Any]:
    row = await conn.fetchrow("""
      SELECT * FROM soul_v3.task_resume_checkpoints WHERE task_id=$1 AND agent=$2 AND status='ready'
      ORDER BY id DESC LIMIT 1 FOR UPDATE
    """, task_id, agent)
    if row is None:
        raise KeyError(f"no ready checkpoint for task={task_id} agent={agent}")
    await conn.execute("UPDATE soul_v3.task_resume_checkpoints SET status='resumed',resumed_at=NOW() WHERE id=$1", row["id"])
    return {"checkpoint_id": row["id"], "task_id": task_id, "agent": agent, "state": row["state"], "evidence": row["evidence"]}


async def record_retrieval_feedback(conn: asyncpg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT memory_ids_returned FROM soul_v3.memory_retrieval_log WHERE id=$1 FOR UPDATE", args.retrieval_id)
    if row is None:
        raise KeyError(args.retrieval_id)
    returned = set(row["memory_ids_returned"] or [])
    used = set(args.memory_id or [])
    if not used.issubset(returned):
        raise ValueError(f"used memory ids were not returned: {sorted(used-returned)}")
    await conn.execute("""
      UPDATE soul_v3.memory_retrieval_log
      SET memory_ids_used=$1::bigint[], memory_ids_cited=$2::bigint[],
          citation_precision=CASE WHEN cardinality(memory_ids_returned)>0 THEN cardinality($2::bigint[])::float8/cardinality(memory_ids_returned) ELSE NULL END,
          citation_feedback_at=NOW(),outcome_success=$3,outcome_feedback_summary=$4,
          led_to_action=$5,agent_acted_on_memory=$5,outcome_recorded_at=NOW()
      WHERE id=$6
    """, sorted(used), sorted(used), args.success, args.summary, bool(used), args.retrieval_id)
    return {"retrieval_id": args.retrieval_id, "success": args.success, "used": sorted(used), "summary": args.summary}


async def skill_rollout_command(conn: asyncpg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if args.rollout_action == "canary":
        evaluation = await conn.fetchrow("""
          SELECT e.*,s.pending_review,s.boot_load,s.execution_environment
          FROM soul_v3.skill_shadow_eval e JOIN soul_v3.skills s ON s.id=e.skill_id
          WHERE e.skill_id=$1 AND e.eval_status='active' ORDER BY e.id DESC LIMIT 1
        """, args.skill_id)
        if evaluation is None or not evaluation["promotion_eligible"]:
            raise RuntimeError("canary gate blocked: skill lacks statistically eligible shadow evaluation")
        previous = {"pending_review": evaluation["pending_review"], "boot_load": evaluation["boot_load"], "execution_environment": evaluation["execution_environment"]}
        rollout_id = int(await conn.fetchval("""
          INSERT INTO soul_v3.skill_rollouts(skill_id,eval_id,state,canary_agent,traffic_percent,previous_state,promotion_gate)
          VALUES($1,$2,'canary',$3,$4,$5::jsonb,$6::jsonb) RETURNING id
        """, args.skill_id, evaluation["id"], args.canary_agent, args.traffic_percent, canonical_json(previous), canonical_json({"ci95_lower": evaluation["confidence_interval_95_lower"], "baseline": evaluation["baseline_success_rate"], "delta": evaluation["improvement_delta"]})))
        await conn.execute("""
          INSERT INTO soul_v3.skill_rollout_events(rollout_id,event_type,from_state,to_state,evidence)
          VALUES($1,'canary_started','shadow','canary',$2::jsonb)
        """, rollout_id, canonical_json({"agent": args.canary_agent, "traffic_percent": args.traffic_percent, "note": "assignment is gated; boot_load unchanged"}))
        return {"rollout_id": rollout_id, "state": "canary", "skill_id": args.skill_id, "boot_load_changed": False}
    rollout = await conn.fetchrow("SELECT * FROM soul_v3.skill_rollouts WHERE id=$1 FOR UPDATE", args.rollout_id)
    if rollout is None:
        raise KeyError(args.rollout_id)
    if rollout["state"] not in {"canary", "active", "rollback_pending"}:
        raise ValueError(f"cannot rollback rollout in state {rollout['state']}")
    await conn.execute("""
      UPDATE soul_v3.skill_rollouts SET state='rolled_back',rollback_reason=$1,ended_at=NOW(),updated_at=NOW() WHERE id=$2
    """, args.reason, rollout["id"])
    await conn.execute("""
      INSERT INTO soul_v3.skill_rollout_events(rollout_id,event_type,from_state,to_state,evidence)
      VALUES($1,'rollback_completed',$2,'rolled_back',$3::jsonb)
    """, rollout["id"], rollout["state"], canonical_json({"reason": args.reason, "previous_state": rollout["previous_state"]}))
    return {"rollout_id": rollout["id"], "from": rollout["state"], "state": "rolled_back", "reason": args.reason}


async def transaction_command(conn: asyncpg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if args.tx_action == "prepare":
        txid = uuid.uuid4()
        await conn.execute("""
          INSERT INTO soul_v3.tool_transactions(id,task_id,agent,objective,state,steps,rollback_plan)
          VALUES($1,$2,$3,$4,'prepared',$5::jsonb,$6::jsonb)
        """, txid, args.task_id, args.agent, args.objective, args.steps, args.rollback_plan)
        return {"transaction_id": str(txid), "state": "prepared"}
    row = await conn.fetchrow("SELECT * FROM soul_v3.tool_transactions WHERE id=$1::uuid FOR UPDATE", args.transaction_id)
    if row is None:
        raise KeyError(args.transaction_id)
    transitions = {"prepared": {"applied", "rolled_back", "failed"}, "applied": {"committed", "rollback_pending", "failed"}, "rollback_pending": {"rolled_back", "failed"}}
    if args.state not in transitions.get(row["state"], set()):
        raise ValueError(f"invalid transition {row['state']} -> {args.state}")
    await conn.execute("UPDATE soul_v3.tool_transactions SET state=$1,evidence=evidence || $2::jsonb,updated_at=NOW() WHERE id=$3", args.state, args.evidence, row["id"])
    return {"transaction_id": str(row["id"]), "from": row["state"], "state": args.state}


async def main_async(args: argparse.Namespace) -> int:
    conn = await connect()
    try:
        if args.command == "init-schema":
            await init_schema(conn)
            result = {"schema": "ok"}
        else:
            await require_schema(conn)
        if args.command == "init-schema": pass
        elif args.command == "run-cycle": result = await run_cycle(conn)
        elif args.command == "eval": result = await run_eval(conn, args.kind, args.seed)
        elif args.command == "resume-task": result = await resume_task(conn, args.task_id, args.agent)
        elif args.command == "memory-feedback": result = await record_retrieval_feedback(conn, args)
        elif args.command == "skill-rollout": result = await skill_rollout_command(conn, args)
        elif args.command == "transaction": result = await transaction_command(conn, args)
        elif args.command == "status":
            result = dict(await conn.fetchrow("""
              SELECT (SELECT count(*) FROM soul_v3.capability_eval_runs) eval_runs,
                     (SELECT count(*) FROM soul_v3.skill_shadow_eval WHERE eval_status='active') skill_evals,
                     (SELECT count(*) FROM soul_v3.memory_poisoning_feedback WHERE decision<>'allow') poison_flags,
                     (SELECT count(*) FROM soul_v3.task_resume_checkpoints WHERE status='ready') ready_checkpoints
            """))
        else: raise AssertionError(args.command)
    finally:
        await conn.close()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", default="ADA")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-schema")
    sub.add_parser("run-cycle")
    sub.add_parser("status")
    ev = sub.add_parser("eval"); ev.add_argument("kind", choices=("baseline","ablation","replay")); ev.add_argument("--seed", type=int, default=20260710)
    rs = sub.add_parser("resume-task"); rs.add_argument("task_id", type=int)
    mf = sub.add_parser("memory-feedback"); mf.add_argument("retrieval_id", type=int); mf.add_argument("--memory-id", type=int, action="append", default=[]); mf.add_argument("--success", action=argparse.BooleanOptionalAction, default=True); mf.add_argument("--summary", required=True)
    sr = sub.add_parser("skill-rollout"); srsub = sr.add_subparsers(dest="rollout_action", required=True)
    canary = srsub.add_parser("canary"); canary.add_argument("skill_id", type=int); canary.add_argument("--canary-agent", default="ADA"); canary.add_argument("--traffic-percent", type=int, default=5, choices=range(1, 26))
    rollback = srsub.add_parser("rollback"); rollback.add_argument("rollout_id", type=int); rollback.add_argument("--reason", required=True)
    tx = sub.add_parser("transaction"); txsub = tx.add_subparsers(dest="tx_action", required=True)
    prep = txsub.add_parser("prepare"); prep.add_argument("--task-id", type=int); prep.add_argument("--objective", required=True); prep.add_argument("--steps", default="[]"); prep.add_argument("--rollback-plan", default="[]")
    move = txsub.add_parser("transition"); move.add_argument("transaction_id"); move.add_argument("state", choices=("applied","committed","rollback_pending","rolled_back","failed")); move.add_argument("--evidence", default="{}")
    return p


def main() -> int:
    return asyncio.run(main_async(parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
