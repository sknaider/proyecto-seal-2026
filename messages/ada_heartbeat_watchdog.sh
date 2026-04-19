#!/bin/bash
# ada_heartbeat_watchdog.sh — Watchdog systemd: detecta Claude loops muertos
# Si proceso ADA vivo pero beat stale >5min → escribe beat latent a event_log.

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
STALE_SECONDS=300

# 1. Proceso ADA activo?
ADA_PID=$(pgrep -f "ada.sh" 2>/dev/null | head -1)
if [ -z "$ADA_PID" ]; then
    for pid in $(pgrep -f "claude" 2>/dev/null); do
        CWD=$(readlink "/proc/$pid/cwd" 2>/dev/null)
        if [ -n "$CWD" ] && [[ "$CWD" == *"proyecto-seal"* ]] && [[ "$CWD" != *"/memory"* ]]; then
            ADA_PID=$pid; break
        fi
    done
fi
if [ -z "$ADA_PID" ]; then exit 0; fi

# 2. Último beat en event_log stale?
HB_AGE=$($VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
import asyncio
from seal_heartbeat import last_beat
async def get_age():
    row = await last_beat('ADA')
    if not row: return 9999
    from datetime import datetime, timezone
    ts = datetime.fromisoformat(row['timestamp'])
    if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - ts).total_seconds())
print(asyncio.run(get_age()))
" 2>/dev/null || echo "9999")

if [ "${HB_AGE:-9999}" -lt "$STALE_SECONDS" ]; then exit 0; fi

# 3. Loop muerto pero proceso vivo → beat latent
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")

$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ADA', {'source':'systemd_watchdog','latent':True,'claude_pid':'$ADA_PID','stale_seconds':$HB_AGE,'gpu_temp':'$GPU_TEMP'}, 'ADA latent — loops muertos, proceso vivo')
print(f'[ada_watchdog] ADA latent (pid=$ADA_PID stale=${HB_AGE}s)')
" 2>/dev/null
