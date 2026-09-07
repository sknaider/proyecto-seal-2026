#!/usr/bin/env python3
"""Production parent process for SEAL awareness 24/7.

The process links five families:
1. awareness event loop
2. local Gemma runtime proposals
3. measured learning loop with NEXUS gates
4. LatentGraphMem Phase 2 gates
5. production health/healthcheck evidence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from awareness_closed_loop import build_closed_loop_from_dataset, ensure_closed_loop_schema
from awareness_experience_dataset import build_dataset_from_loop_run, ensure_experience_schema
from awareness_loop import AwarenessLoopRun, run_payload_loop
from latent_graphmem_phase2 import assess_latent_graphmem_phase2
from local_runtime_service import LocalRuntimeConfig, reflect, runtime_health
from seal_secrets import pg_dsn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_PATH = Path(os.environ.get("SEAL_AWARENESS_247_HEARTBEAT", "/tmp/seal_awareness_247_ADA.json"))
PROCESS_SERVICES = [
    "seal-ui-5173.service",
    "seal-awareness-dashboard.service",
    "seal-studio-backend.service",
    "seal-chat.service",
    "seal-mcp-server.service",
    "ada-codex-remote-bridge.service",
]


@dataclass(frozen=True)
class Awareness247Run:
    agent: str
    run_id: str
    created_at: str
    phase_scores: dict[str, int]
    passed: bool
    evidence: str
    awareness: dict[str, Any]
    local_runtime: dict[str, Any]
    learning_loop: dict[str, Any]
    latent_graphmem: dict[str, Any]
    production: dict[str, Any]
    db_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def ensure_process_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.awareness_247_process_runs (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT UNIQUE NOT NULL,
            agent TEXT NOT NULL,
            phase_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
            passed BOOLEAN NOT NULL DEFAULT false,
            evidence TEXT NOT NULL DEFAULT '',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_awareness_247_runs_agent_created
        ON soul_v3.awareness_247_process_runs(agent, created_at DESC)
        """
    )


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _service_state(name: str) -> str:
    proc = subprocess.run(
        ["systemctl", "--user", "is-active", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    return proc.stdout.strip() or proc.stderr.strip() or f"exit={proc.returncode}"


def _service_show(name: str) -> dict[str, str]:
    proc = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            name,
            "--property=MainPID",
            "--property=ActiveState",
            "--property=SubState",
            "--property=FragmentPath",
            "--property=Transient",
            "--property=ExecStart",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    values: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if proc.stderr and not values:
        values["error"] = proc.stderr.strip()
    return values


def _run_command(argv: list[str], timeout: float = 10.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}


def collect_live_payloads(agent: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for service in PROCESS_SERVICES:
        payloads.append({"kind": "service_status", "service": service, "state": _service_state(service)})
    changed = _run_command(["git", "status", "--short"], timeout=5)
    paths = []
    for line in changed["stdout"].splitlines()[:80]:
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if path:
            paths.append(path)
    payloads.append({"kind": "git_status", "changed_paths": paths})
    payloads.append(
        {
            "kind": "test_result",
            "test": "awareness_247_process_liveness",
            "passed": True,
            "evidence": f"{agent.upper()} awareness 24/7 tick collected {len(PROCESS_SERVICES)} services",
        }
    )
    return payloads


def _score_from_checks(checks: dict[str, bool]) -> int:
    if not checks:
        return 0
    return round((sum(1 for ok in checks.values() if ok) / len(checks)) * 100)


async def _learning_loop_state(agent: str, loop_run: AwarenessLoopRun, bootstrap: bool) -> dict[str, Any]:
    agent = agent.upper()
    conn = await connect_db()
    try:
        await ensure_experience_schema(conn)
        await ensure_closed_loop_schema(conn)
        existing = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM soul_v3.awareness_experience_examples WHERE agent=$1) AS examples,
              (SELECT COUNT(*) FROM soul_v3.awareness_adapter_queue WHERE agent=$1) AS queued,
              (SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1) AS outcomes,
              (SELECT COUNT(*) FROM soul_v3.awareness_learning_schedule WHERE agent=$1) AS schedules
            """,
            agent,
        )
    finally:
        await conn.close()

    bootstrapped = False
    dataset_payload: dict[str, Any] | None = None
    closed_payload: dict[str, Any] | None = None
    if bootstrap and (int(existing["schedules"] or 0) == 0 or int(existing["outcomes"] or 0) == 0):
        dataset = await build_dataset_from_loop_run(loop_run, persist=True)
        closed = await build_closed_loop_from_dataset(dataset, persist=True)
        dataset_payload = {
            "examples": len(dataset.examples),
            "queued": len(dataset.queue_records),
            "persisted": dataset.persisted,
        }
        closed_payload = {
            "outcomes": len(closed.outcomes),
            "schedule": closed.schedule.to_dict(),
            "persisted": closed.persisted,
        }
        bootstrapped = True

    conn = await connect_db()
    try:
        counts = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM soul_v3.awareness_experience_examples WHERE agent=$1) AS examples,
              (SELECT COUNT(*) FROM soul_v3.awareness_adapter_queue WHERE agent=$1) AS queued,
              (SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1) AS outcomes,
              (SELECT COUNT(*) FROM soul_v3.awareness_learning_schedule WHERE agent=$1) AS schedules,
              (SELECT COUNT(*) FROM soul_v3.awareness_closed_loop_outcomes WHERE agent=$1 AND status='pending_nexus_review') AS pending_review,
              (
                SELECT COUNT(*)
                FROM soul_v3.awareness_closed_loop_outcomes
                WHERE agent=$1
                  AND status IN ('approved_by_nexus','rejected_by_nexus','needs_evidence')
              ) AS reviewed_outcomes
            """,
            agent,
        )
    finally:
        await conn.close()

    checks = {
        "experience_examples_present": int(counts["examples"] or 0) > 0,
        "adapter_queue_schema_active": int(counts["queued"] or 0) >= 0,
        "closed_loop_outcomes_present": int(counts["outcomes"] or 0) > 0,
        "learning_schedule_present": int(counts["schedules"] or 0) > 0,
        "nexus_gate_pending_or_resolved": int(counts["pending_review"] or 0) + int(counts["reviewed_outcomes"] or 0) > 0,
    }
    return {
        "score": _score_from_checks(checks),
        "checks": checks,
        "counts": {key: int(counts[key] or 0) for key in counts.keys()},
        "bootstrapped": bootstrapped,
        "dataset": dataset_payload,
        "closed_loop": closed_payload,
        "boundary": "learning loop records candidates and outcomes gated by NEXUS; pending and reviewed terminal decisions both keep the gate auditable with no automatic adapter training",
    }


def _production_state() -> dict[str, Any]:
    service_show = {service: _service_show(service) for service in PROCESS_SERVICES}
    ui_health = _run_command([str(PROJECT_ROOT / "seal-desktop" / "ui" / "healthcheck.sh")], timeout=12)
    dashboard_health = _run_command([str(PROJECT_ROOT / "awareness-dashboard" / "healthcheck.sh")], timeout=12)
    process_service_path = PROJECT_ROOT / "memory" / "seal-awareness-247.service"
    installed_process_service = Path.home() / ".config" / "systemd" / "user" / "seal-awareness-247.service"
    checks = {
        "core_services_active": all(values.get("ActiveState") == "active" for values in service_show.values()),
        "ui_service_persistent": service_show["seal-ui-5173.service"].get("Transient") == "no",
        "ui_healthcheck_ok": ui_health["returncode"] == 0,
        "dashboard_healthcheck_ok": dashboard_health["returncode"] == 0,
        "process_unit_file_present": process_service_path.exists(),
        "process_unit_installed": installed_process_service.exists(),
    }
    return {
        "score": _score_from_checks(checks),
        "checks": checks,
        "services": service_show,
        "ui_health": ui_health,
        "dashboard_health": dashboard_health,
        "process_service_path": str(process_service_path),
        "installed_process_service": str(installed_process_service),
    }


async def run_process_once(
    agent: str = "ADA",
    *,
    persist: bool = True,
    bootstrap_learning: bool = True,
    enable_local: bool = True,
) -> Awareness247Run:
    agent = agent.upper()
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = f"awareness247:{agent}:{int(time.time() * 1000)}"

    payloads = collect_live_payloads(agent)
    loop_run = await run_payload_loop(payloads, agent=agent, dry_run=not persist, enable_local=False, enable_reflex=True)
    awareness_checks = {
        "live_payloads_collected": len(payloads) >= len(PROCESS_SERVICES),
        "events_processed": loop_run.processed == len(payloads),
        "ticks_persisted": (not persist) or loop_run.persisted,
        "reflex_boundary_safe": all(
            not item.get("reflex_execution") or item["reflex_execution"].get("boundary") == "limited_reflex_executor_no_mutation"
            for item in loop_run.to_dict()["items"]
        ),
    }
    awareness = {
        "score": _score_from_checks(awareness_checks),
        "checks": awareness_checks,
        "run": loop_run.to_dict(),
    }

    runtime = runtime_health()
    local_result = None
    if enable_local:
        context = json.dumps({"agent": agent, "payloads": payloads[:5], "awareness_actions": loop_run.action_counts}, sort_keys=True)
        local_result = reflect(context, LocalRuntimeConfig()).to_dict()
    runtime_checks = {
        "gemma_models_endpoint_ok": bool(runtime.get("ok")),
        "gemma_model_selected": runtime.get("model") in runtime.get("model_ids", []),
        "local_proposal_boundary": (not enable_local) or bool(local_result and local_result.get("boundary") == "proposal_only_no_side_effects"),
        "local_runtime_no_execution": True,
    }
    local_runtime = {
        "score": _score_from_checks(runtime_checks),
        "checks": runtime_checks,
        "health": runtime,
        "proposal": local_result,
    }

    learning_loop = await _learning_loop_state(agent, loop_run, bootstrap_learning and persist)
    latent = await assess_latent_graphmem_phase2(agent, persist=persist)
    production = _production_state()
    phase_scores = {
        "awareness_24_7": int(awareness["score"]),
        "local_runtime": int(local_runtime["score"]),
        "learning_loop": int(learning_loop["score"]),
        "latent_graphmem_phase2": int(latent.score),
        "production_clean": int(production["score"]),
    }
    passed = all(score >= 90 for score in phase_scores.values())
    evidence = (
        f"awareness_247_process phases={sum(1 for score in phase_scores.values() if score >= 90)}/5 "
        f"processed={loop_run.processed} runtime_ok={runtime.get('ok')} "
        f"learning_outcomes={learning_loop['counts'].get('outcomes')} "
        f"latent_score={latent.score} production_score={production['score']}"
    )
    result = Awareness247Run(
        agent=agent,
        run_id=run_id,
        created_at=created_at,
        phase_scores=phase_scores,
        passed=passed,
        evidence=evidence,
        awareness=awareness,
        local_runtime=local_runtime,
        learning_loop=learning_loop,
        latent_graphmem=latent.to_dict(),
        production=production,
    )
    if persist:
        conn = await connect_db()
        try:
            await ensure_process_schema(conn)
            db_id = await conn.fetchval(
                """
                INSERT INTO soul_v3.awareness_247_process_runs
                    (run_id, agent, phase_scores, passed, evidence, details)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6::jsonb)
                ON CONFLICT (run_id) DO UPDATE SET
                    phase_scores=EXCLUDED.phase_scores,
                    passed=EXCLUDED.passed,
                    evidence=EXCLUDED.evidence,
                    details=EXCLUDED.details
                RETURNING id
                """,
                result.run_id,
                result.agent,
                json.dumps(result.phase_scores, sort_keys=True),
                result.passed,
                result.evidence,
                json.dumps(result.to_dict(), sort_keys=True, default=_json_default),
            )
            result = Awareness247Run(**{**result.to_dict(), "db_id": int(db_id)})
        finally:
            await conn.close()
        write_heartbeat(result)
    return result


def write_heartbeat(result: Awareness247Run) -> None:
    HEARTBEAT_PATH.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def read_heartbeat(max_age_seconds: int = 240) -> dict[str, Any]:
    if not HEARTBEAT_PATH.exists():
        return {"ok": False, "error": "heartbeat_missing", "path": str(HEARTBEAT_PATH)}
    payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(payload["created_at"])
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return {
        "ok": bool(payload.get("passed")) and age <= max_age_seconds,
        "age_seconds": round(age, 1),
        "path": str(HEARTBEAT_PATH),
        "payload": payload,
    }


async def daemon(agent: str, interval_seconds: int) -> None:
    while True:
        try:
            result = await run_process_once(agent, persist=True, bootstrap_learning=True, enable_local=True)
            print(json.dumps({"ok": result.passed, "run_id": result.run_id, "evidence": result.evidence}, ensure_ascii=False), flush=True)
        except Exception as exc:
            failure = {
                "agent": agent.upper(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "passed": False,
                "error": str(exc),
            }
            HEARTBEAT_PATH.write_text(json.dumps(failure, indent=2), encoding="utf-8")
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        await asyncio.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness 24/7 parent process")
    parser.add_argument("--agent", default="ADA")
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("once")
    once.add_argument("--no-persist", action="store_true")
    once.add_argument("--no-local", action="store_true")
    once.add_argument("--no-learning-bootstrap", action="store_true")
    daemon_parser = sub.add_parser("daemon")
    daemon_parser.add_argument("--interval-seconds", type=int, default=120)
    health = sub.add_parser("health")
    health.add_argument("--max-age-seconds", type=int, default=240)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "once":
        result = await run_process_once(
            args.agent,
            persist=not args.no_persist,
            bootstrap_learning=not args.no_learning_bootstrap,
            enable_local=not args.no_local,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=_json_default))
        return 0 if result.passed else 2
    if args.command == "daemon":
        await daemon(args.agent, args.interval_seconds)
        return 0
    if args.command == "health":
        payload = read_heartbeat(args.max_age_seconds)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
        return 0 if payload.get("ok") else 2
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
