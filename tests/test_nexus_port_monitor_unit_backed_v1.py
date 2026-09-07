"""Un puerto que VUELVE tras el restart de una unidad conocida no es un puerto nuevo.

POR QUE (NEXUS, 3-sep-2026 — falso positivo publicado al equipo)

A las 15:07 mi monitor publico «Nuevos puertos detectados: [8771] — posible
backdoor». Era `seal-mcp-server`, que ADA acababa de reiniciar a las 15:05:57.
El puerto no aparecio: VOLVIO.

Lo peor es que el debounce ya existia... del otro lado. En `closed_ports` hay un
anti-restart-blip que escribi yo el 22-jul por esta misma causa. **Lo puse de un
solo lado.** Un restart cierra el puerto y lo reabre; si el baseline cae mientras
esta caido, el reingreso entra como NUEVO. El filtro de loopback tampoco tapaba
este caso: 8771 bindea 0.0.0.0.

LO QUE ESTE FILTRO NO HACE: no confia en el numero de puerto ni en una lista.
Pregunta QUIEN escucha y solo calla si ese proceso vive en una unidad systemd
propia. Un binario suelto en un puerto nuevo sigue alertando -- y ese es el
control negativo de abajo, que levanta un listener a mano y exige alerta.
"""
from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import threading
import time

import pytest

RUTA = "sandbox-agent/seal_security_monitor.py"


@pytest.fixture(scope="module")
def monitor():
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("secmon_bajo_prueba", raiz / RUTA)
    m = importlib.util.module_from_spec(spec)
    sys.modules["secmon_bajo_prueba"] = m
    try:
        spec.loader.exec_module(m)
    except SystemExit:
        pass
    return m


def ss_lines() -> list[str]:
    return subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=30).stdout.splitlines()


# --- unit ----------------------------------------------------------------

def test_unit_la_unidad_es_la_HOJA_del_cgroup(monitor):
    """Mi primera version devolvia la PRIMERA coincidencia y eso era
    `user@1000.service` para TODO proceso de usuario -- o sea que habria
    silenciado cualquier binario suelto, que es justo lo que el monitor busca.
    Lo vi porque mire el VALOR, no el booleano."""
    cg = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/seal-mcp-server.service\n"
    assert monitor._unidad_de_cgroup(cg) == "seal-mcp-server.service"


def test_unit_con_unidades_ANIDADAS_gana_la_hoja(monitor):
    """El caso que separa `[-1]` de `[0]`, y lo agregue porque el mutante que
    cambiaba uno por otro SOBREVIVIO: con un solo servicio en la ruta, primero
    y ultimo son el mismo y el test no distinguia nada.

    Importa de verdad: un proceso lanzado por una unidad desde otra unidad
    cuelga de las dos, y el dueño es la de adentro. Devolver la de afuera
    atribuye el puerto al padre equivocado.
    """
    cg = ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
          "seal-bridge-nexus.service/seal-mcp-server.service\n")
    assert monitor._unidad_de_cgroup(cg) == "seal-mcp-server.service"


def test_unit_solo_el_user_manager_no_cuenta_como_unidad(monitor):
    """Un proceso suelto del usuario cuelga de user@1000.service y de un .scope:
    ningun .service propio -> no respaldado."""
    cg = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-x.scope\n"
    assert monitor._unidad_de_cgroup(cg) == ""


# --- qa_positive: el caso que produjo el falso positivo -------------------

@pytest.mark.parametrize("puerto,esperado", [(8771, "seal-mcp-server.service"), (8765, "seal-chat.service")])
def test_qa_positive_un_puerto_de_servicio_resuelve_su_unidad(monitor, puerto, esperado):
    lineas = ss_lines()
    if not any(f":{puerto} " in ln for ln in lineas):
        pytest.skip(f"el servicio del puerto {puerto} no esta arriba en esta maquina")
    assert monitor._puerto_respaldado_por_unidad(puerto, lineas) == esperado


# --- qa_negative: lo que TIENE que seguir alertando ------------------------

def test_qa_negative_un_listener_suelto_no_queda_respaldado(monitor):
    """Sin este brazo, el filtro podria estar silenciando todo y los positivos
    de arriba se verian iguales."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    puerto = srv.getsockname()[1]
    srv.listen()
    hilo = threading.Thread(target=lambda: time.sleep(2), daemon=True)
    hilo.start()
    try:
        lineas = ss_lines()
        assert any(f":{puerto} " in ln for ln in lineas), "el listener de prueba no aparecio en ss"
        assert monitor._puerto_respaldado_por_unidad(puerto, lineas) == ""
    finally:
        srv.close()


def test_qa_negative_un_puerto_que_nadie_escucha_no_resuelve(monitor):
    assert monitor._puerto_respaldado_por_unidad(59999, ["LISTEN 0 128 0.0.0.0:1234 0.0.0.0:*"]) == ""


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar(monitor):
    lineas = ss_lines()
    assert lineas, "ss no devolvio nada: el arnes no puede medir"
    with pytest.raises(AssertionError):
        assert monitor._puerto_respaldado_por_unidad(59999, lineas) != ""
