#!/usr/bin/env bash
# Delivery por EFECTO de la regla "un subagente NO lanza subagentes" (IBM Bob §5).
#
# Por qué no alcanza con la suite (ADA, 4-sep-2026): pytest prueba que la DECISIÓN es
# correcta dentro de un proceso. Lo que hay que demostrar acá es otra cosa — que un
# spawn REAL, con su `subprocess.run` y su worker de verdad, se niega cuando lo pide un
# subagente y sigue funcionando cuando lo pide el agente principal.
#
# Los dos brazos son obligatorios. Sin el ARM2, un guard que negara SIEMPRE pasaría el
# ARM1 y el delivery diría "verificado" sobre un sistema roto: es la diferencia entre
# medir que frena y medir que DISCRIMINA.
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
cd "$REPO"

echo "== ARM1: un SUBAGENTE (profundidad 1) intenta lanzar -> debe NEGARSE =="
SEAL_SUBAGENT_DEPTH=1 "$PY" - <<'PY'
import sys, tempfile
sys.path.insert(0, "/home/dadito/IA/proyecto-seal")
from tools.agents.subagent_spawner import SubAgentSpawner, SubAgentNestingError
with tempfile.TemporaryDirectory() as tmp:
    try:
        SubAgentSpawner(work_dir=tmp).spawn(task="intento anidar", agent="ADA", timeout=30)
    except SubAgentNestingError as e:
        print(f"ARM1 OK: negado -> {e}")
        raise SystemExit(0)
print("ARM1 FALLO: un subagente pudo lanzar otro subagente")
raise SystemExit(1)
PY

echo
echo "== ARM2 (control no vacuo): el agente PRINCIPAL lanza -> debe FUNCIONAR =="
env -u SEAL_SUBAGENT_DEPTH "$PY" - <<'PY'
import sys, tempfile
sys.path.insert(0, "/home/dadito/IA/proyecto-seal")
from tools.agents.subagent_spawner import SubAgentSpawner
with tempfile.TemporaryDirectory() as tmp:
    r = SubAgentSpawner(work_dir=tmp).spawn(task="trabajo normal", agent="ADA", timeout=60)
    if not r.success:
        print(f"ARM2 FALLO: el principal no pudo lanzar -> {r.error}")
        raise SystemExit(1)
    print("ARM2 OK: el agente principal lanza normalmente")
PY

echo
echo "== ARM3: la marca VIAJA al proceso hijo (es lo que hace cumplir la regla) =="
env -u SEAL_SUBAGENT_DEPTH "$PY" - <<'PY'
import pathlib, sys, tempfile
sys.path.insert(0, "/home/dadito/IA/proyecto-seal")
from tools.agents.subagent_spawner import SubAgentSpawner
with tempfile.TemporaryDirectory() as tmp:
    w = pathlib.Path(tmp) / "eco.py"
    w.write_text("import os\nprint(os.environ.get('SEAL_SUBAGENT_DEPTH', 'AUSENTE'))\n")
    r = SubAgentSpawner(work_dir=tmp).spawn(task="deci tu profundidad", agent="ADA",
                                            worker_script=str(w), timeout=30)
    got = (r.output or "").strip()
    if got != "1":
        print(f"ARM3 FALLO: el hijo recibio {got!r}; sin la marca el arbol queda abierto")
        raise SystemExit(1)
    print("ARM3 OK: el hijo arranca con SEAL_SUBAGENT_DEPTH=1")
PY

echo
echo "DELIVERY OK: 3/3 brazos"
