from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves


def test_ada_handoff_dispatches_once_and_stays_publicly_silent(monkeypatch):
    engine = nerves.MotivationEngine("ADA")
    maintenance_calls = []
    dispatch_calls = []
    posts = []

    async def maintenance(received):
        maintenance_calls.append(received.agent)
        return "ingeniería: python_syntax_failed:memory/example.py"

    def dispatch():
        dispatch_calls.append("delivered")
        return object()

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_ada_read_only_mission", dispatch)
    monkeypatch.setattr(engine, "_post_chat", post)

    first = asyncio.run(engine._fire_useful_maintenance())
    second = asyncio.run(engine._fire_useful_maintenance())

    assert first == second == "maintenance_fired:value:ADA"
    assert maintenance_calls == ["ADA"]
    assert dispatch_calls == ["delivered"]
    assert posts == []


def test_ada_handoff_failure_is_fail_closed(monkeypatch):
    engine = nerves.MotivationEngine("ADA")

    async def maintenance(_engine):
        return "ingeniería: git_diff_check_failed"

    def broken():
        raise nerves.NervesActionError(
            "mission_handoff_failed:ADA:ValueError"
        )

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_ada_read_only_mission", broken)

    with pytest.raises(
        nerves.NervesActionError,
        match="mission_handoff_failed:ADA:ValueError",
    ):
        asyncio.run(engine._fire_useful_maintenance())
    with pytest.raises(
        nerves.NervesActionError,
        match="maintenance_failed:ADA:mission_handoff",
    ):
        asyncio.run(engine._fire_useful_maintenance())


def test_agent_handoff_does_not_dispatch_ada_adapter_for_unsupported_agent(monkeypatch):
    engine = nerves.MotivationEngine("DUM")
    posts = []

    async def maintenance(_engine):
        return "delivery FINDING: synthetic"

    def forbidden():
        raise AssertionError("ADA adapter must not run for another agent")

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_AGENT_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_dispatch_ada_read_only_mission", forbidden)
    monkeypatch.setattr(engine, "_post_chat", post)

    assert (
        asyncio.run(engine._fire_useful_maintenance())
        == "maintenance_fired:value:DUM"
    )
    assert len(posts) == 1
