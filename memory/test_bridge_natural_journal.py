from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge_natural_journal import (
    build_parser,
    close_task_if_observed,
    is_bridge_natural_event,
    observation_from_rows,
    watcher_heartbeat_line,
)


def test_bridge_natural_event_requires_bridge_action_and_human_sender() -> None:
    row = {
        "id": 1,
        "action": "bridge:final_published chat_id=73900 channel=web_chat",
        "temporary": False,
        "created_at": datetime.now(timezone.utc),
        "evidence": {
            "artifact": "messages/ada_codex_remote_bridge.py",
            "stage": "final_published",
            "sender": "William",
            "chat_id": "73900",
        },
    }

    assert is_bridge_natural_event(row)


def test_bridge_natural_event_rejects_live_probe_and_team_sender() -> None:
    probe = {
        "id": 2,
        "action": "seal-working-state-live-probe marker",
        "temporary": False,
        "evidence": {
            "hook": "PostToolUse",
            "tool": "Bash",
            "command": "seal-working-state-live-probe marker",
        },
    }
    team = {
        "id": 3,
        "action": "bridge:final_published chat_id=73901 channel=web_chat",
        "temporary": False,
        "evidence": {
            "artifact": "messages/ada_codex_remote_bridge.py",
            "stage": "final_published",
            "sender": "NEXUS",
            "chat_id": "73901",
        },
    }

    assert not is_bridge_natural_event(probe)
    assert not is_bridge_natural_event(team)


def test_observation_blocks_claim_when_no_natural_bridge_event() -> None:
    observation = observation_from_rows("ADA", [])

    assert observation.observed is False
    assert observation.natural_event_claim_allowed is False
    assert observation.natural_event_count == 0
    assert observation.missing == ["bridge_natural_event:not_observed"]
    assert "natural_event_claim_allowed=False" in observation.evidence


def test_observation_allows_claim_with_natural_bridge_event() -> None:
    row = {
        "id": 4,
        "action": "bridge:turn_started chat_id=73902 channel=dm:ada:william",
        "temporary": False,
        "created_at": datetime(2026, 5, 20, 2, 10, tzinfo=timezone.utc),
        "evidence": {
            "artifact": "messages/ada_codex_remote_bridge.py",
            "stage": "turn_started",
            "sender": "Henry",
            "chat_id": "73902",
        },
    }

    observation = observation_from_rows("ADA", [row])

    assert observation.observed is True
    assert observation.natural_event_claim_allowed is True
    assert observation.event_ids == [4]
    assert observation.stages == ["turn_started"]
    assert observation.chat_ids == [73902]
    assert observation.missing == []


def test_cli_parser_accepts_observe_and_require_observed() -> None:
    observe = build_parser().parse_args(["--agent", "ADA", "observe"])
    required = build_parser().parse_args(["--agent", "ADA", "require-observed"])
    wait = build_parser().parse_args(
        [
            "--agent",
            "ADA",
            "wait-observed",
            "--timeout-seconds",
            "0.5",
            "--poll-interval-seconds",
            "0.1",
        ]
    )

    assert observe.agent == "ADA"
    assert observe.command == "observe"
    assert required.command == "require-observed"
    assert wait.command == "wait-observed"
    assert wait.timeout_seconds == 0.5
    assert wait.poll_interval_seconds == 0.1


def test_cli_parser_accepts_close_task() -> None:
    close = build_parser().parse_args(
        [
            "--agent",
            "ADA",
            "close-task",
            "--task-id",
            "425",
            "--timeout-seconds",
            "1",
            "--poll-interval-seconds",
            "0.1",
        ]
    )

    assert close.command == "close-task"
    assert close.task_id == 425
    assert close.timeout_seconds == 1
    assert close.poll_interval_seconds == 0.1


