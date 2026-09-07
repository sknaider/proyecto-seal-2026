#!/usr/bin/env python3
"""Fail-closed runtime supervisor for the Claude-based SEAL seats.

The supervisor adopts an existing tmux seat without touching it.  It creates a
new seat only when both the canonical tmux session and the primary Claude
runtime are absent.  A runtime without tmux is ambiguous, so that state is
reported and left untouched instead of risking a duplicate identity.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Seat:
    socket: str
    session: str
    launcher: Path
    cwd: Path


@dataclass(frozen=True)
class RuntimeScan:
    """Result of one /proc scan, including uncertainty that must fail closed."""

    pids: tuple[int, ...]
    unreadable_candidates: tuple[int, ...] = ()

    @property
    def determined(self) -> bool:
        return not self.unreadable_candidates


ROOT = Path(__file__).resolve().parents[1]
SEATS = {
    "ALICE": Seat("seal-alice", "seal-alice", ROOT / "alice_fresh.sh", ROOT / "alice"),
    "FABLE": Seat("seal-fable", "seal-fable", ROOT / "fable.sh", ROOT / "memory"),
    "JARVIS": Seat("seal-jarvis", "seal-jarvis", ROOT / "jarvis_fresh.sh", ROOT / "memory"),
}


def _run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def tmux_session_healthy(seat: Seat) -> bool:
    present = _run(["tmux", "-L", seat.socket, "has-session", "-t", seat.session])
    if present.returncode != 0:
        return False
    panes = _run(
        ["tmux", "-L", seat.socket, "list-panes", "-t", seat.session, "-F", "#{pane_dead}"]
    )
    states = [line.strip() for line in panes.stdout.splitlines() if line.strip()]
    return panes.returncode == 0 and bool(states) and all(state == "0" for state in states)


def _argv_has_agent_name(argv: list[str], agent: str) -> bool:
    """Match the --name argument without accepting AGENT-CLONE as AGENT."""
    expected = agent.upper()
    for index, value in enumerate(argv):
        candidate: str | None = None
        if value == "--name" and index + 1 < len(argv):
            candidate = argv[index + 1]
        elif value.startswith("--name="):
            candidate = value.split("=", 1)[1]
        if candidate is None:
            continue
        normalized = candidate.strip("\"'").upper()
        if re.match(rf"^{re.escape(expected)}(?:\s|—|$)", normalized):
            return True
    return False


def _argv_is_stream_worker(argv: list[str]) -> bool:
    for index, value in enumerate(argv):
        if value == "--output-format" and index + 1 < len(argv):
            if argv[index + 1] == "stream-json":
                return True
        elif value == "--output-format=stream-json":
            return True
    return False


def scan_primary_runtimes(agent: str, proc_root: Path = Path("/proc")) -> RuntimeScan:
    """Find only the interactive Claude principal for ``agent``.

    Programmatic stream-json workers inherit ``SEAL_AGENT`` and therefore must
    not be counted as seats.  The name flag and environment must both agree.
    """
    matches: list[int] = []
    unreadable_candidates: list[int] = []
    expected_agent = agent.strip().upper()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError):
            # We cannot even classify the process.  Preserve uncertainty rather
            # than allowing a false empty scan to trigger tmux destruction.
            unreadable_candidates.append(pid)
            continue
        if comm != "claude":
            continue
        try:
            argv = [
                item.decode("utf-8", errors="replace")
                for item in (entry / "cmdline").read_bytes().split(b"\0")
                if item
            ]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError):
            unreadable_candidates.append(pid)
            continue
        if _argv_is_stream_worker(argv) or not _argv_has_agent_name(argv, expected_agent):
            continue
        try:
            env_raw = (entry / "environ").read_bytes().decode("utf-8", errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError):
            unreadable_candidates.append(pid)
            continue
        env = dict(part.split("=", 1) for part in env_raw.split("\0") if "=" in part)
        if env.get("SEAL_AGENT", "").strip().upper() == expected_agent:
            matches.append(pid)
    return RuntimeScan(tuple(sorted(matches)), tuple(sorted(unreadable_candidates)))


def primary_runtime_pids(agent: str, proc_root: Path = Path("/proc")) -> list[int]:
    """Compatibility projection for read-only callers that only need known PIDs."""
    return list(scan_primary_runtimes(agent, proc_root).pids)


def launch_seat(seat: Seat) -> subprocess.CompletedProcess[str]:
    command = f"exec bash {seat.launcher}"
    return _run(
        [
            "tmux",
            "-L",
            seat.socket,
            "new-session",
            "-d",
            "-s",
            seat.session,
            "-c",
            str(seat.cwd),
            command,
        ]
    )


def retire_stale_seat(seat: Seat) -> subprocess.CompletedProcess[str]:
    """Remove only the canonical tmux shell after runtime absence was proven."""
    return _run(
        ["tmux", "-L", seat.socket, "kill-session", "-t", seat.session],
        timeout=5,
    )


def _wait_for_single_runtime(agent: str, seat: Seat, wait_seconds: float) -> RuntimeScan:
    """Give a just-created tmux pane time to exec its primary runtime."""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        scan = scan_primary_runtimes(agent)
        if not scan.determined:
            return scan
        if len(scan.pids) == 1 and tmux_session_healthy(seat):
            return scan
        if len(scan.pids) > 1 or not tmux_session_healthy(seat):
            return scan
        time.sleep(0.5)
    return scan_primary_runtimes(agent)


def _undetermined(agent: str, scan: RuntimeScan) -> dict[str, object]:
    return {
        "ok": False,
        "agent": agent,
        "status": "runtime_detection_undetermined",
        "runtime_pids": list(scan.pids),
        "unreadable_candidates": list(scan.unreadable_candidates),
    }


def reconcile(agent: str, wait_seconds: float = 30) -> dict[str, object]:
    agent = agent.strip().upper()
    if agent not in SEATS:
        return {"ok": False, "agent": agent, "status": "unsupported_agent"}
    seat = SEATS[agent]
    if tmux_session_healthy(seat):
        scan = scan_primary_runtimes(agent)
        if not scan.determined:
            return _undetermined(agent, scan)
        if not scan.pids:
            # A tmux server appears before the launcher has exec'd Claude.  A
            # bounded grace period prevents the supervisor from killing a
            # legitimate startup while still detecting a stale shell.
            scan = _wait_for_single_runtime(agent, seat, wait_seconds)
        if not scan.determined:
            return _undetermined(agent, scan)
        runtimes = list(scan.pids)
        if len(runtimes) > 1:
            return {
                "ok": False,
                "agent": agent,
                "status": "duplicate_identity",
                "runtime_pids": runtimes,
            }
        if runtimes and tmux_session_healthy(seat):
            return {
                "ok": True,
                "agent": agent,
                "status": "adopted",
                "runtime_pids": runtimes,
            }
        if runtimes:
            return {
                "ok": False,
                "agent": agent,
                "status": "ambiguous_runtime_without_tmux",
                "runtime_pids": runtimes,
            }
        retired = retire_stale_seat(seat)
        if retired.returncode != 0:
            return {
                "ok": False,
                "agent": agent,
                "status": "stale_tmux_retire_failed",
                "runtime_pids": [],
                "detail": (retired.stderr or retired.stdout).strip()[:500],
            }

    existing_scan = scan_primary_runtimes(agent)
    if not existing_scan.determined:
        return _undetermined(agent, existing_scan)
    existing = list(existing_scan.pids)
    if existing:
        return {
            "ok": False,
            "agent": agent,
            "status": "ambiguous_runtime_without_tmux",
            "runtime_pids": existing,
        }

    launched = launch_seat(seat)
    if launched.returncode != 0:
        return {
            "ok": False,
            "agent": agent,
            "status": "launch_failed",
            "detail": (launched.stderr or launched.stdout).strip()[:500],
        }

    recovered_scan = _wait_for_single_runtime(agent, seat, wait_seconds)
    if not recovered_scan.determined:
        return _undetermined(agent, recovered_scan)
    runtimes = list(recovered_scan.pids)
    if tmux_session_healthy(seat) and len(runtimes) == 1:
        return {
            "ok": True,
            "agent": agent,
            "status": "recovered",
            "runtime_pids": runtimes,
        }
    return {
        "ok": False,
        "agent": agent,
        "status": "recovery_unverified",
        "runtime_pids": list(scan_primary_runtimes(agent).pids),
    }


def locked_reconcile(agent: str, wait_seconds: float = 30) -> dict[str, object]:
    """Serialize every supervisor entry point for one identity."""
    lock_root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    lock_path = lock_root / f"seal-agent-runtime-supervisor-{agent.upper()}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return reconcile(agent, wait_seconds=wait_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, choices=sorted(SEATS))
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        result = locked_reconcile(args.agent)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return 0 if result["ok"] else 1
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
