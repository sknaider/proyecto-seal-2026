#!/usr/bin/env python3
"""Emit ALICE's canonical heartbeat from the isolated v2 seat.

The heartbeat is written only when the F5 switch selects ALICE-V2 and the
system seat is active under the expected Unix user.  A dead seat therefore
stops producing heartbeats instead of being hidden by an external timer.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable


SWITCH_PATH = Path("/etc/seal/alice_cuerpo_activo")
SEAT_UNIT = "alice-v2-seat.service"
EXPECTED_USER = "alice-v2-lab"
EXPECTED_UID = 982


def v2_selected(path: Path = SWITCH_PATH) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip().upper() == "ALICE-V2"
    except OSError:
        return False


def _parse_properties(raw: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def active_seat_pid(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    proc_root: Path = Path("/proc"),
) -> int:
    result = runner(
        [
            "systemctl",
            "show",
            SEAT_UNIT,
            "--property=ActiveState",
            "--property=MainPID",
            "--property=User",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("alice-v2 seat state is not measurable")
    properties = _parse_properties(result.stdout)
    if properties.get("ActiveState") != "active":
        raise RuntimeError("alice-v2 seat is not active")
    if properties.get("User") != EXPECTED_USER:
        raise RuntimeError("alice-v2 seat user is not canonical")
    try:
        pid = int(properties.get("MainPID", "0"))
    except ValueError as exc:
        raise RuntimeError("alice-v2 seat PID is invalid") from exc
    if pid <= 0:
        raise RuntimeError("alice-v2 seat PID is absent")

    try:
        status = (proc_root / str(pid) / "status").read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("alice-v2 seat PID is not alive") from exc
    uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
    try:
        real_uid = int(uid_line.split()[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("alice-v2 seat PID uid is not measurable") from exc
    if real_uid != EXPECTED_UID:
        raise RuntimeError("alice-v2 seat PID uid is not isolated")
    return pid


def emit_heartbeat(
    writer: Callable[[str, dict, str], None],
    *,
    switch_path: Path = SWITCH_PATH,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    proc_root: Path = Path("/proc"),
) -> int:
    if not v2_selected(switch_path):
        raise RuntimeError("ALICE-V2 is not the active body")
    pid = active_seat_pid(runner=runner, proc_root=proc_root)
    writer(
        "ALICE",
        {
            "source": "seal-alice-v2-heartbeat.timer",
            "runtime": "alice_v2_isolated",
            "runtime_instance": "ALICE_V2",
            "runtime_detection_status": "present_unique",
            "process_pid": pid,
            "alive": True,
        },
        "ALICE true — runtime ALICE_V2 isolated",
    )
    return pid


def main() -> int:
    memory_dir = Path(__file__).resolve().parents[1] / "memory"
    os.sys.path.insert(0, str(memory_dir))
    from seal_heartbeat import beat_sync

    pid = emit_heartbeat(beat_sync)
    print(f"alice_v2_heartbeat written pid={pid} runtime_instance=ALICE_V2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
