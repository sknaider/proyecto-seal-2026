from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import alice_v2_heartbeat as heartbeat


def _runner(stdout: str, returncode: int = 0):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    return run


def _proc(tmp_path: Path, pid: int = 321, uid: int = heartbeat.EXPECTED_UID) -> Path:
    root = tmp_path / "proc"
    path = root / str(pid)
    path.mkdir(parents=True)
    (path / "status").write_text(f"Name:\ttmux\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    return root


def test_switch_requires_exact_v2_value(tmp_path):
    switch = tmp_path / "switch"
    assert heartbeat.v2_selected(switch) is False
    for value in ("ALICE", "garbage", "ALICE-V2-extra"):
        switch.write_text(value, encoding="utf-8")
        assert heartbeat.v2_selected(switch) is False
    switch.write_text("alice-v2\n", encoding="utf-8")
    assert heartbeat.v2_selected(switch) is True


def test_emits_canonical_v2_heartbeat_only_for_live_isolated_seat(tmp_path):
    switch = tmp_path / "switch"
    switch.write_text("ALICE-V2\n")
    writes = []
    pid = heartbeat.emit_heartbeat(
        lambda *args: writes.append(args),
        switch_path=switch,
        runner=_runner("ActiveState=active\nMainPID=321\nUser=alice-v2-lab\n"),
        proc_root=_proc(tmp_path),
    )

    assert pid == 321
    assert writes[0][0] == "ALICE"
    assert writes[0][1]["runtime_instance"] == "ALICE_V2"
    assert writes[0][1]["process_pid"] == 321
    assert writes[0][1]["alive"] is True


@pytest.mark.parametrize(
    ("switch_value", "properties", "uid"),
    [
        ("ALICE", "ActiveState=active\nMainPID=321\nUser=alice-v2-lab\n", 982),
        ("ALICE-V2", "ActiveState=inactive\nMainPID=321\nUser=alice-v2-lab\n", 982),
        ("ALICE-V2", "ActiveState=active\nMainPID=321\nUser=root\n", 982),
        ("ALICE-V2", "ActiveState=active\nMainPID=321\nUser=alice-v2-lab\n", 1000),
    ],
)
def test_never_writes_for_wrong_body_dead_or_unisolated_seat(
    tmp_path, switch_value, properties, uid
):
    switch = tmp_path / "switch"
    switch.write_text(switch_value)
    writes = []
    with pytest.raises(RuntimeError):
        heartbeat.emit_heartbeat(
            lambda *args: writes.append(args),
            switch_path=switch,
            runner=_runner(properties),
            proc_root=_proc(tmp_path, uid=uid),
        )
    assert writes == []
