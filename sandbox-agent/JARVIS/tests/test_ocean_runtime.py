"""Tests for JARVIS ocean_runtime — drift-aware OCEAN state."""
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


def test_baseline_jarvis_signature():
    """JARVIS baseline: high C (1.0), high O (0.84), low N (0.115)."""
    assert ocean_runtime.BASELINE["conscientiousness"] == 1.0
    assert ocean_runtime.BASELINE["openness"] == 0.84
    assert ocean_runtime.BASELINE["neuroticism"] == 0.115


def test_current_ocean_starts_at_baseline(isolated_state):
    cur = ocean_runtime.current_ocean()
    for trait, expected in ocean_runtime.BASELINE.items():
        assert cur[trait] == expected


def test_drift_starts_zero(isolated_state):
    drift = ocean_runtime.drift_from_baseline()
    assert all(abs(v) < 1e-6 for v in drift.values())
    assert ocean_runtime.total_drift() == 0.0


def test_adjust_changes_value_and_logs_history(isolated_state):
    new_state = ocean_runtime.adjust("openness", 0.05, reason="curious moment")
    assert abs(new_state["openness"] - (0.84 + 0.05)) < 1e-6
    state = ocean_runtime._load()
    assert len(state["history"]) == 1
    assert state["history"][0]["trait"] == "openness"


def test_adjust_clamps_to_unit_interval(isolated_state):
    ocean_runtime.adjust("openness", 5.0)  # would overshoot
    cur = ocean_runtime.current_ocean()
    assert cur["openness"] == 1.0


def test_adjust_unknown_trait_raises(isolated_state):
    with pytest.raises(ValueError):
        ocean_runtime.adjust("nonexistent_trait", 0.1)


def test_llm_temperature_within_bounds(isolated_state):
    t = ocean_runtime.llm_temperature()
    assert 0.05 <= t <= 0.9


def test_llm_temperature_lowered_by_high_C(isolated_state):
    """JARVIS C=1.0 → temp lowered from base 0.4 by 0.15*0.5 = 0.075. Plus O=0.84 adds 0.034."""
    t = ocean_runtime.llm_temperature()
    # Architectural precision: temp should land moderately low
    assert t < 0.5


def test_directness_level_in_unit_interval(isolated_state):
    d = ocean_runtime.directness_level()
    assert 0.0 <= d <= 1.0


def test_status_returns_full_snapshot(isolated_state):
    s = ocean_runtime.status()
    assert "baseline" in s
    assert "current" in s
    assert "drift" in s
    assert "llm_temperature" in s
    assert "directness" in s
