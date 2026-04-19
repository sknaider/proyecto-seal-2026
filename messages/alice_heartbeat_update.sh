#!/bin/bash
# alice_heartbeat_update.sh — Escribe heartbeat ALICE a event_log + JSON (dual write)

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_JSON="$MESSAGES_DIR/alice_claude_heartbeat.json"

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
ALICE_PID=$(ps -C claude -o pid= -o args= 2>/dev/null | awk '/--name ALICE/{print $1}' | head -1)
if [ -z "$ALICE_PID" ]; then
  KITTY_SOCK="/tmp/seal-alice-kitty.sock"
  KITTY_PID=$(pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null | head -1)
  if [ -n "$KITTY_PID" ]; then
    for BASH_PID in $(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null); do
      C_PID=$(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '/claude/{print $1}' | head -1)
      [ -n "$C_PID" ] && ALICE_PID="$C_PID" && break
    done
  fi
fi
[ -z "$ALICE_PID" ] && ALIVE_JSON="false" || ALIVE_JSON="true"

HB_MSG="ALICE ${ALIVE_JSON} — GPU ${GPU_TEMP}C ${GPU_UTIL}%"
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ALICE', {'source': 'timer', 'gpu_temp': '${GPU_TEMP}', 'gpu_util': '${GPU_UTIL}', 'alive': '$ALIVE_JSON' == 'true'}, '${HB_MSG}')
print('[alice_heartbeat] beat written to event_log')
"

cat > "$HB_JSON" << EOF
{
  "agent": "ALICE",
  "alive": ${ALIVE_JSON},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "process_pid": "${ALICE_PID:-0}",
  "gpu_temp": ${GPU_TEMP},
  "gpu_util": ${GPU_UTIL}
}
EOF
