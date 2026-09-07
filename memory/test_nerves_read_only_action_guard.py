from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory.nerves_read_only_action_guard as guard


def _payload(tool_name: str, tool_input: dict, *, session_id: str = "session-a"):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _decision(value: dict) -> str:
    if not value:
        return "pass"
    return value["hookSpecificOutput"]["permissionDecision"]


def test_no_bound_mission_allows(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: [])
    assert _decision(guard.evaluate(_payload("Bash", {"command": "systemctl --user restart x"}))) == "pass"


def test_bound_mission_allows_read_only_bash(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": "systemctl --user status x"}))) == "pass"
    assert _decision(guard.evaluate(_payload("Bash", {"command": "journalctl --user -u x -n 20"}))) == "pass"


def test_bound_mission_denies_service_mutation(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    denied = guard.evaluate(
        _payload("Bash", {"command": "systemctl --user restart seal-instinct-cron.service"})
    )
    assert _decision(denied) == "deny"
    assert "bash_not_allowlisted" in denied["hookSpecificOutput"]["permissionDecisionReason"]


def test_bound_mission_denies_file_tool(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Edit", {"file_path": "/tmp/x"}))) == "deny"


@pytest.mark.parametrize(
    "tool_name",
    ["Agent", "Task", "Skill", "mcp__seal_memory__memory_store"],
)
def test_bound_mission_denies_other_capability_surfaces(
    monkeypatch, tool_name: str
) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload(tool_name, {}))) == "deny"


def test_protected_agent_is_delegated_even_with_completed_mission_bound(
    monkeypatch,
) -> None:
    """The renderer owns this decision; a parallel sibling must not veto it."""

    monkeypatch.setattr(
        guard, "_bound_a2_missions", lambda _session: ["completed-old-mission"]
    )
    protected = {
        "description": guard.NATIVE_SPAWN_DESCRIPTION,
        "subagent_type": guard.NATIVE_PROFILE,
        "name": guard.NATIVE_SPAWN_NAME,
        "prompt": (
            guard.PROMPT_STUB_PREFIX
            + "mission_id=11111111-1111-4111-8111-111111111111\n"
            + "claim_id=22222222-2222-4222-8222-222222222222\n"
        ),
        "run_in_background": True,
    }
    assert _decision(guard.evaluate(_payload("Agent", protected))) == "pass"


def test_partial_protected_agent_is_delegated_for_renderer_to_deny(
    monkeypatch,
) -> None:
    """Malformed protected launches still reach the fail-closed renderer."""

    monkeypatch.setattr(
        guard, "_bound_a2_missions", lambda _session: ["completed-old-mission"]
    )
    partial = {
        "description": guard.NATIVE_SPAWN_DESCRIPTION,
        "subagent_type": "general-purpose",
        "name": "wrong",
        "prompt": "wrong",
        "run_in_background": False,
    }
    assert _decision(guard.evaluate(_payload("Agent", partial))) == "pass"


def test_unrelated_agent_remains_denied_when_a2_session_is_bound(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        guard, "_bound_a2_missions", lambda _session: ["completed-old-mission"]
    )
    unrelated = {
        "description": "ordinary worker",
        "subagent_type": "general-purpose",
        "name": "worker",
        "prompt": "inspect",
    }
    denied = guard.evaluate(_payload("Agent", unrelated))
    assert _decision(denied) == "deny"
    assert "agent_not_allowlisted" in denied["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def test_hook_failure_denies(monkeypatch) -> None:
    def explode(_session):
        raise ValueError("bad state")

    monkeypatch.setattr(guard, "_bound_a2_missions", explode)
    assert _decision(guard.evaluate(_payload("Bash", {"command": "true"}))) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/systemctl --user restart seal-instinct-cron.service",
        "env systemctl --user restart seal-instinct-cron.service",
        "command systemctl --user restart seal-instinct-cron.service",
        "bash -lc 'systemctl --user restart seal-instinct-cron.service'",
        "python3 -c \"import subprocess; subprocess.run(['systemctl','restart','x'])\"",
        "busctl call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
        "org.freedesktop.systemd1.Manager RestartUnit ss x replace",
        "systemd-run --user true",
        "kill 123",
        "dd if=/dev/zero of=/tmp/x",
        "curl -X POST http://localhost:8765/api/x",
        "cat /etc/hosts | tee /tmp/x",
        "journalctl --rotate",
    ],
)
def test_bound_mission_denies_bypass_variants(monkeypatch, command: str) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


