#!/usr/bin/env python3
"""Controlled, non-destructive canary for every useful NERVES role action."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import seal_nerves as nerves


ROOT = Path("/home/dadito/IA/proyecto-seal")
REPORT = ROOT / "research/flywire_results/nerves_activation_canary.json"
CONTRACT = ROOT / "memory/nerves_contract_v3.json"
ARTIFACTS = {
    "ADA": ROOT / "research/flywire_results/nerves_ada_maintenance.jsonl",
    "JARVIS": ROOT / "research/flywire_results/nerves_jarvis_maintenance.jsonl",
    "ALICE": ROOT / "agents/ALICE/orion/orion_nerve_status.log",
    "NEXUS": ROOT / "research/flywire_results/nerves_nexus_maintenance.jsonl",
    "DUM": ROOT / "research/flywire_results/nerves_dum_maintenance.jsonl",
}

def _artifact_update_required(agent: str) -> bool:
    """Follow the canonical contract instead of hard-coding agent identity."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    maintenance = contract.get("shared_runtime", {}).get("maintenance", {})
    return maintenance.get(agent, {}).get("central_mode") != "verify_artifact_only"


async def _one(agent: str) -> dict:
    path = ARTIFACTS[agent]
    before = path.stat().st_mtime_ns if path.exists() else None
    started = time.monotonic()
    engine = nerves.MotivationEngine(agent)
    try:
        result = await asyncio.wait_for(nerves._run_maintenance_action(engine), timeout=30)
    except asyncio.TimeoutError:
        result = f"maintenance_failed:{agent}:TimeoutError"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    after = path.stat().st_mtime_ns if path.exists() else None
    artifact_updated = after is not None and after != before
    failed = bool(result and str(result).startswith("maintenance_failed:"))
    update_required = _artifact_update_required(agent)
    artifact_effect_ok = after is not None and (artifact_updated or not update_required)
    return {
        "agent": agent,
        "status": "fail" if failed or not artifact_effect_ok else "pass",
        "action_result": "issue" if result and not failed else ("failure" if failed else "clean"),
        "elapsed_ms": elapsed_ms,
        "artifact": str(path.relative_to(ROOT)),
        "artifact_updated": artifact_updated,
        "artifact_mode": "verified" if not update_required else "produced",
        "artifact_update_required": update_required,
    }


async def main() -> int:
    agents = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")
    rows = []
    for agent in agents:
        rows.append(await _one(agent))
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "controlled_non_destructive",
        "results": rows,
        "pass": all(row["status"] == "pass" for row in rows),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(REPORT)
    REPORT.chmod(0o600)
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
