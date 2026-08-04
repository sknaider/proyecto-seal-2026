#!/usr/bin/env python3
"""Runner reproducible de los tests propios de SEAL (NEXUS, 3-ago-2026).

POR QUE EXISTE (paso 1 del gate de calidad, coordinado con ADA). Medido hoy:

    tests propios (sin vendor)   787
    versionados                  310
    SIN versionar                477   (60%)

Un test que no se puede EJECUTAR de forma reproducible no protege de nada —sólo
certifica el día que lo escribiste—. Este runner es la ENTRADA MEDIBLE sobre la
que ADA construye el gate: descubre los tests propios, los corre, y reporta
pass/fail/error en JSON estable, agnóstico de quién los escribió.

DISCIPLINA, y son las lecciones de hoy cableadas:
  - EXCLUYE vendor/site-packages/corpus/papers: contar los tests de terceros
    infla el número (me paso de 6.848 a 787 al filtrarlos).
  - Un test que ni COLECTA (import roto) NO es un pase: es un error, y se
    reporta como tal. El silencio de "0 tests corridos" no es verde.
  - Salida por EFECTO: rc del runner refleja si hubo fallos, no si el proceso
    terminó. rc=0 sólo si todos pasan y ninguno erra al colectar.

Uso:  seal_test_runner.py [--json out.json] [--solo <subdir>]
Sale: 0 todos verdes · 1 hubo fallos/errores · 3 no se pudo medir nada
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path("/home/dadito/IA/proyecto-seal")
PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"

# Árboles de TERCEROS: sus tests no son nuestros y no cuentan.
EXCLUIR = (
    "node_modules", "/.venv/", "site-packages", "/analisis/", "/corpus/",
    "/dify-ref/", "/characters/", "/papers/", "/.git/", "__pycache__",
)


def descubrir(raiz: Path, solo: str | None) -> list[Path]:
    tests = []
    base = raiz / solo if solo else raiz
    for p in base.rglob("test_*.py"):
        s = str(p)
        if any(x in s for x in EXCLUIR):
            continue
        tests.append(p)
    return sorted(tests)


def _estilo(t: Path) -> str:
    """pytest (tiene funciones test_*) o script (main+exit, se corre python x.py).

    LO ENCONTRE CORRIENDO ESTE RUNNER SOBRE MIS PROPIOS TESTS: los de hoy son
    SCRIPTS EJECUTABLES —`main()` + `sys.exit()`—, no funciones pytest. pytest
    los reportaba SIN_TESTS o los rompia con "unexpected SystemExit", cuando en
    realidad PASAN corridos como `python3 x.py` (rc=0). **Un runner que asume UN
    solo estilo da falsos rojos sobre el otro** — el defecto del dia: imponer un
    formato que no es el que el test usa de verdad. Se corre cada uno como SE
    CORRE, no como el runner preferiria.
    """
    try:
        txt = t.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "pytest"
    import re
    if re.search(r"^\s*def test_\w+\s*\(", txt, re.M):
        return "pytest"
    if '__name__' in txt and '__main__' in txt:
        return "script"
    return "pytest"        # por defecto pytest; si no colecta, se ve abajo


def correr_uno(t: Path) -> dict:
    """Un test = un proceso, corrido en SU estilo. PASS/FAIL/SIN_TESTS/TIMEOUT."""
    rel = str(t.relative_to(RAIZ))
    estilo = _estilo(t)
    cmd = ([PY, str(t)] if estilo == "script"
           else [PY, "-m", "pytest", str(t), "-q", "--no-header", "-p", "no:cacheprovider"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(RAIZ))
    except subprocess.TimeoutExpired:
        return {"test": rel, "estilo": estilo, "estado": "TIMEOUT", "detalle": "120s"}
    salida = (r.stdout or "") + (r.stderr or "")
    if estilo == "script":
        # script: rc del propio test. 0 pass, cualquier otro fail.
        estado = "PASS" if r.returncode == 0 else "FAIL"
    else:
        # pytest rc: 0 pass · 1 fail · 5 no-tests-collected
        estado = {0: "PASS", 5: "SIN_TESTS"}.get(r.returncode, "FAIL")
    ultima = next((l for l in reversed(salida.splitlines()) if l.strip()), "")
    return {"test": rel, "estilo": estilo, "estado": estado, "detalle": ultima[:100]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--solo", help="subdirectorio a acotar (ej. agents/NEXUS)")
    ap.add_argument("--listar", action="store_true", help="solo listar, no correr")
    args = ap.parse_args()

    tests = descubrir(RAIZ, args.solo)
    if not tests:
        print("SIN_MEDIBLE: no se descubrió ningún test propio", file=sys.stderr)
        return 3
    if args.listar:
        for t in tests:
            print(t.relative_to(RAIZ))
        print(f"\n{len(tests)} tests propios", file=sys.stderr)
        return 0

    resultados = [correr_uno(t) for t in tests]
    from collections import Counter
    cuenta = Counter(r["estado"] for r in resultados)
    malos = [r for r in resultados if r["estado"] not in ("PASS",)]

    print(f"tests: {len(tests)}  " + "  ".join(f"{k}={v}" for k, v in sorted(cuenta.items())))
    for r in malos[:40]:
        print(f"  [{r['estado']}] {r['test']}: {r['detalle']}")

    if args.json:
        args.json.write_text(json.dumps(
            {"total": len(tests), "cuenta": dict(cuenta), "resultados": resultados},
            ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if not malos else 1


if __name__ == "__main__":
    sys.exit(main())
