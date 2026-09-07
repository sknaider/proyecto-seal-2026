"""SPECTRE ocean_runtime — OCEAN baseline tracker with delta audit trail.

Ref: spec_spectre_contract_v2.md §NIVEL 1 — OCEAN_baseline is the personality anchor.
Runtime tracking: any observed OCEAN delta is recorded so drift from baseline is auditable.

Schema:
  ocean_baseline: {O, C, E, A, N} — immutable (from boot)
  ocean_runtime:  {O, C, E, A, N} — current observed state (updated per session)
  ocean_deltas:   list[{ts, dimension, baseline, runtime, delta, source}]

Stored in working_state.json (sandbox). Production path → soul_v3.ocean_runtime.

Invariants:
  - baseline never mutated after first write
  - delta recorded every time runtime diverges from last snapshot
  - max 100 delta entries kept (FIFO)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent
WORKING_STATE_PATH = _BASE / "state" / "working_state.json"

AGENT_ID = "SPECTRE"

_BASELINE_KEY = "ocean_baseline"
_RUNTIME_KEY = "ocean_runtime"
_DELTAS_KEY = "ocean_deltas"
_MAX_DELTAS = 100

OCEAN_DIMS = ("O", "C", "E", "A", "N")


# ── State I/O ─────────────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        if WORKING_STATE_PATH.exists():
            return json.loads(WORKING_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Core API ──────────────────────────────────────────────────────────────────

def initialize_ocean_baseline(ocean: dict[str, float]) -> dict[str, float]:
    """Write OCEAN baseline to working_state. Idempotent — won't overwrite existing.

    Returns the stored baseline (existing or newly written).
    """
    _validate_ocean(ocean)
    state = _read_state()

    if _BASELINE_KEY in state:
        return state[_BASELINE_KEY]

    state[_BASELINE_KEY] = {dim: float(ocean[dim]) for dim in OCEAN_DIMS}
    state.setdefault(_RUNTIME_KEY, state[_BASELINE_KEY].copy())
    state.setdefault(_DELTAS_KEY, [])
    _write_state(state)

    print(
        f"[SPECTRE/ocean] baseline initialized — O={ocean['O']:.3f} C={ocean['C']:.3f} "
        f"E={ocean['E']:.3f} A={ocean['A']:.3f} N={ocean['N']:.3f}",
        flush=True,
    )
    return state[_BASELINE_KEY]


def update_ocean_runtime(
    ocean: dict[str, float],
    source: str = "observation",
) -> list[dict[str, Any]]:
    """Update runtime OCEAN state and record any deltas vs current runtime snapshot.

    source: what triggered the update ("session_reflect", "self_reflect", "observation", etc.)
    Returns list of new delta entries recorded (empty if no change).
    """
    _validate_ocean(ocean)
    state = _read_state()

    baseline: dict[str, float] = state.get(_BASELINE_KEY, {})
    prev_runtime: dict[str, float] = state.get(_RUNTIME_KEY, baseline.copy() if baseline else {})
    deltas: list[dict] = state.get(_DELTAS_KEY, [])

    new_runtime = {dim: float(ocean[dim]) for dim in OCEAN_DIMS}
    new_delta_entries: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()

    for dim in OCEAN_DIMS:
        prev_val = prev_runtime.get(dim, new_runtime[dim])
        new_val = new_runtime[dim]
        base_val = baseline.get(dim, new_val)

        if abs(new_val - prev_val) > 1e-6:
            entry: dict[str, Any] = {
                "ts": ts,
                "dimension": dim,
                "baseline": round(base_val, 4),
                "previous_runtime": round(prev_val, 4),
                "runtime": round(new_val, 4),
                "delta_from_baseline": round(new_val - base_val, 4),
                "delta_from_previous": round(new_val - prev_val, 4),
                "source": source,
            }
            deltas.append(entry)
            new_delta_entries.append(entry)
            print(
                f"[SPECTRE/ocean] Δ{dim}: {prev_val:.3f}→{new_val:.3f} "
                f"(baseline={base_val:.3f}, Δbaseline={new_val - base_val:+.3f}) [{source}]",
                flush=True,
            )

    state[_RUNTIME_KEY] = new_runtime
    state[_DELTAS_KEY] = deltas[-_MAX_DELTAS:]
    _write_state(state)

    if not new_delta_entries:
        print(f"[SPECTRE/ocean] runtime update — no delta (all dims unchanged) [{source}]", flush=True)

    return new_delta_entries


def get_ocean_snapshot() -> dict[str, Any]:
    """Return current OCEAN state: baseline, runtime, and max delta per dimension."""
    state = _read_state()
    baseline = state.get(_BASELINE_KEY, {})
    runtime = state.get(_RUNTIME_KEY, {})
    deltas = state.get(_DELTAS_KEY, [])

    max_deltas: dict[str, float] = {}
    for entry in deltas:
        dim = entry["dimension"]
        d = abs(entry["delta_from_baseline"])
        if d > abs(max_deltas.get(dim, 0.0)):
            max_deltas[dim] = entry["delta_from_baseline"]

    return {
        "baseline": baseline,
        "runtime": runtime,
        "current_deltas": {
            dim: round(runtime.get(dim, 0.0) - baseline.get(dim, 0.0), 4)
            for dim in OCEAN_DIMS
            if baseline
        },
        "max_observed_deltas": max_deltas,
        "total_delta_events": len(deltas),
    }


def get_ocean_delta_history(limit: int = 20) -> list[dict]:
    """Return most recent delta events (newest first)."""
    state = _read_state()
    deltas = state.get(_DELTAS_KEY, [])
    return list(reversed(deltas))[:limit]


def ocean_drift_alarm(threshold: float = 0.15) -> list[dict[str, Any]]:
    """Return dimensions where |runtime - baseline| exceeds threshold.

    Threshold=0.15 means 15 OCEAN points drift from boot baseline → alert.
    """
    snap = get_ocean_snapshot()
    baseline = snap["baseline"]
    runtime = snap["runtime"]
    alarms = []

    for dim in OCEAN_DIMS:
        if dim not in baseline or dim not in runtime:
            continue
        drift = abs(runtime[dim] - baseline[dim])
        if drift > threshold:
            alarms.append({
                "dimension": dim,
                "baseline": baseline[dim],
                "runtime": runtime[dim],
                "drift": round(drift, 4),
                "threshold": threshold,
            })

    return alarms


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_ocean(ocean: dict[str, float]) -> None:
    missing = [d for d in OCEAN_DIMS if d not in ocean]
    if missing:
        raise ValueError(f"Missing OCEAN dimensions: {missing}")
    for dim in OCEAN_DIMS:
        v = ocean[dim]
        if not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"OCEAN[{dim}]={v} out of range [0.0, 1.0]")
