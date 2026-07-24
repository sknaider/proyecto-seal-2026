#!/usr/bin/env python3
"""Declarative lifecycle smoke test; it cannot authorize A2 activation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "memory"))

from nerves_self_created import (  # noqa: E402
    build_sibling_review,
    compile_self_created_mission,
    propose_self_created_nerve,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    skill_dir = ROOT / "skills" / "seal-nerves-self-creation"
    observations = [
        {
            "observation_id": "canary-observation-1",
            "pattern_id": "verified-repeated-need",
            "observed_at": "2026-07-22T10:00:00+00:00",
            "evidence_sha256": _sha("canary-evidence-1"),
            "verified": True,
        },
        {
            "observation_id": "canary-observation-2",
            "pattern_id": "verified-repeated-need",
            "observed_at": "2026-07-22T18:00:00+00:00",
            "evidence_sha256": _sha("canary-evidence-2"),
            "verified": True,
        },
        {
            "observation_id": "canary-observation-3",
            "pattern_id": "verified-repeated-need",
            "observed_at": "2026-07-23T10:00:00+00:00",
            "evidence_sha256": _sha("canary-evidence-3"),
            "verified": True,
        },
    ]
    with tempfile.TemporaryDirectory(prefix="seal-self-created-canary-") as raw:
        work = Path(raw)
        candidate = propose_self_created_nerve(
            agent="ADA",
            name="seal-nerves-self-creation",
            pattern_id="verified-repeated-need",
            observations=observations,
            skill_dir=skill_dir,
            output_dir=work / "ledger",
            root=ROOT,
            created_at="2026-07-24T12:00:00+00:00",
        )
        review = build_sibling_review(
            candidate,
            reviewer="NEXUS",
            verdict="APPROVED",
            evidence_sha256=_sha("independent-canary-review"),
            reviewed_at="2026-07-24T12:01:00+00:00",
            root=ROOT,
        )
        mission = compile_self_created_mission(
            candidate,
            objective="Verify the SELF_CREATED lifecycle without any system mutation.",
            root=ROOT,
        )
        replay = propose_self_created_nerve(
            agent="ADA",
            name="seal-nerves-self-creation",
            pattern_id="verified-repeated-need",
            observations=observations,
            skill_dir=skill_dir,
            output_dir=work / "ledger",
            root=ROOT,
            created_at="2026-07-24T12:00:00+00:00",
        )
        result = {
            "schema": "seal.nerves.self_created_lifecycle_smoke.v1",
            "ok": candidate == replay,
            "candidate_id": candidate["candidate_id"],
            "mission_id": mission["mission_id"],
            "reviewer": review["reviewer"],
            "allowed_tools": mission["allowed_tools"],
            "network": mission["network"],
            "mutations": 0,
            "production_writes": 0,
            "activation": False,
            "effective_canary_required": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
