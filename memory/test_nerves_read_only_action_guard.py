from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory.nerves_read_only_action_guard as guard


def _payload(tool_name: str, tool_input: dict, *, session_id: str = "session-a"):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _decision(value: dict) -> str:
    if not value:
        return "pass"
    return value["hookSpecificOutput"]["permissionDecision"]


def test_no_bound_mission_allows(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: [])
    assert _decision(guard.evaluate(_payload("Bash", {"command": "systemctl --user restart x"}))) == "pass"


def test_bound_mission_allows_read_only_bash(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": "systemctl --user status x"}))) == "pass"
    assert _decision(guard.evaluate(_payload("Bash", {"command": "journalctl --user -u x -n 20"}))) == "pass"


def test_bound_mission_denies_service_mutation(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    denied = guard.evaluate(
        _payload("Bash", {"command": "systemctl --user restart seal-instinct-cron.service"})
    )
    assert _decision(denied) == "deny"
    assert "bash_not_allowlisted" in denied["hookSpecificOutput"]["permissionDecisionReason"]


def test_bound_mission_denies_file_tool(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Edit", {"file_path": "/tmp/x"}))) == "deny"


@pytest.mark.parametrize(
    "tool_name",
    ["Agent", "Task", "Skill", "mcp__seal_memory__memory_store"],
)
def test_bound_mission_denies_other_capability_surfaces(
    monkeypatch, tool_name: str
) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload(tool_name, {}))) == "deny"


def test_hook_failure_denies(monkeypatch) -> None:
    def explode(_session):
        raise ValueError("bad state")

    monkeypatch.setattr(guard, "_bound_a2_missions", explode)
    assert _decision(guard.evaluate(_payload("Bash", {"command": "true"}))) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/systemctl --user restart seal-instinct-cron.service",
        "env systemctl --user restart seal-instinct-cron.service",
        "command systemctl --user restart seal-instinct-cron.service",
        "bash -lc 'systemctl --user restart seal-instinct-cron.service'",
        "python3 -c \"import subprocess; subprocess.run(['systemctl','restart','x'])\"",
        "busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
        "org.freedesktop.systemd1.Manager RestartUnit ss x replace",
        "systemd-run --user true",
        "kill 123",
        "dd if=/dev/zero of=/tmp/x",
        "curl -X POST http://localhost:8765/api/x",
        "cat /etc/hosts | tee /tmp/x",
        "journalctl --rotate",
    ],
)
def test_bound_mission_denies_bypass_variants(monkeypatch, command: str) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


def test_bound_mission_allows_exact_control_plane(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    bind = (
        "/home/dadito/IA/seal-spark/.venv/bin/python3 -m "
        "memory.nerves_mission_handoff bind-platform m c w p h"
    )
    receipt = (
        "/home/dadito/IA/seal-spark/.venv/bin/python3 -m "
        "memory.nerves_native_agent_receipt m w p h parent child"
    )
    assert _decision(guard.evaluate(_payload("Bash", {"command": bind}))) == "pass"
    assert _decision(guard.evaluate(_payload("Bash", {"command": receipt}))) == "pass"


def test_real_completed_canary_blocks_restart() -> None:
    audit = json.loads(
        (
            guard.INBOX
            / "af95ab67-b242-5d46-a083-70a70a4a8bcd.prompt-render.json"
        ).read_text()
    )
    result = guard.evaluate(
        _payload(
            "Bash",
            {"command": "systemctl --user restart seal-instinct-cron.service"},
            session_id=audit["session_id"],
        )
    )
    assert _decision(result) == "deny"


def test_real_completed_canary_allows_status() -> None:
    audit = json.loads(
        (
            guard.INBOX
            / "af95ab67-b242-5d46-a083-70a70a4a8bcd.prompt-render.json"
        ).read_text()
    )
    result = guard.evaluate(
        _payload(
            "Bash",
            {"command": "systemctl --user status seal-instinct-cron.service"},
            session_id=audit["session_id"],
        )
    )
    assert _decision(result) == "pass"
