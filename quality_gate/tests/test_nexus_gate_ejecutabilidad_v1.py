"""Brazos de los dos defectos que solo aparecen cuando el gate EJECUTA.

QUE LOS GENERA (NEXUS, 9-sep-2026, medido sobre los 123 manifiestos del repo):

    76  empiezan sus comandos con `python3` a secas -> /usr/bin/python3, SIN pytest
    38  declaran coverage.source con un ARCHIVO     -> coverage no recolecta nada

Ninguno de los dos se veia, porque la ruta normal del gate es STATIC_OK y NO
EJECUTA. Estaban en verde por no haberse corrido nunca.

POR QUE EL ARREGLO VA EN EL GATE Y NO EN LOS 114 MANIFIESTOS, y esto es una
medicion, no una preferencia: `_review_digest` cubre el cuerpo del manifiesto,
asi que cambiarles el `argv` o el `source` INVALIDA sus firmas. Simulado en
memoria sobre alice-heartbeat-writer-v1: 8de2ea3e -> 057f20b5. Un arreglo
mecanico se convertiria en 114 re-revisiones.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from quality_gate import gate  # noqa: E402


# ─────────────── el interprete ───────────────

def test_qa_positive_un_python3_pelado_se_resuelve_al_interprete_del_gate():
    argv = gate._resolver_interprete(["python3", "-m", "pytest", "-q", "x.py"], RAIZ)
    assert argv[0] == gate._quality_python(RAIZ)
    assert argv[0] != "python3"
    assert argv[1:] == ["-m", "pytest", "-q", "x.py"], "el resto del comando no se toca"


def test_qa_negative_el_interprete_del_sistema_NO_tiene_pytest():
    """El control que da sentido a todo el arreglo. Si algun dia /usr/bin/python3
    tuviera pytest, este brazo se pone rojo y hay que revisar si el arreglo sigue
    haciendo falta — no borrarlo sin pensar."""
    r = subprocess.run(["/usr/bin/python3", "-c", "import pytest"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "No module named 'pytest'" in r.stderr


def test_qa_negative_una_ruta_EXPLICITA_no_se_pisa():
    """Una ruta escrita a mano puede ser deliberada -otro venv, otra version-.
    Solo se resuelve un nombre PELADO."""
    for explicito in ("/usr/bin/python3", "./python3", "/home/dadito/IA/seal-spark/.venv/bin/python3"):
        assert gate._resolver_interprete([explicito, "-m", "pytest"], RAIZ)[0] == explicito


def test_qa_control_un_argv_vacio_no_explota():
    assert gate._resolver_interprete([], RAIZ) == []


def test_qa_control_un_comando_que_no_es_python_no_se_toca():
    assert gate._resolver_interprete(["bash", "-n", "x.sh"], RAIZ)[0] == "bash"


def test_el_argv_REPORTADO_es_el_que_se_ejecuto(tmp_path):
    """Si el resultado dijera `python3` mientras corrio otro binario, el recibo
    mentiria sobre lo que se ejecuto: nadie podria reproducirlo."""
    (tmp_path / "t.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    filas = gate._execute_commands(tmp_path, [{
        "id": "x", "kind": "unit", "expected_exit": 0,
        "argv": ["python3", "-m", "pytest", "-q", "t.py"]}])
    assert filas[0].observed_exit == 0, filas[0].stderr
    assert filas[0].argv[0] == gate._quality_python(tmp_path)
    assert filas[0].argv[0] != "python3"


# ─────────────── la cobertura ───────────────

@pytest.fixture()
def proyecto(tmp_path):
    (tmp_path / "paq").mkdir()
    (tmp_path / "paq" / "sujeto.py").write_text(
        "def usada():\n    return 1\n\n\ndef no_usada():\n    return 2\n", encoding="utf-8")
    (tmp_path / "paq" / "otro.py").write_text("def x():\n    return 3\n", encoding="utf-8")
    (tmp_path / "t.py").write_text(
        "import sys; sys.path.insert(0, 'paq')\n"
        "from sujeto import usada\n"
        "def test_ok(): assert usada() == 1\n", encoding="utf-8")
    return tmp_path


def _medir(repo, source):
    return gate._measure_coverage(repo, {
        "source": source, "omit": ["*/site-packages/*"],
        "pytest_args": ["-q", "t.py"], "timeout": 120})


def test_qa_positive_un_ARCHIVO_como_source_ahora_SI_recolecta(proyecto):
    """El defecto exacto de los 38: antes esto reventaba con
    coverage_report_failed porque `--source` con un archivo no recolecta nada."""
    r = _medir(proyecto, ["paq/sujeto.py"])
    assert r["num_statements"] > 0, "no recolecto nada: volvio el defecto"
    assert r["percent"] > 0


def test_el_archivo_mide_SOLO_el_sujeto_y_no_el_directorio(proyecto):
    """No es solo que ande: mide MEJOR. Con el directorio entraba `otro.py`, que
    no es el sujeto, y diluia el porcentaje."""
    solo = _medir(proyecto, ["paq/sujeto.py"])
    dire = _medir(proyecto, ["paq"])
    assert solo["num_statements"] < dire["num_statements"], \
        "el archivo deberia medir menos sentencias que el directorio que lo contiene"
    assert solo["percent"] > dire["percent"]


def test_qa_positive_un_DIRECTORIO_sigue_funcionando_como_antes(proyecto):
    """Control de no-regresion: los 85 manifiestos que ya declaraban un
    directorio no pueden cambiar de comportamiento."""
    r = _medir(proyecto, ["paq"])
    assert r["num_statements"] > 0


def test_mezclar_directorio_y_archivo_reporta_SOLO_el_archivo(proyecto):
    """Version endurecida. La anterior solo pedia `num_statements > 0` y el
    mutante M7 -quitar el `--include` del PASO DE REPORTE- le SOBREVIVIA.

    Estuve a punto de declararlo equivalente razonando que `coverage run` ya
    filtra en la recoleccion. Lo medi en vez de suponerlo y es FALSO:

        con --include en el reporte:  ['paq/sujeto.py']              2 sentencias, 100%
        sin --include en el reporte:  ['paq/otro.py','sujeto.py']    4 sentencias,  50%

    El `--source=paq` recolecta el directorio entero; el `--include` del reporte
    es lo unico que acota el numero al sujeto. Sin el, la cobertura queda diluida
    por archivos que no son el sujeto — justo el defecto que este carril arregla.
    """
    r = _medir(proyecto, ["paq", "paq/sujeto.py"])
    solo = _medir(proyecto, ["paq/sujeto.py"])
    assert r["num_statements"] == solo["num_statements"], \
        "el reporte debe acotarse al archivo declarado, no al directorio que lo contiene"
    assert r["percent"] == solo["percent"]


# ─────────────── por que el arreglo va aca ───────────────

def test_tocar_el_argv_de_un_manifiesto_INVALIDA_su_firma():
    """La medicion que decide el diseño. Si esto fuera falso, lo correcto seria
    arreglar los 114 manifiestos y no el gate."""
    import copy
    p = RAIZ / "quality" / "manifests" / "alice-heartbeat-writer-v1.json"
    if not p.is_file():
        pytest.skip("el manifiesto de referencia no esta en este arbol")
    m = json.loads(p.read_text(encoding="utf-8"))
    antes = gate._review_digest(m)
    sim = copy.deepcopy(m)
    for c in sim.get("commands", []):
        a = c.get("argv") or []
        if a and a[0] == "python3":
            a[0] = "/otro/python3"
    assert gate._review_digest(sim) != antes, \
        "si cambiar el argv NO moviera el digest, convendria arreglar los manifiestos"
    assert gate._review_digest(json.loads(p.read_text(encoding="utf-8"))) == antes, \
        "la simulacion no debe tocar el archivo en disco"


# ─────────────── sujeto que coverage.py no puede medir ───────────────

def test_un_sujeto_SHELL_declara_que_no_aplica_en_vez_de_fallar_opaco(tmp_path):
    """5 manifiestos tienen un `.sh` como sujeto y daban `coverage_report_failed:`
    con stderr VACIO — un error que no dice su causa y hay que deducir.

    coverage.py mide Python; un script de shell no se puede medir con el, ni con
    --source ni con --include. Se DECLARA, no se finge un numero.
    """
    (tmp_path / "s.sh").write_text("#!/bin/bash\necho hola\n", encoding="utf-8")
    (tmp_path / "t.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    r = gate._measure_coverage(tmp_path, {
        "source": ["s.sh"], "omit": ["*/site-packages/*"],
        "pytest_args": ["-q", "t.py"], "timeout": 120})
    assert "no_aplica" in r, "debe declarar que no aplica, no devolver un porcentaje mudo"
    assert "no-Python" in r["no_aplica"]
    assert r["percent"] == 0.0


def test_qa_negative_un_sh_JUNTO_a_un_py_NO_desactiva_la_medicion(tmp_path):
    """La puerta de escape mas peligrosa: si bastara UN `.sh` en la lista para
    saltear la cobertura, cualquiera la apagaria agregando un script. Solo se
    declara no-aplicable cuando NADA de lo declarado es medible."""
    (tmp_path / "paq").mkdir()
    (tmp_path / "paq" / "sujeto.py").write_text("def usada():\n    return 1\n", encoding="utf-8")
    (tmp_path / "s.sh").write_text("#!/bin/bash\necho x\n", encoding="utf-8")
    (tmp_path / "t.py").write_text(
        "import sys; sys.path.insert(0, 'paq')\nfrom sujeto import usada\n"
        "def test_ok(): assert usada() == 1\n", encoding="utf-8")
    r = gate._measure_coverage(tmp_path, {
        "source": ["s.sh", "paq/sujeto.py"], "omit": ["*/site-packages/*"],
        "pytest_args": ["-q", "t.py"], "timeout": 120})
    assert "no_aplica" not in r, "con un sujeto Python presente la cobertura DEBE medirse"
    assert r["num_statements"] > 0
