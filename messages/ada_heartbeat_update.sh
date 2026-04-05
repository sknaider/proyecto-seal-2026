#!/bin/bash
# ada_heartbeat_update.sh — Actualiza ada_claude_heartbeat.json
# Llamado por el loop de Claude Code cada 5min para señalar que la sesión está viva.
# DUM Watchdog monitorea este archivo.

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_FILE="$MESSAGES_DIR/ada_claude_heartbeat.json"

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
PID=$$

python3 -c "
import json, os
data = {
    'agent': 'ADA',
    'source': 'claude_code_session',
    'alive': True,
    'timestamp': '${TS}',
    'gpu_temp': ${GPU_TEMP} if '${GPU_TEMP}' != 'null' else None,
    'gpu_util': ${GPU_UTIL} if '${GPU_UTIL}' != 'null' else None,
    'pid': ${PID},
}
with open('${HB_FILE}', 'w') as f:
    json.dump(data, f, indent=2)
print(f'[ada_heartbeat] {data[\"timestamp\"]} — ADA Claude Code alive')
"
