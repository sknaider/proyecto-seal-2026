#!/usr/bin/env bash
# Vigila la rotacion de Store-A (FAMILIA A, /run/user/1000/seal/*.token).
#
# Por que existe: el timer `seal-identity-rotate.timer` quedo habilitado el
# 29-ago 05:32 y su PRIMER `rotated` real cae ~3-sep sobre las credenciales
# vivas de los cuatro. Hasta hoy nadie vio a ese rotador rotar un token real
# -- las corridas dan `not_due`, que prueba abstencion correcta y no capacidad
# de actuar (FABLE, 29-ago). Sin esto, la primera accion destructiva del
# mecanismo ocurriria sin observador.
#
# Estados NOMBRADOS, no un rc suelto (patron de ALICE en su oraculo de la
# familia B): un codigo que funde "no le tocaba" con "actuo y verifique" no
# permite decir «roto y lo vi» en vez de «no fallo».
set -uo pipefail

STORE="${SEAL_STOREA_DIR:-/run/user/1000/seal}"
VENTANA_H="${SEAL_STOREA_WINDOW_H:-72}"

clasifica() {  # clasifica <dir> ; imprime ESTADO y devuelve rc
  # La lista se lee EN CADA LLAMADA, no al arrancar el script: el self-test la
  # cambia por caso, y con la captura temprana clasifica seguia buscando los
  # cuatro agentes reales dentro de la fixture -> 4 ilegibles -> rc=3 siempre.
  local AGENTES="${SEAL_STOREA_AGENTS:-ADA ALICE JARVIS NEXUS}"
  local dir="$1" ahora vencidos en_ventana sanos meta exp faltan
  ahora=$(date -u +%s); vencidos=0; en_ventana=0; sanos=0; faltan=0
  for ag in $AGENTES; do
    meta="$dir/$ag.token.meta.json"
    if [ ! -r "$meta" ]; then faltan=$((faltan+1)); continue; fi
    exp=$(python3 -c "
import json,sys
from datetime import datetime,timezone
m=json.load(open(sys.argv[1]))
d=datetime.fromisoformat(m['expires_at'])
print(int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()))" "$meta" 2>/dev/null) || { faltan=$((faltan+1)); continue; }
    if   [ "$exp" -le "$ahora" ]; then vencidos=$((vencidos+1))
    elif [ $(( (exp - ahora) / 3600 )) -le "$VENTANA_H" ]; then en_ventana=$((en_ventana+1))
    else sanos=$((sanos+1)); fi
  done
  echo "    agentes: $AGENTES"
  echo "    sanos=$sanos  en_ventana=$en_ventana  vencidos=$vencidos  ilegibles=$faltan"
  if   [ "$faltan" -gt 0 ];    then echo "    -> ILEGIBLE_no_puedo_medir";                 return 3
  elif [ "$vencidos" -gt 0 ];  then echo "    -> FALLA_vencido_sin_rotar";                 return 1
  elif [ "$en_ventana" -gt 0 ];then echo "    -> EN_VENTANA_debe_rotar_en_la_proxima_corrida"; return 2
  else                              echo "    -> ABSTENIDO_ninguno_en_ventana";            return 0; fi
}

if [ "${1:-}" = "--self-test" ]; then
  # Fixtures con respuesta CONOCIDA: prueban el CLASIFICADOR, no el store real.
  fallos=0
  tmp=$(mktemp -d /tmp/oraculo_storeA_selftest_XXXXXX) || exit 3
  case "$tmp" in /tmp/oraculo_storeA_selftest_*) ;; *) echo "guard: ruta inesperada"; exit 3;; esac
  siembra() { python3 -c "
import json,sys
from datetime import datetime,timezone,timedelta
d,ag,h=sys.argv[1],sys.argv[2],int(sys.argv[3])
json.dump({'agent':ag,'expires_at':(datetime.now(timezone.utc)+timedelta(hours=h)).isoformat()},
          open(f'{d}/{ag}.token.meta.json','w'))"  "$1" "$2" "$3"; }
  prueba() {  # prueba <etiqueta> <rc esperado> <horas...>
    local etq="$1" esp="$2"; shift 2
    local d; d=$(mktemp -d "$tmp/caso_XXXX")
    local i=0; for h in "$@"; do siembra "$d" "AG$i" "$h"; i=$((i+1)); done
    SEAL_STOREA_AGENTS=$(seq -f 'AG%g' 0 $(( $# - 1 )) | tr '\n' ' ')
    export SEAL_STOREA_AGENTS
    clasifica "$d" >/dev/null 2>&1; local rc=$?
    if [ "$rc" = "$esp" ]; then echo "  OK   $etq (rc=$rc)"; else echo "  FALLA $etq: esperaba $esp, dio $rc"; fallos=$((fallos+1)); fi
  }
  prueba "VERDE  ninguno en ventana"      0  500 500
  prueba "ROJO-A uno EN VENTANA"          2  500  10
  prueba "ROJO-B uno VENCIDO"             1  500  -5
  # ROJO-C necesita una meta REALMENTE ilegible. Mi primera version sembraba
  # una meta sana y esperaba rc=3: el caso no existia y el self-test lo cazo.
  d_ile=$(mktemp -d "$tmp/caso_ile_XXXX")
  printf '{"agent": "AG0", "expires_at": ' > "$d_ile/AG0.token.meta.json"   # JSON truncado
  SEAL_STOREA_AGENTS="AG0" clasifica "$d_ile" >/dev/null 2>&1; rc_ile=$?
  if [ "$rc_ile" = 3 ]; then echo "  OK   ROJO-C meta ilegible (rc=3)"; \
  else echo "  FALLA ROJO-C: esperaba 3, dio $rc_ile"; fallos=$((fallos+1)); fi
  rm -rf "$tmp"
  echo "  fallos=$fallos"
  [ "$fallos" -eq 0 ] || exit 1
  exit 0
fi

echo "=== oraculo rotacion Store-A  ($(date -Is)) ==="
clasifica "$STORE"
