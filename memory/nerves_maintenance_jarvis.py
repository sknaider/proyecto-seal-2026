#!/usr/bin/env python3
"""Read-only dependency/identity pulse for JARVIS NERVES."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path("/home/dadito/IA/proyecto-seal")
ARTIFACT = ROOT / "research/flywire_results/nerves_jarvis_maintenance.jsonl"
WATCH = ROOT / "tools/jarvis_nerves_watch.py"


def _watch_check() -> tuple[str, list[str], str]:
    """Load JARVIS's bounded check as an action, never as another daemon."""
    spec = importlib.util.spec_from_file_location("seal_jarvis_nerves_watch", WATCH)
    if spec is None or spec.loader is None:
        raise RuntimeError("jarvis_nerves_watch loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check()


def _run() -> str | None:
    state, findings, detail = _watch_check()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "action_source": str(WATCH.relative_to(ROOT)),
        "state": state,
        "findings": findings[:10],
        "detail": detail[:800],
        "status": "clean" if state == "GREEN" else "issue",
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(ARTIFACT, 0o600)
    if state == "GREEN":
        return None
    summary = "; ".join(findings[:3]) or "instrumento sin detalle"
    return f"integridad {state}: {summary[:500]} · artifact={ARTIFACT}"


async def integrity_pulse() -> str | None:
    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    print(asyncio.run(integrity_pulse()) or "clean")
