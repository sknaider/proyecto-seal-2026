#!/bin/bash
# ADA autonomous heartbeat — runs every 5min via cron
HEARTBEAT_FILE="/home/dadito/IA/proyecto-seal/messages/ada_claude_heartbeat.json"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
python3 -c "
import json
data = {
    'agent': 'ADA',
    'alive': True,
    'timestamp': '$TIMESTAMP',
    'process_pid': '',
    'gpu_temp': $GPU_TEMP,
    'gpu_util': $GPU_UTIL,
    'sleep_mode': False
}
with open('$HEARTBEAT_FILE', 'w') as f:
    json.dump(data, f)
"
