#!/usr/bin/env python3
"""Root-mediated, witnessed reconciliation for indeterminate browser effects."""

from __future__ import annotations

import argparse
import os
import pwd
from pathlib import Path

from mcp_web_soul_control import BrowserControlPlane
from mcp_web_soul_security import AuditTrail


STATE_ROOT = Path("/var/lib/seal-mcp-web-soul-operator")
CONTROL_PATH = Path("/var/lib/seal-mcp-web-cdp/control/control.sqlite3")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-id", required=True)
    resolution = parser.add_mutually_exclusive_group(required=True)
    resolution.add_argument("--effect-occurred", action="store_true")
    resolution.add_argument("--no-effect", action="store_true")
    parser.add_argument("--operator", default="William")
    args = parser.parse_args(argv)

    expected_uid = pwd.getpwnam("seal-mcp-web-operator").pw_uid
    if os.geteuid() != expected_uid:
        raise SystemExit("run only via root -> seal-mcp-web-operator")
    audit = AuditTrail(
        STATE_ROOT / "audit" / "operator.jsonl",
        agent="OPERATOR",
        witness_socket="/run/seal-audit-witness/witness.sock",
        witness_required=True,
    )
    payload = {
        "operation_id": str(args.operation_id),
        "effect_occurred": bool(args.effect_occurred),
        "operator": str(args.operator),
    }
    audit.append(
        tool="remote_effect_reconcile.intent",
        arguments=payload,
        ok=True,
        action_class="OPERATOR",
        required=True,
    )
    result = BrowserControlPlane(
        CONTROL_PATH, shared_group="seal-mcp-web-control",
    ).reconcile_remote_effect(
        str(args.operation_id),
        effect_occurred=bool(args.effect_occurred),
        operator=str(args.operator),
    )
    audit.append(
        tool="remote_effect_reconcile",
        arguments=payload,
        ok=True,
        action_class="OPERATOR",
        required=True,
    )
    print("reconciled" if result["new_effect"] else "already-reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
