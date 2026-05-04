"""Tests for ALICE handlers — webchat polling + dispatch + filters."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE / "kernel"))
sys.path.insert(0, str(_BASE / "handlers"))

import alice_handlers  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(alice_handlers, "CURSOR_PATH", tmp_path / "cursor.json")
    monkeypatch.setattr(alice_handlers, "PROCESSED_IDS_PATH", tmp_path / "processed.json")
    yield tmp_path


# ── _directed_at_other_agent ─────────────────────────────────────────────────

def test_directed_first_word_jarvis():
    assert alice_handlers._directed_at_other_agent("jarvis revisa esto")


def test_directed_first_word_nexus():
    assert alice_handlers._directed_at_other_agent("NEXUS, ven aquí")


def test_directed_strips_channel_prefix():
    assert alice_handlers._directed_at_other_agent("[Matrix] ada que opinas")


def test_not_directed_alice_first_word():
    assert not alice_handlers._directed_at_other_agent("alice puedes ayudarme")


def test_not_directed_general():
    assert not alice_handlers._directed_at_other_agent("hola equipo, ¿cómo va el cluster?")


# ── _should_process ──────────────────────────────────────────────────────────

def test_should_process_william_to_alice():
    evt = {
        "id": "1",
        "from": "William",
        "to": "ALICE",
        "message": "analiza estos costos",
        "timestamp": "2026-05-04T10:00:00Z",
    }
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is True
    assert reason == "ok"


def test_drop_internal_sender_jarvis():
    evt = {"id": "2", "from": "JARVIS", "to": "ALICE", "message": "hola"}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert reason.startswith("internal_sender")


def test_drop_self_sender_alice():
    evt = {"id": "3", "from": "ALICE", "to": "William", "message": "hola"}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert "ALICE" in reason


def test_drop_untrusted_sender():
    evt = {"id": "4", "from": "Random", "to": "ALICE", "message": "hi"}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert reason.startswith("untrusted_sender")


def test_drop_already_processed():
    evt = {"id": "5", "from": "William", "to": "ALICE", "message": "x"}
    ok, reason = alice_handlers._should_process(evt, {"5"})
    assert ok is False
    assert reason == "already_processed"


def test_drop_not_addressed():
    evt = {"id": "6", "from": "William", "to": "JARVIS", "message": "x"}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert "not_addressed" in reason


def test_drop_directed_at_other_in_equipo():
    evt = {
        "id": "7",
        "from": "William",
        "to": "equipo",
        "message": "[Matrix] jarvis revisa el cluster",
    }
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    # Either equipo_no_mention or directed_at_other — both legitimate drops
    assert reason in ("directed_at_other_agent", "equipo_no_mention")


def test_equipo_with_alice_mention_passes():
    evt = {
        "id": "8",
        "from": "William",
        "to": "equipo",
        "message": "alice, dame el costo total",
    }
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is True


def test_drop_empty_message():
    evt = {"id": "9", "from": "William", "to": "ALICE", "message": "   "}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert reason == "empty"


def test_drop_self_prefix():
    evt = {"id": "10", "from": "William", "to": "ALICE", "message": "[ALICE] echo"}
    ok, reason = alice_handlers._should_process(evt, set())
    assert ok is False
    assert reason == "self_prefix"


# ── cursor / processed_ids persistence ──────────────────────────────────────

def test_cursor_round_trip(isolated_paths):
    alice_handlers._save_cursor("2026-05-04T10:00:00Z")
    assert alice_handlers._load_cursor() == "2026-05-04T10:00:00Z"


def test_load_cursor_no_file_returns_empty(isolated_paths):
    assert alice_handlers._load_cursor() == ""


def test_processed_ids_round_trip(isolated_paths):
    alice_handlers._save_processed_ids({"a", "b", "c"})
    loaded = alice_handlers._load_processed_ids()
    assert loaded == {"a", "b", "c"}


def test_processed_ids_caps_at_max(isolated_paths, monkeypatch):
    monkeypatch.setattr(alice_handlers, "PROCESSED_IDS_MAX", 5)
    big = {f"id{i}" for i in range(20)}
    alice_handlers._save_processed_ids(big)
    loaded = alice_handlers._load_processed_ids()
    assert len(loaded) == 5


# ── event_loop integration ──────────────────────────────────────────────────

def test_event_loop_dispatches_valid_message(isolated_paths, monkeypatch):
    """event_loop should call cortex.process_message for messages that pass filters."""
    msg = {
        "id": "evt-1",
        "from": "William",
        "to": "ALICE",
        "message": "test analysis",
        "timestamp": "2026-05-04T10:00:00Z",
    }

    async def fake_poll():
        return [msg]

    dispatched = []

    async def fake_process(content, sender="?"):
        dispatched.append((content, sender))

    monkeypatch.setattr(alice_handlers, "_poll_once", fake_poll)
    monkeypatch.setattr(alice_handlers, "process_message", fake_process)

    async def runner():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            alice_handlers.event_loop(stop, poll_interval_s=0.01),
            stopper(),
        )

    asyncio.run(runner())
    assert len(dispatched) == 1
    assert dispatched[0] == ("test analysis", "William")


def test_event_loop_skips_internal_sender(isolated_paths, monkeypatch):
    msg = {
        "id": "evt-2",
        "from": "JARVIS",
        "to": "ALICE",
        "message": "hola",
        "timestamp": "2026-05-04T10:00:00Z",
    }

    async def fake_poll():
        return [msg]

    dispatched = []

    async def fake_process(content, sender="?"):
        dispatched.append(content)

    monkeypatch.setattr(alice_handlers, "_poll_once", fake_poll)
    monkeypatch.setattr(alice_handlers, "process_message", fake_process)

    async def runner():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            alice_handlers.event_loop(stop, poll_interval_s=0.01),
            stopper(),
        )

    asyncio.run(runner())
    assert dispatched == []
