from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/nerves_ada_codex_canary.py"
ARTIFACT = ROOT / "research/flywire_results/nerves_ada_codex_canary_v3.jsonl"
INBOX = ROOT / "research/flywire_results/nerves_orchestrator_inbox/ADA"


def _snapshot() -> tuple[bytes | None, tuple[str, ...]]:
    artifact = ARTIFACT.read_bytes() if ARTIFACT.exists() else None
    handoffs = tuple(sorted(p.name for p in INBOX.glob("*.handoff.json")))
    return artifact, handoffs


def test_help_is_side_effect_free() -> None:
    before = _snapshot()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "usage:" in proc.stdout
    assert _snapshot() == before
