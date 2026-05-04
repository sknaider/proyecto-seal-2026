"""ALICE ocean_runtime — live OCEAN personality state for cortex.

Loads baseline OCEAN from constants, exposes drift-aware getters that
inflect cortex behavior (temperature, verbosity, risk tolerance, etc.).

ALICE OCEAN (from soul snapshot 2026-05-04):
  Openness=0.845 (high curiosity, questions assumptions)
  Conscientiousness=0.819 (precision-first)
  Extraversion=0.803 (direct communicator)
  Agreeableness=0.30 (says hard truths)
  Neuroticism=0.21 (calm under pressure)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "ALICE"
_BASE = Path(__file__).parent.parent
STATE_PATH = _BASE / "state" / "ocean_runtime.json"

BASELINE = {
    "openness": 0.845,
    "conscientiousness": 0.819,
    "extraversion": 0.803,
    "agreeableness": 0.30,
    "neuroticism": 0.21,
}


def _load() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {"current": dict(BASELINE), "history": []}


def _save(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def current_ocean() -> dict[str, float]:
    return dict(_load().get("current", BASELINE))


def drift_from_baseline() -> dict[str, float]:
    cur = current_ocean()
    return {k: round(cur.get(k, BASELINE[k]) - BASELINE[k], 4) for k in BASELINE}


def total_drift() -> float:
    return round(sum(abs(v) for v in drift_from_baseline().values()), 4)


def adjust(trait: str, delta: float, reason: str = "") -> dict[str, float]:
    if trait not in BASELINE:
        raise ValueError(f"unknown trait {trait!r}; expected one of {list(BASELINE)}")
    state = _load()
    cur = state.setdefault("current", dict(BASELINE))
    new_val = max(0.0, min(1.0, cur.get(trait, BASELINE[trait]) + delta))
    cur[trait] = round(new_val, 4)
    state.setdefault("history", []).append({
        "trait": trait,
        "delta": delta,
        "new_value": new_val,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    state["history"] = state["history"][-200:]
    _save(state)
    return cur


def llm_temperature() -> float:
    """Map OCEAN to LLM sampling temperature.
    High C → low temp (precise). High O → some warmth. Default 0.4.
    """
    cur = current_ocean()
    base = 0.4
    base -= 0.15 * (cur["conscientiousness"] - 0.5)
    base += 0.10 * (cur["openness"] - 0.5)
    return round(max(0.05, min(0.9, base)), 3)


def directness_level() -> float:
    """0.0 (very diplomatic) → 1.0 (blunt). High E + low A = direct."""
    cur = current_ocean()
    return round(cur["extraversion"] * (1.0 - cur["agreeableness"]), 3)


def status() -> dict[str, Any]:
    return {
        "baseline": BASELINE,
        "current": current_ocean(),
        "drift": drift_from_baseline(),
        "total_drift": total_drift(),
        "llm_temperature": llm_temperature(),
        "directness": directness_level(),
    }
