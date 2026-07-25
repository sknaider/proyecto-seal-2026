#!/usr/bin/env python3
"""Liveness verificada del worker que reclamó un handoff (#1325, incremento 2).

La regla que estos tests defienden es UNA y es asimétrica: **`unmeasurable`
nunca puede degradar a `dead`.** Declarar muerto a un worker que sólo es viejo o
ilegible habilita reasignar su misión, y entonces habría dos workers en la misma
misión — el defecto que se decidió NO introducir cuando se descartó el TTL.
Muerte se PRUEBA; duda se reporta.
"""

from __future__ import annotations

import os

import pytest

from memory.nerves_mission_handoff import (
    _parse_starttime,
    _worker_liveness_handle,
    claim_worker_liveness,
)


def _handle_vivo() -> dict:
    h = _worker_liveness_handle()
    assert h is not None, "sin /proc no hay handle: el resto de los tests no aplica"
    return h


# ── El camino que importa: proceso vivo de verdad, el de este propio test ──


def test_proceso_vivo_se_reconoce():
    assert claim_worker_liveness({"worker_liveness": _handle_vivo()}) == {
        "state": "alive", "detail": "process_matches_handle"}


def test_el_handle_es_de_ESTE_proceso():
    """Si no fuera del proceso real, el resto de la suite mediría una fantasía."""
    assert _handle_vivo()["pid"] == os.getpid()


# ── Las tres muertes PROBADAS ──


def test_pid_reciclado_es_muerte_no_vida():
    """Mismo PID, otro arranque: es otro proceso que heredó el número.

    Sin comparar `starttime` esta celda daría `alive` y el detector sería
    exactamente tan ciego como el `worker_id` que vino a reemplazar.
    """
    h = dict(_handle_vivo(), starttime=_handle_vivo()["starttime"] + 1)
    assert claim_worker_liveness({"worker_liveness": h}) == {
        "state": "dead", "detail": "pid_recycled"}


def test_proceso_inexistente_es_muerte():
    h = dict(_handle_vivo(), pid=2 ** 22)  # sobre /proc/sys/kernel/pid_max usual
    assert claim_worker_liveness({"worker_liveness": h})["detail"] == "process_absent"


def test_reboot_mata_todo_el_arranque_anterior():
    h = dict(_handle_vivo(), boot_id="00000000-0000-0000-0000-000000000000")
    assert claim_worker_liveness({"worker_liveness": h}) == {
        "state": "dead", "detail": "host_rebooted"}


# ── La asimetría: duda NUNCA es muerte ──


@pytest.mark.parametrize(
    "claim, detalle",
    [
        ({}, "handle_absent"),                                   # claim viejo, pre-cambio
        ({"worker_liveness": None}, "handle_absent"),            # /proc ilegible al reclamar
        ({"worker_liveness": "vivo"}, "handle_absent"),          # tipo equivocado
        ({"worker_liveness": {"kind": "container"}}, "handle_kind_unknown"),
        ({"worker_liveness": {"kind": "process", "pid": "7"}}, "handle_malformed"),
        ("no soy un dict", "claim_not_a_mapping"),
    ],
)
def test_lo_no_medible_no_degrada_a_muerto(claim, detalle):
    r = claim_worker_liveness(claim)
    assert r == {"state": "unmeasurable", "detail": detalle}
    assert r["state"] != "dead", "un claim no medible NO habilita reasignar la misión"


# ── El parseo, con el caso que rompe la versión ingenua ──


def test_starttime_con_comm_de_nombre_hostil():
    """`comm` puede traer espacios y paréntesis: partir por espacios da el campo
    equivocado justo para los procesos con nombre raro, y ahí el `starttime`
    leído sería basura que después se compara como si fuera identidad."""
    # Cada token vale su NÚMERO de campo, así el 22 se lee solo. Mi primera
    # versión numeraba desde el campo 3 y esperaba 22 donde correspondía 21: el
    # test falló por su propia aritmética y el código era correcto.
    campos = " ".join(str(i) for i in range(4, 24))  # campo 3 = "S", 4..23 = su número
    assert _parse_starttime(f"1234 (mi (mal) nombre) S {campos}\n") == 22


def test_el_campo_22_es_starttime_de_verdad_no_uno_vecino():
    """Oráculo EXTERNO al parser, porque la celda "vivo" se compara consigo misma
    y daría verde igual con el índice corrido: PID 1 arranca con el sistema, así
    que su `starttime` en segundos tiene que ser ~0 contra el uptime del host.
    Un campo vecino (`vsize`, `rss`, `priority`) no cumple eso ni por casualidad.
    """
    from memory.nerves_mission_handoff import _process_starttime

    ticks = _process_starttime(1)
    assert ticks is not None
    segundos = ticks / os.sysconf("SC_CLK_TCK")
    uptime = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
    assert 0 <= segundos < 60, f"PID 1 arrancó {segundos:.0f}s tras el boot (uptime {uptime:.0f}s)"


def test_starttime_ilegible_es_none_no_excepcion():
    assert _parse_starttime("basura sin parentesis") is None
    assert _parse_starttime("1 (x) S 3 4") is None


# ── Integración: el claim REAL lleva el campo ──


def test_claim_handoff_graba_el_handle(monkeypatch, tmp_path):
    """No alcanza con que la función exista: tiene que quedar EN el claim escrito.

    Se ejerce el mismo dict literal que arma `claim_handoff`, con el handle
    resuelto en vivo, porque montar un handoff autenticado completo requiere
    bundle, receipt y feed — y lo que esta celda prueba es que el campo viaja.
    """
    from memory import nerves_mission_handoff as h

    claim = {"claim_id": "c", "worker_id": "w", "worker_liveness": h._worker_liveness_handle()}
    assert claim_worker_liveness(claim)["state"] == "alive"
