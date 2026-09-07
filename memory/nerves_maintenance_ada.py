#!/usr/bin/env python3
"""Deterministic engineering pulse for ADA's useful NERVES drive.

The pulse is deliberately read-mostly: it validates the current Python working
surface and Git whitespace without importing project modules or changing code.
Every execution leaves a small 0600 JSONL artifact; only failures are returned
to the NERVES dispatcher.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone


ROOT = Path("/home/dadito/IA/proyecto-seal")
ARTIFACT = ROOT / "research/flywire_results/nerves_ada_maintenance.jsonl"
SCOPES = ("memory", "messages", "scripts", "tools", "sandbox-agent")


def _changed_python_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--", *SCOPES],
        cwd=ROOT,
        capture_output=True,
        timeout=20,
        check=False,
    )
    paths: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if len(raw) < 4:
            continue
        rel = raw[3:].decode("utf-8", errors="surrogateescape")
        if " -> " in rel:
            rel = rel.rsplit(" -> ", 1)[1]
        path = ROOT / rel
        if path.suffix == ".py" and path.is_file():
            paths.append(path)
    # El tamaño del working tree no puede convertir el pulso en un falso verde.
    # Estos son archivos ya identificados por git, no un walk recursivo del repo;
    # se revisan todos y el timeout del proceso NERVES sigue siendo el guard duro.
    return paths


def _run() -> str | None:
    findings: list[str] = []
    diff = subprocess.run(
        ["git", "diff", "--check", "--", *SCOPES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if diff.returncode:
        findings.append("git_diff_check_failed")

    changed = _changed_python_files()
    syntax_failures: list[str] = []
    for path in changed:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            syntax_failures.append(str(path.relative_to(ROOT)))
    if syntax_failures:
        findings.append(f"python_syntax_failed:{','.join(syntax_failures[:5])}")

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": "ADA",
        "action": "engineering_pulse",
        "changed_python_checked": len(changed),
        "diff_check_ok": diff.returncode == 0,
        "syntax_failures": syntax_failures,
        "status": "issue" if findings else "clean",
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(ARTIFACT, 0o600)
    if findings:
        return f"ingeniería: {'; '.join(findings)} · artifact={ARTIFACT}"
    return None


async def engineering_pulse() -> str | None:
    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    print(asyncio.run(engineering_pulse()) or "clean")