def test_bound_mission_allows_exact_control_plane(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    bind = (
        "/home/dadito/IA/seal-spark/.venv/bin/python3 -m "
        "memory.nerves_mission_handoff bind-platform m c w p h"
    )
    receipt = (
        "/home/dadito/IA/seal-spark/.venv/bin/python3 -m "
        "memory.nerves_native_agent_receipt m w p h parent child"
    )
    assert _decision(guard.evaluate(_payload("Bash", {"command": bind}))) == "pass"
    assert _decision(guard.evaluate(_payload("Bash", {"command": receipt}))) == "pass"


_VENV = "/home/dadito/IA/seal-spark/.venv/bin/python3"
_RECEIPT = f"{_VENV} -m memory.nerves_native_agent_receipt"
_HANDOFF = f"{_VENV} -m memory.nerves_mission_handoff"


@pytest.mark.parametrize(
    "command",
    [
        # El agujero de banderas: antes TODO esto pasaba por un `return True` pelado.
        f"{_RECEIPT} --help",  # probado por FABLE: muta
        f"{_RECEIPT} --any-unknown-flag x",
        f"{_RECEIPT} emit --force",
        f"{_RECEIPT} render-prompt cmd mission out",  # escribe el archivo de salida
        f"{_HANDOFF} claim m w --evil",
        # Banderas fuera de la allowlist en ejecutables de solo lectura.
        "grep --devices=read /dev/sda",
        "git log --ext-diff",
        # Basename secuestrado: mismo nombre, otra ruta.
        "/tmp/evil/seal_send.py JARVIS hola",
        "/tmp/evil/cat /etc/shadow",
    ],
)
def test_bound_mission_denies_flag_and_path_escapes(monkeypatch, command: str) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        # Control POSITIVO: el uso legítimo tiene que seguir pasando, o el guard
        # de banderas sería un "deniega todo" que se ve igual de verde.
        f"{_RECEIPT} m w p h parent child",
        f"{_RECEIPT} m w p h parent child --state /tmp/s.json",
        f"{_HANDOFF} claim m w",
        f"{guard.ROOT}/scripts/seal_send.py JARVIS hola",
        "grep -rn pattern memory/",
        "tail -n 20 /var/log/syslog",
        "head -20 /var/log/syslog",
        "git log --oneline -5",
    ],
)
def test_bound_mission_still_allows_legitimate_read_only(
    monkeypatch, command: str
) -> None:
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "pass"


@pytest.mark.parametrize(
    "command",
    [
        "scripts/seal_send.py JARVIS equipo hola",
        "scripts/seal_send.py JARVIS equipo hola --channel web_chat --type coordination",
        "./scripts/seal_send.py JARVIS equipo hola --channel web_chat",
    ],
)
def test_relative_repo_script_allowed_from_foreign_cwd(
    monkeypatch, tmp_path: Path, command: str
) -> None:
    """Regresión reportada por JARVIS: el guard lo dejó mudo desde su sandbox.

    Mi suite original corría siempre desde la raíz del repo, así que una ruta
    relativa resolvía por casualidad al script correcto y el bug era invisible.
    El cwd es parte de la entrada: si no se varía, no se está probando.
    """
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    monkeypatch.chdir(tmp_path)
    assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "pass"


