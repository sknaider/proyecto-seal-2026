"""Botón «ADA Claude» en Studio — corre los tests node del helper puro ``claudeRoom.ts`` bajo pytest.

Carril `ada-sala-privada-user-room-20260908` (parte 2). El gate exige que cada comando del manifiesto sea pytest;
los tests del helper viven en ``seal-studio/frontend/tests/claudeRoom.test.mjs`` (node, sin JSX, sin red). Este
wrapper los ejecuta y exige que los 4 pasen; si node falta, el test se salta con motivo, no pasa en silencio.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE_TEST = REPO / "seal-studio" / "frontend" / "tests" / "claudeRoom.test.mjs"
HELPER = REPO / "seal-studio" / "frontend" / "src" / "app" / "v2" / "claudeRoom.ts"


def _correr() -> subprocess.CompletedProcess[str]:
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")
    return subprocess.run(["node", "--experimental-strip-types", "--test", str(NODE_TEST)],
                          capture_output=True, text=True, cwd=str(REPO), timeout=120)


def test_unit_el_helper_y_sus_tests_existen():
    assert HELPER.is_file() and NODE_TEST.is_file()
    assert "export function parseBodyRoom" in HELPER.read_text(encoding="utf-8")


def test_positivo_los_4_tests_node_del_boton_pasan():
    r = _correr()
    salida = r.stdout + r.stderr
    assert r.returncode == 0, salida[-2000:]
    m = re.search(r"^# pass (\d+)$", salida, re.M)
    assert m and int(m.group(1)) == 4, salida[-2000:]
    assert re.search(r"^# fail 0$", salida, re.M), salida[-2000:]


def test_negativo_un_helper_roto_hace_fallar_a_node(tmp_path):
    # Control del oráculo: el mismo runner con un helper que devuelve siempre null debe FALLAR, no pasar.
    roto = tmp_path / "src" / "app" / "v2"; roto.mkdir(parents=True)
    (roto / "claudeRoom.ts").write_text("export function parseBodyRoom(){return null}\n"
                                        "export function findBodyRoom(){return null}\n"
                                        "export function topicsWithoutBodyRooms(t){return t||[]}\n", encoding="utf-8")
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "claudeRoom.test.mjs").write_text(NODE_TEST.read_text(encoding="utf-8"), encoding="utf-8")
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")
    r = subprocess.run(["node", "--experimental-strip-types", "--test", str(tests / "claudeRoom.test.mjs")],
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=120)
    assert r.returncode != 0
