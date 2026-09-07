#!/usr/bin/env python3
"""Cheap infrastructure pulse for DUM NERVES (no mutation, no token use)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from datetime import datetime, timezone


ROOT = Path("/home/dadito/IA/proyecto-seal")
ARTIFACT = ROOT / "research/flywire_results/nerves_dum_maintenance.jsonl"
PORTS = (5433, 7687, 8765, 8768, 9090, 11434)


def _port_up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _run() -> str | None:
    failed_ports = [port for port in PORTS if not _port_up(port)]
    root_usage = shutil.disk_usage("/")
    disk_pct = round(root_usage.used * 100 / root_usage.total, 1)
    zombie_proc = subprocess.run(
        ["ps", "-eo", "stat="], capture_output=True, text=True,
        timeout=8, check=False,
    )
    zombies = sum(1 for line in zombie_proc.stdout.splitlines() if line.startswith("Z"))
    findings: list[str] = []
    if failed_ports:
        findings.append(f"ports_down={failed_ports}")
    if disk_pct >= 90:
        findings.append(f"disk_root={disk_pct}%")
    if zombies:
        findings.append(f"zombies={zombies}")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": "DUM",
        "action": "infrastructure_pulse",
        "ports": {str(port): port not in failed_ports for port in PORTS},
        "disk_root_pct": disk_pct,
        "zombies": zombies,
        "status": "issue" if findings else "clean",
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(ARTIFACT, 0o600)
    if findings:
        return f"infraestructura: {'; '.join(findings)} · artifact={ARTIFACT}"
    return None


async def infrastructure_pulse() -> str | None:
    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    print(asyncio.run(infrastructure_pulse()) or "clean")
