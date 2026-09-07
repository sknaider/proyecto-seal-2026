#!/usr/bin/env python3
"""Stage the first evidence-backed ADA SELF_CREATED A2 nerve candidate."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))

from nerves_self_created import (  # noqa: E402
    _payload_hash,
    _write_private_once,
    compile_self_created_mission,
    propose_self_created_nerve,
)


SKILL_SCRIPT = (
    ROOT
    / "skills/seal-ada-ack-latency-triage/scripts/ack_latency_triage.py"
)
SPEC = importlib.util.spec_from_file_location("ack_latency_triage", SKILL_SCRIPT)
triage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(triage)

EVIDENCE_DIR = ROOT / "research/flywire_results/nerves_self_created"
SOURCE = EVIDENCE_DIR / "ada_ack_latency_source.json"
TRIAGE = EVIDENCE_DIR / "ada_ack_latency_triage.json"


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    fresh = triage.audit_bundle(source)
    stored = json.loads(TRIAGE.read_text(encoding="utf-8"))
    if fresh != stored:
        raise RuntimeError("triage_artifact_drift")
    observations = [
        {
            "observation_id": item["observation_id"],
            "pattern_id": item["pattern_id"],
            "observed_at": item["observed_at"],
            "evidence_sha256": item["evidence_sha256"],
            "verified": item["verified"],
        }
        for item in fresh["observations"]
    ]
    candidate = propose_self_created_nerve(
        agent="ADA",
        name="ada_ack_latency_triage",
        pattern_id=triage.PATTERN_ID,
        observations=observations,
        skill_dir=ROOT / "skills/seal-ada-ack-latency-triage",
        output_dir=EVIDENCE_DIR,
        root=ROOT,
        created_at=max(item["observed_at"] for item in observations),
    )
    mission = compile_self_created_mission(
        candidate,
        objective=(
            "Diagnose a typed public William-to-ADA ACK latency incident and "
            "return evidence without any system effect."
        ),
        root=ROOT,
    )
    mission_path = EVIDENCE_DIR / f"{candidate['candidate_id']}.mission.json"
    canary_path = EVIDENCE_DIR / f"{candidate['candidate_id']}.canary.json"
    _write_private_once(mission_path, mission)
    canary = {
        "schema": "seal.nerves.self_created_canary.v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "passed": (
            fresh["ok"]
            and fresh["observation_count"] == 3
            and fresh["late_ack_count"] == 3
        ),
        "risk_class": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
        "evidence_sha256": fresh["result_sha256"],
    }
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    _write_private_once(canary_path, canary)
    result = {
        "ok": canary["passed"],
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "mission_id": mission["mission_id"],
        "canary_sha256": canary["canary_sha256"],
        "status": candidate["status"],
        "review": "pending_independent_sibling",
        "activation": "pending_candidate_specific_william_approval",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
        "artifact_modes": {
            path.name: oct(path.stat().st_mode & 0o777)
            for path in (SOURCE, TRIAGE, canary_path, mission_path)
        },
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
