"""Brazos del esperador de puerto.

Nace de un caso contado: `seal-mcp-web-soul-operator` acumuló 87 reinicios entre
el 27-ago y el 7-sep, y **82 de ellos** eran arrancar antes que Postgres —78
`ConnectionRefusedError` contra 127.0.0.1:5433 y 4 «the database system is
starting up»—. La unidad esperaba al witness con un `ExecStartPre` y no esperaba
a la base.

Los brazos usan un socket REAL en un puerto efímero, no un doble: lo que se
prueba es que sabe distinguir un puerto que acepta de uno que no, y esa
distinción no se puede simular sin volver el test circular.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import pathlib
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import seal_espera_puerto as ep  # noqa: E402


@pytest.fixture()
def puerto_libre():
    """Un puerto que nadie escucha: se reserva y se suelta."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def puerto_abierto():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    yield s.getsockname()[1]
    s.close()


# ───────────────────────── qa_positive: el puerto acepta ─────────────────────

def test_qa_positive_un_puerto_que_ESCUCHA_se_detecta_enseguida(puerto_abierto):
    r = ep.espera("127.0.0.1", puerto_abierto, timeout=5, intervalo=0.05, margen=0)
    assert r["ok"] is True
    assert r["motivo"] == "abierto"
    assert r["intentos"] == 1, "no deberia necesitar reintentos con el puerto ya abierto"


def test_qa_positive_espera_a_que_el_puerto_APAREZCA(puerto_libre):
    """El caso real: el servicio arranca antes que la base y debe esperarla."""
    listo = threading.Event()

    def abrir_tarde():
        time.sleep(0.4)
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", puerto_libre))
        s.listen(4)
        listo.set()
        time.sleep(3)
        s.close()

    t = threading.Thread(target=abrir_tarde, daemon=True)
    t.start()
    r = ep.espera("127.0.0.1", puerto_libre, timeout=6, intervalo=0.05, margen=0)
    assert listo.is_set()
    assert r["ok"] is True
    assert r["intentos"] > 1, "deberia haber reintentado mientras el puerto no estaba"


# ───────────────────── qa_negative: falla, y falla ruidoso ───────────────────

def test_qa_negative_un_puerto_CERRADO_agota_el_plazo_y_FALLA(puerto_libre):
    """No sigue de largo: si la base no está, el servicio debe fallar. Lo que se
    evita es el bucle de 78 reintentos, no el fallo."""
    r = ep.espera("127.0.0.1", puerto_libre, timeout=0.5, intervalo=0.05, margen=0)
    assert r["ok"] is False
    assert r["motivo"] == "timeout"
    assert r["segundos"] >= 0.5


def test_qa_negative_timeout_no_positivo_no_espera_para_siempre(puerto_libre):
    r = ep.espera("127.0.0.1", puerto_libre, timeout=0, intervalo=0.05)
    assert r["ok"] is False
    assert r["motivo"] == "timeout_no_positivo"


# ─────────────────────────────── el margen ───────────────────────────────────

def test_el_margen_exige_que_el_puerto_SIGA_abierto(puerto_abierto):
    """`--reintentos-tras-abrir` existe porque el puerto abre ANTES de que la base
    acepte consultas: 4 de los 87 reinicios fueron «the database system is
    starting up» con el puerto ya escuchando."""
    t0 = time.monotonic()
    r = ep.espera("127.0.0.1", puerto_abierto, timeout=5, intervalo=0.05, margen=0.3)
    assert r["ok"] is True
    assert time.monotonic() - t0 >= 0.3, "con margen debe seguir sondeando un rato mas"


def test_qa_control_si_el_puerto_se_CAE_durante_el_margen_no_lo_da_por_bueno(puerto_libre):
    """Un puerto que abre y se cae es justo la base arrancando. Con reloj
    inyectado para no depender de tiempos reales."""
    intentos = {"n": 0}

    def conectar_falso(direccion, timeout=None):
        intentos["n"] += 1
        if intentos["n"] == 1:          # abre una vez
            class F:
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return F()
        raise OSError("se cayo")        # y despues se cae

    import contextlib
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(ep.socket, "create_connection", conectar_falso)
        r = ep.espera("127.0.0.1", 1, timeout=0.4, intervalo=0.02, margen=5.0)
    assert r["ok"] is False
    assert r["motivo"] in ("timeout", "abierto_pero_inestable")


# ──────────────────────────── la interfaz de línea ───────────────────────────

