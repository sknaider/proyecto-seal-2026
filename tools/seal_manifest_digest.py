#!/usr/bin/env python3
"""Calcula el `manifest_digest` de un manifiesto EXACTAMENTE como lo hace el gate.

POR QUE EXISTE (NEXUS, 9-sep-2026):
Dos agentes distintos calcularon este hash a mano y les dio mal, cuatro veces en
dos dias:

    ALICE  8-sep   pidio re-firmar algo que estaba bien
    JARVIS 9-sep   pidio escribir un digest que el gate RECHAZA (probado:
                   con su valor -> independent_review_receipt_stale)

Siempre por lo mismo: `json.dumps` sin `ensure_ascii=False`. Los manifiestos
llevan acentos -las evidencias se escriben en castellano-, y sin esa bandera los
caracteres se escapan a \\uXXXX y el hash cambia.

**Recordar una bandera es criterio; importar la funcion del gate es mecanismo.**
Este script es la version de linea de comandos de ese mecanismo, para quien
prefiera un comando antes que un import.

Uso:
    python3 tools/seal_manifest_digest.py quality/manifests/<archivo>.json
    python3 tools/seal_manifest_digest.py <archivo>.json --verificar
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def digest_de(ruta: pathlib.Path) -> str:
    """El digest segun el gate. NO se reimplementa: se importa."""
    sys.path.insert(0, str(RAIZ))
    from quality_gate.gate import _review_digest
    return _review_digest(json.loads(ruta.read_text(encoding="utf-8")))


def guardado_en(ruta: pathlib.Path) -> str | None:
    m = json.loads(ruta.read_text(encoding="utf-8"))
    return ((m.get("review") or {}).get("receipt") or {}).get("manifest_digest")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifiesto")
    ap.add_argument("--verificar", action="store_true",
                    help="compara con el que ya esta escrito y sale 1 si difiere")
    a = ap.parse_args(argv)
    p = pathlib.Path(a.manifiesto)
    if not p.is_file():
        print(f"no existe: {p}", file=sys.stderr)
        return 2
    calculado = digest_de(p)
    if not a.verificar:
        print(calculado)
        return 0
    actual = guardado_en(p)
    if actual == calculado:
        print(f"OK  {p.name}: el digest guardado coincide")
        return 0
    print(f"DIFIERE  {p.name}\n  guardado : {actual}\n  correcto : {calculado}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
