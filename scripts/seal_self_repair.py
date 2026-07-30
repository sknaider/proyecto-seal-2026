#!/usr/bin/env python3
"""Bounded, evidence-producing self-repair for SEAL agent services.

The caller identity comes from SEAL_AGENT.  The CLI intentionally accepts an
action name, never an arbitrary systemd unit or PID.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_ACTIONS: dict[str, dict[str, str]] = {
    "ADA": {
        "bridge": "ada-codex-remote-bridge.service",
        "channel_monitor": "seal-channel-monitor@ADA.service",
        "stream_relay": "seal-ada-codex-stream-relay.service",
        "visible_poller": "seal-ada-codex-poller.service",
        "visible_terminal": "seal-ada-codex-autostart.service",
    },
    "ALICE": {
        "bridge": "seal-bridge-alice.service",
        "channel_monitor": "seal-channel-monitor@ALICE.service",
    },
    "FABLE": {
        "channel_monitor": "seal-channel-monitor@FABLE.service",
    },
    "JARVIS": {
        "bridge": "seal-bridge-jarvis.service",
        "channel_monitor": "seal-channel-monitor@JARVIS.service",
    },
    "NEXUS": {
        "bridge": "seal-bridge-nexus.service",
        "channel_monitor": "seal-channel-monitor@NEXUS.service",
        "visible_terminal": "nexus-terminal.service",
    },
}

CONTROL_UNITS = {
    "ADA": "seal-channel-monitor@JARVIS.service",
    "ALICE": "seal-channel-monitor@FABLE.service",
    "FABLE": "seal-channel-monitor@NEXUS.service",
    "JARVIS": "seal-channel-monitor@ADA.service",
    "NEXUS": "seal-channel-monitor@ALICE.service",
}

SHOW_PROPERTIES = (
    "LoadState",
    "Type",
    "ActiveState",
    "SubState",
    "MainPID",
    "InvocationID",
    "Result",
)


def default_receipt_root(environ: dict[str, str] | None = None) -> Path:
    """Return the durable per-user evidence store.

    Receipts authorize claims of completed repair, so `/tmp` is not an
    acceptable default: it is cleaned on reboot/age and cannot support later
    audit. `XDG_DATA_HOME` is honored without accepting a CLI path override.
    """
    env = os.environ if environ is None else environ
    data_home = env.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "seal" / "autonomy_receipts"


@dataclass(frozen=True)
class UnitSnapshot:
    unit: str
    observed_at: str
    load_state: str
    unit_type: str
    active_state: str
    sub_state: str
    main_pid: int
    invocation_id: str
    result: str


@dataclass(frozen=True)
class RepairReceipt:
    schema: str
    receipt_id: str
    agent: str
    operation: str
    action: str
    unit: str
    reason: str
    started_at: str
    finished_at: str
    opportunity: dict[str, Any]
    expected: dict[str, Any]
    before: UnitSnapshot
    after: UnitSnapshot
    negative_control_before: UnitSnapshot
    negative_control_after: UnitSnapshot
    checks: dict[str, bool]
    command: list[str]
    returncode: int
    output: str
    result: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_agent(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    agent = env.get("SEAL_AGENT", "").strip().upper()
    if not agent:
        raise PermissionError("SEAL_AGENT is required; identity is fail-closed")
    if agent not in AGENT_ACTIONS:
        raise PermissionError(f"agent {agent!r} has no self-repair policy")
    return agent


def resolve_unit(agent: str, action: str) -> str:
    try:
        return AGENT_ACTIONS[agent][action]
    except KeyError as exc:
        allowed = sorted(AGENT_ACTIONS.get(agent, {}))
        raise PermissionError(
            f"action {action!r} is not allowed for {agent}; allowed={allowed}"
        ) from exc


def _run(argv: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def snapshot_unit(unit: str) -> UnitSnapshot:
    argv = ["systemctl", "--user", "show", unit]
    for prop in SHOW_PROPERTIES:
        argv.extend(["-p", prop])
    result = _run(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot inspect {unit}: {detail}")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key:
            values[key] = value
    return UnitSnapshot(
        unit=unit,
        observed_at=utc_now(),
        load_state=values.get("LoadState", "unknown"),
        unit_type=values.get("Type", "unknown"),
        active_state=values.get("ActiveState", "unknown"),
        sub_state=values.get("SubState", "unknown"),
        main_pid=int(values.get("MainPID", "0") or 0),
        invocation_id=values.get("InvocationID", ""),
        result=values.get("Result", ""),
    )


def _changed(before: UnitSnapshot, after: UnitSnapshot) -> bool:
    return (
        bool(after.invocation_id)
        and after.invocation_id != before.invocation_id
    ) or (after.main_pid > 0 and after.main_pid != before.main_pid)


def _unchanged(before: UnitSnapshot, after: UnitSnapshot) -> bool:
    return (
        before.main_pid == after.main_pid
        and before.invocation_id == after.invocation_id
        and before.active_state == after.active_state
    )


def _wait_for_restart(unit: str, before: UnitSnapshot, timeout: float) -> UnitSnapshot:
    deadline = time.monotonic() + timeout
    last = snapshot_unit(unit)
    while time.monotonic() < deadline:
        if last.active_state == "active" and _changed(before, last):
            return last
        time.sleep(0.2)
        last = snapshot_unit(unit)
    return last


def receipt_path(receipt: RepairReceipt, base: Path | None = None) -> Path:
    root = base or default_receipt_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    directory = root / receipt.agent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory / f"{receipt.receipt_id}.json"


def write_receipt(receipt: RepairReceipt, base: Path | None = None) -> Path:
    path = receipt_path(receipt, base=base)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def execute(
    agent: str,
    action: str,
    operation: str,
    reason: str,
    timeout: float = 8,
) -> RepairReceipt:
    unit = resolve_unit(agent, action)
    control_unit = CONTROL_UNITS[agent]
    before = snapshot_unit(unit)
    control_before = snapshot_unit(control_unit)
    if before.load_state != "loaded":
        raise RuntimeError(f"{unit} is not loaded; refusing blind repair")
    if before.unit_type != "simple":
        raise RuntimeError(
            f"{unit} has Type={before.unit_type}; v1 only repairs continuously "
            "supervised Type=simple services"
        )

    command = ["systemctl", "--user", operation, unit]
    started_at = utc_now()
    result = _run(command, timeout=timeout)
    if operation == "restart" and result.returncode == 0:
        after = _wait_for_restart(unit, before, timeout=timeout)
    else:
        after = snapshot_unit(unit)
    control_after = snapshot_unit(control_unit)
    finished_at = utc_now()

    target_active = after.active_state == "active"
    target_changed = _changed(before, after) if operation == "restart" else True
    control_unchanged = _unchanged(control_before, control_after)
    command_succeeded = result.returncode == 0
    checks = {
        "command_succeeded": command_succeeded,
        "positive_control_target_active": target_active,
        "by_effect_target_changed": target_changed,
        "negative_control_other_agent_unchanged": control_unchanged,
        "subject_matches_policy": unit == AGENT_ACTIONS[agent][action],
    }
    passed = all(checks.values())
    output = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    return RepairReceipt(
        schema="seal.autonomy.repair-receipt.v1",
        receipt_id=f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:10]}",
        agent=agent,
        operation=operation,
        action=action,
        unit=unit,
        reason=reason,
        started_at=started_at,
        finished_at=finished_at,
        opportunity={"command_invoked": True, "operation": operation},
        expected={
            "target_active": True,
            "target_changed": operation == "restart",
            "other_agent_unchanged": True,
        },
        before=before,
        after=after,
        negative_control_before=control_before,
        negative_control_after=control_after,
        checks=checks,
        command=command,
        returncode=result.returncode,
        output=output,
        result="verified" if passed else "failed",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded SEAL self-repair")
    parser.add_argument("operation", choices=("restart", "status"))
    parser.add_argument("action", help="policy action name; arbitrary units are not accepted")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--list", action="store_true", dest="list_actions")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        agent = resolve_agent()
        if args.list_actions:
            print(json.dumps({"agent": agent, "actions": AGENT_ACTIONS[agent]}, indent=2))
            return 0
        receipt = execute(agent, args.action, args.operation, args.reason, args.timeout)
        path = write_receipt(receipt)
        print(json.dumps({"receipt": str(path), **asdict(receipt)}, ensure_ascii=False))
        return 0 if receipt.result == "verified" else 2
    except (PermissionError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"result": "denied", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
