from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
SPEC = importlib.util.spec_from_file_location(
    "nerves_e2e_canary", ROOT / "memory/nerves_e2e_canary.py"
)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def _write_env(directory: Path, agent: str, user: str) -> Path:
    path = directory / f"soul_nerves_{agent.lower()}_db.env"
    path.write_text(
        f"SEAL_DB_DSN=postgresql://{user}:opaque@localhost:5433/seal_memory\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_runtime_dsn_accepts_exact_per_agent_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(canary, "RUNTIME_ENV_DIR", tmp_path)
    _write_env(tmp_path, "ADA", "svc_soul_nerves_ada")
    assert "svc_soul_nerves_ada" in canary._runtime_dsn("ADA")


def test_runtime_dsn_rejects_cross_agent_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(canary, "RUNTIME_ENV_DIR", tmp_path)
    _write_env(tmp_path, "ADA", "svc_soul_nerves_nexus")
    with pytest.raises(RuntimeError, match="identity mismatch"):
        canary._runtime_dsn("ADA")


def test_runtime_dsn_rejects_insecure_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr(canary, "RUNTIME_ENV_DIR", tmp_path)
    path = _write_env(tmp_path, "ADA", "svc_soul_nerves_ada")
    path.chmod(0o640)
    with pytest.raises(RuntimeError, match="0600"):
        canary._runtime_dsn("ADA")
