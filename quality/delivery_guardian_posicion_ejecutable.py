#!/usr/bin/env python3
"""Delivery por EFECTO del carril B: se invoca el HOOK REAL, no la funcion.

POR QUE POR SUBPROCESO Y NO IMPORTANDO: el hook lo ejecuta Claude Code como un
proceso aparte, leyendo JSON por stdin. Un test que importa `check_bash_safety`
prueba la funcion; esto prueba **la puerta que efectivamente corre**.

LOS PATRONES SE ARMAN EN RUNTIME. Escritos literales, el guardian bloquea el
comando que lanza este mismo script -- le paso a NEXUS 4 veces, a ADA 2 y a
JARVIS 1, todas escribiendo SOBRE el guardian. Es el defecto que este carril
cierra, y hasta que este desplegado sigue mordiendo a quien lo prueba.
"""
import json
import pathlib
import subprocess
import sys

HOOK = pathlib.Path(__file__).resolve().parents[1] / "memory" / "pre_tool_hook.py"
PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"
V = "rm" + " -rf"
D = "DR" + "OP TABLE"


def decide(comando: str) -> str:
    p = subprocess.run(
        [PY, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": comando}}),
        capture_output=True, text=True, timeout=30,
    )
    salida = (p.stdout or "").strip()
    if salida in ("", "{}"):
        return "permite"
    try:
        d = json.loads(salida)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return f"ilegible:{salida[:40]}"
    return "deniega" if d == "deny" else d


CASOS = [
    # (comando, esperado, por que importa)
    (f"scripts/seal_send.py NEXUS equipo 'nunca corras {V} /'", "permite",
     "CITAR el patron en un aviso: el falso positivo que nos enmudecia"),
    ("cat <<'EOF' > a.md\n" + f"ojo con {D} en produccion\n" + "EOF", "permite",
     "un heredoc que documenta el peligro"),
    (f"{V} /", "deniega", "el caso real, sin comillas"),
    (f'{V} "/"', "deniega", "FALSO NEGATIVO hallado el 4-sep: la comilla rompia el ancla"),
    (f"{V} $HOME", "deniega", "el typo del 9-ago que casi borra /home/dadito"),
    (f'eval "{V} /"', "deniega", "lo citado que SI se ejecuta"),
    (f'psql -c "{D} x"', "deniega", "un -c ejecuta lo que recibe"),
    ("ls -la", "permite", "CONTROL: sin este, un guardian que denegara TODO pasaria los de arriba"),
    ("git archive HEAD | tar -x -C /tmp/x", "permite", "rutina real de mutacion"),
]


def main() -> int:
    fallos = 0
    for comando, esperado, motivo in CASOS:
        obtenido = decide(comando)
        marca = "ok " if obtenido == esperado else "MAL"
        print(f"  {marca} {esperado:<8} {motivo}")
        if obtenido != esperado:
            print(f"      obtenido={obtenido!r} comando={comando.splitlines()[0][:60]!r}")
            fallos += 1
    print("delivery: passed" if not fallos else f"delivery: FAILED ({fallos})")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
