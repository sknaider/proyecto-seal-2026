#!/usr/bin/env python3
"""Controlled threshold→action→artifact→cooldown/reset canary for NERVES."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit
import uuid

import seal_nerves as nerves


ROOT = Path("/home/dadito/IA/proyecto-seal")
REPORT = ROOT / "research/flywire_results/nerves_e2e_canary.json"
CONTRACT = ROOT / "memory/nerves_contract_v3.json"
RUNTIME_ENV_DIR = Path.home() / ".config/seal"
ARTIFACTS = {
    "ADA": ROOT / "research/flywire_results/nerves_ada_maintenance.jsonl",
    "JARVIS": ROOT / "research/flywire_results/nerves_jarvis_maintenance.jsonl",
    "ALICE": ROOT / "agents/ALICE/orion/orion_nerve_status.log",
    "NEXUS": ROOT / "research/flywire_results/nerves_nexus_maintenance.jsonl",
    "DUM": ROOT / "research/flywire_results/nerves_dum_maintenance.jsonl",
}


def _runtime_dsn(agent: str) -> str:
    """Load one agent's direct-login DSN without sourcing or exposing it."""
    normalized = agent.strip().upper()
    if normalized not in ARTIFACTS:
        raise RuntimeError(f"unsupported NERVES agent: {normalized}")
    path = RUNTIME_ENV_DIR / f"soul_nerves_{normalized.lower()}_db.env"
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"runtime env is not a regular file: {path}")
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise RuntimeError(f"runtime env must be owner-only 0600: {path}")

    values = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "SEAL_DB_DSN":
            values.append(value.strip().strip('"').strip("'"))
    if len(values) != 1 or not values[0]:
        raise RuntimeError(f"runtime env must contain exactly one SEAL_DB_DSN: {path}")

    expected = f"svc_soul_nerves_{normalized.lower()}"
    if urlsplit(values[0]).username != expected:
        raise RuntimeError(f"runtime env identity mismatch: expected {expected}")
    return values[0]

def _artifact_update_required(agent: str) -> bool:
    """Follow the canonical contract instead of hard-coding agent identity."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    maintenance = contract.get("shared_runtime", {}).get("maintenance", {})
    return maintenance.get(agent, {}).get("central_mode") != "verify_artifact_only"


async def _one(agent: str) -> dict:
    run_id = f"canary-{agent.lower()}-{uuid.uuid4()}"
    previous_db_url = nerves.DB_URL
    engine = None
    artifact = ARTIFACTS[agent]
    before_mtime = artifact.stat().st_mtime_ns if artifact.exists() else None
    started = time.monotonic()
    fired: list[dict] = []
    snapshot: list[dict] = []

    async def never_suppress() -> bool:
        return False

    async def no_flush() -> None:
        return None

    try:
        nerves.DB_URL = _runtime_dsn(agent)
        engine = nerves.MotivationEngine(
            agent,
            trigger_source="controlled_e2e_canary",
            run_id=run_id,
        )
        await engine.connect()
        engine._should_suppress = never_suppress
        engine._flush_queue_if_idle = no_flush
        async with engine.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tank,value,last_update,last_fired,fire_count,metadata "
                "FROM motivation_states WHERE agent=$1 AND tank=ANY($2::text[]) ORDER BY tank",
                agent, list(nerves.TANKS),
            )
            snapshot = [dict(row) for row in rows]
            await conn.execute(
                "UPDATE motivation_states SET value=0,last_update=NOW(),last_fired=NULL "
                "WHERE agent=$1 AND tank=ANY($2::text[])",
                agent, list(nerves.TANKS),
            )
            threshold = float(
                nerves.AGENT_TANK_OVERRIDES.get(agent, {})
                .get("curiosity", {})
                .get("threshold", nerves.TANKS["curiosity"]["threshold"])
            )
            before_fire_count = int(
                next(row["fire_count"] for row in snapshot if row["tank"] == "curiosity")
            )
        # Exercise the real atomic stimulus path; direct DB pressure injection
        # would skip one of the stages this canary claims to verify.
        base_delta = float(nerves.STIMULI["topic_interesting"])
        stimulated = await engine.stimulate(
            "topic_interesting",
            multiplier=(threshold + 5.0) / base_delta,
            target_tank="curiosity",
        )
        fired = await asyncio.wait_for(engine.tick(), timeout=60)
        async with engine.pool.acquire() as conn:
            state_after = await conn.fetchrow(
                "SELECT value,last_fired,fire_count FROM motivation_states "
                "WHERE agent=$1 AND tank='curiosity'",
                agent,
            )
        ledger_rows = [
            json.loads(line)
            for line in nerves.ACTION_LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        action_evidence = [row for row in ledger_rows if row.get("run_id") == run_id]
        ledger_statuses = {row.get("status") for row in action_evidence}
        after_mtime = artifact.stat().st_mtime_ns if artifact.exists() else None
        artifact_updated = after_mtime is not None and after_mtime != before_mtime
        update_required = _artifact_update_required(agent)
        artifact_effect_ok = after_mtime is not None and (
            artifact_updated or not update_required
        )
        passed = (
            len(fired) == 1
            and fired[0].get("tank") == "curiosity"
            and str(fired[0].get("result", "")).startswith("maintenance_fired:")
            and artifact_effect_ok
            and float(stimulated.get("curiosity", 0.0)) >= threshold
            and state_after is not None
            and float(state_after["value"]) == 0.0
            and state_after["last_fired"] is not None
            and int(state_after["fire_count"]) == before_fire_count + 1
            and ledger_statuses
                == {"claimed", "effect_verified", "reset_committed"}
            and all(
                row.get("trigger_source") == "controlled_e2e_canary"
                for row in action_evidence
            )
        )
        return {
            "agent": agent,
            "db_identity": urlsplit(nerves.DB_URL).username,
            "status": "pass" if passed else "fail",
            "fired": fired,
            "artifact_updated": artifact_updated,
            "artifact_mode": "verified" if not update_required else "produced",
            "artifact_update_required": update_required,
            "stimulus_path": "MotivationEngine.stimulate(topic_interesting)",
            "run_id": run_id,
            "state_reset": state_after is not None and float(state_after["value"]) == 0.0,
            "fire_count_incremented": state_after is not None and int(state_after["fire_count"]) == before_fire_count + 1,
            "ledger_provenance": sorted(ledger_statuses),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "agent": agent,
            "status": "fail",
            "error": f"{type(exc).__name__}:{exc}",
            "fired": fired,
            "artifact_updated": False,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        try:
            if engine is not None and engine.pool and snapshot:
                async with engine.pool.acquire() as conn:
                    for row in snapshot:
                        await conn.execute(
                            "UPDATE motivation_states SET value=$1,last_update=$2,last_fired=$3,"
                            "fire_count=$4,metadata=$5 WHERE agent=$6 AND tank=$7",
                            row["value"], row["last_update"], row["last_fired"],
                            row["fire_count"], row["metadata"], agent, row["tank"],
                        )
        finally:
            try:
                if engine is not None:
                    await engine.close()
            finally:
                nerves.DB_URL = previous_db_url


async def main() -> int:
    if os.environ.get("SEAL_NERVES_USEFUL") != "1":
        raise RuntimeError("SEAL_NERVES_USEFUL=1 is required")
    rows = []
    for agent in ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM"):
        rows.append(await _one(agent))
    report = {
        "schema": "seal.nerves_e2e_canary.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "controlled_real_db_snapshot_restore",
        "results": rows,
        "pass": all(row["status"] == "pass" for row in rows),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(REPORT)
    os.chmod(REPORT, 0o600)
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
