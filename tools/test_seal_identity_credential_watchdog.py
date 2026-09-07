from __future__ import annotations

import os
import sys
from pathlib import Path

import seal_identity_credential_watchdog as watchdog


def _proc(
    root: Path,
    pid: int,
    *,
    argv: list[str],
    executable: str,
    mode: str | None = "ENFORCE",
) -> None:
    proc = root / str(pid)
    proc.mkdir()
    (proc / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    entries = [] if mode is None else [f"SEAL_TOKEN_LIFECYCLE_MODE={mode}"]
    (proc / "environ").write_bytes(b"\0".join(e.encode() for e in entries) + b"\0")
    os.symlink(executable, proc / "exe")


def test_lifecycle_mode_reads_the_unique_python_server(tmp_path: Path) -> None:
    _proc(
        tmp_path,
        101,
        argv=[sys.executable, "/repo/memory/mcp_server_v4.py"],
        executable=sys.executable,
    )

    assert watchdog.live_server_lifecycle_mode(tmp_path) == ("ENFORCE", 101)


def test_lifecycle_mode_ignores_process_that_only_mentions_server(tmp_path: Path) -> None:
    _proc(
        tmp_path,
        100,
        argv=["/usr/bin/bash", "grep mcp_server_v4.py"],
        executable="/usr/bin/bash",
        mode=None,
    )
    _proc(
        tmp_path,
        101,
        argv=[sys.executable, "/repo/memory/mcp_server_v4.py"],
        executable=sys.executable,
    )

    assert watchdog.live_server_lifecycle_mode(tmp_path) == ("ENFORCE", 101)


def test_lifecycle_mode_rejects_two_python_candidates_as_ambiguous(tmp_path: Path) -> None:
    _proc(
        tmp_path,
        100,
        argv=[sys.executable, "/tmp/mcp_server_v4.py"],
        executable=sys.executable,
        mode="OFF",
    )
    _proc(
        tmp_path,
        101,
        argv=[sys.executable, "/repo/memory/mcp_server_v4.py"],
        executable=sys.executable,
    )

    assert watchdog.live_server_lifecycle_mode(tmp_path) == (None, None)


def test_default_roster_does_not_promote_external_token_to_private_identity(
    tmp_path: Path,
) -> None:
    for agent in ("ADA", "FABLE", "ALICE-V2"):
        (tmp_path / f"{agent}.token").write_text("opaque\n", encoding="utf-8")

    agents, unmonitored, external = watchdog.select_default_roster(
        tmp_path,
        known_agents=("ADA", "ALICE", "JARVIS", "NEXUS", "DUM", "SPECTRE"),
        default_agents=("ADA", "ALICE", "JARVIS", "NEXUS"),
    )

    assert agents == ("ADA", "ALICE", "JARVIS", "NEXUS")
    assert unmonitored == ("DUM", "SPECTRE")
    assert external == ("ALICE-V2", "FABLE")
