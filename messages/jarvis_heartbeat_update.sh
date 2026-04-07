#!/bin/bash
# jarvis_heartbeat_update.sh — Actualiza jarvis_claude_heartbeat.json
# Llamado por el loop de Claude Code cada 5min para señalar que la sesión está viva.
# DUM Watchdog monitorea este archivo.

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_FILE="$MESSAGES_DIR/jarvis_claude_heartbeat.json"

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
TS=$(date +"%Y-%m-%dT%H:%M:%S%:z")
PID=$$

python3 -c "
import json, os
data = {
    'agent': 'JARVIS',
    'source': 'claude_code_session',
    'alive': True,
    'timestamp': '${TS}',
    'gpu_temp': ${GPU_TEMP} if '${GPU_TEMP}' != 'null' else None,
    'gpu_util': ${GPU_UTIL} if '${GPU_UTIL}' != 'null' else None,
    'pid': ${PID},
}
with open('${HB_FILE}', 'w') as f:
    json.dump(data, f, indent=2)
print(f'[jarvis_heartbeat] {data[\"timestamp\"]} — JARVIS Claude Code alive')
"

# Also write to event_log (feeds observation_analyze)
/home/dadito/IA/seal-spark/.venv/bin/python3 "$MESSAGES_DIR/event_log_write.py" \
  --agent JARVIS --type heartbeat \
  --content "JARVIS alive — GPU ${GPU_TEMP}C ${GPU_UTIL}%" 2>/dev/null &
