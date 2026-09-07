#!/usr/bin/env bash
# Trinquete de transito ficha -> codigo, para el pre-commit.
#
# Por que NO bloquea con los 11 rojos de hoy: un gate que impide TODO commit se
# apaga en una hora, y un control que todos puentean certifica en vez de frenar.
# Bloquea solo lo que EMPEORA respecto de la linea base.
#
# Por que falla CERRADO sin linea base: es el defecto que este mismo verificador
# caza en gate.py:263 (`return []` = "sin regresion" cuando falta la base).
#
# LIMITE DECLARADO: lee el arbol de trabajo, no el indice.
set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
python3 "$REPO/tools/transito/transito_ficha_codigo.py" --ratchet
rc=$?
case "$rc" in
  0) exit 0 ;;
  2) echo "[TRANSITO] commit BLOQUEADO: introduce una regla nueva en rojo." >&2 ;;
  3) echo "[TRANSITO] commit BLOQUEADO: falta la linea base (falla cerrado a proposito)." >&2 ;;
  *) echo "[TRANSITO] commit BLOQUEADO: el verificador salio con rc=$rc." >&2 ;;
esac
echo "[TRANSITO] para saltarlo deliberadamente:  git commit --no-verify" >&2
exit 1
