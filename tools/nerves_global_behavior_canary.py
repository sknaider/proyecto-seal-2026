#!/usr/bin/env python3
"""Run the GLOBAL protocol matrix in a private disposable ledger.

The result is intentionally not a live-behavior canary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))

from nerves_global_behavior import run_behavior_matrix  # noqa: E402


REPORT = ROOT / "research/flywire_results/nerves_global_behavior_canary.json"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="seal-global-behavior-") as raw:
        result = run_behavior_matrix(Path(raw))
    report = {
        **result,
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "synthetic_protocol_validator",
        "not_production_behavior_evidence": True,
        "ledger_retained": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    tmp.replace(REPORT)
    os.chmod(REPORT, 0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
