#!/usr/bin/env python3
"""Compile the latest JARVIS integrity episode into the NERVES v4 shadow ledger.

No worker is launched and no production state is changed. Exit 0 means a
schema-valid mission was created or joined; exit 3 means there is no actionable
episode after the latest GREEN boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory.nerves_mission_shadow import (  # noqa: E402
    DEFAULT_LEDGER,
    DEFAULT_MANIFEST_DIR,
    ShadowMissionLedger,
    compile_jarvis_integrity_mission,
    latest_actionable_episode,
    load_artifact_records,
    write_shadow_manifest,
)


DEFAULT_ARTIFACT = (
    ROOT / "research/flywire_results/nerves_jarvis_maintenance.jsonl"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    args = parser.parse_args()

    episode = latest_actionable_episode(load_artifact_records(args.artifact))
    if episode is None:
        print(
            json.dumps(
                {
                    "schema": "seal.nerves.shadow-canary.v1",
                    "status": "no_actionable_episode",
                    "artifact": str(args.artifact),
                },
                sort_keys=True,
            )
        )
        return 3

    record, anchor = episode
    mission = compile_jarvis_integrity_mission(
        record,
        artifact_path=args.artifact,
        episode_anchor=anchor,
    )
    ledger = ShadowMissionLedger(args.ledger)
    opened = ledger.open_or_join(mission)
    manifest_path = write_shadow_manifest(opened.mission, args.manifest_dir)
    verification = ledger.verify()
    payload = {
        "schema": "seal.nerves.shadow-canary.v1",
        "status": "mission_created" if opened.created else "mission_joined",
        "mission_id": opened.mission["mission_id"],
        "idempotency_key": opened.mission["idempotency_key"],
        "sequence": opened.sequence,
        "ledger_verified": verification.ok,
        "ledger_head_hash": verification.head_hash,
        "risk_class": opened.mission["risk_class"],
        "worker_launched": False,
        "manifest_path": str(manifest_path),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if verification.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