def _cli(*args):
    return subprocess.run([sys.executable, str(pathlib.Path(ep.__file__)), *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_devuelve_0_cuando_el_puerto_acepta(puerto_abierto):
    p = _cli("127.0.0.1", str(puerto_abierto), "--timeout", "5", "--reintentos-tras-abrir", "0")
    assert p.returncode == 0, p.stderr
    assert "acepta conexiones" in p.stdout


def test_cli_devuelve_1_y_explica_cuando_no(puerto_libre):
    p = _cli("127.0.0.1", str(puerto_libre), "--timeout", "0.4", "--intervalo", "0.05")
    assert p.returncode == 1
    assert "NO acepta conexiones" in p.stderr
    assert str(puerto_libre) in p.stderr, "el mensaje debe decir QUE puerto fallo"


def test_qa_control_el_error_nombra_host_puerto_y_tiempo(puerto_libre):
    """Un ExecStartPre que falla sin decir qué esperaba deja al siguiente
    mirando un `status=1` sin causa."""
    p = _cli("127.0.0.1", str(puerto_libre), "--timeout", "0.3", "--intervalo", "0.05")
    for pieza in ("127.0.0.1", str(puerto_libre), "motivo="):
        assert pieza in p.stderr


def test_qa_control_no_lee_NADA_del_socket():
    """Conecta y cierra. Si algún día leyera, un servicio que saluda con datos
    sensibles los tendría en el log del arranque."""
    fuente = pathlib.Path(ep.__file__).read_text(encoding="utf-8")
    for prohibido in (".recv(", ".read(", "makefile("):
        assert prohibido not in fuente, f"el esperador no debe leer del socket: {prohibido}"


# ───────── hueco hallado mutando este mismo carril (NEXUS, 8-sep) ─────────

def test_una_CAIDA_reinicia_el_margen_y_no_solo_lo_estira():
    """Mutante E2: borrar `abierto_en = None` del `except` SOBREVIVÍA.

    `test_qa_control_si_el_puerto_se_CAE...` no lo veía porque acepta DOS
    motivos (`timeout` o `abierto_pero_inestable`) y el mutante devuelve uno de
    los dos igual. **Pasaba por el camino equivocado.**

    Lo que el mutante rompe de verdad: si el puerto abre, se cae y vuelve a
    abrir, el margen tiene que contar desde la REAPERTURA. Sin el reinicio
    sigue contando desde la primera vez y el arranque se declara bueno con la
    base todavía inestable — que es exactamente el caso de los 4 reinicios por
    `the database system is starting up`.

    Reloj inyectado: el resultado no depende de tiempos reales.
    """
    t = {"ahora": 0.0}
    intentos = {"n": 0}

    def reloj():
        return t["ahora"]

    def dormir(s):
        t["ahora"] += s

    class Conexion:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def conectar(direccion, timeout=None):
        intentos["n"] += 1
        if intentos["n"] == 2:          # abre, SE CAE, vuelve a abrir
            raise OSError("se cayo")
        return Conexion()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(ep.socket, "create_connection", conectar)
        r = ep.espera("127.0.0.1", 1, timeout=3.0, intervalo=0.5, margen=1.0,
                      reloj=reloj, dormir=dormir)

    assert r["ok"] is True
    # con el reinicio: reabre en t=1.0 y recien cumple el margen en t=2.0.
    # sin el reinicio (mutante E2): cierra en t=1.0, al 3er intento.
    assert r["intentos"] >= 5, (
        "el margen no se reinicio tras la caida: se dio por bueno en el intento "
        f"{r['intentos']}, contando desde la PRIMERA apertura"
    )
    assert r["segundos"] >= 2.0


def test_qa_control_el_puerto_que_se_cae_y_NO_vuelve_da_inestable():
    """Aprieta el brazo viejo, que aceptaba dos motivos y por eso no distinguía.
    Acá el puerto abre una vez y ya no vuelve: el motivo es exactamente uno."""
    t = {"ahora": 0.0}
    intentos = {"n": 0}

    def conectar(direccion, timeout=None):
        intentos["n"] += 1
        if intentos["n"] == 1:
            class F:
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return F()
        raise OSError("se cayo y no vuelve")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(ep.socket, "create_connection", conectar)
        r = ep.espera("127.0.0.1", 1, timeout=1.0, intervalo=0.25, margen=5.0,
                      reloj=lambda: t["ahora"],
                      dormir=lambda s: t.__setitem__("ahora", t["ahora"] + s))

    assert r["ok"] is False
    assert r["motivo"] == "timeout", "abrio una vez y no volvio: el margen nunca se cumplio"
