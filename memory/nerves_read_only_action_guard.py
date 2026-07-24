#!/usr/bin/env python3
"""Fail closed on writes causally downstream of a JARVIS A2 mission.

The native reasoner receipt is evidence, not an action capability.  Claude's
parent session may inspect and integrate that evidence, but it must not turn a
read-only recommendation into a write.  This PreToolUse hook binds the parent
session to its owner-only prompt-render audit and denies mutating tools until
an external owner records an explicit release.

This is a cooperative same-UID boundary, not a replacement for a container or
database identity.  It prevents accidental authority drift in the live Claude
surface and leaves an unambiguous denial reason in the transcript.  An A2
mission is never "released" into write authority: any repair must arrive as a
new, separately authorized mission with its own risk class and evidence.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shlex
import stat
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS"
STATE = INBOX.parent / "JARVIS.state.json"
MUTATING_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
SYSTEMCTL_READ_VERBS = frozenset(
    {
        "status",
        "show",
        "cat",
        "is-active",
        "is-failed",
        "is-enabled",
        "list-units",
        "list-unit-files",
        "list-timers",
        "list-dependencies",
    }
)
SYSTEMCTL_MUTATING_VERBS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "reload",
        "try-restart",
        "reload-or-restart",
        "enable",
        "disable",
        "reenable",
        "mask",
        "unmask",
        "kill",
        "reset-failed",
        "set-property",
        "edit",
        "revert",
    }
)
JOURNALCTL_MUTATING_PREFIXES = (
    "--vacuum-",
    "--rotate",
    "--flush",
    "--sync",
    "--relinquish-var",
    "--smart-relinquish-var",
)
READ_ONLY_EXECUTABLES = frozenset(
    {
        "cat",
        "grep",
        "head",
        "jq",
        "ls",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "sha256sum",
        "stat",
        "tail",
        "wc",
    }
)
GIT_READ_VERBS = frozenset(
    {"status", "diff", "log", "show", "rev-parse", "ls-files"}
)
CONTROL_PLANE_MODULES = frozenset(
    {
        "memory.nerves_mission_handoff",
        "memory.nerves_native_agent_receipt",
    }
)
SHELL_META = frozenset("\n\r;&|><`")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _read_private_json(
    path: Path, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise ValueError(f"untrusted_mode:{path.name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                raise ValueError(f"oversize:{path.name}")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if (
        expected_sha256 is not None
        and hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise ValueError(f"digest_mismatch:{path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"not_object:{path.name}")
    if path.name.endswith(".prompt-render.json") and raw != _canonical_bytes(value) + b"\n":
        raise ValueError(f"noncanonical:{path.name}")
    return value


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"nerves_a2_read_only_denied:{reason}",
        }
    }


def _allow(reason: str) -> dict[str, Any]:
    del reason
    return {}


def _bound_a2_missions(session_id: str) -> list[str]:
    state = _read_private_json(STATE)
    deliveries = state.get("deliveries")
    if not isinstance(deliveries, dict):
        raise ValueError("state_deliveries_invalid")
    bound: list[str] = []
    for mission_id, record in deliveries.items():
        if not isinstance(record, dict):
            continue
        if record.get("status") not in {"claimed", "running", "completed"}:
            continue
        audit_path = INBOX / f"{mission_id}.prompt-render.json"
        if not audit_path.exists():
            continue
        audit = _read_private_json(audit_path)
        if audit.get("session_id") != session_id:
            continue
        handoff_path = Path(str(record.get("inbox_path", "")))
        handoff = _read_private_json(
            handoff_path, expected_sha256=str(record.get("handoff_sha256", ""))
        )
        manifest_path = Path(str(handoff.get("bindings", {}).get("manifest_path", "")))
        manifest = _read_private_json(
            manifest_path,
            expected_sha256=str(
                handoff.get("bindings", {}).get("manifest_sha256", "")
            ),
        )
        if (
            manifest.get("mission_id") == mission_id
            and manifest.get("risk_class") == "A2_READ_ONLY"
        ):
            bound.append(mission_id)
    return sorted(bound)


def _python_control_plane_is_allowed(argv: list[str]) -> bool:
    executable = Path(argv[0]).name
    if executable not in {"python", "python3"}:
        return False
    try:
        module_index = argv.index("-m")
    except ValueError:
        return False
    if module_index + 1 >= len(argv):
        return False
    module = argv[module_index + 1]
    if module not in CONTROL_PLANE_MODULES:
        return False
    if module == "memory.nerves_mission_handoff":
        return (
            module_index + 2 < len(argv)
            and argv[module_index + 2] in {"claim", "bind-platform"}
        )
    return True


def _bash_is_allowlisted_read_only(command: str) -> bool:
    if (
        not command
        or any(char in command for char in SHELL_META)
        or "$(" in command
        or "${" in command
    ):
        return False
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    executable = Path(argv[0]).name
    if executable in READ_ONLY_EXECUTABLES:
        return True
    if executable == "systemctl":
        return (
            not any(token in SYSTEMCTL_MUTATING_VERBS for token in argv[1:])
            and sum(token in SYSTEMCTL_READ_VERBS for token in argv[1:]) == 1
        )
    if executable == "journalctl":
        return not any(
            token == prefix or token.startswith(prefix)
            for token in argv[1:]
            for prefix in JOURNALCTL_MUTATING_PREFIXES
        )
    if executable == "git":
        return len(argv) >= 2 and argv[1] in GIT_READ_VERBS
    if executable == "seal_send.py":
        return len(argv) >= 3 and argv[1] == "JARVIS"
    return _python_control_plane_is_allowed(argv)


def _initial_agent_spawn_allowed(
    tool_input: Mapping[str, Any], missions: list[str]
) -> bool:
    if len(missions) != 1:
        return False
    state = _read_private_json(STATE)
    record = state.get("deliveries", {}).get(missions[0])
    if not isinstance(record, dict) or record.get("status") != "claimed":
        return False
    if record.get("platform_binding") is not None:
        return False
    claim = record.get("claim")
    if not isinstance(claim, dict):
        return False
    expected_prompt = (
        "SEAL_NERVES_RENDER_V1\n"
        f"mission_id={missions[0]}\n"
        f"claim_id={claim.get('claim_id')}\n"
    )
    return (
        set(tool_input)
        == {
            "description",
            "subagent_type",
            "name",
            "prompt",
            "run_in_background",
        }
        and tool_input.get("description") == "Nerves JARVIS one-turn reasoner"
        and tool_input.get("subagent_type") == "nerves-jarvis-reasoner"
        and tool_input.get("name") == "jarvis_nerves_reasoner"
        and tool_input.get("prompt") == expected_prompt
        and tool_input.get("run_in_background") is True
    )


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PreToolUse":
        return _deny("hook_event_mismatch")
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(session_id, str) or not session_id:
        return _deny("session_id_missing")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return _deny("tool_payload_invalid")
    try:
        missions = _bound_a2_missions(session_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _deny(f"mission_state_untrusted:{type(exc).__name__}")
    if not missions:
        return _allow("no_bound_a2_mission")
    mission_token = ",".join(missions)
    if tool_name == "Agent":
        try:
            if _initial_agent_spawn_allowed(tool_input, missions):
                return _allow(f"initial_agent_spawn:mission={mission_token}")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return _deny(f"agent_not_allowlisted:mission={mission_token}")
    if tool_name in MUTATING_TOOLS:
        return _deny(f"mutating_tool:{tool_name}:mission={mission_token}")
    if tool_name in {"Read", "Glob", "Grep", "SendMessage"}:
        return _allow(f"read_only_tool:{tool_name}:mission={mission_token}")
    if tool_name != "Bash":
        return _deny(f"tool_not_allowlisted:{tool_name}:mission={mission_token}")
    command = tool_input.get("command")
    if not isinstance(command, str):
        return _deny(f"bash_command_missing:mission={mission_token}")
    if not _bash_is_allowlisted_read_only(command):
        return _deny(f"bash_not_allowlisted:mission={mission_token}")
    return _allow(f"allowlisted_read_only_bash:mission={mission_token}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload_not_object")
        result = evaluate(payload)
    except Exception as exc:  # hook failures must not fail open
        result = _deny(f"hook_failure:{type(exc).__name__}")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
