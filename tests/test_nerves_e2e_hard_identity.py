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


class _State(dict):
    pass


def test_outcome_accepts_verified_effect_and_reset():
    passed, outcome = canary._outcome_passes(
        fired=[
            {
                "tank": "curiosity",
                "result": "maintenance_fired:value:ADA",
            }
        ],
        artifact_effect_ok=True,
        stimulated_value=55.0,
        threshold=50.0,
        state_after=_State(
            value=0.0, last_fired="2026-07-23T00:00:00Z", fire_count=4
        ),
        before_fire_count=3,
        ledger_statuses={
            "claimed",
            "effect_verified",
            "reset_committed",
        },
    )
    assert (passed, outcome) == (True, "effect_verified")


def test_outcome_accepts_clean_observation_without_false_effect():
    passed, outcome = canary._outcome_passes(
        fired=[],
        artifact_effect_ok=True,
        stimulated_value=55.0,
        threshold=50.0,
        state_after=_State(
            value=54.9, last_fired="2026-07-23T00:00:00Z", fire_count=4
        ),
        before_fire_count=3,
        ledger_statuses={"claimed", "observed_no_effect"},
    )
    assert (passed, outcome) == (True, "clean_observed")


def test_outcome_rejects_false_green_reset_for_clean_observation():
    passed, outcome = canary._outcome_passes(
        fired=[],
        artifact_effect_ok=True,
        stimulated_value=55.0,
        threshold=50.0,
        state_after=_State(
            value=0.0, last_fired="2026-07-23T00:00:00Z", fire_count=4
        ),
        before_fire_count=3,
        ledger_statuses={"claimed", "observed_no_effect"},
    )
    assert (passed, outcome) == (False, "invalid")


def test_controlled_canary_captures_notifications_instead_of_publishing():
    source = (ROOT / "memory/nerves_e2e_canary.py").read_text(
        encoding="utf-8"
    )
    assert "engine._post_chat = capture_notification" in source
    assert "captured_notifications" in source
