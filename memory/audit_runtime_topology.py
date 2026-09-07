#!/usr/bin/env python3
"""Audit SEAL runtime systemd topology without changing services."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass


AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS", "DUM")


@dataclass
class Unit:
    name: str
    load: str
    active: str
    sub: str
    description: str
    category: str
    agent: str | None
    expected_inactive: bool
    risk: str


def timer_targets() -> set[str]:
    result = subprocess.run(
        ["systemctl", "--user", "list-timers", "--all", "--no-pager", "--plain"],
        text=True,
        capture_output=True,
        check=True,
    )
    targets: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".service"):
            targets.add(parts[-1])
    return targets


def classify(
    name: str,
    description: str,
    active: str,
    sub: str,
    timer_backed: bool,
) -> tuple[str, str | None, bool, str]:
    text = f"{name} {description}".lower()
    agent = None
    for candidate in AGENTS:
        if candidate.lower() in text:
            agent = candidate
            break

    if "timer" in name:
        return "timer", agent, False, "ok"
    if timer_backed and active == "inactive" and sub == "dead":
        return "scheduled_oneshot", agent, True, "ok"
    if "legacy" in text or "alias" in text:
        return "legacy", agent, active == "inactive", "ok" if active == "inactive" else "review"
    if any(k in text for k in ("checkpoint", "heartbeat", "nerves", "soul health", "gpu monitor", "watcher", "research", "continuity", "sleep", "backup", "maintenance", "lifecycle", "resurrect", "durable cron", "wake")):
        expected_inactive = active == "inactive" and sub == "dead"
        return "scheduled_oneshot", agent, expected_inactive, "ok"
    if "not-found" in active or "not-found" in name:
        return "missing_alias", agent, False, "review"
    if any(k in text for k in ("bridge", "poller", "whisper", "monitor", "chat", "mcp", "studio", "dashboard", "event bus")):
        risk = "ok" if active == "active" else "review"
        return "long_running", agent, False, risk
    return "other", agent, active == "inactive" and sub == "dead", "ok"


def list_units() -> list[Unit]:
    timers = timer_targets()
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "list-units",
            "--type=service",
            "--type=timer",
            "--all",
            "--no-pager",
            "--plain",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    units: list[Unit] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("UNIT ") or line.startswith("LOAD ") or line.startswith("To show"):
            continue
        match = re.match(r"^([^\s]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$", line)
        if not match:
            continue
        name, load, active, sub, description = match.groups()
        if not re.search(r"(seal|soul|ada|alice|jarvis|nexus|dum)", name, re.I):
            continue
        category, agent, expected_inactive, risk = classify(
            name, description, active, sub, name in timers
        )
        units.append(Unit(name, load, active, sub, description, category, agent, expected_inactive, risk))
    return units


def main() -> None:
    units = list_units()
    summary: dict[str, int] = {}
    risks: list[Unit] = []
    for unit in units:
        summary[unit.category] = summary.get(unit.category, 0) + 1
        if unit.risk != "ok":
            risks.append(unit)
    print(json.dumps({
        "unit_count": len(units),
        "summary": summary,
        "risks": [asdict(unit) for unit in risks],
    }, indent=2))


if __name__ == "__main__":
    main()
