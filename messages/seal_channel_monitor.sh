#!/bin/bash
# seal_channel_monitor.sh — Daemon exclusivo de canal por agente
# Llamado por systemd: seal-channel-monitor@ADA.service
# Escribe eventos filtrados a /tmp/seal_events_{AGENT}.log
# El agente lee ESE archivo — nunca el JSONL crudo.

set -euo pipefail

AGENT="${1:?Uso: $0 AGENT}"
CHANNEL="/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
FILTER="/home/dadito/IA/proyecto-seal/messages/seal_monitor_filter.py"
OUTPUT="/tmp/seal_events_${AGENT}.log"
PYTHON="/home/dadito/IA/seal-spark/.venv/bin/python3"

# Fallback a python3 del sistema si el venv no existe
[ -x "$PYTHON" ] || PYTHON="python3"

# Limpiar archivo al arrancar (no acumular eventos viejos del boot anterior)
: > "$OUTPUT"

echo "[$(date '+%H:%M:%S')] seal_channel_monitor ${AGENT} iniciado — escribiendo a ${OUTPUT}" >&2

exec tail -n 0 -F "$CHANNEL" | "$PYTHON" "$FILTER" --agent "$AGENT" >> "$OUTPUT"
