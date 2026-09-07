"""Control del contrato del lanzador: aplicado al lanzador de v1 (alice_fresh.sh) DEBE fallar.

Si el contrato pasara contra v1 (skip-permissions, --name ALICE, SEAL_AGENT=ALICE, web_chat), el test
no discriminaría v1 de v2 y su verde no diría nada. Corre el módulo de contrato en un subproceso con
ALICE_V2_LAUNCHER apuntando a alice_fresh.sh y exige rc==1 con varias fallas, y además que el módulo
contra el sujeto real siga en verde (rc==0): las dos celdas, no una.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CONTRACT = HERE / "test_alice_v2_shadow_launcher_v1.py"
def _repo_launcher_v1() -> pathlib.Path:
    """Sube desde este archivo hasta encontrar alice_fresh.sh (NEXUS 00:18: un parents[N] fijo revienta con
    IndexError si el test se copia a otra profundidad, y el arnés reporta «error» que se lee como «muerto»)."""
    for padre in HERE.parents:
        cand = padre / "alice_fresh.sh"
        if cand.is_file():
            return cand
    raise RuntimeError("no encuentro alice_fresh.sh subiendo desde este archivo")


V1_LAUNCHER = _repo_launcher_v1()
V2_LAUNCHER = HERE.parent / "alice_v2_shadow.sh"


def _run(launcher: pathlib.Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ALICE_V2_LAUNCHER": str(launcher), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(CONTRACT)],
                          capture_output=True, text=True, env=env, timeout=120)


def test_el_contrato_falla_contra_el_lanzador_de_v1():
    assert V1_LAUNCHER.is_file(), f"no encuentro {V1_LAUNCHER}"
    rc = _run(V1_LAUNCHER)
    assert rc.returncode == 1, f"rc={rc.returncode}: el contrato NO discrimina v1\n{rc.stdout[-800:]}"
    # Las fallas esperadas son de fondo, no una sola casual: skip-permissions, nombre, identidad, canal.
    for marker in ("test_sin_dangerously_skip_permissions", "test_nombre_no_activa_el_singleton_guard_de_v1",
                   "test_identidad_de_instancia_no_la_de_v1", "test_salida_solo_al_canal_de_sombra"):
        # pytest imprime la ruta completa del módulo antes de `::`; se ancla en el nombre del test.
        assert any(line.startswith("FAILED") and line.rstrip().endswith(f"::{marker}") for line in rc.stdout.splitlines()), \
            f"esperaba que fallara {marker} contra v1\n{rc.stdout[-600:]}"


def test_el_contrato_pasa_contra_el_sujeto_real():
    rc = _run(V2_LAUNCHER)
    assert rc.returncode == 0, rc.stdout[-800:]
