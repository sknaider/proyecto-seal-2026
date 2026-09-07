#!/usr/bin/env bash
# Test de la alerta de disco. Prueba la DECISION, no la publicacion.
#
# POR QUE ASI: el 7-sep probe publicando y salio mal dos veces. Primero mi
# "control positivo" no publico nada -la idempotencia deduplico- y yo lei el
# exit 0 como exito. Despues publique una alerta CRITICA FALSA al canal con los
# campos cruzados ("uso 632G% · libres 83"), que JARVIS y NEXUS tuvieron que
# desmentirle a William. Un test que publica no es un test: es un incidente.
set -uo pipefail
S=/home/dadito/IA/proyecto-seal/tools/seal_disk_alert.sh
fallas=0
chequeo() { # nombre, condicion_ok
  if [ "$2" = "ok" ]; then printf '  PASS  %s\n' "$1"; else printf '  FAIL  %s\n' "$1"; fallas=$((fallas+1)); fi
}

# 1. Disco sano (632 G libres reales, umbral 100 G) -> NO dispara.
rm -f /tmp/seal_disk_alert_last
out=$(SEAL_DISK_DRYRUN=1 "$S" 2>/dev/null)
chequeo "disco sano no dispara" "$([ -z "$out" ] && echo ok || echo no)"

# 2. Umbral por encima del libre -> dispara AVISO.
rm -f /tmp/seal_disk_alert_last
out=$(SEAL_DISK_DRYRUN=1 SEAL_DISK_WARN_GB=999999 "$S" 2>/dev/null)
chequeo "poco espacio dispara aviso" "$(echo "$out" | grep -q 'nivel=aviso' && echo ok || echo no)"

# 3. LOS CAMPOS EN SU LUGAR. Este es el test que faltaba el 7-sep:
#    la alerta rota decia "uso 632G% · libres 83" y publicaba igual.
chequeo "libres antes que uso, sin % en los GB" \
  "$(echo "$out" | grep -qE 'libres [0-9]+G +· +uso [0-9]+%' && echo ok || echo no)"

# 4. Umbral critico -> nivel critico, no aviso.
rm -f /tmp/seal_disk_alert_last
out=$(SEAL_DISK_DRYRUN=1 SEAL_DISK_CRIT_GB=999999 "$S" 2>/dev/null)
chequeo "poco espacio dispara critico" "$(echo "$out" | grep -q 'nivel=critico' && echo ok || echo no)"

# 5. NO REPETIR: mismo nivel dos veces seguidas -> la segunda calla.
rm -f /tmp/seal_disk_alert_last
SEAL_DISK_DRYRUN=1 SEAL_DISK_WARN_GB=999999 "$S" >/dev/null 2>&1
out=$(SEAL_DISK_DRYRUN=1 SEAL_DISK_WARN_GB=999999 "$S" 2>/dev/null)
chequeo "no repite el mismo nivel" "$([ -z "$out" ] && echo ok || echo no)"

rm -f /tmp/seal_disk_alert_last
printf '%s\n' "---"
[ "$fallas" -eq 0 ] && { echo "5/5 PASS"; exit 0; } || { echo "$fallas FALLAS"; exit 1; }
