#!/bin/bash
# peer_health_check.sh — Check all 3 SEAL agents' heartbeats
# Usage: peer_health_check.sh <SELF_AGENT>
#   Reports agents with stale heartbeat (>10 min) or missing.
#   Prints OK / ALERT:<details>
# Created 2026-04-13 | Updated 2026-04-17: migrated to event_log (JSON files eliminated)

SELF="${1:-UNKNOWN}"
STALE_THRESHOLD=600  # 10 minutes

AGENTS=(JARVIS ADA ALICE DUM NEXUS)
ALERTS=()

# Check heartbeats via event_log (Soul DB) — JSON files eliminated in heartbeat unification sprint
HEARTBEAT_CHECK=$(/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import asyncio, asyncpg, json
from datetime import datetime, timezone

async def check():
    results = {}
    all_agents = ['JARVIS', 'ADA', 'ALICE', 'DUM', 'NEXUS']
    try:
        conn = await asyncpg.connect('postgresql://seal:REDACTADO@localhost:5433/seal_memory')
        for agent in all_agents:
            row = await conn.fetchrow(
                \"\"\"SELECT created_at FROM soul_v3.event_log
                WHERE agent = \$1 AND event_type = 'heartbeat'
                ORDER BY created_at DESC LIMIT 1\"\"\", agent)
            if row:
                age = (datetime.now(timezone.utc) - row['created_at'].replace(tzinfo=timezone.utc)).total_seconds()
                results[agent] = int(age)
            else:
                results[agent] = -1
        await conn.close()
    except Exception as e:
        for agent in all_agents:
            results[agent] = -2  # DB error
    print(json.dumps(results))

asyncio.run(check())
" 2>/dev/null)

for agent in "${AGENTS[@]}"; do
  [ "$agent" = "$SELF" ] && continue
  AGE=$(python3 -c "import json,sys; d=json.loads('$HEARTBEAT_CHECK'); print(d.get('$agent', -1))" 2>/dev/null)
  if [ "$AGE" = "-2" ]; then
    ALERTS+=("$agent:db_error")
  elif [ "$AGE" = "-1" ]; then
    ALERTS+=("$agent:no_heartbeat_in_db")
  elif [ "$AGE" -gt "$STALE_THRESHOLD" ]; then
    ALERTS+=("$agent:stale_${AGE}s")
  fi
done

if [ ${#ALERTS[@]} -eq 0 ]; then
  echo "OK"
  exit 0
fi

# William 2026-04-13: auto-recover stale agents, don't wait for human
RECOVERED=()
for alert in "${ALERTS[@]}"; do
  AGENT="${alert%%:*}"
  REASON="${alert##*:}"
  # Only auto-relaunch on stale heartbeat (process likely dead),
  # not on missing/unparseable (could be benign boot state)
  if [[ "$REASON" == stale_* ]]; then
    RELAUNCH_LOG="/tmp/peer_relaunch_${AGENT,,}.log"
    /home/dadito/IA/proyecto-seal/seal_relaunch.sh "$AGENT" --force >"$RELAUNCH_LOG" 2>&1 &
    RECOVERED+=("$AGENT")
  fi
done

if [ ${#RECOVERED[@]} -gt 0 ]; then
  MSG="peer_health ALERT:${ALERTS[*]} RECOVERED:${RECOVERED[*]}"
else
  MSG="peer_health ALERT:${ALERTS[*]}"
fi
echo "$MSG"

# Auto-notify William via web_chat (no Claude wake-up needed).
# Silent on network error so systemd timer doesn't flap.
curl -s --max-time 3 -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys;print(json.dumps({'from':'DUM','to':'William','type':'alert','channel':'web_chat','message':sys.argv[1]}))" "$MSG")" \
  >/dev/null 2>&1 || true

# Alerts are informational — exit 0 so systemd doesn't mark service as FAILED
exit 0
