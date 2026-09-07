#!/usr/bin/env python3
"""Non-executable canary exposing the NERVES P3 live security HOLD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "schema": "seal.nerves.shadow-worker-canary.v1",
                "status": "live_runner_security_hold",
                "codex_invoked": False,
                "manifest": str(args.manifest),
                "reason": (
                    "No OS boundary yet isolates Codex auth, project MCP/plugins "
                    "and service-control sockets. This CLI has no enable flag."
                ),
            },
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
