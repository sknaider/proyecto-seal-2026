#!/usr/bin/env python3
"""Vigila el tamaño del índice de memoria compartido y avisa ANTES del corte.

Por qué existe (2-sep-2026, NEXUS): el índice cruzó el límite de lectura de
24.400 bytes y las entradas del final quedaron invisibles — se cargaban
truncadas y nadie lo notaba. El único aviso existente es el hook del harness,
que (a) sólo dispara si el archivo se edita con las tools Edit/Write —editarlo
por bash lo apaga— y (b) se lo lleva únicamente quien edita, no el equipo.

Cinco escriben y el recorte es un evento manual que alguien tiene que
acordarse de hacer.  Esto lo vuelve una señal, no un recordatorio.

Salida: JSON a stdout.  Exit 0 = por debajo del umbral de aviso,
1 = pasado el umbral de aviso, 2 = pasado el corte de lectura (hay pérdida).
No escribe en el índice ni lo recorta: avisar es todo lo que hace.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ATENCION — PROCEDENCIA DE ESTOS NUMEROS (2-sep-2026):
#   READ_LIMIT es INFERIDO, no medido. Se sabe (verificado) que la etiqueta del
#   harness reporta BYTES en KB decimal; de ahi se ASUMIO que el corte usa la
#   misma unidad y cae en 24.400. NADIE produjo todavia un caso que muestre el
#   truncado ocurriendo en ese byte exacto.
#   Consecuencia: este guard avisa en un umbral heredado de una hipotesis. Sirve
#   como alarma temprana; NO lo cites como autoridad sobre donde corta de verdad.
#   Para medirlo hace falta un caso que DEBERIA truncarse y observar que se
#   pierde — ver "producí un caso que DEBERIA incrementar el cero" en la memoria
#   del equipo. Hasta entonces WARN_AT da el margen que cubre el error posible.
READ_LIMIT = 24_400   # INFERIDO (ver arriba)
WARN_AT    = 22_000   # margen deliberado: absorbe un READ_LIMIT real algo menor
TARGET     = 17_100

DEFAULT_INDEX = Path.home() / ".claude/projects/-home-dadito-IA-proyecto-seal/memory/MEMORY.md"


def audit(path: Path) -> dict:
    raw = path.read_bytes()
    total = len(raw)
    text = raw.decode("utf-8", errors="replace")

    links = re.findall(r"\]\(([^)]+\.md)\)", text)
    link_bytes = sum(len(l.encode()) + 3 for l in links)
    # ENTRADAS != ENLACES: varias líneas agrupan 3-4 enlaces del mismo tema.
    # El presupuesto de prosa se reparte por ENTRADA; dividir por enlaces lo
    # subestima ~45 % y hace parecer inalcanzable un objetivo que no lo es.
    # (defecto encontrado por FABLE revisando este archivo, 2-sep-2026)
    # Y la brecha CRECE con el tiempo: fusionar temas en una linea es la
    # politica de recorte del indice (20 de 67 lineas agrupan varios
    # enlaces a proposito, medido por ALICE). Un guard que divida por
    # enlaces se vuelve MAS equivocado cuanto mejor se comprime.
    # Dos numeros, dos preguntas: ENTRADAS para el presupuesto de prosa,
    # ENLACES para la perdida por truncado.
    entries = [l for l in text.splitlines() if l.startswith("- ")]

    # cuántos enlaces sobreviven al corte de lectura: el dato que importa
    visible = len(re.findall(r"\]\(([^)]+\.md)\)", raw[:READ_LIMIT].decode("utf-8", errors="ignore")))

    # enlaces rotos: apuntan a un archivo que no existe
    broken = [l for l in links if not (path.parent / l).exists()]

    if total > READ_LIMIT:
        status, code = "OVER_READ_LIMIT", 2
    elif total > WARN_AT:
        status, code = "WARN", 1
    else:
        status, code = "OK", 0

    return {
        "status": status,
        "exit_code": code,
        "path": str(path),
        "bytes": total,
        "warn_at": WARN_AT,
        "read_limit": READ_LIMIT,
        "target": TARGET,
        "headroom_to_read_limit": READ_LIMIT - total,
        "links_total": len(links),
        "links_visible": visible,
        "links_lost_to_truncation": len(links) - visible,
        "links_broken": broken,
        # el techo real no lo pone la prosa sino la CANTIDAD de entradas
        "irreducible_link_bytes": link_bytes,
        "entries": len(entries),
        "prose_budget_at_target": TARGET - link_bytes,
        "prose_bytes_per_entry_at_target": (TARGET - link_bytes) // len(entries) if entries else 0,
        "avg_entry_bytes_now": round(sum(len(e.encode()) for e in entries) / len(entries)) if entries else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--quiet-when-ok", action="store_true",
                    help="no imprime nada si está por debajo del umbral de aviso")
    args = ap.parse_args()

    if not args.index.exists():
        print(json.dumps({"status": "INDEX_MISSING", "path": str(args.index)}))
        return 3

    result = audit(args.index)
    if result["exit_code"] == 0 and args.quiet_when_ok:
        return 0
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
