from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_nerves as nerves


def test_jarvis_shadow_mission_created_notifies_once_without_action(monkeypatch):
    engine = nerves.MotivationEngine("JARVIS")
    maintenance_calls = []
    mission_calls = []
    posts = []

    class Opened:
        created = True
        mission = {
            "mission_id": "de66eea8-2c82-526d-af7e-9f4162bc3ca3",
            "risk_class": "A2_READ_ONLY",
        }

    async def maintenance(received):
        maintenance_calls.append(received.agent)
        return "integridad FINDING: synthetic"

    def open_mission():
        mission_calls.append("opened")
        return Opened()

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_SHADOW", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_open_jarvis_shadow_mission", open_mission)
    monkeypatch.setattr(engine, "_post_chat", post)

    first = asyncio.run(engine._fire_useful_maintenance())
    second = asyncio.run(engine._fire_useful_maintenance())

    assert first == second == "maintenance_fired:value:JARVIS"
    assert maintenance_calls == ["JARVIS"]
    assert mission_calls == ["opened"]
    assert len(posts) == 1
    assert posts[0][1] == "William"
    assert "mission_id=de66eea8-2c82-526d-af7e-9f4162bc3ca3" in posts[0][0]
    assert "risk=A2_READ_ONLY" in posts[0][0]
    assert "SHADOW" in posts[0][0]
    assert "NO ACTION" in posts[0][0]
    assert "worker_launched=false" in posts[0][0]


def test_jarvis_shadow_mission_joined_does_not_duplicate_notice(monkeypatch):
    engine = nerves.MotivationEngine("JARVIS")
    posts = []

    class Opened:
        created = False
        mission = {
            "mission_id": "de66eea8-2c82-526d-af7e-9f4162bc3ca3",
            "risk_class": "A2_READ_ONLY",
        }

    async def maintenance(_engine):
        return "integridad FINDING: same episode"

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_SHADOW", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_open_jarvis_shadow_mission", lambda: Opened())
    monkeypatch.setattr(engine, "_post_chat", post)

    assert (
        asyncio.run(engine._fire_useful_maintenance())
        == "maintenance_fired:value:JARVIS"
    )
    assert posts == []


def test_jarvis_shadow_compiler_failure_is_fail_closed(monkeypatch):
    engine = nerves.MotivationEngine("JARVIS")
    posts = []

    async def maintenance(_engine):
        return "integridad FINDING: synthetic"

    def broken_compiler():
        raise nerves.NervesActionError("mission_shadow_failed:JARVIS:ValueError")

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_SHADOW", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_open_jarvis_shadow_mission", broken_compiler)
    monkeypatch.setattr(engine, "_post_chat", post)

    with pytest.raises(
        nerves.NervesActionError,
        match="mission_shadow_failed:JARVIS:ValueError",
    ):
        asyncio.run(engine._fire_useful_maintenance())
    with pytest.raises(
        nerves.NervesActionError,
        match="maintenance_failed:JARVIS:mission_shadow",
    ):
        asyncio.run(engine._fire_useful_maintenance())
    assert posts == []


def test_shadow_flag_is_jarvis_only(monkeypatch):
    engine = nerves.MotivationEngine("NEXUS")
    posts = []

    async def maintenance(_engine):
        return "security_probe_failed"

    def forbidden_shadow():
        raise AssertionError("non-JARVIS agent must not open a shadow mission")

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_SHADOW", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(nerves, "_open_jarvis_shadow_mission", forbidden_shadow)
    monkeypatch.setattr(engine, "_post_chat", post)

    assert (
        asyncio.run(engine._fire_useful_maintenance())
        == "maintenance_fired:value:NEXUS"
    )
    assert len(posts) == 1
    assert "CRITICAL" in posts[0][0]


def test_jarvis_handoff_dispatches_once_and_stays_publicly_silent(monkeypatch):
    engine = nerves.MotivationEngine("JARVIS")
    maintenance_calls = []
    dispatch_calls = []
    posts = []

    async def maintenance(received):
        maintenance_calls.append(received.agent)
        return "integridad FINDING: synthetic"

    def dispatch():
        dispatch_calls.append("delivered")
        return object()

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_HANDOFF", True)
    monkeypatch.setattr(nerves, "NERVES_MISSION_SHADOW", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(
        nerves, "_dispatch_jarvis_read_only_mission", dispatch
    )
    monkeypatch.setattr(engine, "_post_chat", post)

    first = asyncio.run(engine._fire_useful_maintenance())
    second = asyncio.run(engine._fire_useful_maintenance())

    assert first == second == "maintenance_fired:value:JARVIS"
    assert maintenance_calls == ["JARVIS"]
    assert dispatch_calls == ["delivered"]
    assert posts == []


def test_jarvis_handoff_failure_is_fail_closed(monkeypatch):
    engine = nerves.MotivationEngine("JARVIS")
    posts = []

    async def maintenance(_engine):
        return "integridad FINDING: synthetic"

    def broken_dispatch():
        raise nerves.NervesActionError(
            "mission_handoff_failed:JARVIS:ValueError"
        )

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(
        nerves, "_dispatch_jarvis_read_only_mission", broken_dispatch
    )
    monkeypatch.setattr(engine, "_post_chat", post)

    with pytest.raises(
        nerves.NervesActionError,
        match="mission_handoff_failed:JARVIS:ValueError",
    ):
        asyncio.run(engine._fire_useful_maintenance())
    with pytest.raises(
        nerves.NervesActionError,
        match="maintenance_failed:JARVIS:mission_handoff",
    ):
        asyncio.run(engine._fire_useful_maintenance())
    assert posts == []


def test_handoff_flag_is_jarvis_only(monkeypatch):
    engine = nerves.MotivationEngine("ALICE")
    posts = []

    async def maintenance(_engine):
        return "delivery FINDING: synthetic"

    def forbidden_dispatch():
        raise AssertionError("non-JARVIS agent must not dispatch this pilot")

    async def post(message, to="equipo"):
        posts.append((message, to))

    monkeypatch.setattr(nerves, "NERVES_MISSION_HANDOFF", True)
    monkeypatch.setattr(nerves, "_run_maintenance_action", maintenance)
    monkeypatch.setattr(
        nerves, "_dispatch_jarvis_read_only_mission", forbidden_dispatch
    )
    monkeypatch.setattr(engine, "_post_chat", post)

    assert (
        asyncio.run(engine._fire_useful_maintenance())
        == "maintenance_fired:value:ALICE"
    )
    assert len(posts) == 1
    assert "CRITICAL" in posts[0][0]
