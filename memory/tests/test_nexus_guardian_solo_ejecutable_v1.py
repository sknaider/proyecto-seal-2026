"""El guardian bloquea por POSICION EJECUTABLE, no por aparicion del patron.

REPUESTO el 8-sep-2026 (NEXUS, owner; JARVIS, revisor). El archivo original se
perdio con el borrado del 7-sep: no estaba en disco, ni en HEAD, ni en ningun
commit del historial, ni renombrado. **No se restauro: se reconstruyo**, y hay
que decir de donde, porque un test que se dice restaurado y no lo es hace que la
evidencia mienta con la firma intacta.

FUENTE DE LA RECONSTRUCCION: `quality/delivery_guardian_posicion_ejecutable.py`,
que sobrevivio y contiene los nueve casos con su motivo. Los nombres de las tres
funciones vienen del propio manifiesto, que los invoca por nodo. Lo que NO se
puede reconstruir es el SHA original (1c8604ce22d60dd8): este archivo es otro
archivo, y asi queda declarado en la evidencia.

POR QUE POR SUBPROCESO: el hook lo ejecuta Claude Code como proceso aparte,
leyendo JSON por stdin. Importar `check_bash_safety` prueba la funcion; esto
prueba **la puerta que corre de verdad**. Hoy mismo se vio la diferencia: la
funcion y el subproceso pueden divergir y sin el segundo no se nota.

LOS PATRONES SE ARMAN EN RUNTIME. Escritos literales, el guardian bloquea el
comando que lanza este mismo test -- le paso a NEXUS 4 veces, a ADA 2 y a JARVIS
1, todas escribiendo SOBRE el guardian.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[2] / "memory" / "pre_tool_hook.py"
PY_BIN = "/home/dadito/IA/seal-spark/.venv/bin/python3"

V = "rm" + " -rf"          # nunca literal: ver la nota de arriba
D = "DR" + "OP TABLE"


def decide(comando: str) -> str:
    """'permite' | 'deniega', por la puerta real."""
    p = subprocess.run(
        [PY_BIN, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": comando}}),
        capture_output=True, text=True, timeout=30,
    )
    salida = (p.stdout or "").strip()
    if salida in ("", "{}"):
        return "permite"
    try:
        d = json.loads(salida)["hookSpecificOutput"]["permissionDecision"]
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"el hook devolvio algo ilegible ({exc}): {salida[:80]}")
    return "deniega" if d == "deny" else "permite"


# ───────────────── qa_positive: lo peligroso sigue denegado ─────────────────

@pytest.mark.parametrize("comando,motivo", [
    (f"{V} /", "el caso real, sin comillas"),
    (f'{V} "/"', "FALSO NEGATIVO del 4-sep: la comilla rompia el ancla"),
    (f"{V} $HOME", "el typo del 9-ago que casi borra /home/dadito"),
    (f'eval "{V} /"', "lo citado que SI se ejecuta"),
    (f'psql -c "{D} x"', "un -c ejecuta lo que recibe"),
])
def test_qa_positive_el_patron_EJECUTABLE_sigue_denegado(comando, motivo):
    assert decide(comando) == "deniega", motivo


# ────────── qa_negative: citar el patron NO es ejecutarlo ──────────

@pytest.mark.parametrize("comando,motivo", [
    (f"scripts/seal_send.py NEXUS equipo 'nunca corras {V} /'",
     "CITAR el patron en un aviso: el falso positivo que nos enmudecia"),
    ("cat <<'EOF' > a.md\n" + f"ojo con {D} en produccion\n" + "EOF",
     "un heredoc CON COMILLAS que documenta el peligro"),
])
def test_qa_negative_el_patron_dentro_de_un_literal_NO_se_bloquea(comando, motivo):
    assert decide(comando) == "permite", motivo


# ───────────────── qa_control: el arnes distingue ─────────────────

def test_control_no_vacuo_el_arnes_distingue():
    """Sin este control, un guardian que denegara TODO pasaria los positivos y
    uno que permitiera TODO pasaria los negativos. Aca se exige que el MISMO
    arnes de las dos respuestas."""
    assert decide("ls -la") == "permite"
    assert decide("git archive HEAD | tar -x -C /tmp/x") == "permite", \
        "rutina real de mutacion: si esto se bloquea, el arnes de mutacion muere"
    assert decide(f"{V} /") == "deniega"


def test_control_el_guardian_esta_VIVO_en_el_subproceso():
    """Un hook que crashea devuelve stdout vacio, y 'vacio' se lee como permite.
    Este brazo exige que exista al menos UNA denegacion real: si el hook se
    rompiera, todos los negativos seguirian verdes y nadie lo notaria."""
    p = subprocess.run(
        [PY_BIN, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": f"{V} /"}}),
        capture_output=True, text=True, timeout=30,
    )
    assert p.returncode == 0, f"el hook fallo: {p.stderr[:200]}"
    assert "deny" in (p.stdout or ""), "el hook no denego nada: puede estar crasheando"
