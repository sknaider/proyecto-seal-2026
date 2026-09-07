#!/usr/bin/env python3
"""Readiness fail-loud del endpoint sync; distingue proceso de socket listo."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request


def tailnet_ipv4() -> str:
    result = subprocess.run(
        ["tailscale", "ip", "-4"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return ""
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")


def probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            body = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and body == {
            "status": "up",
            "service": "seal_sync_endpoint",
        }
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return False


def wait_ready(bind: str, port: int, timeout_s: float) -> bool:
    if not bind:
        return False
    url = f"http://{bind}:{port}/health"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe(url):
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="")
    parser.add_argument("--port", type=int, default=8778)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    bind = args.bind or tailnet_ipv4()
    ready = wait_ready(bind, args.port, args.timeout)
    print(json.dumps({
        "schema": "seal.sync.readiness.v1",
        "bind": bind,
        "port": args.port,
        "ready": ready,
    }))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
