#!/bin/bash
# jarvis_process_heartbeat.sh — Fallback heartbeat JARVIS (migrado a event_log)
# Corre via crontab cada 3 min.

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"

JARVIS_PID=$(ps aux | grep "claude.*JARVIS" | grep -v grep | grep -v "JARVIS_MAYOR" | awk '{print $2}' | head -1)
if [ -z "$JARVIS_PID" ]; then exit 0; fi

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")

$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('JARVIS', {'source':'process_watchdog','pid':'$JARVIS_PID','gpu_temp':'$GPU_TEMP','gpu_util':'$GPU_UTIL'}, 'JARVIS alive — process watchdog fallback')
" 2>/dev/null
