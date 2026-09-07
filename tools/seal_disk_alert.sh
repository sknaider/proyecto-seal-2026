#!/usr/bin/env bash
# Alerta de disco al canal del equipo.
#
# POR QUE EXISTE: el 7-sep-2026 el disco raiz llego al 100 % con 4,7 MB libres
# y NADIE lo detecto. DUM vigila GPU, CPU y servicios; el Stability Guard mira
# unidades y latidos. El disco no lo miraba nadie: lo destapo un `pytest` que
# no pudo escribir su salida (ENOSPC). Un sistema que se entera de que se quedo
# sin disco porque un test falla, se entera tarde.
#
# UMBRAL POR ESPACIO LIBRE, NO POR PORCENTAJE (correccion de JARVIS, 00:33):
# este disco VIVE al 80-90 % porque guarda modelos de cientos de GB. Un umbral
# de "85 % usado" suena siempre y se vuelve ruido — exactamente lo que mide mi
# propio baseline del Pilar III (77 % de mis alertas no eran ni auditables).
# Lo que importa no es el porcentaje: es si el sistema PUEDE ESCRIBIR.
# 100 GB libres es sano al 97 %; 4,7 MB es fatal a cualquier porcentaje.
set -uo pipefail

LIBRE_AVISO_GB="${SEAL_DISK_WARN_GB:-100}"   # avisar por debajo de esto
LIBRE_CRITICO_GB="${SEAL_DISK_CRIT_GB:-20}"  # gritar por debajo de esto
ESTADO=/tmp/seal_disk_alert_last

uso=$(df --output=pcent / | tail -1 | tr -d ' %')
libre=$(df -h --output=avail / | tail -1 | tr -d ' ')
libre_gb=$(df --output=avail -BG / | tail -1 | tr -d ' G')

nivel=ok
[ "$libre_gb" -lt "$LIBRE_AVISO_GB" ] && nivel=aviso
[ "$libre_gb" -lt "$LIBRE_CRITICO_GB" ] && nivel=critico

# No repetir la misma alerta cada 10 min: solo avisar cuando CAMBIA el nivel.
# Un vigilante que grita igual todo el tiempo se vuelve ruido y deja de leerse
# — es la misma leccion del ratio senal/ruido del Pilar III (77 % de mis alertas
# no eran ni auditables).
previo=$(cat "$ESTADO" 2>/dev/null || echo ok)
printf '%s' "$nivel" > "$ESTADO"
[ "$nivel" = "$previo" ] && exit 0
[ "$nivel" = "ok" ] && exit 0

top=$(du -xh --max-depth=1 / 2>/dev/null | sort -rh | sed -n '2,5p' | awk '{printf "  %s  %s\n",$1,$2}')

MSG=$(printf '%s\n\n```console\nlibres %s  ·  uso %s%%\n```\n\n**Lo que mas pesa:**\n\n```console\n%s\n```\n\n**Comprobar antes de borrar nada:** que sea un duplicado (`cmp` o tamanos), que no lo use ningun proceso, y ruta LITERAL.\n' \
  "$([ "$nivel" = critico ] && echo '🔴 **DISCO CRITICO**' || echo '⚠️ **Disco alto**')" "$libre" "$uso" "$top")

# SEAL_DISK_DRYRUN=1 imprime y no publica. Existe porque probar esto publicando
# ensucia el canal del equipo Y la idempotencia deduplica el segundo intento:
# el 7-sep mi "control positivo" no publico nada y yo lo lei como exito, y en
# otra corrida publique una alerta CRITICA falsa con los campos cruzados.
if [ "${SEAL_DISK_DRYRUN:-0}" = "1" ]; then
  printf 'DRYRUN nivel=%s libre_gb=%s\n%s\n' "$nivel" "$libre_gb" "$MSG"
  exit 0
fi

/home/dadito/IA/proyecto-seal/scripts/seal_send.py ALICE equipo "$MSG" \
  --channel web_chat --type alert \
  --idempotency-key "seal-disk-${nivel}-$(date +%Y%m%d%H)" >/dev/null 2>&1 || true
exit 0
