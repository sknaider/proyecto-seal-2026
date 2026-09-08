from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("seal_agent_runtime_supervisor.py")
UNIT_PATH = MODULE_PATH.parents[1] / "ops/systemd/seal-agent-runtime-supervisor@.service"
SPEC = importlib.util.spec_from_file_location("seat_supervisor", MODULE_PATH)
assert SPEC and SPEC.loader
SUPERVISOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUPERVISOR
SPEC.loader.exec_module(SUPERVISOR)


def test_roster_is_closed_and_uses_canonical_launchers():
    assert set(SUPERVISOR.SEATS) == {"ALICE", "FABLE", "JARVIS"}
    assert SUPERVISOR.SEATS["ALICE"].launcher.name == "alice_fresh.sh"
    assert SUPERVISOR.SEATS["FABLE"].launcher.name == "fable.sh"
    assert SUPERVISOR.SEATS["JARVIS"].launcher.name == "jarvis_fresh.sh"


def test_existing_session_is_adopted_without_launch(monkeypatch):
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: True)
    monkeypatch.setattr(
        SUPERVISOR, "scan_primary_runtimes", lambda _agent: SUPERVISOR.RuntimeScan((101,))
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    assert SUPERVISOR.reconcile("ALICE") == {
        "ok": True,
        "agent": "ALICE",
        "status": "adopted",
        "runtime_pids": [101],
    }


def test_stale_tmux_without_runtime_is_retired_then_recovered(monkeypatch):
    states = iter([True, True])
    pids = iter([[], [], [], [404]])
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: next(states))
    monkeypatch.setattr(
        SUPERVISOR,
        "scan_primary_runtimes",
        lambda _agent: SUPERVISOR.RuntimeScan(tuple(next(pids))),
    )
    monkeypatch.setattr(SUPERVISOR, "time", SimpleNamespace(monotonic=lambda: 0))
    monkeypatch.setattr(
        SUPERVISOR,
        "retire_stale_seat",
        lambda _seat: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = SUPERVISOR.reconcile("ALICE", wait_seconds=0)
    assert result == {
        "ok": True,
        "agent": "ALICE",
        "status": "recovered",
        "runtime_pids": [404],
    }


def test_duplicate_identity_is_not_adopted(monkeypatch):
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: True)
    monkeypatch.setattr(
        SUPERVISOR,
        "scan_primary_runtimes",
        lambda _agent: SUPERVISOR.RuntimeScan((101, 102)),
    )
    result = SUPERVISOR.reconcile("ALICE")
    assert result == {
        "ok": False,
        "agent": "ALICE",
        "status": "duplicate_identity",
        "runtime_pids": [101, 102],
    }


def test_runtime_without_tmux_fails_closed(monkeypatch):
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: False)
    monkeypatch.setattr(
        SUPERVISOR, "scan_primary_runtimes", lambda _agent: SUPERVISOR.RuntimeScan((202,))
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    result = SUPERVISOR.reconcile("FABLE")
    assert result["ok"] is False
    assert result["status"] == "ambiguous_runtime_without_tmux"
    assert result["runtime_pids"] == [202]


def test_absent_seat_is_recovered_and_verified(monkeypatch):
    states = iter([False, True, True])
    pids = iter([[], [303]])
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: next(states))
    monkeypatch.setattr(
        SUPERVISOR,
        "scan_primary_runtimes",
        lambda _agent: SUPERVISOR.RuntimeScan(tuple(next(pids))),
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = SUPERVISOR.reconcile("JARVIS", wait_seconds=1)
    assert result == {
        "ok": True,
        "agent": "JARVIS",
        "status": "recovered",
        "runtime_pids": [303],
    }


def test_unknown_agent_cannot_expand_authority():
    result = SUPERVISOR.reconcile("DUM")
    assert result == {"ok": False, "agent": "DUM", "status": "unsupported_agent"}


def test_adoption_revalidates_tmux_after_runtime_scan(monkeypatch):
    states = iter([True, False])
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: next(states))
    monkeypatch.setattr(
        SUPERVISOR, "scan_primary_runtimes", lambda _agent: SUPERVISOR.RuntimeScan((101,))
    )
    result = SUPERVISOR.reconcile("ALICE")
    assert result == {
        "ok": False,
        "agent": "ALICE",
        "status": "ambiguous_runtime_without_tmux",
        "runtime_pids": [101],
    }


