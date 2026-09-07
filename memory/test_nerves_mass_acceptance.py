"""1,000 semantically distinct NERVES acceptance scenarios.

Matrix = 5 agents × 5 live tanks × 40 voltage/decay/cooldown profiles.  Every
profile changes values consumed by the LIF mechanism; IDs are not used to
inflate the count.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves


AGENTS = ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM")
TANK_NAMES = tuple(nerves.TANKS)
PROFILES = tuple(range(40))
CASES = [
    (agent, tank, profile)
    for agent in AGENTS
    for tank in TANK_NAMES
    for profile in PROFILES
]


class _Acquire:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        rows = self.rows

        class _Conn:
            async def fetch(self, *_args):
                return rows

        return _Conn()

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    def acquire(self):
        return _Acquire(self.rows)


@pytest.mark.parametrize(("agent", "tank", "profile"), CASES)
def test_nerves_acceptance_scenario(agent: str, tank: str, profile: int):
    cfg = nerves.TANKS[tank]
    override = nerves.AGENT_TANK_OVERRIDES.get(agent, {}).get(tank, {})
    threshold = float(override.get("threshold", cfg.get("threshold", 50.0)))
    cooldown_s = float(override.get("cooldown_s", cfg.get("cooldown_s", 0)))
    now = datetime.now(timezone.utc)
    voltage_factor = 0.20 + (profile % 10) * 0.25
    age_fraction = (profile // 10) * 0.15
    tau = float(cfg.get("decay_tau_s", 3600.0))
    tau_eff = float(nerves._circ_tau(tank, tau))
    age_s = tau_eff * age_fraction
    value = threshold * voltage_factor
    cooldown_mode = profile % 4
    if cooldown_s <= 0 or cooldown_mode == 0:
        last_fired = None
        in_cooldown = False
    elif cooldown_mode == 1:
        last_fired = now
        in_cooldown = True
    elif cooldown_mode == 2:
        last_fired = now - timedelta(seconds=cooldown_s * 0.5)
        in_cooldown = True
    else:
        last_fired = now - timedelta(seconds=cooldown_s * 2.0)
        in_cooldown = False
    live_row = {
        "tank": tank,
        "value": value,
        "last_update": now - timedelta(seconds=age_s),
        "last_fired": last_fired,
        "fire_count": profile,
    }
    historical_row = {
        "tank": "boredom",
        "value": 100.0,
        "last_update": now,
        "last_fired": None,
        "fire_count": 999,
    }
    engine = nerves.MotivationEngine(agent)
    engine.pool = _Pool([live_row, historical_row])
    states = asyncio.run(engine.get_states())

    assert set(states) == {tank}
    expected_value = value * math.exp(-age_s / tau_eff)
    assert states[tank]["value"] == pytest.approx(expected_value, rel=2e-3, abs=0.02)
    assert states[tank]["in_cooldown"] is in_cooldown
    assert states[tank]["above_threshold"] is (
        expected_value >= threshold and not in_cooldown
    )

    severity = "overdue_3h" if profile % 2 else "overdue_1h"
    # GAM regenerates a long numeric id on every sensor pass; only that known
    # source must dedupe by stable title. Stable application ids are tested
    # separately and must remain distinct even when titles match.
    task_a = {"id": f"gam_{profile:017d}1", "title": f"stable-task-{agent}-{tank}-{profile}",
              "deadline_state": severity}
    task_b = {"id": f"gam_{profile:017d}2", "title": task_a["title"],
              "deadline_state": severity}
    alert_log = {}
    first = nerves._dedupe_task_alerts([task_a], severity, 1000.0, alert_log, 60.0)
    duplicate = nerves._dedupe_task_alerts([task_b], severity, 1030.0, alert_log, 60.0)
    reminder = nerves._dedupe_task_alerts([task_b], severity, 1061.0, alert_log, 60.0)
    assert len(first) == 1 and duplicate == [] and len(reminder) == 1

    other = "overdue_1h" if severity == "overdue_3h" else "overdue_3h"
    mixed = [task_a, {**task_b, "title": task_b["title"] + "-other", "deadline_state": other}]
    assert nerves._task_alert_candidates(mixed, severity) == [task_a]


def test_mass_matrix_has_exactly_1000_unique_behavioral_inputs():
    assert len(CASES) == 1000
    signatures = {
        (agent, tank, 0.20 + (profile % 10) * 0.25, (profile // 10) * 0.15)
        for agent, tank, profile in CASES
    }
    assert len(signatures) == 1000
