#!/bin/bash
# seal_ada_gpu_monitor.sh — Zero-Turn GPU monitor
# Corre por systemd timer. Si GPU > 80°C → escribe a ada_gpu_alert.jsonl (FileChanged hook)
# Si temp OK → sale (0 tokens Claude)

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
ALERT_FILE="$MESSAGES_DIR/ada_gpu_alert.jsonl"
LOCK="/tmp/seal_ada_gpu_monitor.lock"

if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    [ "$LOCK_AGE" -lt 60 ] && exit 0
    rm -f "$LOCK"
fi
( set -C; echo $$ > "$LOCK" ) 2>/dev/null || exit 0
trap "rm -f $LOCK" EXIT

# Leer temperatura GPU
GPU_DATA=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null)
if [ -z "$GPU_DATA" ]; then exit 0; fi

TEMP=$(echo "$GPU_DATA" | awk -F',' '{print $1}' | tr -d ' ')

if [ -n "$TEMP" ] && [ "$TEMP" -gt 80 ] 2>/dev/null; then
    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    UTIL=$(echo "$GPU_DATA" | awk -F',' '{print $2}' | tr -d ' ')
    GPU_TEMP="$TEMP" GPU_UTIL="$UTIL" GPU_TS="$TS" GPU_ALERT_FILE="$ALERT_FILE" python3 -c "
import json, os
t = os.environ['GPU_TEMP']; u = os.environ['GPU_UTIL']
data = {'type': 'gpu_alert', 'timestamp': os.environ['GPU_TS'], 'temp': int(t), 'util': u, 'message': f'GPU ALERT: {t}°C / util {u}'}
with open(os.environ['GPU_ALERT_FILE'], 'a') as f:
    f.write(json.dumps(data, ensure_ascii=False) + '\n')
" 2>/dev/null
fi
