"""Los dos frenos del arnes de mutacion deben EJERCERSE, no estar escritos.

Cada freno tiene su brazo positivo (frena lo que debe) y su CONTROL (deja pasar
lo legitimo). Sin el control, "arreglarlo" negandose a todo pasaria en verde y
el arnes seria inservible — que es el modo de fallar que ya me mordio hoy.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from seal_mutacion_segura import (  # noqa: E402
    ArnesInseguro, MARCA_GUARDA, verificar_entorno, verificar_mutacion, mutar,
)

FUENTE_CON_GUARDA = f"""#!/usr/bin/env bash
borrar() {{
  {MARCA_GUARDA}: sin esto, el find de abajo apunta a cualquier lado
  case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac
  find "$ruta" -mindepth 1 -delete
}}
saludar() {{ echo hola; }}
"""


# ── freno 2: no se muta una guarda ────────────────────────────────────────
def test_RECHAZA_mutar_la_linea_de_la_guarda():
    """El caso EXACTO del 7-sep: quitar el `case` que protege el find."""
    with pytest.raises(ArnesInseguro, match="GUARDA-DESTRUCTIVA"):
        verificar_mutacion(FUENTE_CON_GUARDA, 'case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac')


def test_CONTROL_deja_mutar_una_linea_normal():
    """Sin esto, un arnes que rechaza TODO pasaria por seguro."""
    verificar_mutacion(FUENTE_CON_GUARDA, "echo hola")  # no levanta


def test_rechaza_un_ancla_ambigua():
    """Un mutante que no sabe que muta no prueba nada (me paso hoy: `19 passed`
    cuatro veces sin haber tocado un byte)."""
    fuente = "x = 1\nx = 1\n"
    with pytest.raises(ArnesInseguro, match="2 veces"):
        verificar_mutacion(fuente, "x = 1")


def test_rechaza_un_ancla_inexistente():
    with pytest.raises(ArnesInseguro, match="no aparece"):
        verificar_mutacion(FUENTE_CON_GUARDA, "esto no esta")


# ── freno 1: no arranca con permiso de escritura donde vive el trabajo ────
def test_el_freno_de_entorno_SE_EJERCE(tmp_path):
    """Contra un directorio escribible: debe negarse. Se ejerce el permiso
    creando un temporal, no se lee `stat` (el camino entero manda)."""
    with pytest.raises(ArnesInseguro, match="ESCRIBIR"):
        verificar_entorno(raices=(str(tmp_path),))


def test_CONTROL_entorno_sin_escritura_pasa(tmp_path):
    solo_lectura = tmp_path / "ro"
    solo_lectura.mkdir()
    solo_lectura.chmod(0o500)
    try:
        verificar_entorno(raices=(str(solo_lectura),))  # no levanta
    finally:
        solo_lectura.chmod(0o700)


def test_CONTROL_raiz_inexistente_no_bloquea(tmp_path):
    """Una raiz que no existe no puede ser escribible; no debe frenar."""
    verificar_entorno(raices=(str(tmp_path / "no_existe"),))


def test_mutar_aplica_LOS_DOS_frenos(tmp_path, monkeypatch):
    """La via autorizada no puede saltarse ninguno."""
    monkeypatch.setattr("seal_mutacion_segura._RAICES_PROHIBIDAS", (str(tmp_path / "nada"),))
    salida = mutar(FUENTE_CON_GUARDA, "echo hola", "echo chau")
    assert "echo chau" in salida and "echo hola" not in salida
    with pytest.raises(ArnesInseguro):
        mutar(FUENTE_CON_GUARDA, 'case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac', ":")
