#!/usr/bin/env bash
# Corre los tres detectores nacidos del borrado del 7-sep-2026 y AVISA SOLO
# SI HAY HALLAZGO (regla de William, 2-ago: brief por hallazgo, no por reloj).
#
# POR QUE ESTE ARCHIVO EXISTE: los tres detectores encontraron perdidas reales
# el dia que se escribieron y despues no los corre nadie. Un detector que no
# se ejecuta no es una defensa: es documentacion de una defensa.
set -uo pipefail
REPO=/home/dadito/IA/proyecto-seal
SP=/home/dadito/IA/seal-spark/.venv/lib/python3.12/site-packages
salida=$(mktemp -t seal-integridad-XXXXXX)   # /tmp lo barre tmpfiles.d; sin cleanup
hay=0

corre() {  # corre <titulo> <comando...>
  local t="$1"; shift
  local out; out=$("$@" 2>&1); local rc=$?
  # rc=3 significa "hay pendientes de clasificar", no "hay un hallazgo".
  # No dispara aviso: una alarma que suena en cada corrida se silencia.
  if [ $rc -eq 3 ]; then
    { echo "== $t (solo pendientes de clasificar, sin accion)"; echo "$out"; echo; } >> "$salida"
    return 0
  fi
  if [ $rc -ne 0 ]; then
    hay=1
    { echo "== $t"; echo "$out"; echo; } >> "$salida"
  fi
}

corre "archivos que un servicio necesita y NO estan en git" \
      python3 "$REPO/tools/seal_detector_no_versionado.py" "$REPO"
corre "credenciales embebidas en unidades systemd" \
      python3 "$REPO/tools/seal_detector_credenciales_unidades.py"
corre "paquetes mutilados (importan y estan vacios)" \
      python3 "$REPO/tools/seal_detector_paquetes_vacios.py" "$SP"
# El de EXISTENCIA va aparte de los tres anteriores a proposito: los otros son
# diferenciales y un arbol VACIADO les parece sano (lo midio FABLE con un
# senuelo el 7-sep). Este es el unico que grita cuando ya no queda nada.
corre "rutas criticas que faltan (arbol vaciado)" \
      python3 "$REPO/tools/seal_detector_existencia.py"

if [ "$hay" = 0 ]; then
  echo "sin hallazgos en los tres detectores"
  exit 0
fi

MSG=$(printf '%s\n' \
  "⚠️ **Chequeo de integridad de recuperación: HAY HALLAZGOS.**" "" \
  '```console' "$(head -c 2500 "$salida")" '```' "" \
  "Los tres detectores nacieron del borrado del 7-sep. Este aviso sale SOLO cuando alguno encuentra algo.")
python3 "$REPO/scripts/seal_send.py" ALICE equipo "$MSG" \
  --channel web_chat --type alert \
  --idempotency-key "alice-integridad-$(date +%Y%m%d%H)" >/dev/null 2>&1
cat "$salida"
exit 1
