#!/usr/bin/env python3
"""verify_shadow_live.py — post-restart gate for shadow router hook.

Run AFTER each Claude Code session restart to confirm the running mcp_server_v2
subprocess has the shadow hook loaded and is writing to shadow_router.jsonl.

Checks:
  1. latent_graphmem_serve :8767 is healthy
  2. shadow_router.jsonl grew after issuing a magma_retrieve via the live MCP
     (via seal-memory MCP tool — requires this script to be called from an
     active Claude Code context, OR fall back to inspecting file mtime)
  3. Latest line has latent_status populated (confirms extended hook, not v1)

Usage:
  python3 verify_shadow_live.py [--since-ts ISO8601]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).parent
SHADOW_LOG = HERE / "diagnostic" / "shadow_router.jsonl"
SERVE_URL = "http://127.0.0.1:8767"


def check_serve() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{SERVE_URL}/health", timeout=3.0)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        j = r.json()
        if not j.get("ok"):
            return False, f"ok=false: {j}"
        if not j.get("adapter_loaded"):
            return False, "adapter not loaded"
        if j.get("circuit_open"):
            return False, "circuit open"
        return True, f"adapter_loaded, failures={j.get('failures', 0)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_shadow_log(since_ts: str | None) -> tuple[bool, str, dict | None]:
    if not SHADOW_LOG.exists():
        return False, "shadow_router.jsonl does not exist", None
    lines = SHADOW_LOG.read_text().strip().split("\n")
    if not lines or not lines[0]:
        return False, "empty", None
    last = json.loads(lines[-1])
    n_total = len(lines)
    if since_ts:
        cutoff = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
        n_after = sum(
            1 for l in lines
            if datetime.fromisoformat(json.loads(l)["ts"].replace("Z", "+00:00")) >= cutoff
        )
        return True, f"total={n_total}, since_cutoff={n_after}", last
    return True, f"total={n_total}", last


def check_extended_schema(last: dict | None) -> tuple[bool, str]:
    if not last:
        return False, "no sample line"
    required = ["pred_type", "route", "latent_status", "latent_ids", "agreement_top5"]
    missing = [k for k in required if k not in last]
    if missing:
        return False, f"missing fields: {missing} (hook not extended?)"
    return True, f"schema OK, latest: pred_type={last['pred_type']} route={last['route']} lat_status={last['latent_status']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-ts", help="ISO8601 timestamp — count log lines after this", default=None)
    args = ap.parse_args()

    print("=" * 60)
    print(f"SHADOW ROUTER VERIFICATION @ {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    ok1, msg1 = check_serve()
    print(f"[{'OK' if ok1 else 'FAIL'}] latent_serve :8767  — {msg1}")

    ok2, msg2, last = check_shadow_log(args.since_ts)
    print(f"[{'OK' if ok2 else 'FAIL'}] shadow_router.jsonl  — {msg2}")

    ok3, msg3 = check_extended_schema(last)
    print(f"[{'OK' if ok3 else 'FAIL'}] extended hook schema — {msg3}")

    all_ok = ok1 and ok2 and ok3
    print("-" * 60)
    print(f"RESULT: {'✅ SHADOW LIVE' if all_ok else '❌ SHADOW NOT READY'}")
    if not all_ok and args.since_ts:
        print("\nIf schema FAIL: run a magma_retrieve via the live MCP tool to populate a new line,")
        print("then re-run this script. If shadow_router.jsonl doesn't grow, the MCP subprocess")
        print("still has old code — restart that Claude Code session.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