def test_cli_parser_accepts_watch_close_task() -> None:
    watch = build_parser().parse_args(
        [
            "--agent",
            "ADA",
            "watch-close-task",
            "--task-id",
            "425",
            "--poll-interval-seconds",
            "0.1",
            "--heartbeat-seconds",
            "1",
        ]
    )

    assert watch.command == "watch-close-task"
    assert watch.task_id == 425
    assert watch.poll_interval_seconds == 0.1
    assert watch.heartbeat_seconds == 1


def test_close_task_blocks_when_no_natural_bridge_event() -> None:
    class FakeConn:
        def __init__(self) -> None:
            self.executed: list[tuple] = []

        async def fetchrow(self, *_args):
            return {"id": 425, "agent": "ADA", "status": "pending"}

        async def execute(self, *args):
            self.executed.append(args)

    conn = FakeConn()
    observation = observation_from_rows("ADA", [])

    closure = asyncio.run(close_task_if_observed(conn, observation, task_id=425))

    assert closure.allowed_to_close is False
    assert closure.closed is False
    assert closure.missing == ["bridge_natural_event:not_observed"]
    assert conn.executed == []


def test_close_task_completes_task_gam_and_working_state_when_observed() -> None:
    class FakeConn:
        def __init__(self) -> None:
            self.task = {
                "id": 425,
                "agent": "ADA",
                "title": "Await first natural William/Henry bridge journal event",
                "status": "pending",
                "priority": 3,
                "deadline": None,
                "created_at": datetime(2026, 5, 20, 2, 18, tzinfo=timezone.utc),
                "completed_at": None,
            }
            self.executed: list[tuple[Any, ...]] = []
            self.working_state_closed = False
            self.gam_updated = False

        async def fetchrow(self, query: str, *args):
            if "FROM soul_v3.agent_tasks" in query:
                return self.task
            raise AssertionError(f"unexpected fetchrow: {query}")

        async def fetchval(self, query: str, *args):
            if "FROM soul_v3.gam_event_graph" in query:
                return 485
            raise AssertionError(f"unexpected fetchval: {query}")

        async def execute(self, query: str, *args):
            self.executed.append((query, *args))
            if "UPDATE soul_v3.agent_tasks" in query:
                self.task = {
                    **self.task,
                    "status": "completed",
                    "completed_at": datetime(2026, 5, 20, 3, 15, tzinfo=timezone.utc),
                }
            if "UPDATE soul_v3.gam_event_graph" in query:
                self.gam_updated = True
            if "UPDATE soul_v3.working_state" in query:
                self.working_state_closed = True

    row = {
        "id": 188,
        "action": "bridge:final_published chat_id=74001 channel=web_chat",
        "temporary": False,
        "created_at": datetime(2026, 5, 20, 3, 14, tzinfo=timezone.utc),
        "evidence": {
            "artifact": "messages/ada_codex_remote_bridge.py",
            "stage": "final_published",
            "sender": "William",
            "chat_id": "74001",
        },
    }
    conn = FakeConn()
    observation = observation_from_rows("ADA", [row])

    closure = asyncio.run(close_task_if_observed(conn, observation, task_id=425))

    assert closure.allowed_to_close is True
    assert closure.closed is True
    assert closure.task_status_before == "pending"
    assert closure.task_status_after == "completed"
    assert closure.gam_event_id == 485
    assert closure.missing == []
    assert conn.gam_updated is True
    assert conn.working_state_closed is True
    assert any("pending_validations=ARRAY[]::text[]" in call[0] for call in conn.executed)
    assert "event_ids=[188]" in closure.evidence


def test_watcher_heartbeat_line_is_compact() -> None:
    class FakeConn:
        async def fetchrow(self, *_args):
            return {"id": 425, "agent": "ADA", "status": "pending"}

        async def execute(self, *_args):
            raise AssertionError("no update expected")

    observation = observation_from_rows("ADA", [])
    closure = asyncio.run(close_task_if_observed(FakeConn(), observation, task_id=425))

    line = watcher_heartbeat_line(closure)

    assert line.startswith("bridge_natural_close_watch ")
    assert "task_id=425" in line
    assert "observed=False" in line
    assert "\n" not in line
