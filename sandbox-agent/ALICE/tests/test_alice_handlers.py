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


def test_load_cursor_no_file_returns_now_iso(isolated_paths):
    """When no cursor file exists, must initialize to NOW (ISO timestamp), NOT empty.
    Empty cursor would replay all historical messages — bug 04-may-2026 13:17.
    """
    cursor = alice_handlers._load_cursor()
    assert cursor != ""
    assert "T" in cursor and ("+" in cursor or "Z" in cursor)
    assert alice_handlers.CURSOR_PATH.exists(), "cursor file should be persisted on init"


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


# ── timezone-aware comparison (regression for bug 04-may-2026 13:24) ────────

def test_ts_to_dt_parses_utc_and_lima():
    """Regression: cursor stored UTC vs message ts in Lima must compare correctly."""
    cursor = alice_handlers._ts_to_dt("2026-05-04T18:23:38.200289+00:00")
    msg = alice_handlers._ts_to_dt("2026-05-04T13:23:49.799442-05:00")
    assert cursor is not None and msg is not None
    # Same wall-clock instant ±11s; msg should be slightly LATER in absolute time
    assert msg > cursor


def test_ts_to_dt_handles_z_suffix():
    dt = alice_handlers._ts_to_dt("2026-05-04T18:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_ts_to_dt_returns_none_on_garbage():
    assert alice_handlers._ts_to_dt("") is None
    assert alice_handlers._ts_to_dt("not-a-timestamp") is None


def test_event_loop_lima_offset_message_passes_cursor(isolated_paths, monkeypatch):
    """E2E regression: cursor seeded NOW UTC + message arrives Lima offset in future
    → must dispatch (not get filtered by string compare bug)."""
    from datetime import datetime, timezone, timedelta
    cursor_iso = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    alice_handlers._save_cursor(cursor_iso)
    # Lima offset (-05:00) — same instant as future UTC
    future_lima_iso = (datetime.now(timezone(timedelta(hours=-5))) + timedelta(seconds=5)).isoformat()
    msg = {
        "id": "evt-tz",
        "from": "William",
        "to": "ALICE",
        "message": "tz regression",
        "timestamp": future_lima_iso,
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
    assert dispatched == [("tz regression", "William")]


# ── event_loop integration ──────────────────────────────────────────────────

def test_event_loop_dispatches_valid_message(isolated_paths, monkeypatch):
    """event_loop should call cortex.process_message for messages that pass filters.

    Note: pre-seed cursor with a date older than the test message — otherwise
    the new NOW-init cursor would filter the test event out.
    """
    alice_handlers._save_cursor("2000-01-01T00:00:00Z")
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
    alice_handlers._save_cursor("2026-05-04T00:00:00Z")

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
    alice_handlers._save_cursor("2000-01-01T00:00:00Z")
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
