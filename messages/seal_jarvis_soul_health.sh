#!/bin/bash
# seal_jarvis_soul_health.sh — Zero-Turn SOUL health watcher (JARVIS-side)
# Corre por systemd timer. Si ALERT → escribe a jarvis_soul_alert.jsonl (FileChanged hook lo inyecta)
# Si ALL_OK → sale silencioso (0 tokens Claude)
# Espejo de seal_ada_soul_health.sh para JARVIS.

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
ALERT_FILE="$MESSAGES_DIR/jarvis_soul_alert.jsonl"
CHECK_SCRIPT="$MESSAGES_DIR/soul_health_check.sh"
LOCK="/tmp/seal_jarvis_soul_health.lock"

if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    [ "$LOCK_AGE" -lt 60 ] && exit 0
    rm -f "$LOCK"
fi
( set -C; echo $$ > "$LOCK" ) 2>/dev/null || exit 0
trap "rm -f $LOCK" EXIT

RESULT=$(bash "$CHECK_SCRIPT" 2>/dev/null | head -1)

if echo "$RESULT" | grep -q "^ALERT"; then
    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # Fix: pass via env vars — avoids shell quoting bugs if RESULT contains quotes/backslashes
    SOUL_ALERT_MSG="$RESULT" SOUL_ALERT_TS="$TS" SOUL_ALERT_FILE="$ALERT_FILE" \
    python3 -c "
import json, os
data = {
    'type': 'alert',
    'agent': 'JARVIS',
    'timestamp': os.environ['SOUL_ALERT_TS'],
    'message': os.environ['SOUL_ALERT_MSG'],
}
with open(os.environ['SOUL_ALERT_FILE'], 'a') as f:
    f.write(json.dumps(data, ensure_ascii=False) + '\n')
" 2>/dev/null
fi
