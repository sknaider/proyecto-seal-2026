#!/bin/bash
# ALICE autonomous heartbeat — runs every 5min via cron
HEARTBEAT_FILE="/home/dadito/IA/proyecto-seal/messages/alice_claude_heartbeat.json"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MEMORY_DIR="/home/dadito/IA/proyecto-seal/memory"
VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"

# Write to event_log (truth) — same as jarvis_heartbeat_update.sh
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ALICE', {'source': 'cron'}, 'ALICE heartbeat')
" 2>/dev/null

# JSON fallback (resurrect compat)
python3 -c "
import json
data = {'agent': 'ALICE', 'alive': True, 'timestamp': '$TIMESTAMP', 'process_pid': '', 'gpu_temp': 38, 'gpu_util': 3, 'sleep_mode': False}
with open('$HEARTBEAT_FILE', 'w') as f:
    json.dump(data, f)
"
