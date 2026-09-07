#!/usr/bin/env python3
"""Create one idempotent synthetic ADA A2 handoff for the isolated Codex runner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.nerves_agent_mission_core import (
    ADA_ROUTE,
    compile_ada_engineering_mission,
    deliver_handoff,
)
from memory.nerves_mission_handoff import _canonical_bytes, _secure_create

ARTIFACT = (
    ROOT / "research/flywire_results/nerves_ada_codex_canary_v3.jsonl"
)
RECORD = {
    "ts": "2026-07-24T01:50:00+00:00",
    "agent": "ADA",
    "action": "engineering_pulse",
    "changed_python_checked": 1,
    "diff_check_ok": True,
    "syntax_failures": ["memory/CANARY_DO_NOT_EDIT.py"],
    "status": "issue",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one idempotent ADA Codex A2 canary handoff."
    )
    return parser.parse_args()


def main() -> int:
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(RECORD) + b"\n"
    created = _secure_create(ARTIFACT, raw, mode=0o600)
    if not created and ARTIFACT.read_bytes() != raw:
        raise RuntimeError("canary_artifact_replay_mismatch")
    os.chmod(ARTIFACT, 0o600)
    compiled = compile_ada_engineering_mission(ARTIFACT)
    handoff = deliver_handoff(compiled, route=ADA_ROUTE)
    print(
        json.dumps(
            {
                "ok": True,
                "artifact_created": created,
                "mission_created": compiled.created,
                "handoff_created": handoff.created,
                "mission_id": handoff.mission_id,
                "handoff_path": str(handoff.handoff_path),
                "handoff_sha256": handoff.handoff_sha256,
                "status": handoff.status,
                "live_notified": handoff.live_notified,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    _parse_args()
    sys.exit(main())
