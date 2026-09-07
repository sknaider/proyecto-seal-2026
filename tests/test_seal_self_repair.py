from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import seal_self_repair
from seal_self_repair import AGENT_ACTIONS, UnitSnapshot, execute, resolve_agent, resolve_unit


def test_identity_is_fail_closed() -> None:
    with pytest.raises(PermissionError, match="SEAL_AGENT is required"):
        resolve_agent({})


def test_un_nombre_sin_politica_es_rechazado() -> None:
    """La SEGUNDA puerta de resolve_agent: identidad presente pero sin politica.

    Hallada por ADA el 4-sep revisando este manifiesto. `resolve_agent` tiene DOS guardas
    -identidad vacia, e identidad sin entrada en AGENT_ACTIONS- y la suite solo cubria la
    primera: su mutante `if agent not in AGENT_ACTIONS:` -> `if False:` dejaba los 15 tests
    en verde. Sin esta prueba, cualquier nombre inventado en SEAL_AGENT pasaria la puerta y
    caeria mas adelante, donde el fallo ya no dice que la identidad no tenia politica.
    """
    # OJO CON EL FIXTURE: resolve_agent normaliza con .strip().upper(), asi que "jarvis"
    # en minuscula SI tiene politica y es un caso VALIDO, no uno rechazado. Mi primera
    # version lo listaba como desconocido -- comparaba el nombre crudo contra AGENT_ACTIONS
    # y el propio test fallaba. Los nombres de abajo siguen sin politica DESPUES de normalizar.
    for nombre in ("DESCONOCIDO", "ALICE-V2", "seal", "  fable-juez  "):
        assert nombre.strip().upper() not in AGENT_ACTIONS, (
            f"fixture invalido: {nombre!r} SI tiene politica al normalizar"
        )
        with pytest.raises(PermissionError, match="has no self-repair policy"):
            resolve_agent({"SEAL_AGENT": nombre})


def test_la_identidad_se_normaliza_antes_de_decidir() -> None:
    """`jarvis` en minuscula y con espacios es JARVIS: la puerta mira el valor normalizado."""
    assert resolve_agent({"SEAL_AGENT": "  jarvis  "}) == "JARVIS"


def test_un_agente_con_politica_pasa_las_dos_puertas() -> None:
    """Control del anterior: si rechazara SIEMPRE, el negativo pasaria por la razon equivocada."""
    for agent in AGENT_ACTIONS:
        assert resolve_agent({"SEAL_AGENT": agent}) == agent


def test_each_supported_agent_has_only_fixed_own_units() -> None:
    for agent, actions in AGENT_ACTIONS.items():
        assert actions
        for unit in actions.values():
            assert agent.casefold() in unit.casefold()
            assert unit.endswith(".service")


def test_arbitrary_unit_is_denied() -> None:
    with pytest.raises(PermissionError, match="not allowed"):
        resolve_unit("ADA", "../../ssh.service")


def test_cross_agent_action_is_not_reachable_from_ada_policy() -> None:
    assert "seal-channel-monitor@JARVIS.service" not in AGENT_ACTIONS["ADA"].values()
    with pytest.raises(PermissionError):
        resolve_unit("ADA", "jarvis_channel_monitor")


def test_known_action_resolves_to_exact_unit() -> None:
    assert resolve_unit("NEXUS", "visible_terminal") == "nexus-terminal.service"


