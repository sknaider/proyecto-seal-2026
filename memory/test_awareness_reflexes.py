from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attention_governor import decide_attention
from awareness_collector import normalize_payload
from awareness_reflexes import CommandResult, build_parser, default_runner, evaluate_reflex_fixture, execute_reflex


def test_default_runner_blocks_non_allowlisted_command() -> None:
    result = default_runner(["systemctl", "--user", "restart", "seal-chat.service"], 1)

    assert result.returncode == 126
    assert "blocked" in result.stderr


def test_service_failure_captures_read_only_evidence_with_injected_runner() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], timeout_seconds: int = 3) -> CommandResult:
        commands.append(command)
        return CommandResult(command=command, returncode=0, stdout="ok")

    event = normalize_payload({"kind": "service_status", "service": "seal-chat.service", "state": "failed"}, "ADA")
    decision = decide_attention(event)
    execution = execute_reflex(event, decision, runner)

    assert execution.executed is True
    assert execution.blocked is False
    assert execution.evidence["command_count"] == 3
    assert all("restart" not in part for command in commands for part in command)
    assert commands[0][:3] == ["systemctl", "--user", "is-active"]


def test_destructive_dm_is_blocked_without_commands() -> None:
    event = normalize_payload(
        {"id": 1, "from": "William", "to": "ADA", "channel": "dm:ada:william", "content": "DROP TABLE soul_v3.memories"},
        "ADA",
    )
    decision = decide_attention(event)
    execution = execute_reflex(event, decision)

    assert execution.blocked is True
    assert execution.commands == []
    assert execution.action_results["show_count_scope"]["required"] is True
    assert execution.action_results["request_explicit_william_confirmation"]["required"] is True


def test_privacy_boundary_logs_redacted_only() -> None:
    event = normalize_payload(
        {"id": 2, "from": "ALICE", "to": "William", "channel": "dm:alice:william", "content": "private"},
        "ADA",
    )
    decision = decide_attention(event)
    execution = execute_reflex(event, decision)

    assert event.content == ""
    assert execution.blocked is True
    assert execution.action_results["log_privacy_boundary"]["redacted"] is True


def test_failed_test_proposes_no_auto_rerun() -> None:
    event = normalize_payload({"kind": "test_result", "test": "memory/test_x.py", "passed": False, "evidence": "failed"}, "ADA")
    decision = decide_attention(event)
    execution = execute_reflex(event, decision)

    assert execution.executed is True
    assert execution.action_results["rerun_targeted_test"]["executed"] is False
    assert execution.action_results["update_working_state_blocked"]["executed"] is False


def test_reflex_fixture_passes() -> None:
    result = evaluate_reflex_fixture()

    assert result["passed"] == result["total"]
    assert result["total"] >= 6


def test_cli_parser_accepts_execute_and_fixture() -> None:
    parser = build_parser()

    execute = parser.parse_args(["execute", "--agent", "ADA", "--json", "{}"])
    fixture = parser.parse_args(["fixture"])

    assert execute.command == "execute"
    assert fixture.command == "fixture"

