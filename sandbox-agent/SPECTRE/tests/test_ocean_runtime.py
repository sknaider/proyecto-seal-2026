"""SPECTRE ocean_runtime tests.

O1: initialize_ocean_baseline() writes baseline, idempotent
O2: initialize_ocean_baseline() validates missing dimensions
O3: initialize_ocean_baseline() validates out-of-range values
O4: update_ocean_runtime() records delta when value changes
O5: update_ocean_runtime() records NO delta when values unchanged
O6: update_ocean_runtime() caps deltas at _MAX_DELTAS (100)
O7: get_ocean_snapshot() returns correct baseline/runtime/current_deltas
O8: get_ocean_delta_history() returns most recent first, respects limit
O9: ocean_drift_alarm() fires on dimension exceeding threshold
O10: ocean_drift_alarm() silent when all dims within threshold
O11: delta_from_baseline and delta_from_previous computed correctly
O12: baseline never overwritten after first write
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import ocean_runtime as oc

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ O{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ O{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST OCEAN RUNTIME ===")

_BASE_OCEAN = {"O": 0.774, "C": 0.949, "E": 0.662, "A": 0.507, "N": 0.172}
_ALT_OCEAN  = {"O": 0.800, "C": 0.949, "E": 0.662, "A": 0.507, "N": 0.172}


def _patched(state_path: Path):
    return mock.patch.object(oc, "WORKING_STATE_PATH", state_path)


def _fresh(tmp: Path) -> Path:
    p = tmp / "working_state.json"
    p.write_text("{}")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# O1: initialize_ocean_baseline() writes baseline, idempotent
# ─────────────────────────────────────────────────────────────────────────────

def o1_init_idempotent():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            b1 = oc.initialize_ocean_baseline(_BASE_OCEAN)
            b2 = oc.initialize_ocean_baseline(_ALT_OCEAN)  # second call — must NOT overwrite
        state = json.loads(sp.read_text())

    assert b1 == b2, "Second call must return same baseline"
    assert state["ocean_baseline"]["O"] == 0.774, "Baseline must not be overwritten"

test("initialize_ocean_baseline(): idempotent — second call returns same baseline", o1_init_idempotent)


# ─────────────────────────────────────────────────────────────────────────────
# O2: validates missing dimensions
# ─────────────────────────────────────────────────────────────────────────────

def o2_validate_missing():
    bad = {"O": 0.5, "C": 0.5}  # missing E, A, N
    raised = False
    try:
        oc._validate_ocean(bad)
    except ValueError as e:
        raised = True
        assert "E" in str(e) or "A" in str(e) or "N" in str(e)
    assert raised, "Must raise ValueError for missing dimensions"

test("_validate_ocean(): raises ValueError for missing dimensions", o2_validate_missing)


# ─────────────────────────────────────────────────────────────────────────────
# O3: validates out-of-range values
# ─────────────────────────────────────────────────────────────────────────────

def o3_validate_range():
    bad = {**_BASE_OCEAN, "N": 1.5}
    raised = False
    try:
        oc._validate_ocean(bad)
    except ValueError as e:
        raised = True
        assert "N" in str(e)
    assert raised, "Must raise ValueError for out-of-range value"

test("_validate_ocean(): raises ValueError for value outside [0.0, 1.0]", o3_validate_range)


# ─────────────────────────────────────────────────────────────────────────────
# O4: update_ocean_runtime() records delta when value changes
# ─────────────────────────────────────────────────────────────────────────────

def o4_delta_recorded():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            deltas = oc.update_ocean_runtime(_ALT_OCEAN, source="test")

    assert len(deltas) == 1, f"Expected 1 delta (O changed), got {len(deltas)}"
    assert deltas[0]["dimension"] == "O"
    assert abs(deltas[0]["delta_from_previous"] - 0.026) < 0.001

test("update_ocean_runtime(): records delta entry when dimension changes", o4_delta_recorded)


# ─────────────────────────────────────────────────────────────────────────────
# O5: update_ocean_runtime() records NO delta when values unchanged
# ─────────────────────────────────────────────────────────────────────────────

def o5_no_delta_when_unchanged():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            oc.update_ocean_runtime(_BASE_OCEAN, source="first")
            deltas = oc.update_ocean_runtime(_BASE_OCEAN, source="second")  # same values

    assert len(deltas) == 0, f"Expected 0 deltas (no change), got {len(deltas)}"

test("update_ocean_runtime(): no delta when values identical to previous runtime", o5_no_delta_when_unchanged)


# ─────────────────────────────────────────────────────────────────────────────
# O6: update_ocean_runtime() caps deltas at _MAX_DELTAS
# ─────────────────────────────────────────────────────────────────────────────

def o6_delta_cap():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            toggle = False
            # Each toggle changes O, producing 1 delta per call
            for _ in range(oc._MAX_DELTAS + 10):
                val = 0.600 if toggle else 0.900
                oc.update_ocean_runtime({**_BASE_OCEAN, "O": val}, source="cap_test")
                toggle = not toggle
            state = json.loads(sp.read_text())

    assert len(state["ocean_deltas"]) == oc._MAX_DELTAS, (
        f"Expected {oc._MAX_DELTAS} deltas, got {len(state['ocean_deltas'])}"
    )

test(f"update_ocean_runtime(): caps delta history at {oc._MAX_DELTAS}", o6_delta_cap)


# ─────────────────────────────────────────────────────────────────────────────
# O7: get_ocean_snapshot() returns correct fields
# ─────────────────────────────────────────────────────────────────────────────

def o7_snapshot_structure():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            oc.update_ocean_runtime(_ALT_OCEAN, source="snap_test")
            snap = oc.get_ocean_snapshot()

    assert "baseline" in snap
    assert "runtime" in snap
    assert "current_deltas" in snap
    assert "max_observed_deltas" in snap
    assert "total_delta_events" in snap

    assert abs(snap["current_deltas"]["O"] - 0.026) < 0.001
    assert snap["current_deltas"]["C"] == 0.0
    assert snap["total_delta_events"] == 1

test("get_ocean_snapshot(): correct keys and delta values", o7_snapshot_structure)


# ─────────────────────────────────────────────────────────────────────────────
# O8: get_ocean_delta_history() returns most recent first, respects limit
# ─────────────────────────────────────────────────────────────────────────────

def o8_history_order_and_limit():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            vals = [0.6, 0.7, 0.8, 0.9, 0.5]
            for v in vals:
                oc.update_ocean_runtime({**_BASE_OCEAN, "O": v}, source="hist")
            history = oc.get_ocean_delta_history(limit=3)

    assert len(history) == 3, f"Expected 3 entries, got {len(history)}"
    # Most recent is O=0.5 (from 0.9)
    assert abs(history[0]["runtime"] - 0.5) < 0.001, f"Most recent O should be 0.5: {history[0]['runtime']}"

test("get_ocean_delta_history(): most recent first, limit respected", o8_history_order_and_limit)


# ─────────────────────────────────────────────────────────────────────────────
# O9: ocean_drift_alarm() fires on dimension exceeding threshold
# ─────────────────────────────────────────────────────────────────────────────

def o9_alarm_fires():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            # O drifts 0.3 above baseline (0.774 + 0.3 = 1.0, capped)
            drifted = {**_BASE_OCEAN, "O": 0.974}  # drift = 0.2 > threshold 0.15
            oc.update_ocean_runtime(drifted, source="alarm_test")
            alarms = oc.ocean_drift_alarm(threshold=0.15)

    assert len(alarms) == 1, f"Expected 1 alarm, got {len(alarms)}: {alarms}"
    assert alarms[0]["dimension"] == "O"
    assert alarms[0]["drift"] > 0.15

test("ocean_drift_alarm(): fires when dimension drift exceeds threshold", o9_alarm_fires)


# ─────────────────────────────────────────────────────────────────────────────
# O10: ocean_drift_alarm() silent when all dims within threshold
# ─────────────────────────────────────────────────────────────────────────────

def o10_alarm_silent():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline(_BASE_OCEAN)
            small_drift = {**_BASE_OCEAN, "O": _BASE_OCEAN["O"] + 0.05}  # 0.05 < 0.15
            oc.update_ocean_runtime(small_drift, source="small")
            alarms = oc.ocean_drift_alarm(threshold=0.15)

    assert len(alarms) == 0, f"Expected no alarms, got {alarms}"

test("ocean_drift_alarm(): silent when all dimensions within threshold", o10_alarm_silent)


# ─────────────────────────────────────────────────────────────────────────────
# O11: delta_from_baseline and delta_from_previous computed correctly
# ─────────────────────────────────────────────────────────────────────────────

def o11_delta_values():
    base_o = 0.700
    step1_o = 0.750
    step2_o = 0.800

    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            oc.initialize_ocean_baseline({**_BASE_OCEAN, "O": base_o})
            oc.update_ocean_runtime({**_BASE_OCEAN, "O": step1_o}, source="s1")
            d2 = oc.update_ocean_runtime({**_BASE_OCEAN, "O": step2_o}, source="s2")

    assert len(d2) == 1
    entry = d2[0]
    assert abs(entry["delta_from_baseline"] - (step2_o - base_o)) < 0.001, (
        f"delta_from_baseline: expected {step2_o - base_o:.3f}, got {entry['delta_from_baseline']}"
    )
    assert abs(entry["delta_from_previous"] - (step2_o - step1_o)) < 0.001, (
        f"delta_from_previous: expected {step2_o - step1_o:.3f}, got {entry['delta_from_previous']}"
    )

test("update_ocean_runtime(): delta_from_baseline and delta_from_previous correct", o11_delta_values)


# ─────────────────────────────────────────────────────────────────────────────
# O12: baseline never overwritten after first write
# ─────────────────────────────────────────────────────────────────────────────

def o12_baseline_immutable():
    with tempfile.TemporaryDirectory() as d:
        sp = _fresh(Path(d))
        with _patched(sp):
            b1 = oc.initialize_ocean_baseline(_BASE_OCEAN)
            # Try to initialize again with different values
            completely_different = {"O": 0.1, "C": 0.1, "E": 0.1, "A": 0.1, "N": 0.1}
            b2 = oc.initialize_ocean_baseline(completely_different)
            state = json.loads(sp.read_text())

    assert b1 == b2, "Second init must return original baseline"
    assert state["ocean_baseline"]["O"] == _BASE_OCEAN["O"], "Baseline O must be original value"

test("initialize_ocean_baseline(): baseline immutable after first write", o12_baseline_immutable)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Ocean Runtime Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