def test_help_points_bypass_ack_to_stability_guard(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        seal_self_repair.build_parser().parse_args(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "scripts/seal_agent_stability_guard.py" in help_text
    assert "--ack-out-of-band" in help_text


def test_default_receipt_root_is_durable_and_honors_xdg(tmp_path) -> None:
    root = seal_self_repair.default_receipt_root(
        {"XDG_DATA_HOME": str(tmp_path / "data")}
    )
    assert root == tmp_path / "data" / "seal" / "autonomy_receipts"


def test_oneshot_actions_are_excluded_from_v1() -> None:
    assert "visible_terminal" not in AGENT_ACTIONS["ALICE"]
    assert "visible_terminal" not in AGENT_ACTIONS["FABLE"]
    assert "nerves" not in AGENT_ACTIONS["FABLE"]


def _snapshot(unit: str, pid: int, invocation: str, state: str = "active") -> UnitSnapshot:
    return UnitSnapshot(
        unit=unit,
        observed_at=datetime.now(timezone.utc).isoformat(),
        load_state="loaded",
        unit_type="simple",
        active_state=state,
        sub_state="running" if state == "active" else "failed",
        main_pid=pid,
        invocation_id=invocation,
        result="success" if state == "active" else "exit-code",
    )


def test_restart_receipt_requires_target_change_and_unchanged_control(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    snapshots = iter(
        [
            _snapshot(target, 10, "target-old"),
            _snapshot(control, 20, "control-same"),
            _snapshot(target, 11, "target-new"),
            _snapshot(control, 20, "control-same"),
        ]
    )
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))
    monkeypatch.setattr(
        seal_self_repair,
        "_run",
        lambda _argv, timeout=15: CompletedProcess(_argv, 0, "", ""),
    )

    receipt = execute("ADA", "channel_monitor", "restart", "test", timeout=0.01)

    assert receipt.result == "verified"
    assert all(receipt.checks.values())


def test_restart_receipt_fails_when_negative_control_changes(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    snapshots = iter(
        [
            _snapshot(target, 10, "target-old"),
            _snapshot(control, 20, "control-old"),
            _snapshot(target, 11, "target-new"),
            _snapshot(control, 21, "control-new"),
        ]
    )
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))
    monkeypatch.setattr(
        seal_self_repair,
        "_run",
        lambda _argv, timeout=15: CompletedProcess(_argv, 0, "", ""),
    )

    receipt = execute("ADA", "channel_monitor", "restart", "test", timeout=0.01)

    assert receipt.result == "failed"
    assert receipt.checks["negative_control_other_agent_unchanged"] is False


def test_restart_rejects_oneshot_even_if_policy_drifts(monkeypatch) -> None:
    target = AGENT_ACTIONS["ADA"]["channel_monitor"]
    control = seal_self_repair.CONTROL_UNITS["ADA"]
    oneshot = _snapshot(target, 0, "oneshot")
    object.__setattr__(oneshot, "unit_type", "oneshot")
    snapshots = iter([oneshot, _snapshot(control, 20, "control")])
    monkeypatch.setattr(seal_self_repair, "snapshot_unit", lambda _unit: next(snapshots))

    with pytest.raises(RuntimeError, match="only repairs continuously supervised"):
        execute("ADA", "channel_monitor", "restart", "test")


def test_receipt_is_written_to_private_durable_tree(tmp_path, monkeypatch) -> None:
    receipt = seal_self_repair.RepairReceipt(
        schema="seal.autonomy.repair-receipt.v1",
        receipt_id="durable-test",
        agent="ADA",
        operation="restart",
        action="channel_monitor",
        unit=AGENT_ACTIONS["ADA"]["channel_monitor"],
        reason="test",
        started_at="2026-07-30T00:00:00+00:00",
        finished_at="2026-07-30T00:00:01+00:00",
        opportunity={"command_invoked": True, "operation": "restart"},
        expected={"target_active": True},
        before=_snapshot("target", 1, "old"),
        after=_snapshot("target", 2, "new"),
        negative_control_before=_snapshot("control", 3, "same"),
        negative_control_after=_snapshot("control", 3, "same"),
        checks={"passed": True},
        command=["systemctl", "--user", "restart", "target"],
        returncode=0,
        output="",
        result="verified",
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    path = seal_self_repair.write_receipt(receipt)

    assert path == tmp_path / "data" / "seal" / "autonomy_receipts" / "ADA" / "durable-test.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700


def test_shared_chat_action_is_nexus_only() -> None:
    """3-sep-2026: seal-chat es compartido; sólo NEXUS lo reinicia con recibo (William 14:12)."""
    assert resolve_unit("NEXUS", "chat_server") == "seal-chat.service"
    for other in ("ADA", "ALICE", "FABLE", "JARVIS"):
        with pytest.raises(PermissionError, match="not allowed"):
            resolve_unit(other, "chat_server")


def test_shared_actions_do_not_leak_into_agent_actions() -> None:
    for actions in AGENT_ACTIONS.values():
        assert "seal-chat.service" not in actions.values()


def test_shared_chat_negative_control_is_outside_the_cascade() -> None:
    """Reiniciar seal-chat arrastra a los monitores: el control no puede ser uno de ellos."""
    control = seal_self_repair.resolve_control_unit("NEXUS", "chat_server")
    assert control == "seal-failover-watcher.service"
    assert "channel-monitor" not in control
    assert control != seal_self_repair.resolve_unit("NEXUS", "chat_server")
    # el camino propio no cambia
    assert seal_self_repair.resolve_control_unit("NEXUS", "bridge") == seal_self_repair.CONTROL_UNITS["NEXUS"]
