#!/bin/bash
# jarvis_process_heartbeat.sh — Fallback heartbeat para JARVIS
# Corre via crontab del sistema cada 3 min.
# Si el proceso claude --name JARVIS está vivo → actualiza heartbeat.
# Esto evita que DUM reinicie JARVIS en bucle cuando el loop interno falla.

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_FILE="$MESSAGES_DIR/jarvis_claude_heartbeat.json"

# Verificar si el proceso JARVIS está corriendo
JARVIS_PID=$(ps aux | grep "claude.*JARVIS" | grep -v grep | grep -v "JARVIS_MAYOR" | awk '{print $2}' | head -1)

if [ -z "$JARVIS_PID" ]; then
    # JARVIS no está corriendo — no tocar el heartbeat (DUM debe saberlo)
    exit 0
fi

# JARVIS está vivo como proceso → actualizar heartbeat
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
TS=$(date +"%Y-%m-%dT%H:%M:%S%:z")

python3 -c "
import json
data = {
    'agent': 'JARVIS',
    'source': 'process_watchdog',
    'alive': True,
    'timestamp': '${TS}',
    'pid': ${JARVIS_PID},
    'gpu_temp': ${GPU_TEMP} if '${GPU_TEMP}' != 'null' else None,
    'gpu_util': ${GPU_UTIL} if '${GPU_UTIL}' != 'null' else None,
    'note': 'fallback from process check — session loop may not be running'
}
with open('${HB_FILE}', 'w') as f:
    json.dump(data, f, indent=2)
"