def test_undetermined_scan_never_retires_or_launches(monkeypatch):
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: True)
    monkeypatch.setattr(
        SUPERVISOR,
        "scan_primary_runtimes",
        lambda _agent: SUPERVISOR.RuntimeScan((), (999,)),
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "retire_stale_seat",
        lambda _seat: (_ for _ in ()).throw(AssertionError("must not retire")),
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    result = SUPERVISOR.reconcile("ALICE")
    assert result["ok"] is False
    assert result["status"] == "runtime_detection_undetermined"
    assert result["unreadable_candidates"] == [999]


def test_undetermined_scan_without_tmux_never_launches(monkeypatch):
    """Sin tmux, un /proc ilegible NO es un asiento vacío: no se lanza a ciegas (ADA, 8-sep-2026)."""
    monkeypatch.setattr(SUPERVISOR, "tmux_session_healthy", lambda _seat: False)
    monkeypatch.setattr(
        SUPERVISOR,
        "scan_primary_runtimes",
        lambda _agent: SUPERVISOR.RuntimeScan((), (999,)),
    )
    monkeypatch.setattr(
        SUPERVISOR,
        "launch_seat",
        lambda _seat: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    result = SUPERVISOR.reconcile("ALICE")
    assert result["ok"] is False
    assert result["status"] == "runtime_detection_undetermined"
    assert result["unreadable_candidates"] == [999]


def test_systemd_template_binds_identity_and_hardening_to_the_instance():
    """NEXUS S2/S4/S5/S6 (8-sep-2026): un brazo que sólo mira que la línea EXISTA no observa a quién apunta.

    - SEAL_AGENT tiene que salir del especificador %i: fijo, las tres instancias creen ser el mismo agente.
    - El preflight y el servicio tienen que llevar `--agent %i`: sin eso adoptan el asiento equivocado.
    - El endurecimiento declarado (NoNewPrivileges, UMask 0077) no se cae en silencio.
    """
    lineas = [l.strip() for l in UNIT_PATH.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    assert "Environment=SEAL_AGENT=%i" in lineas
    assert not any(l.startswith("Environment=SEAL_AGENT=") and l != "Environment=SEAL_AGENT=%i" for l in lineas)
    pre = [l for l in lineas if l.startswith("ExecStartPre=")]
    start = [l for l in lineas if l.startswith("ExecStart=")]
    assert len(pre) == 1 and pre[0].endswith("seal_agent_runtime_supervisor.py --agent %i --once")
    assert len(start) == 1 and " --agent %i" in start[0] and "--once" not in start[0]
    assert "NoNewPrivileges=true" in lineas
    assert "UMask=0077" in lineas


def _fake_proc(root: Path, pid: int, *, name: str, agent: str) -> None:
    proc = root / str(pid)
    proc.mkdir(parents=True)
    (proc / "comm").write_text("claude\n", encoding="utf-8")
    (proc / "cmdline").write_bytes(b"claude\0--name\0" + name.encode() + b"\0")
    (proc / "environ").write_bytes(f"SEAL_AGENT={agent}\0".encode())


def test_name_argument_does_not_count_hyphenated_clone(tmp_path):
    _fake_proc(tmp_path, 101, name="NEXUS", agent="NEXUS")
    _fake_proc(tmp_path, 102, name="NEXUS-CLONE", agent="NEXUS")
    scan = SUPERVISOR.scan_primary_runtimes("NEXUS", tmp_path)
    assert scan == SUPERVISOR.RuntimeScan((101,))


def test_name_argument_accepts_descriptive_em_dash(tmp_path):
    _fake_proc(tmp_path, 101, name="JARVIS — Arquitecto", agent="JARVIS")
    scan = SUPERVISOR.scan_primary_runtimes("JARVIS", tmp_path)
    assert scan == SUPERVISOR.RuntimeScan((101,))


def test_locked_reconcile_uses_agent_scoped_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        SUPERVISOR,
        "reconcile",
        lambda agent, wait_seconds=30: {"ok": True, "agent": agent},
    )
    assert SUPERVISOR.locked_reconcile("FABLE") == {"ok": True, "agent": "FABLE"}
    assert (tmp_path / "seal-agent-runtime-supervisor-FABLE.lock").is_file()


def test_systemd_preflight_serializes_before_legacy_boot_launchers():
    unit = UNIT_PATH.read_text(encoding="utf-8")
    assert "ExecStartPre=" in unit and "--once" in unit
    assert "Before=seal-boot-wake.service alice-terminal.service fable-terminal.service" in unit
