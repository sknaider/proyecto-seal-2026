#!/usr/bin/env python3
"""Fail-closed capacity probe for the local SUIE service."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


HEALTH_URL = os.environ.get("SUIE_HEALTH_URL", "http://127.0.0.1:8791/health")
TOKEN_PATH = Path(
    os.environ.get(
        "SUIE_TOKEN_FILE",
        "/home/dadito/IA/proyecto-seal/var/soul_ingestion/service.token",
    )
)


def probe() -> dict[str, object]:
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if len(token) < 32 or TOKEN_PATH.stat().st_mode & 0o077:
        raise RuntimeError("token file missing, invalid, or too broadly readable")
    request = urllib.request.Request(
        HEALTH_URL,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"health endpoint returned HTTP {response.status}")
        payload = json.load(response)
    expected = {
        "ok": True,
        "status": "healthy",
        "database": "seal_memory",
        "database_login": "svc_soul_ingestion",
        "database_role": "pr_ingestion_processor",
        "memory_write": False,
        "artifact_store": "ready",
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if payload.get("staging_tables", 0) < 6:
        mismatches["staging_tables"] = {"expected": ">=6", "actual": payload.get("staging_tables")}
    if mismatches:
        raise RuntimeError(f"capacity contract mismatch: {mismatches}")
    return {
        "ok": True,
        "service": "seal-soul-ingestion",
        "database_login": payload["database_login"],
        "database_role": payload["database_role"],
        "staging_tables": payload["staging_tables"],
        "memory_write": payload["memory_write"],
    }


def main() -> int:
    try:
        print(json.dumps(probe(), sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(
            json.dumps(
                {"ok": False, "service": "seal-soul-ingestion", "error": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
