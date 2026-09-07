#!/usr/bin/env bash
# Banco de pruebas de la guarda del arnes de mutacion.
#
# POR QUE EXISTE: el 7-sep-2026 medi seis escenarios a mano y encontre tres
# agujeros. Esa medicion vivia SOLO en mi historial de comandos, que es
# exactamente el defecto que estuvimos cerrando todo el dia: algo util que
# existe en un solo lugar y desaparece con el.
#
# QUE MIDE, y son dos preguntas distintas por escenario:
#   1. que DICE la guarda            (habilita / niega)
#   2. donde puede ESCRIBIR de verdad (creando un archivo, no leyendo permisos)
# Un escenario esta MAL cuando la guarda habilita y ademas puede escribir
# fuera de su area de trabajo.
#
# NO muta nada y NO corre el arnes: solo interroga la guarda.
set -uo pipefail
REPO=/home/dadito/IA/proyecto-seal
IMG=python:3.12-slim
COPIA=$(mktemp -d /tmp/seal-banco-arnes-XXXXXX)   # /tmp lo barre tmpfiles.d
git -C "$REPO" archive HEAD | tar -x -C "$COPIA"
chmod -R a+rX "$COPIA"; chmod a+w "$COPIA"

guarda() {  # imprime HABILITA / NIEGA / IMPORT_FALLA
  docker run --rm "$@" -w /trabajo "$IMG" python3 -c "
import sys; sys.path.insert(0,'tools')
try:
    from seal_mutacion_segura import verificar, ArnesInseguro
except Exception:
    print('IMPORT_FALLA'); raise SystemExit
try:
    verificar(arena='/trabajo'); print('HABILITA')
except ArnesInseguro: print('NIEGA')
" 2>/dev/null | tail -1
}

escribe() {  # imprime donde puede escribir
  docker run --rm "$@" -w /trabajo "$IMG" python3 -c "
import tempfile
out=[]
for d in ('/home/dadito','/mnt/spark-2','/trabajo'):
    try:
        with tempfile.NamedTemporaryFile(dir=d): out.append(d)
    except OSError: pass
print(','.join(out) or 'ninguno')
" 2>/dev/null | tail -1
}

caso() {  # caso <esperado> <etiqueta> -- <args docker...>
  local esperado="$1" etiqueta="$2"; shift 3
  local g e estado
  g=$(guarda "$@"); e=$(escribe "$@")
  if [ "$g" = "$esperado" ]; then estado="OK"; else estado="MAL"; fi
  printf '  %-46s guarda=%-9s escribe=%-28s %s\n' "$etiqueta" "$g" "$e" "$estado"
  [ "$estado" = "MAL" ] && return 1 || return 0
}

fallos=0
echo "BANCO DEL ARNES DE MUTACION — $(date '+%F %T')"
caso HABILITA "1 sin privilegios + copia en /tmp"   -- --user 65534:65534 -v "$COPIA:/trabajo" || fallos=$((fallos+1))
caso NIEGA    "2 contenedor como root"              -- --user 0:0         -v "$COPIA:/trabajo" || fallos=$((fallos+1))
caso NIEGA    "3 uid 1000 + repo REAL montado"      -- --user 1000:1000   -v "$REPO:/trabajo"  || fallos=$((fallos+1))
caso NIEGA    "4 uid 1000 + /home montado"          -- --user 1000:1000   -v "$COPIA:/trabajo" -v /home/dadito:/home/dadito || fallos=$((fallos+1))
caso NIEGA    "5 uid 1000 + NFS de respaldo"        -- --user 1000:1000   -v "$COPIA:/trabajo" -v /mnt/spark-2:/mnt/spark-2 || fallos=$((fallos+1))
printf '  %-46s ' "6 host, sin contenedor"
python3 -c "
import sys; sys.path.insert(0,'$REPO/tools')
from seal_mutacion_segura import verificar, ArnesInseguro
try:
    verificar(arena='$REPO'); print('guarda=HABILITA   <- MAL')
except ArnesInseguro: print('guarda=NIEGA      OK')"
echo
if [ "$fallos" != 0 ]; then
  echo "ESCENARIOS MAL: $fallos — la guarda habilita donde puede escribir fuera de su area"
  exit 1
fi
echo "los 6 escenarios se comportan como deben"
