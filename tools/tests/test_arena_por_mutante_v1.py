"""Brazos del arnés de mutación.

Cada brazo nace de un defecto REAL: cuatro que cometí ejecutando el 7-sep, y cinco que ADA
encontró leyendo el código en tres pasadas, sin correr nada. Los suyos son los que yo no
podía ver: había probado los casos que se me ocurrían.
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tools"))
import arena_por_mutante as arn  # noqa: E402

ARNES = RAIZ / "tools" / "arena_por_mutante.sh"


def _repo_minimo(tmp_path):
    """Un repo git MINIMO y desechable, con su propio seal_arena.sh y el arnes copiado.

    ADA (21:13): las arenas del helper nacian en /tmp/seal-arena-min-* -FUERA de tmp_path
    y sin limpieza-, y el brazo de portabilidad corria disk-alert desde el repositorio
    COMPARTIDO. Las dos cosas son dependencias del entorno en un test que existe
    justamente para probar aislamiento. Ahora todo vive bajo el tmp_path que pytest
    administra y borra.
    """
    repo = tmp_path / "repo"
    (repo / "tools" / "tests").mkdir(parents=True)
    arenas = tmp_path / "arenas"
    arenas.mkdir()
    (repo / "tools" / "sujeto.py").write_text("VALOR = 4\n", encoding="utf-8")
    (repo / "tools" / "tests" / "test_sujeto.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import sujeto\n"
        "def test_vale_cuatro():\n    assert sujeto.VALOR == 4\n"
        "def test_es_par():\n    assert sujeto.VALOR % 2 == 0\n",
        encoding="utf-8")
    (repo / "tools" / "seal_arena.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nshift\n'
        f'a=$(mktemp -d {arenas}/arena-XXXXXX)\n'
        'tree=$(git write-tree)\ngit archive "$tree" -- "$@" | tar -x -C "$a"\n'
        'echo "$a"\n', encoding="utf-8")
    for f in ("arena_por_mutante.py", "arena_por_mutante.sh"):
        (repo / "tools" / f).write_text((RAIZ / "tools" / f).read_text(encoding="utf-8"),
                                        encoding="utf-8")
        (repo / "tools" / f).chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def _spec_minimo(tmp_path, mutantes):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "rutas": ["tools"],
        "sujetos_y_tests": ["tools/sujeto.py", "tools/tests/test_sujeto.py"],
        "harness": ["tools/tests/test_sujeto.py"],
        "mutantes": mutantes,
    }), encoding="utf-8")
    return spec


# ── qa_positive ────────────────────────────────────────────────────────────
def test_qa_positive_un_fallo_de_prueba_es_una_MUERTE():
    assert arn.clasifica(1, "1 failed, 17 passed in 0.2s", 18) == "KILLED"


def test_qa_positive_control_verde_es_SUPERVIVENCIA():
    assert arn.clasifica(0, "18 passed in 0.2s", 18) == "SURVIVED"


def test_qa_negative_un_verde_con_MENOS_brazos_no_es_supervivencia():
    """ADA, 21:10 — un mutante que hace desaparecer tests deja el resto en verde.

    Declararlo SURVIVED afirma que el mutante no fue detectado, cuando lo que pasó es
    que la corrida dejó de ser comparable con el control.
    """
    assert arn.clasifica(0, "5 passed in 0.1s", 18) == "INVALIDA"
    assert arn.clasifica(0, "no tests ran in 0.1s", 18) == "INVALIDA"


def test_qa_positive_fallan_TODOS_los_brazos_y_sigue_siendo_muerte():
    """ADA, 21:00 — lo dedujo LEYENDO. Mi regex exigía que existiera 'N passed', así que
    una corrida donde fallan los 18 —perfectamente válida— la habría tirado como inválida.

    Yo no lo vi habiéndolo ejecutado, porque nunca produje ese caso.
    """
    assert arn.clasifica(1, "18 failed in 0.3s", 18) == "KILLED"


# ── qa_negative ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rc,resumen", [
    (2, "1 failed, 17 passed in 0.2s"),   # interrupcion
    (3, "internal error"),                # error interno de pytest
    (4, "usage error"),                   # uso incorrecto
    (5, "no tests ran in 0.1s"),          # no colecto nada
])
def test_qa_negative_un_rc_que_no_es_1_NUNCA_es_una_muerte(rc, resumen):
    """ADA, 20:56 y 21:00: pytest devuelve rc!=0 tambien cuando NO PUDO CORRER.

    Contar eso como 'mutante muerto' certifica que el test detecto algo cuando en
    realidad nunca se ejecuto. Es el defecto que mas caro sale: convierte un arnés
    roto en un arnés que siempre aprueba.
    """
    assert arn.clasifica(rc, resumen, 18) == "INVALIDA"


def test_qa_negative_si_corrieron_MENOS_brazos_la_corrida_es_invalida():
    """Un mutante que hace desaparecer brazos no los 'mata': impide que existan."""
    assert arn.clasifica(1, "1 failed, 5 passed in 0.2s", 18) == "INVALIDA"


def test_qa_negative_una_ruta_que_sale_del_repo_se_rechaza_ANTES_de_nada(tmp_path):
    """ADA, 21:00 — el orden importaba: yo validaba DESPUÉS de hashear, así que una ruta
    inválida moría con un FileNotFoundError crudo en vez de con el motivo real.

    Mi prueba anterior 'pasaba' por casualidad: ../../../etc/hosts existe.
    """
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "rutas": ["tools"], "sujetos_y_tests": ["../../../etc/hosts"],
        "harness": ["tools/tests/test_arena_por_mutante_v1.py"],
        "mutantes": [{"id": "x", "subject": "../../../etc/hosts", "ancla": "a", "reemplazo": "b"}],
    }), encoding="utf-8")
    r = subprocess.run(["bash", str(ARNES), str(spec)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 0
    assert "ruta invalida" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-400:]


# ── qa_control ─────────────────────────────────────────────────────────────
def test_qa_control_el_arnes_RESUELVE_EL_REPO_desde_cualquier_directorio(tmp_path):
    """20:47 — `git rev-parse --show-toplevel` dependia del CWD y el arnes moria fuera del
    arbol. ADA (21:10): mi primera version lo llamaba SIN spec, asi que moria en `${1:?}`
    antes de resolver el repositorio. ADA (21:13): y la segunda corria disk-alert desde el
    repositorio COMPARTIDO, con lo cual el brazo dependia del arbol real.

    Ahora: repo MINIMO propio, launcher invocado desde OTRO directorio, rc == 0 exigido.
    """
    repo = _repo_minimo(tmp_path)
    spec = _spec_minimo(tmp_path, [])
    ajeno = tmp_path / "otro"
    ajeno.mkdir()
    r = subprocess.run(["bash", str(repo / "tools" / "arena_por_mutante.sh"), str(spec)],
                       capture_output=True, text=True, cwd=str(ajeno), timeout=600)
    salida = r.stdout + r.stderr
    assert r.returncode == 0, salida[-500:]
    assert "no es un repositorio git" not in salida, salida[-400:]
    assert "[base] control VERDE" in r.stdout, salida[-400:]


def test_qa_control_el_diagnostico_conserva_archivo_linea_y_stderr(monkeypatch, tmp_path):
    """ADA, 21:00 y 21:10 — mi versión anterior INSPECCIONABA EL FUENTE buscando que no
    reapareciera `startswith("E ")`: un brazo de presencia de texto, el tipo más débil.

    Éste ejerce `corre()` con un proceso simulado cuyo diagnóstico viene precedido por
    `archivo:línea` y que además escribe en stderr, y exige que ambos se conserven.
    """
    class Falso:
        returncode = 1
        stdout = ("tools/x.py:41: AssertionError: el bloque perdio una entrada\n"
                  "FAILED tools/x.py::test_algo\n"
                  "1 failed, 17 passed in 0.2s")
        stderr = "Traceback interno del runner\nRuntimeError: algo del entorno"

    monkeypatch.setattr(arn.subprocess, "run", lambda *a, **k: Falso())
    rc, resumen, nodeids, diag = arn.corre(tmp_path, ["x"])

    assert rc == 1 and resumen == "1 failed, 17 passed in 0.2s"
    assert nodeids == ["tools/x.py::test_algo"]
    unido = "\n".join(diag["stdout_cola"])
    assert "tools/x.py:41: AssertionError: el bloque perdio una entrada" in unido, (
        "se perdio un diagnostico precedido por archivo:linea")
    assert "RuntimeError: algo del entorno" in "\n".join(diag["stderr_cola"]), (
        "se perdio el stderr")


def test_qa_control_el_test_NO_es_vacuo():
    """Si `clasifica` devolviera siempre lo mismo, todos los brazos de arriba pasarían
    por la razón equivocada."""
    veredictos = {
        arn.clasifica(0, "18 passed", 18),
        arn.clasifica(1, "1 failed, 17 passed", 18),
        arn.clasifica(2, "1 failed, 17 passed", 18),
    }
    assert veredictos == {"SURVIVED", "KILLED", "INVALIDA"}


def test_qa_control_DE_PUNTA_A_PUNTA_sobre_un_repo_minimo(tmp_path):
    """ADA (21:10): el comando de entrega llamaba a `clasifica()` — ejercita la funcion, no
    el flujo. Y para evitar la circularidad no hace falta otro motor de mutacion: alcanza
    con un repo MINIMO desechable y resultados esperados FIJOS, comprobados por pytest sin
    volver a llamar al clasificador del arnes.

    Nunca sobre rutas productivas: repo y arenas viven bajo tmp_path (ADA, 21:13).
    """
    repo = _repo_minimo(tmp_path)
    spec = _spec_minimo(tmp_path, [
        {"id": "detectado", "subject": "tools/sujeto.py",
         "ancla": "VALOR = 4", "reemplazo": "VALOR = 5"},
        {"id": "no-detectado", "subject": "tools/sujeto.py",
         "ancla": "VALOR = 4", "reemplazo": "VALOR = 4  # sin efecto"},
        {"id": "corrida-rota", "subject": "tools/sujeto.py",
         "ancla": "VALOR = 4", "reemplazo": "VALOR = ((("},
    ])
    r = subprocess.run([sys.executable, str(repo / "tools" / "arena_por_mutante.py"),
                        str(spec), str(repo)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-600:]
    salida = json.loads(r.stdout.strip().splitlines()[-1])
    obtenido = {m["id"]: m["result"] for m in salida["mutantes"]}
    assert obtenido == {"detectado": "KILLED",
                        "no-detectado": "SURVIVED",
                        "corrida-rota": "INVALIDA"}, obtenido
    muerto = next(m for m in salida["mutantes"] if m["id"] == "detectado")
    assert any("test_vale_cuatro" in n for n in muerto["nodeids"]), muerto["nodeids"]
    # Las arenas se comprueban por las rutas que el PROPIO arnes reporta, no barriendo
    # /tmp. Mi version anterior media el delta global, y ADA (21:15) marco que eso no
    # identifica al creador: una arena de otro agente durante el test lo haria fallar.
    # Antes de eso use una asercion absoluta, que fallaba por restos de corridas viejas.
    # Las tres versiones median el directorio compartido; sólo ésta mide lo que hicimos.
    reportadas = re.findall(r"arena=(\S+)", r.stdout)
    # ADA (21:16): contar MENCIONES no es contar arenas —la misma ruta repetida cuatro
    # veces pasaba—, y `startswith` compara TEXTO, no pertenencia de rutas resueltas.
    # Se exigen cuatro rutas DISTINTAS y se comprueba pertenencia con rutas resueltas.
    distintas = {pathlib.Path(a).resolve() for a in reportadas}
    assert len(distintas) == 4, (
        f"esperaba 4 arenas DISTINTAS (1 base + 3 mutantes); hubo {len(distintas)} "
        f"sobre {len(reportadas)} menciones: {sorted(map(str, distintas))}")
    # ADA (21:17): `resolve()` sin modo estricto acepta rutas INEXISTENTES, asi que
    # cuatro nombres inventados dentro de tmp_path habrian pasado. Se exige que sean
    # directorios REALES y que coincidan con el inventario de tmp_path/arenas.
    arenas_dir = (tmp_path / "arenas").resolve()
    # ADA (21:18): al atar al inventario BORRE la comprobacion de pertenencia y eso fue
    # una regresion mia. Un enlace dentro de `arenas` puede resolver AFUERA: las dos
    # colecciones resolverian al mismo destino y la igualdad pasaria igual. Se conservan
    # las dos comprobaciones, que miden cosas distintas.
    # ADA (21:19): la pertenencia va contra `arenas_dir`, NO contra tmp_path. Un enlace
    # hacia un directorio HERMANO dentro de tmp_path -por ejemplo tmp_path/repo- seguia
    # pasando: esta dentro del temporal pero no es una arena.
    fuera = [str(a) for a in distintas if not a.is_relative_to(arenas_dir)]
    assert not fuera, f"el arnes reporto arenas fuera de {arenas_dir}: {fuera}"
    no_dir = [str(a) for a in distintas if not a.is_dir()]
    assert not no_dir, f"rutas reportadas que no son directorios existentes: {no_dir}"
    inventario = {d.resolve() for d in arenas_dir.iterdir() if d.is_dir()}
    assert distintas == inventario, (
        "las arenas reportadas no coinciden con el inventario de "
        f"{arenas_dir}: reportadas={sorted(map(str, distintas))} "
        f"inventario={sorted(map(str, inventario))}")
