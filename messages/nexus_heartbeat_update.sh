#!/bin/bash
# nexus_heartbeat_update.sh — Escribe heartbeat NEXUS a event_log + JSON (dual write)

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_JSON="$MESSAGES_DIR/nexus_claude_heartbeat.json"

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")

# El guard EXIGE `runtime_detection_status` y este escritor no lo emitia: por eso
# `heartbeat_ok` daba 0 de 5 aunque los cinco latidos estuvieran al dia en
# event_log (medido 7-sep 15:01). Y publicaba un PID tomado con `head -1`, es
# decir el PRIMERO, sin comprobar que fuera el UNICO: afirmaba identidad sin
# medirla.
#
# Contrato del guard: solo `present_unique` puede llevar PID; `present_unique` y
# `present_ambiguous` exigen alive=true.
NEXUS_PIDS=$(ps -C claude -o pid= -o args= 2>/dev/null | awk '/--name NEXUS/{print $1}')
NEXUS_N=$(printf '%s\n' "$NEXUS_PIDS" | grep -c '[0-9]' || true)
NEXUS_PID=$(printf '%s\n' "$NEXUS_PIDS" | head -1 | tr -d ' ')
if [ -z "$NEXUS_PID" ]; then
  KITTY_SOCK="/tmp/seal-nexus-kitty.sock"
  KITTY_PID=$(pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null | head -1)
  if [ -n "$KITTY_PID" ]; then
    for BASH_PID in $(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null); do
      C_PID=$(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '/claude/{print $1}' | head -1)
      [ -n "$C_PID" ] && NEXUS_PID="$C_PID" && break
    done
  fi
fi
if [ "${NEXUS_N:-0}" -eq 1 ]; then
  RUNTIME_STATUS="present_unique"; ALIVE_JSON="true"
elif [ "${NEXUS_N:-0}" -gt 1 ]; then
  # Hay asiento, pero no puedo decir CUAL: no se publica PID.
  RUNTIME_STATUS="present_ambiguous"; ALIVE_JSON="true"; NEXUS_PID=""
elif [ -n "$NEXUS_PID" ]; then
  # Llegado por la terminal kitty: no prueba presencia NI ausencia del asiento.
  RUNTIME_STATUS="indeterminate"; ALIVE_JSON="true"; NEXUS_PID=""
else
  RUNTIME_STATUS="absent"; ALIVE_JSON="false"; NEXUS_PID=""
fi

HB_MSG="NEXUS ${ALIVE_JSON} — GPU ${GPU_TEMP}C ${GPU_UTIL}%"
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('NEXUS', {'source': 'timer', 'gpu_temp': '${GPU_TEMP}', 'gpu_util': '${GPU_UTIL}', 'alive': '$ALIVE_JSON' == 'true'}, '${HB_MSG}')
print('[nexus_heartbeat] beat written to event_log')
"

# /tmp ts file — zero-token liveness signal (spec_heartbeat_zero_token)
date +%s > /tmp/nexus_heartbeat.ts
echo "{\"agent\":\"NEXUS\",\"ts\":$(date +%s),\"iso\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"alive\":${ALIVE_JSON}}" \
  >> "$MESSAGES_DIR/nexus_heartbeat.jsonl"

cat > "$HB_JSON" << EOF
{
  "agent": "NEXUS",
  "alive": ${ALIVE_JSON},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime_detection_status": "${RUNTIME_STATUS}",
  "process_pid": "${NEXUS_PID:-0}",
  "gpu_temp": ${GPU_TEMP},
  "gpu_util": ${GPU_UTIL}
}
EOF
