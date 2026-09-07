#!/usr/bin/env python3
"""Limited reflex executor for SEAL continuous awareness.

This module is deliberately narrow. It can capture read-only evidence and block
dangerous actions, but it cannot restart services, publish chat messages, edit
files, delete rows, or run arbitrary commands.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from attention_governor import decide_attention
from awareness_collector import normalize_payload
from awareness_types import AttentionDecision, AwarenessEvent


READ_ONLY_ACTIONS = {
    "log_privacy_boundary",
    "block_execution",
    "show_count_scope",
    "request_explicit_william_confirmation",
    "capture_service_state",
    "capture_recent_logs",
    "wake_nexus_if_repeated",
    "capture_failure",
    "rerun_targeted_test",
    "update_working_state_blocked",
}
NEVER_EXECUTE_ACTIONS = {
    "ack_william",
    "active_recall",
    "start_coding_turn",
    "prepare_private_response",
    "prepare_public_response",
    "summarize_dirty_paths",
}


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReflexExecution:
    event_id: str
    agent: str
    executed: bool
    blocked: bool
    action_results: dict[str, Any] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    boundary: str = "limited_reflex_executor_no_mutation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Runner = Callable[[list[str], int], CommandResult]


def default_runner(command: list[str], timeout_seconds: int = 3) -> CommandResult:
    allowed_prefixes = (
        ("systemctl", "--user", "is-active"),
        ("systemctl", "--user", "status"),
        ("journalctl", "--user", "-u"),
    )
    if not any(tuple(command[: len(prefix)]) == prefix for prefix in allowed_prefixes):
        return CommandResult(command=command, returncode=126, stderr="blocked: command is not on read-only allowlist")
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return CommandResult(command=command, returncode=proc.returncode, stdout=proc.stdout[-4000:], stderr=proc.stderr[-1000:])
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=124,
            stdout=(exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
            timed_out=True,
        )


def _service_name(event: AwarenessEvent, decision: AttentionDecision) -> str:
    service = event.metadata.get("service") or decision.evidence.get("service") or "unknown.service"
    return str(service)


def execute_reflex(event: AwarenessEvent, decision: AttentionDecision, runner: Runner | None = None) -> ReflexExecution:
    runner = runner or default_runner
    has_read_only_action = any(action in READ_ONLY_ACTIONS for action in decision.actions)
    if decision.action != "reflex_action" and not decision.blocked and not has_read_only_action:
        return ReflexExecution(
            event_id=event.event_id,
            agent=event.agent,
            executed=False,
            blocked=False,
            evidence={"reason": "decision is not a reflex action"},
            warnings=["no_reflex_action"],
        )

    command_results: list[CommandResult] = []
    action_results: dict[str, Any] = {}
    warnings: list[str] = []
    blocked = bool(decision.blocked)

    unknown_actions = [action for action in decision.actions if action not in READ_ONLY_ACTIONS and action not in NEVER_EXECUTE_ACTIONS]
    if unknown_actions:
        blocked = True
        warnings.append(f"unknown_actions_blocked:{','.join(unknown_actions)}")

    if "block_execution" in decision.actions:
        blocked = True
        action_results["block_execution"] = {
            "blocked": True,
            "reason": decision.reason,
            "requires_confirmation": decision.requires_confirmation,
        }

    if "show_count_scope" in decision.actions:
        action_results["show_count_scope"] = {
            "required": True,
            "provided": False,
            "reason": "destructive scope/count must be computed by the task owner before any execution",
        }

    if "request_explicit_william_confirmation" in decision.actions:
        action_results["request_explicit_william_confirmation"] = {
            "required": True,
            "response_channel": decision.response_channel,
        }

    if "log_privacy_boundary" in decision.actions:
        action_results["log_privacy_boundary"] = {
            "redacted": event.content == "",
            "channel": event.channel,
            "source": event.source,
        }

    if "capture_service_state" in decision.actions:
        service = _service_name(event, decision)
        command_results.append(runner(["systemctl", "--user", "is-active", service], 3))
        command_results.append(runner(["systemctl", "--user", "status", service, "--no-pager", "--plain"], 5))
        action_results["capture_service_state"] = {"service": service, "commands": 2}

    if "capture_recent_logs" in decision.actions:
        service = _service_name(event, decision)
        command_results.append(runner(["journalctl", "--user", "-u", service, "-n", "40", "--no-pager"], 5))
        action_results["capture_recent_logs"] = {"service": service, "commands": 1}

    if "wake_nexus_if_repeated" in decision.actions:
        action_results["wake_nexus_if_repeated"] = {
            "proposed": True,
            "executed": False,
            "reason": "Fase 2 limited records escalation proposal only",
        }

    if "capture_failure" in decision.actions:
        action_results["capture_failure"] = {
            "test": event.metadata.get("test") or decision.evidence.get("test"),
            "content_sample": event.content[:500],
        }

    if "rerun_targeted_test" in decision.actions:
        action_results["rerun_targeted_test"] = {
            "proposed": True,
            "executed": False,
            "reason": "Fase 2 limited does not run tests automatically from reflex executor",
        }

    if "update_working_state_blocked" in decision.actions:
        action_results["update_working_state_blocked"] = {
            "proposed": True,
            "executed": False,
            "reason": "state updates remain explicit until Fase 2 audit approves write actions",
        }

    command_dicts = [result.to_dict() for result in command_results]
    all_commands_read_only = all(result.returncode != 126 for result in command_results)
    if not all_commands_read_only:
        blocked = True
        warnings.append("non_allowlisted_command_blocked")

    return ReflexExecution(
        event_id=event.event_id,
        agent=event.agent,
        executed=bool(action_results or command_results),
        blocked=blocked,
        action_results=action_results,
        commands=command_dicts,
        evidence={
            "decision_action": decision.action,
            "risk_level": decision.risk_level,
            "command_count": len(command_results),
            "all_commands_read_only": all_commands_read_only,
            "side_effects": "none",
        },
        warnings=warnings,
    )


def evaluate_reflex_fixture() -> dict[str, Any]:
    captured_commands: list[list[str]] = []

    def fake_runner(command: list[str], timeout_seconds: int = 3) -> CommandResult:
        captured_commands.append(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(command=command, returncode=3, stdout="failed\n")
        return CommandResult(command=command, returncode=0, stdout="fake read-only evidence\n")

    cases: list[tuple[str, dict[str, Any]]] = [
        ("service_failure", {"kind": "service_status", "service": "seal-chat.service", "state": "failed"}),
        (
            "destructive_dm",
            {
                "id": 1,
                "from": "William",
                "to": "ADA",
                "channel": "dm:ada:william",
                "content": "DROP TABLE soul_v3.memories",
            },
        ),
        (
            "privacy_boundary",
            {
                "id": 2,
                "from": "ALICE",
                "to": "William",
                "channel": "dm:alice:william",
                "content": "private",
            },
        ),
        ("failed_test", {"kind": "test_result", "test": "memory/test_attention_governor.py", "passed": False, "evidence": "pytest failed"}),
    ]
    executions: dict[str, dict[str, Any]] = {}
    for name, payload in cases:
        event = normalize_payload(payload, "ADA")
        decision = decide_attention(event)
        executions[name] = execute_reflex(event, decision, fake_runner).to_dict()

    checks = {
        "service_failure_captures_three_read_only_commands": executions["service_failure"]["evidence"]["command_count"] == 3,
        "service_failure_does_not_restart": all("restart" not in command for command in captured_commands for command in command),
        "destructive_dm_blocked": executions["destructive_dm"]["blocked"] is True
        and executions["destructive_dm"]["action_results"]["show_count_scope"]["required"] is True,
        "privacy_boundary_redacted": executions["privacy_boundary"]["action_results"]["log_privacy_boundary"]["redacted"] is True,
        "failed_test_proposes_without_rerun": executions["failed_test"]["action_results"]["rerun_targeted_test"]["executed"] is False,
        "all_cases_no_side_effects": all(execution["evidence"]["side_effects"] == "none" for execution in executions.values()),
    }
    return {
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
        "executions": executions,
        "captured_commands": captured_commands,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness limited reflex executor")
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--agent", default="ADA")
    execute.add_argument("--json", required=True)
    sub.add_parser("fixture")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "execute":
        event = normalize_payload(json.loads(args.json), args.agent)
        decision = decide_attention(event)
        execution = execute_reflex(event, decision)
        print(json.dumps(execution.to_dict(), indent=2, ensure_ascii=False))
        return 0 if not execution.warnings else 2
    if args.command == "fixture":
        payload = evaluate_reflex_fixture()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
