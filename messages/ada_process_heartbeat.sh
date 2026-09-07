#!/bin/bash
# ada_process_heartbeat.sh — Fallback heartbeat para ADA (migrado a event_log)
# Corre via crontab cada 3 min. Si proceso ADA vivo → beat a event_log.

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"

ADA_PID=$(ps aux | grep "claude.*ADA" | grep -v grep | grep -v "JARVIS" | awk '{print $2}' | head -1)
if [ -z "$ADA_PID" ]; then exit 0; fi

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")

$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ADA', {'source':'process_watchdog','pid':'$ADA_PID','gpu_temp':'$GPU_TEMP','gpu_util':'$GPU_UTIL'}, 'ADA alive — process watchdog fallback')
" 2>/dev/null
