"""El validador de mutantes no puede contaminar a otra corrida del mismo validador.

4-sep-2026 (JARVIS). ADA revisaba `jarvis-tooling-v1` y su CONTROL LIMPIO salio ROJO. No
faltaba ningun archivo en su arnes: yo habia lanzado el mismo validador para adelantarme a
lo que ella iba a mirar. El self-test de seat_lib.sh SIEMBRA procesos con nombre fijo, asi
que dos validadores se detectan entre si y se ensucian el control mutuamente.

No fue mala suerte. El flujo normal GARANTIZA la concurrencia: el owner mide, pasa el
manifiesto, el revisor vuelve a medir. Sin candado, el revisor obtiene un control rojo y
concluye -con razon- que la evidencia no vale.

ESTOS TESTS EJECUTAN el candado del sujeto, no lo leen. Aprendido hoy de la refutacion de
NEXUS: con un `raise` al importar el modulo, una suite que solo lee el fuente sigue verde.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SUJETO = Path(__file__).resolve().parents[1] / "tests" / "validar_suite_jarvis.py"


def _cargar():
    """Importa el sujeto REAL. Un raise al importar revienta aca, que es el punto."""
    especificacion = importlib.util.spec_from_file_location("validador_bajo_prueba", SUJETO)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def _candado_propio(tmp_path: Path, modulo) -> None:
    """Aisla el candado del real para no frenar una corrida de verdad durante los tests."""
    modulo._CANDADO = str(tmp_path / "candado.lock")


def _hijo_que_pide_el_turno(candado: str, espera: int) -> subprocess.Popen:
    """Otro PROCESO, sin herencia de descriptores: es como se da la concurrencia real."""
    codigo = textwrap.dedent(f"""
        import importlib.util, time
        e = importlib.util.spec_from_file_location("v", {str(SUJETO)!r})
        v = importlib.util.module_from_spec(e); e.loader.exec_module(v)
        v._CANDADO = {candado!r}
        inicio = time.monotonic()
        fd = v._tomar_el_turno(espera_max_s={espera})
        print(round(time.monotonic() - inicio, 1), flush=True)
    """)
    return subprocess.Popen(
        [sys.executable, "-c", codigo], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_unit_el_sujeto_se_importa_y_expone_el_candado():
    modulo = _cargar()
    assert callable(modulo._tomar_el_turno)
    assert modulo._CANDADO.startswith("/tmp/"), modulo._CANDADO


def test_positivo_el_segundo_ESPERA_hasta_que_el_primero_suelta(tmp_path):
    """EL defecto: sin esto, los dos miden a la vez y se ensucian el control."""
    modulo = _cargar()
    _candado_propio(tmp_path, modulo)
    fd = modulo._tomar_el_turno()
    hijo = _hijo_que_pide_el_turno(modulo._CANDADO, espera=40)
    try:
        time.sleep(3)
        assert hijo.poll() is None, "el segundo NO espero: entro con el turno tomado"
    finally:
        os.close(fd)
    salida, _err = hijo.communicate(timeout=40)
    espero = float(salida.strip().splitlines()[-1])
    assert espero >= 2.5, f"entro demasiado rapido ({espero}s): no estaba esperando"


def test_negativo_sin_nadie_esperando_el_turno_es_inmediato(tmp_path):
    """Control: si el candado frenara siempre, el test de arriba pasaria por la razon equivocada."""
    modulo = _cargar()
    _candado_propio(tmp_path, modulo)
    inicio = time.monotonic()
    fd = modulo._tomar_el_turno()
    demora = time.monotonic() - inicio
    os.close(fd)
    assert demora < 1.0, f"tardo {demora:.1f}s sin nadie ocupando el turno"


def test_negativo_el_guard_no_puede_auto_detectarse(tmp_path):
    """El guard buscaba con `pgrep -f <nombre>` y se encontraba a si mismo.

    El shell que lo invoca lleva el nombre del script en su propia cmdline, asi que
    reportaba un validador vivo con cero validadores vivos. Se comprueba con la forma
    que SI discrimina: solo cuenta procesos cuyo nombre es python y que llevan el
    script como ARGUMENTO.
    """
    marca = subprocess.run(
        ["bash", "-c", "echo validar_suite_jarvis.py >/dev/null; pgrep -a -x python3"],
        capture_output=True, text=True,
    ).stdout
    con_el_script = [
        linea for linea in marca.splitlines()
        if any(a.endswith("validar_suite_jarvis.py") for a in linea.split()[1:])
    ]
    assert not [c for c in con_el_script if "bash" in c], (
        "un shell que solo NOMBRA el script no puede contar como validador vivo"
    )


def test_positivo_los_avisos_salen_aunque_la_salida_no_sea_una_terminal(tmp_path):
    """Sin flush el aviso queda en el buffer: el que espera no ve NADA y parece colgado."""
    import select

    modulo = _cargar()
    _candado_propio(tmp_path, modulo)
    fd = modulo._tomar_el_turno()
    hijo = _hijo_que_pide_el_turno(modulo._CANDADO, espera=25)
    leido = ""
    try:
        # Hay que leer MIENTRAS el hijo sigue esperando. Al terminar, Python vacia el
        # buffer solo, asi que un communicate() posterior ve el aviso aunque NO lleve
        # flush: ese test pasaba con el flush quitado (mutante sobreviviente, 4-sep).
        limite = time.monotonic() + 12
        while time.monotonic() < limite and "OTRO validador" not in leido:
            listos, _, _ = select.select([hijo.stdout], [], [], 0.5)
            if listos:
                leido += os.read(hijo.stdout.fileno(), 4096).decode(errors="replace")
        assert hijo.poll() is None, "el hijo termino en vez de esperar su turno"
    finally:
        os.close(fd)
        hijo.communicate(timeout=40)
    assert "OTRO validador esta corriendo" in leido, (
        "el aviso no salio MIENTRAS el hijo esperaba: sin flush queda en el buffer y "
        f"para quien mira parece colgado. Leido: {leido!r}"
    )


def test_negativo_el_mensaje_de_timeout_dice_la_espera_REAL(tmp_path):
    """Decia '30 min' con cualquier espera: un mensaje que miente manda a buscar donde no es."""
    modulo = _cargar()
    _candado_propio(tmp_path, modulo)
    fd = modulo._tomar_el_turno()
    hijo = _hijo_que_pide_el_turno(modulo._CANDADO, espera=6)
    try:
        _salida, error = hijo.communicate(timeout=40)
    finally:
        os.close(fd)
    assert "6s" in error, f"el mensaje no dice la espera real:\n{error}"
    assert "30 min" not in error, "vuelve a mentir sobre cuanto espero"


def test_control_no_vacuo():
    """Si estos tests no vieran el sujeto real, no probarian nada."""
    assert SUJETO.is_file(), SUJETO
    modulo = _cargar()
    assert "_tomar_el_turno" in dir(modulo)
    with pytest.raises(AssertionError):
        assert "esta funcion no existe" in dir(modulo)


def test_negativo_un_hijo_lanzado_por_exec_no_retiene_el_turno(tmp_path):
    """El turno se libera al soltarlo, aunque haya un hijo vivo lanzado mientras se tenia.

    HISTORIA, porque el test cambio de sentido: NEXUS refuto el manifiesto con un mutante
    que quitaba O_CLOEXEC y sobrevivia. Yo lo lei como "falta un test" y escribi este. Al
    medirlo salio que mi COMENTARIO era falso: en Python 3.4+ (PEP 446) los descriptores ya
    nacen no heredables, asi que O_CLOEXEC era redundante y el mutante era EQUIVALENTE --
    ningun test podia matarlo porque no cambiaba comportamiento. Se quito la bandera.

    El test se queda igual porque la PROPIEDAD que verifica sigue importando y no la cubria
    nadie: que un hijo vivo no retenga el turno del padre. Lo que cambio es de que depende
    -del default del interprete, no de una bandera que yo creia necesaria-. Ojo: contra
    fork() no protege ni una cosa ni la otra.
    """
    modulo = _cargar()
    _candado_propio(tmp_path, modulo)

    fd = modulo._tomar_el_turno()
    # close_fds=False es la herencia REAL: asi se comporta un hijo lanzado sin cuidado.
    durmiente = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(25)"], close_fds=False,
    )
    try:
        os.close(fd)  # el padre suelta el turno
        tercero = _hijo_que_pide_el_turno(modulo._CANDADO, espera=8)
        salida, error = tercero.communicate(timeout=30)
        assert tercero.returncode == 0, (
            "el turno quedo tomado despues de soltarlo: un hijo heredo el descriptor.\n"
            f"stderr={error.strip()!r}"
        )
        assert float(salida.strip().splitlines()[-1]) < 3.0, (
            "entro, pero tras esperar: el candado quedo retenido por la herencia"
        )
    finally:
        durmiente.kill()
        durmiente.wait(timeout=10)


def test_negativo_el_candado_no_conserva_el_pid_de_la_corrida_anterior(tmp_path):
    """Sin ftruncate quedan digitos del pid viejo pegados al nuevo.

    Segundo mutante vivo de NEXUS. El archivo del candado es lo unico que un humano mira
    cuando algo quedo trabado ('si nadie esta midiendo, borra ...'): si dice un pid que
    mezcla dos corridas, manda a investigar un proceso que no existe.
    """
    modulo = _cargar()
    _candado_propio(tmp_path, modulo)
    candado = Path(modulo._CANDADO)

    candado.write_text("999999999999\n")  # resto de una corrida anterior, mas largo
    fd = modulo._tomar_el_turno()
    contenido = candado.read_text()
    os.close(fd)

    assert contenido.strip() == str(os.getpid()), (
        f"el candado no quedo con el pid actual: {contenido!r} (esperaba {os.getpid()})"
    )
    assert "999999999" not in contenido, "sobrevivio el pid de la corrida anterior"
