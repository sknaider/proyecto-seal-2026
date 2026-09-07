"""ALICE scenario_simulator — Monte Carlo + sensitivity for cost/ROI models.

Pure-python implementation (no numpy hard dep) — keeps daemon footprint small.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Callable


@dataclass
class Distribution:
    """Triangular distribution: low, mode, high."""
    low: float
    mode: float
    high: float

    def sample(self, rng: random.Random | None = None) -> float:
        r = rng or random
        return r.triangular(self.low, self.high, self.mode)


def monte_carlo(
    fn: Callable[[dict[str, float]], float],
    inputs: dict[str, Distribution],
    runs: int = 10_000,
    seed: int | None = None,
) -> dict:
    rng = random.Random(seed)
    results: list[float] = []
    for _ in range(runs):
        sample = {k: dist.sample(rng) for k, dist in inputs.items()}
        results.append(fn(sample))

    results.sort()
    n = len(results)
    return {
        "runs": runs,
        "mean": round(statistics.mean(results), 4),
        "median": round(statistics.median(results), 4),
        "stdev": round(statistics.stdev(results), 4) if n > 1 else 0.0,
        "min": round(results[0], 4),
        "max": round(results[-1], 4),
        "p05": round(results[int(0.05 * n)], 4),
        "p25": round(results[int(0.25 * n)], 4),
        "p75": round(results[int(0.75 * n)], 4),
        "p95": round(results[int(0.95 * n)], 4),
    }


def sensitivity_one_at_a_time(
    fn: Callable[[dict[str, float]], float],
    base_inputs: dict[str, float],
    delta_pct: float = 0.10,
) -> list[dict]:
    base_value = fn(dict(base_inputs))
    rows: list[dict] = []
    for key, base in base_inputs.items():
        if base == 0:
            continue
        up = dict(base_inputs); up[key] = base * (1 + delta_pct)
        down = dict(base_inputs); down[key] = base * (1 - delta_pct)
        v_up = fn(up)
        v_down = fn(down)
        rows.append({
            "input": key,
            "base_value": base,
            "delta_pct": delta_pct,
            "output_at_+delta": round(v_up, 4),
            "output_at_-delta": round(v_down, 4),
            "elasticity": round((v_up - v_down) / (2 * delta_pct * base_value), 4) if base_value else 0.0,
        })
    rows.sort(key=lambda r: abs(r["elasticity"]), reverse=True)
    return rows