def test_relative_escape_still_denied_from_foreign_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    """Control: aflojar la relativa no puede reabrir el secuestro por basename."""
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    monkeypatch.chdir(tmp_path)
    for command in (
        "../../evil/seal_send.py JARVIS equipo hola",
        "/tmp/evil/seal_send.py JARVIS equipo hola",
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


def test_escaped_multiline_message_passes_without_loosening_shell_meta(
    monkeypatch,
) -> None:
    """La salida al choque entre el guard y la regla de oro de formato de William.

    Bajo una misión A2 no había NINGUNA vía para un mensaje multilínea: el heredoc
    mete un salto real (`SHELL_META`), el pipe también, y las tools de escritura
    están denegadas.  `seal_send.py --message-escaped` decodifica `\\n` del lado
    del script, así que el comando viaja en una sola línea y la barrera queda
    intacta — se disuelve el conflicto en vez de aflojar el control.
    """
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])
    escaped = 'scripts/seal_send.py JARVIS "Titulo:\\n- uno\\n- dos" --message-escaped'
    assert _decision(guard.evaluate(_payload("Bash", {"command": escaped}))) == "pass"

    # Controles: la vía nueva no puede abrir nada de lo que ya estaba cerrado.
    for command in (
        'scripts/seal_send.py JARVIS "Titulo:\n- uno"',  # salto REAL sigue denegado
        # Deniega porque `argv[1]` tiene que ser JARVIS.  Con DOS posicionales ese
        # token es el DESTINATARIO (ver test de aridad más abajo), no el remitente.
        'scripts/seal_send.py William "x" --message-escaped',
        '/tmp/evil/seal_send.py JARVIS "x" --message-escaped',  # basename secuestrado
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


def test_seal_send_flags_are_enumerated_like_every_other_branch(monkeypatch) -> None:
    """La rama de seal_send.py era la única que aceptaba CUALQUIER bandera.

    Eso dejaba a una sesión confinada auto-otorgarse `--unique-contribution`, que
    del lado del server bypasea el council-deny.  Un override de coordinación no
    puede ser auto-declarable por el propio confinado.
    """
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])

    # Control POSITIVO primero: el uso real medido en el repo tiene que seguir
    # pasando, o esto vuelve a ser una mordaza en vez de un allowlist.
    for command in (
        'scripts/seal_send.py JARVIS "hola"',
        'scripts/seal_send.py JARVIS "hola" --channel web_chat --type coordination',
        'scripts/seal_send.py JARVIS "hola" --in-reply-to abc123',
        'scripts/seal_send.py JARVIS "a\\nb" --message-escaped --idempotency-key k1',
        # La escalada legítima tiene que poder salir: `argv[1]` es el REMITENTE, no
        # el destinatario, así que una sesión A2 puede escribirle a William — y
        # seal_autonomy_guard le bloquea el pedido si NO declara el gate.
        'scripts/seal_send.py JARVIS William "x" --approval-gate destructive',
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "pass"

    # La remediación que el PROPIO server prescribe cuando dispara el council-deny
    # (chat_server.py:2256 dice textualmente "reenvia con unique_contribution=true y
    # contribution_reason") tiene que poder salir.  Negarla acá volvía inalcanzable la
    # única salida documentada y dejaba mudo al confinado — mismo modo de falla que el
    # heredoc.  El override rinde cuentas donde corresponde: exige motivo >=20 chars,
    # queda logueado con remitente y razón, y no cubre duplicados.
    for command in (
        'scripts/seal_send.py JARVIS equipo "x" --unique-contribution '
        '--contribution-reason "aporta el diferencial del guard que nadie midio"',
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "pass"

    for command in (
        'scripts/seal_send.py JARVIS "x" --bandera-inventada',
        'scripts/seal_send.py JARVIS "x" --force',
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == "deny"


def test_argv1_clamp_changes_meaning_with_positional_arity(monkeypatch) -> None:
    """El chequeo `argv[1] == "JARVIS"` hace DOS controles distintos, según la aridad.

    `from_agent` es `nargs="?"` en seal_send.py, así que el token que el guard mira
    en crudo cambia de rol con la cantidad de posicionales:

        3 posicionales -> argv[1] es el REMITENTE     -> clamp de IDENTIDAD
        2 posicionales -> argv[1] es el DESTINATARIO  -> clamp de ALCANCE

    Está escrito como test porque el equipo lo dedujo mal DOS veces en un día, en
    las dos direcciones: primero "acota el destinatario", después —al corregirlo—
    "es puramente anti-suplantación".  Cada lectura era correcta sobre la aridad
    que quien la hizo había probado, y falsa como generalización.  Dos lentes
    independientes coincidieron porque ninguna varió la aridad: la variable que
    nadie varía es la que esconde el hallazgo.  Fijar las dos filas acá es más
    barato que volver a discutirlo desde el código.
    """
    monkeypatch.setattr(guard, "_bound_a2_missions", lambda _session: ["m1"])

    for command, expected in (
        ('scripts/seal_send.py JARVIS "x"', "pass"),  # 2-pos: to=JARVIS
        ('scripts/seal_send.py William "x"', "deny"),  # 2-pos: to=William
        ('scripts/seal_send.py JARVIS William "x"', "pass"),  # 3-pos: from=JARVIS
        ('scripts/seal_send.py NEXUS JARVIS "x"', "deny"),  # 3-pos: from=NEXUS
        # No hay forma de firmar como otro pisando SEAL_AGENT: el ejecutable tiene
        # que ser el PRIMER token, así que el prefijo de entorno no llega a correr.
        ('SEAL_AGENT=ADA scripts/seal_send.py JARVIS "x"', "deny"),
        ('env SEAL_AGENT=ADA scripts/seal_send.py JARVIS "x"', "deny"),
    ):
        assert _decision(guard.evaluate(_payload("Bash", {"command": command}))) == expected


def test_guard_documents_that_it_is_not_containment_evidence() -> None:
    """El punto 3 de JARVIS vive en el archivo, no solo en un mensaje de chat."""
    doc = guard.__doc__ or ""
    assert "NO CITAR ESTE GUARD COMO EVIDENCIA DE CONTENCIÓN" in doc


def test_real_completed_canary_blocks_restart() -> None:
    audit = json.loads(
        (
            guard.INBOX
            / "af95ab67-b242-5d46-a083-70a70a4a8bcd.prompt-render.json"
        ).read_text()
    )
    result = guard.evaluate(
        _payload(
            "Bash",
            {"command": "systemctl --user restart seal-instinct-cron.service"},
            session_id=audit["session_id"],
        )
    )
    assert _decision(result) == "deny"


def test_real_completed_canary_allows_status() -> None:
    audit = json.loads(
        (
            guard.INBOX
            / "af95ab67-b242-5d46-a083-70a70a4a8bcd.prompt-render.json"
        ).read_text()
    )
    result = guard.evaluate(
        _payload(
            "Bash",
            {"command": "systemctl --user status seal-instinct-cron.service"},
            session_id=audit["session_id"],
        )
    )
    assert _decision(result) == "pass"
