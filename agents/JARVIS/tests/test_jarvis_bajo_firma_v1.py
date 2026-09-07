#!/usr/bin/env python3
"""Suite de `tools/seal_bajo_firma.py`.

SUJETO INTERCAMBIABLE A PROPOSITO. La ruta sale de `SEAL_BAJO_FIRMA_PATH`, con
el archivo del repo como default. Un test que CABLEA la ruta de su sujeto no es
revisable por un tercero: el override del revisor queda inerte y el mutante mide
produccion dos veces, dando el mismo verde que el original.
    (ficha: a_test_that_hardcodes_its_subject_is_not_reviewable_20260831)

    SEAL_BAJO_FIRMA_PATH=/tmp/mutante.py python3 -m pytest <este archivo>

CADA CASO TRAE SU CONTRA-CASO. Un assert que solo comprueba lo que DEBE estar
pasa igual si el predicado devuelve siempre True. Donde afirmo "aparece", afirmo
tambien "y esto otro NO aparece".
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

_DEFECTO = pathlib.Path(__file__).resolve().parents[3] / "tools" / "seal_bajo_firma.py"
SUJETO = pathlib.Path(os.environ.get("SEAL_BAJO_FIRMA_PATH", str(_DEFECTO)))


@pytest.fixture(scope="module")
def mod():
    if not SUJETO.is_file():
        pytest.fail(f"sujeto ausente: {SUJETO}   (un sujeto que falta no es un test que pasa)")
    spec = importlib.util.spec_from_file_location("sujeto_bajo_firma", SUJETO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _manifiesto(d: pathlib.Path, nombre, *, status, subjects=(), tests=(), revisor="NEXUS"):
    (d / nombre).write_text(json.dumps({
        "review": {"status": status},
        "independent_reviewer": revisor,
        "subjects": list(subjects),
        "tests": list(tests),
    }), encoding="utf-8")


# --------------------------------------------------------------- indice

def test_indice_toma_approved_y_DESCARTA_pending(mod, tmp_path, monkeypatch):
    """El caso que refutaria: si tomara todo, un manifiesto pending tambien saldria."""
    monkeypatch.setattr(mod, "MANIFESTS", tmp_path)
    _manifiesto(tmp_path, "aprobado.json", status="approved", subjects=["src/firmado.py"])
    _manifiesto(tmp_path, "pendiente.json", status="pending", subjects=["src/pendiente.py"])

    idx = mod.indice_de_firmas()
    assert "src/firmado.py" in idx, "un subject aprobado tiene que estar en el indice"
    assert "src/pendiente.py" not in idx, (
        "CONTRA-CASO: un manifiesto `pending` NO firma nada. Si aparece, el indice "
        "no filtra por status y todo el guard sobre-avisa.")


def test_indice_incluye_los_TESTS_no_solo_los_subjects(mod, tmp_path, monkeypatch):
    """Esta es la que me habria salvado el 29-ago: rompi un archivo que era `test`."""
    monkeypatch.setattr(mod, "MANIFESTS", tmp_path)
    _manifiesto(tmp_path, "m.json", status="approved",
                subjects=["src/s.py"], tests=["tests/t.py"])
    idx = mod.indice_de_firmas()
    assert "tests/t.py" in idx, (
        "un archivo listado como TEST de una unidad firmada tambien esta bajo firma")
    assert "tests/otro.py" not in idx, "CONTRA-CASO: no inventa rutas"


def test_manifiesto_ilegible_avisa_y_no_tumba(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "MANIFESTS", tmp_path)
    (tmp_path / "roto.json").write_text("{ esto no es json", encoding="utf-8")
    _manifiesto(tmp_path, "sano.json", status="approved", subjects=["src/s.py"])

    idx = mod.indice_de_firmas()
    assert "src/s.py" in idx, "un manifiesto roto no puede cegar a los sanos"
    assert "AVISO" in capsys.readouterr().err, (
        "un archivo ilegible tiene que AVISAR: un descarte mudo se lee como ausencia")


# ----------------------------------------------------------------- main

def test_main_distingue_bajo_firma_de_libre(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MANIFESTS", tmp_path)
    _manifiesto(tmp_path, "m.json", status="approved", subjects=["src/firmado.py"])

    assert mod.main(["src/firmado.py"]) == 1, "bajo firma -> rc=1"
    assert mod.main(["src/cualquiera.py"]) == 0, "CONTRA-CASO: libre -> rc=0"


def test_cero_firmas_devuelve_0_pero_lo_DECLARA(mod, tmp_path, monkeypatch, capsys):
    """El rc=0 mas peligroso del programa: 'no esta bajo firma' cuando NO HAY firmas.

    Es un cero por AUSENCIA DEL UNIVERSO, no por ausencia del objeto. Si algun dia
    alguien lo silencia, este test se pone rojo.
    """
    monkeypatch.setattr(mod, "MANIFESTS", tmp_path)
    _manifiesto(tmp_path, "solo_pending.json", status="pending", subjects=["src/x.py"])

    assert mod.main(["src/x.py"]) == 0
    salida = capsys.readouterr().out
    assert "NO HAY NINGUN manifiesto" in salida and "no permiso" in salida, (
        "un cero sin firmas TIENE que decir que es ausencia de firmas, no permiso")


def test_sin_argumentos_es_error_de_uso(mod):
    assert mod.main([]) == 2


# --------------------------------------------------------------- staged

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t"); _git(d, "config", "user.name", "t")
    return d


def test_staged_ve_el_nombre_VIEJO_de_un_renombre(mod, repo, monkeypatch):
    """El defecto que fallaba ABIERTO: `--name-only` emite solo el nombre NUEVO,
    y el VIEJO es el que figura en el manifiesto. Renombrar un archivo firmado
    pasaba sin aviso."""
    (repo / "viejo.py").write_text("x = 1\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    _git(repo, "mv", "viejo.py", "nuevo.py")

    monkeypatch.setattr(mod, "RAIZ", repo)
    rutas = mod.staged()
    assert "viejo.py" in rutas, (
        "sin el nombre VIEJO, renombrar un archivo firmado esquiva el guard entero")
    assert "nuevo.py" in rutas, "y el nuevo tambien: es un archivo sin cubrir"


def test_staged_no_escapa_rutas_no_ascii(mod, repo, monkeypatch):
    """Sin `-z`, git quotea: arbol_n.py -> "\\303\\241rbol...". Esa cadena no coincide
    con ninguna ruta del manifiesto y el archivo firmado se reporta LIBRE."""
    nombre = "árbol_ñ.py"
    (repo / nombre).write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")

    monkeypatch.setattr(mod, "RAIZ", repo)
    rutas = mod.staged()
    assert nombre in rutas, f"la ruta real tiene que salir sin escapar; salio: {rutas}"
    assert not any("\\3" in r for r in rutas), "CONTRA-CASO: nada quoteado por git"


def test_staged_ve_un_TYPECHANGE(mod, repo, monkeypatch):
    """Tercer defecto que fallaba ABIERTO, hallado por NEXUS y verificado por FABLE.

    `--diff-filter=ACMRD` EXCLUYE `T`. Convertir un archivo firmado en symlink lo
    sacaba del listado y `bajo_firma` lo reportaba `libre`. Medido: sin filtro git
    emite `T|firmado.sh`; con `ACMRD` no emite NADA.

    ALCANCE, y lo digo porque mi primera version del comentario mentia: esto
    comprueba el EFECTO para `T`, asi que muere con `ACMRD` pero SOBREVIVE a
    `ACMRDT` -- medido, 11 passed. El principio "no enumeres estados" no lo puede
    defender un test de efecto: hace falta el estructural de abajo.
    """
    (repo / "firmado.sh").write_text("x\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    (repo / "firmado.sh").unlink()
    (repo / "firmado.sh").symlink_to("/etc/hostname")
    _git(repo, "add", "firmado.sh")

    monkeypatch.setattr(mod, "RAIZ", repo)
    assert "firmado.sh" in mod.staged(), (
        "un typechange (archivo -> symlink) tiene que aparecer; si no, convertir "
        "un archivo firmado en symlink esquiva el guard entero")


def test_staged_NO_lleva_ningun_diff_filter(mod, repo, monkeypatch):
    """El unico test ESTRUCTURAL de la suite, y esta justificado.

    Un test de efecto solo puede cubrir los estados que a uno se le ocurren; el
    contrato aca es precisamente "no enumeres estados". `ACMRD` dejaba afuera `T`;
    `ACMRDT` deja afuera `U` y lo que git invente. La unica forma de defender el
    principio es mirar el argv.

    Se pone rojo con CUALQUIER acote, ancho o angosto -- que es lo que el test de
    efecto de arriba no puede hacer.
    """
    visto = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def espia(argv, **kw):
        visto["argv"] = argv
        return _R()

    monkeypatch.setattr(mod, "RAIZ", repo)
    monkeypatch.setattr(mod.subprocess, "run", espia)
    mod.staged()
    assert visto["argv"], "el espia no capturo la invocacion: el test no midio nada"
    assert not any(str(a).startswith("--diff-filter") for a in visto["argv"]), (
        f"acote reintroducido: {visto['argv']}. El default los lista TODOS; "
        "ampliar la lista solo mueve el punto ciego al proximo estado.")


def test_staged_aborta_si_git_falla_y_NO_devuelve_vacio(mod, tmp_path, monkeypatch):
    """M6 de FABLE. El camino de error tiene que ABORTAR, no devolver [].

    Una lista vacia se lee como 'no hay nada staged' = permiso. Es el mismo
    fallar-abierto que los otros tres, un nivel mas arriba: no en QUE pide, sino
    en que hace cuando no puede pedir.
    """
    monkeypatch.setattr(mod, "RAIZ", tmp_path)   # no es un repo git
    with pytest.raises(SystemExit) as e:
        mod.staged()
    assert e.value.code == 2, "git roto -> rc=2, nunca una lista vacia"


def test_staged_vacio_no_es_un_error(mod, repo, monkeypatch):
    monkeypatch.setattr(mod, "RAIZ", repo)
    assert mod.staged() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
