#!/bin/bash
# seal_monitor_connect.sh — Conecta al monitor externo con exclusividad garantizada
# Uso: bash seal_monitor_connect.sh ADA
# Garantía: si ya hay una instancia corriendo, esta sale inmediatamente (flock -n)
# El systemd service seal-channel-monitor@{AGENT} alimenta el archivo de eventos.

AGENT="${1:?Uso: $0 AGENT}"
LOCKFILE="/tmp/seal_monitor_connect_${AGENT}.lock"
EVENTS="/tmp/seal_events_${AGENT}.log"

# Si el archivo de eventos no existe, el servicio systemd aún no arrancó
if [ ! -f "$EVENTS" ]; then
    echo "[seal_monitor_connect] WARN: $EVENTS no existe — esperando servicio..." >&2
    sleep 3
fi

# flock -n: no-blocking — si ya hay una instancia corriendo, salir inmediatamente
# Esto garantiza a nivel OS que solo hay UN tail por agente, incluso si Monitor
# tool es llamado múltiples veces en la misma sesión de Claude
exec flock -n "$LOCKFILE" tail -n 0 -F "$EVENTS"
