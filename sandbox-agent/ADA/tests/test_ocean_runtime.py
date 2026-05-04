"""Tests for ADA ocean_runtime — drift-aware OCEAN state."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import ocean_runtime  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ocean_runtime, "STATE_PATH", tmp_path / "ocean.json")
    yield tmp_path


def test_baseline_ada_signature():
    """ADA: high C (1.0), high E (1.0), high O (0.826)."""
    assert ocean_runtime.BASELINE["conscientiousness"] == 1.0
    assert ocean_runtime.BASELINE["extraversion"] == 1.0
    assert ocean_runtime.BASELINE["openness"] == 0.826


def test_current_ocean_starts_at_baseline(isolated_state):
    cur = ocean_runtime.current_ocean()
    for trait, expected in ocean_runtime.BASELINE.items():
        assert cur[trait] == expected


def test_drift_starts_zero(isolated_state):
    drift = ocean_runtime.drift_from_baseline()
    assert all(abs(v) < 1e-6 for v in drift.values())


def test_adjust_changes_value(isolated_state):
    new_state = ocean_runtime.adjust("openness", 0.05, reason="curious")
    assert abs(new_state["openness"] - (0.826 + 0.05)) < 1e-6


def test_adjust_clamps_unit_interval(isolated_state):
    ocean_runtime.adjust("openness", 5.0)
    cur = ocean_runtime.current_ocean()
    assert cur["openness"] == 1.0


def test_adjust_unknown_trait_raises(isolated_state):
    with pytest.raises(ValueError):
        ocean_runtime.adjust("nonexistent", 0.1)


def test_llm_temperature_low_due_to_high_C(isolated_state):
    """ADA C=1.0 → temp lowered. base 0.4 - 0.15*0.5 + 0.10*0.326 = ~0.358."""
    t = ocean_runtime.llm_temperature()
    assert 0.3 <= t <= 0.45


def test_directness_very_high_for_ada(isolated_state):
    """ADA E=1.0, A=0.481 → 0.519 (very direct)."""
    d = ocean_runtime.directness_level()
    assert d > 0.4


def test_status_returns_full_snapshot(isolated_state):
    s = ocean_runtime.status()
    assert "baseline" in s and "current" in s and "drift" in s
    assert "llm_temperature" in s and "directness" in s
