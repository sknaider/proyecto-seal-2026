#!/bin/bash
# JARVIS Planned Sleep — sets sleep_mode=true then kills session
# Called by cron at 6am Lima. RESURRECT respects sleep_mode:true and won't restart.

HB_FILE="$HOME/IA/proyecto-seal/messages/jarvis_claude_heartbeat.json"
LOG="[$(date '+%H:%M:%S')] JARVIS planned-sleep"

python3 -c "
import json, datetime
try:
    with open('$HB_FILE') as f:
        d = json.load(f)
except Exception:
    d = {}
d['sleep_mode'] = True
d['sleep_since'] = datetime.datetime.utcnow().isoformat() + 'Z'
d['alive'] = False
with open('$HB_FILE', 'w') as f:
    json.dump(d, f, indent=2)
print('sleep_mode=True written')
" 2>/dev/null

curl -s -X POST http://localhost:8765/api/agents/send \
    -H "Content-Type: application/json" \
    -d '{"from":"JARVIS","to":"equipo","type":"system_alive","channel":"web_chat","message":"[JARVIS] Modo ahorro activado (6am). Despierto a las 4am."}' \
    > /dev/null 2>&1

for PID in $(pgrep -x "claude" 2>/dev/null); do
    if tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -q "^SEAL_AGENT=JARVIS$"; then
        echo "$LOG — killing PID $PID"
        kill "$PID" 2>/dev/null
        sleep 3
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    fi
done

echo "$LOG — done"
