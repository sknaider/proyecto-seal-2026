#!/usr/bin/env bash
# Un mutante = UNA arena nueva. Nunca se reutiliza una copia entre mutantes.
#
# POR QUE (7-sep-2026 20:35): monte tres mutantes en una sola copia restaurando con `cp`
# entre uno y otro. La restauracion no dejo el archivo como estaba y el tercer mutante
# heredo la permutacion del segundo: reporte un rojo que no existia y firme un recibo
# apoyado en el. ADA lo cazo por la forma de la lista observada -era literalmente mi
# mutante anterior- y ALICE lo confirmo midiendo por su lado.
#
# Una copia reutilizada NO es una copia limpia. Y el `sha256` se verifica del SUJETO y de
# los TESTS antes de cada corrida, no solo del script.
#
#   arena_por_mutante.sh <spec.json>
# spec: {"rutas": [...], "harness": [...], "mutantes": [{"id","subject","ancla","reemplazo"}]}
set -euo pipefail
SPEC="${1:?uso: arena_por_mutante.sh <spec.json>}"
# El repo sale de la UBICACION DEL SCRIPT, no del directorio actual: `git rev-parse`
# desde /tmp aborta con "no es un repositorio git" y el arnes queda inservible fuera del
# arbol. Es la clase de defecto que el 7-sep aparecio cuatro veces en cuatro duenos
# distintos, y solo se ve corriendo desde otro lado.
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
"${SEAL_PY:-/home/dadito/IA/seal-spark/.venv/bin/python3}" \
  "$(dirname "${BASH_SOURCE[0]}")/arena_por_mutante.py" "$SPEC" "$REPO"
