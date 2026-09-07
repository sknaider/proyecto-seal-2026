"""El detector de codigo fantasma debe DISCRIMINAR, no contar cualquier cosa.

POR QUE EXISTE: el 7-sep la maquina mostraba 126 servicios activos y 0 failed
mientras 17 procesos corrian desde ejecutables borrados —incluidos `claude` y
`codex`—. `active` no distingue un proceso sano de uno irrecuperable. Este
detector agrega esa pregunta; los tests fijan que la responda BIEN.

El brazo que mas importa es el CONTROL: ADA corrigio el 7-sep que `(deleted)` NO
prueba codigo perdido —un proceso puede conservar el inodo viejo aunque la ruta
ya este restaurada—. Un detector que cuente esos casos inunda de falsos
positivos y se vuelve ruido, que es como muere un vigilante.
"""
import os
import pathlib
import subprocess
import sys
import time

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from seal_codigo_fantasma import revisar, barrer, _borrado  # noqa: E402


def _proceso_sobre_copia(tmp_path, borrar: bool):
    """Arranca un proceso desde una COPIA del interprete y opcionalmente la borra.

    Es el unico modo honesto de probarlo: fabricar el estado real —un proceso
    vivo cuyo ejecutable ya no esta— en vez de simularlo con un mock.
    """
    copia = tmp_path / "python_copia"
    copia.write_bytes(pathlib.Path(sys.executable).read_bytes())
    copia.chmod(0o755)
    p = subprocess.Popen([str(copia), "-c", "import time; time.sleep(30)"])
    for _ in range(50):                      # esperar a que /proc/<pid>/exe exista
        if pathlib.Path(f"/proc/{p.pid}/exe").exists():
            break
        time.sleep(0.1)
    if borrar:
        copia.unlink()
    return p


def test_DETECTA_un_proceso_cuyo_ejecutable_fue_borrado(tmp_path):
    """El caso del 7-sep, fabricado de verdad."""
    p = _proceso_sobre_copia(tmp_path, borrar=True)
    try:
        r = revisar(str(p.pid))
        assert r is not None, "no vio un proceso con su ejecutable borrado"
        assert any("python_copia" in f for f in r["faltan"])
    finally:
        p.kill(); p.wait()


def test_CONTROL_un_proceso_normal_NO_es_fantasma(tmp_path):
    """Sin este brazo, un detector que dice que si a todo pasaria en verde."""
    p = _proceso_sobre_copia(tmp_path, borrar=False)
    try:
        assert revisar(str(p.pid)) is None, "marco como fantasma un proceso sano"
    finally:
        p.kill(); p.wait()


def test_CONTROL_ruta_restaurada_NO_cuenta_como_fantasma(tmp_path):
    """La correccion de ADA: `(deleted)` no prueba codigo perdido.

    Se borra la copia y se vuelve a crear en la MISMA ruta: el proceso sigue
    con el inodo viejo (`(deleted)`) pero el codigo SI esta en disco.
    """
    copia = tmp_path / "python_copia"
    p = _proceso_sobre_copia(tmp_path, borrar=True)
    try:
        copia.write_bytes(pathlib.Path(sys.executable).read_bytes())
        copia.chmod(0o755)
        assert os.readlink(f"/proc/{p.pid}/exe").endswith("(deleted)")
        assert revisar(str(p.pid)) is None, (
            "conto como fantasma una ruta ya restaurada: eso inunda de falsos positivos"
        )
    finally:
        p.kill(); p.wait()


def test_el_ayudante_reconoce_la_marca_y_solo_esa():
    assert _borrado("/x/y (deleted)") == "/x/y"
    assert _borrado("/x/y") is None
    assert _borrado(None) is None
    assert _borrado("/x/y (deleted) mas") is None   # no confundir con un nombre raro


def test_un_pid_que_no_existe_no_rompe():
    assert revisar("999999999") is None


def test_barrer_devuelve_la_METRICA_y_su_detalle():
    r = barrer()
    assert set(r) == {"fantasmas", "detalle"}
    assert r["fantasmas"] == len(r["detalle"])
    assert isinstance(r["fantasmas"], int)


def test_el_codigo_de_salida_ES_la_metrica(tmp_path):
    """0 fantasmas -> exit 0. Es lo que permite usarlo desde un timer."""
    r = subprocess.run([sys.executable, str(RAIZ / "seal_codigo_fantasma.py")],
                       capture_output=True, text=True, timeout=120)
    import json
    d = json.loads(r.stdout)
    assert r.returncode == (1 if d["fantasmas"] else 0)
